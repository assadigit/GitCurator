"""v0.26.0 SWOT hardening — regression tests.

Each test locks one weakness/threat fix from the v0.26.0 SWOT pass:
- the mirror <-> linking import cycle is gone (one-way import graph)
- no bare ``except:`` anywhere in the package (they swallowed
  KeyboardInterrupt / SystemExit silently)
- TLS verification is an explicit config flag (``verify_ssl``), not a
  silent hard-disable scattered through the network paths
- per-run processing summaries are capped (``summary_keep_last``)
"""

import ast
import os
import shutil
import subprocess
import sys
import unittest

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

PKG = os.path.join(APP_ROOT, "gitcurator")


class TestImportCycleBroken(unittest.TestCase):
    """v0.26.0: core.linking no longer imports core.mirror (not even
    lazily). core.mirror_keys is the shared leaf both depend on."""

    def test_linking_importable_without_mirror(self):
        """A fresh process importing linking must NOT pull mirror in."""
        code = (
            "import sys, gitcurator.core.linking; "
            "sys.exit(1 if 'gitcurator.core.mirror' in sys.modules else 0)"
        )
        env = dict(os.environ)
        env["QT_QPA_PLATFORM"] = "offscreen"
        r = subprocess.run([sys.executable, "-c", code], cwd=APP_ROOT,
                           env=env, capture_output=True, text=True,
                           timeout=120)
        self.assertEqual(r.returncode, 0,
                         f"linking still imports mirror: {r.stderr[-400:]}")

    def test_shared_key_objects_are_identical(self):
        """mirror re-exports the leaf objects — one definition, not two."""
        from gitcurator.core import mirror, mirror_keys, linking
        self.assertIs(mirror.MIRROR_KEY, mirror_keys.MIRROR_KEY)
        self.assertIs(mirror._MIRROR_RE, mirror_keys._MIRROR_RE)
        self.assertIs(mirror._unquote, mirror_keys._unquote)
        self.assertIs(linking._MIRROR_RE, mirror_keys._MIRROR_RE)
        self.assertIs(linking._unquote, mirror_keys._unquote)


class TestNoBareExcept(unittest.TestCase):
    """v0.26.0: bare ``except:`` blocks caught BaseException — a Ctrl+C
    (KeyboardInterrupt) or sys.exit() inside those handlers was silently
    swallowed. All 12 sites were narrowed to ``except Exception:``; this
    test keeps the package clean (AST-based, so strings/comments don't
    fool it)."""

    def _iter_bare(self):
        for root, dirs, files in os.walk(PKG):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for fname in sorted(files):
                if not fname.endswith(".py"):
                    continue
                path = os.path.join(root, fname)
                with open(path, encoding="utf-8") as f:
                    tree = ast.parse(f.read(), filename=path)
                for node in ast.walk(tree):
                    if (isinstance(node, ast.ExceptHandler)
                            and node.type is None):
                        yield os.path.relpath(path, APP_ROOT), node.lineno

    def test_no_bare_except_in_package(self):
        bare = list(self._iter_bare())
        self.assertEqual(
            bare, [],
            "bare `except:` catches KeyboardInterrupt/SystemExit too — "
            "use `except Exception:` (or a narrower type): "
            f"{bare}")


class TestVerifySslFlag(unittest.TestCase):
    """v0.26.0: TLS verification on the non-websites outbound paths is
    the explicit ``verify_ssl`` config key (default False = the
    historical censored-network behavior), via core.netctx."""

    def test_flag_reader_defaults(self):
        from gitcurator.core import netctx
        self.assertFalse(netctx.verify_ssl_enabled(None))
        self.assertFalse(netctx.verify_ssl_enabled({}))
        self.assertFalse(netctx.verify_ssl_enabled({'verify_ssl': False}))
        self.assertTrue(netctx.verify_ssl_enabled({'verify_ssl': True}))

    def test_context_disabled_by_default(self):
        import ssl
        from gitcurator.core import netctx
        ctx = netctx.outbound_ssl_context(None)
        self.assertEqual(ctx.verify_mode, ssl.CERT_NONE)
        self.assertFalse(ctx.check_hostname)

    def test_context_enabled_by_flag(self):
        import ssl
        from gitcurator.core import netctx
        ctx = netctx.outbound_ssl_context({'verify_ssl': True})
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(ctx.check_hostname)

    def test_config_example_documents_the_flag(self):
        import json
        example = json.load(open(os.path.join(APP_ROOT, "config.example.json"),
                                 encoding="utf-8"))
        self.assertIn('verify_ssl', example,
                      "config.example.json must document verify_ssl")
        self.assertIs(example['verify_ssl'], False,
                      "default must preserve the historical behavior")


