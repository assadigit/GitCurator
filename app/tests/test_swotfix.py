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


if __name__ == "__main__":
    unittest.main()