class TestSummaryCap(unittest.TestCase):
    """v0.26.0: per-run processing_summary_*.txt files are capped to
    ``summary_keep_last`` (default 10) — they used to accumulate forever
    and VaultSeal committed every one.

    v0.66.0 — THE VAULT IS THE LIBRARY: the summaries (and the .md run
    reports) now live in the app's ``reports/`` folder (or the
    ``reports_dir`` config override — which is how these tests stay
    hermetic), not the vault root — one .md per batch in the owner's
    Obsidian vault was graph-node pollution. The cap itself is
    unchanged; the rotation reads the reports folder (its writer's new
    home), and the vault root is never listed again."""

    def _worker_stub(self, config, vault):
        from gitcurator.gui.worker.reports import WorkerReportsMixin

        class _Log:
            def emit(self, *_a, **_k):
                pass

        class _Stub(WorkerReportsMixin):
            def __init__(self):
                self.config = config
                self.total = 0
                self._processed_log = []
                self.log_message = _Log()

        return _Stub()

    def _setup(self, n, pattern, extra_cfg=None):
        """A hermetic reports dir + n seeded summaries + the stub.
        The reports folder is a SIBLING of the vault (never inside it —
        the vault root must stay empty for the empty-root assertion)."""
        import tempfile
        tmp = tempfile.mkdtemp(prefix='swot-cap-')
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        vault = os.path.join(tmp, 'vault')
        reports = os.path.join(tmp, 'reports')
        os.makedirs(vault, exist_ok=True)
        os.makedirs(reports, exist_ok=True)
        for i in range(n):
            with open(os.path.join(reports, pattern(i)), "w",
                      encoding="utf-8") as f:
                f.write("old")
        cfg = {'vault_path': vault, 'reports_dir': reports,
               'pipelines': {}}
        cfg.update(extra_cfg or {})
        return vault, reports, self._worker_stub(cfg, vault)

    def _txt_in(self, d):
        return sorted(f for f in os.listdir(d)
                      if f.startswith("processing_summary_")
                      and f.endswith(".txt"))

    def test_prune_keeps_newest_n(self):
        vault, reports, stub = self._setup(
            12, lambda i:
            f"processing_summary_2026010{i // 10}{i % 10}_0000{i:02d}.txt",
            {'summary_keep_last': 5})
        # a lookalike file that must NEVER be touched
        keep_me = os.path.join(reports, "processing_summary_notes.md")
        with open(keep_me, "w", encoding="utf-8") as f:
            f.write("not a run summary")
        out = stub._generate_summary_log()
        self.assertTrue(out and os.path.isfile(out))
        self.assertTrue(out.startswith(reports),
                        "the summary lands in the reports folder, "
                        "not the vault")
        remaining = self._txt_in(reports)
        self.assertEqual(len(remaining), 5,
                         "keep=5 keeps the 5 NEWEST files total "
                         "(including the one just written)")
        self.assertTrue(os.path.isfile(keep_me),
                        "the lookalike is never touched")
        # the vault root never received a single file
        self.assertEqual(os.listdir(vault), [])

    def test_default_cap_is_ten(self):
        vault, reports, stub = self._setup(
            20, lambda i:
            f"processing_summary_2026010{i // 10}{i % 10}_0000{i % 10:02d}.txt")
        stub._generate_summary_log()
        remaining = self._txt_in(reports)
        self.assertEqual(len(remaining), 10,
                         "default keep=10: the 10 newest, including "
                         "the one just written")

    def test_zero_disables_pruning(self):
        vault, reports, stub = self._setup(
            14, lambda i: f"processing_summary_20260101_0000{i:02d}.txt",
            {'summary_keep_last': 0})
        stub._generate_summary_log()
        remaining = self._txt_in(reports)
        self.assertEqual(len(remaining), 15, "keep everything + new")

    def test_the_default_reports_home_is_outside_the_vault(self):
        from gitcurator.gui.worker.reports import WorkerReportsMixin
        from gitcurator.constants import APP_DIR
        default = WorkerReportsMixin._reports_dir()
        self.assertTrue(os.path.isabs(default))
        self.assertEqual(default, os.path.join(APP_DIR, 'reports'))
        self.assertNotIn('vault', default.lower())


class TestDevToolImportHygiene(unittest.TestCase):
    """v0.26.0: tools/diagnose_code.py ran its config load + banner at
    IMPORT time (read config.json, printed, leaked cfg/f as module
    attrs when a config.json existed). The work now lives inside
    functions behind the __main__ guard — importing must be silent and
    side-effect free."""

    def test_diagnose_code_imports_silently(self):
        code = (
            "import io, contextlib, sys\n"
            "buf = io.StringIO()\n"
            "with contextlib.redirect_stdout(buf):\n"
            "    import gitcurator.tools.diagnose_code as m\n"
            "out = buf.getvalue()\n"
            "bad = out or hasattr(m, 'API_ID') or hasattr(m, 'cfg') "
            "or hasattr(m, 'f')\n"
            "sys.exit(1 if bad else 0)\n"
        )
        env = dict(os.environ)
        env["QT_QPA_PLATFORM"] = "offscreen"
        r = subprocess.run([sys.executable, "-c", code], cwd=APP_ROOT,
                           env=env, capture_output=True, text=True,
                           timeout=120)
        self.assertEqual(r.returncode, 0,
                         f"import has side effects: {r.stdout[-300:]} "
                         f"{r.stderr[-300:]}")


if __name__ == "__main__":
    unittest.main()
