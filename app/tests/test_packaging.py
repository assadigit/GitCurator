"""Packaging — the first Windows zip (v0.14.1).

Acceptance under test (the owner's ask: "the first .zip version to
test locally"):

- the zip builds from tracked files ONLY (git ls-files) — local junk,
  cache.db, reports/ and any live credential store can never ship;
- developer-only / optional-infra trees are excluded (tests/, _attic/,
  cloudflare-bot/, list of changes.txt);
- a tracked secret-bearing file under app/ refuses the build outright;
- every .bat in the zip is pure ASCII with CRLF endings (the v0.09.1
  codepage bug class can never ship again);
- the build is deterministic — two builds from the same tree are
  byte-identical (sha256);
- everything a first-run Windows user needs is present: main.py, the
  gitcurator package, requirements.txt, config.example.json, the six
  launchers, the quickstart, taxonomy, prompts, VERSION.
"""

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_ROOT not in sys.path:
    sys.path.insert(0, _APP_ROOT)

from gitcurator.tools.build_zip import (                     # noqa: E402
    ZIP_ROOT, _bat_bytes, build_zip, is_banned, is_excluded)

_REPO_ROOT = os.path.dirname(_APP_ROOT)
_BATS = [
    "1-INSTALL.bat",
    "GitCurator.bat",
    "GitCurator-DRY-RUN.bat",
    "GitCurator-CLI.bat",
    "GitCurator-CLI-Setup.bat",
    "Start-GitCurator-CLI.bat",
]


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class BatRules(unittest.TestCase):
    """_bat_bytes: the ASCII + CRLF enforcement for launchers."""

    def test_lf_normalized_to_crlf(self):
        out = _bat_bytes(b"@echo off\r\nrem one\nrem two\n")
        self.assertEqual(out, b"@echo off\r\nrem one\r\nrem two\r\n")
        # idempotent on already-CRLF input
        self.assertEqual(_bat_bytes(out), out)

    def test_non_ascii_bat_refused(self):
        with self.assertRaises(SystemExit):
            _bat_bytes("@echo off\nrem caf\xe9\n".encode("latin-1"))


class PolicyTables(unittest.TestCase):
    """is_excluded / is_banned: what ships and what can never ship."""

    def test_excluded_trees(self):
        for rel in ("app/tests/test_core.py", "app/_attic/README.md",
                    "app/cloudflare-bot/src/index.js",
                    "app/cloudflare-bot/dashboard/package.json",
                    "app/list of changes.txt"):
            self.assertTrue(is_excluded(rel), rel)

    def test_shipped_files_not_excluded(self):
        for rel in ("app/main.py", "app/gitcurator/gui/app.py",
                    "app/WINDOWS-QUICKSTART.md", "app/1-INSTALL.bat",
                    "app/requirements.txt", "app/config.example.json",
                    "app/taxonomy/website-library-categories.md",
                    "app/prompts/w01_category.txt", "app/about_me.md",
                    "app/system_prompt.txt",
                    "app/headless mode command.txt"):
            self.assertFalse(is_excluded(rel), rel)

    def test_banned_secrets(self):
        for rel in ("app/config.json", "app/config.local.json",
                    "app/installer.config.json", "app/cache.db",
                    "app/error_outbox.db", "app/.env",
                    "app/.env.local", "app/session.session",
                    "app/telethon.session-journal"):
            self.assertTrue(is_banned(rel), rel)
        # the EXAMPLE config is the template — it must ship
        self.assertFalse(is_banned("app/config.example.json"))


class SyntheticBuild(unittest.TestCase):
    """build_zip on a temp tree: layout, VERSION stamp, determinism."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pkgzip_")
        self.src = os.path.join(self.tmp, "src")
        os.makedirs(os.path.join(self.src, "gitcurator", "core"))
        files = {
            "main.py": "print('hi')\n",
            "requirements.txt": "# reqs\n",
            "config.example.json": "{}\n",
            "WINDOWS-QUICKSTART.md": "# quick\n",
            os.path.join("gitcurator", "__init__.py"): "",
            os.path.join("gitcurator", "core", "x.py"): "X = 1\n",
            "1-INSTALL.bat": "@echo off\nrem lf file\n",
        }
        for rel, text in files.items():
            p = os.path.join(self.src, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8", newline="") as fh:
                fh.write(text)
        self.sources = [
            (f"app/{rel}", os.path.join(self.src, rel)) for rel in files
        ]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_layout_and_version_stamp(self):
        out = os.path.join(self.tmp, "out", "t.zip")
        lines = []
        build_zip(self.sources, out, "9.9.9", log=lines.append)
        with zipfile.ZipFile(out) as zf:
            names = zf.namelist()
            self.assertEqual(names, sorted(names), "entries sorted")
            for rel in ("main.py", "requirements.txt",
                        "config.example.json", "WINDOWS-QUICKSTART.md",
                        "gitcurator/__init__.py", "gitcurator/core/x.py",
                        "1-INSTALL.bat", "VERSION"):
                self.assertIn(f"{ZIP_ROOT}/{rel}", names, rel)
            # the LF .bat shipped as CRLF
            bat = zf.read(f"{ZIP_ROOT}/1-INSTALL.bat")
            self.assertEqual(bat, b"@echo off\r\nrem lf file\r\n")
            # VERSION stamped from the repo, not copied as an app file
            self.assertEqual(zf.read(f"{ZIP_ROOT}/VERSION"), b"9.9.9\n")
            self.assertIsNone(zf.testzip())

    def test_deterministic_bytes(self):
        out1 = os.path.join(self.tmp, "a.zip")
        out2 = os.path.join(self.tmp, "b.zip")
        build_zip(self.sources, out1, "9.9.9", log=lambda *_: None)
        build_zip(self.sources, out2, "9.9.9", log=lambda *_: None)
        self.assertEqual(_sha256(out1), _sha256(out2))


class RealRepoBuild(unittest.TestCase):
    """The real build, run exactly as CI / the maintainer runs it."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="pkgreal_")
        cls.out = os.path.join(cls.tmp, "GitCurator-test.zip")
        proc = subprocess.run(
            [sys.executable,
             os.path.join(_APP_ROOT, "gitcurator", "tools", "build_zip.py"),
             "--out", cls.out],
            cwd=_APP_ROOT, capture_output=True, text=True, timeout=120,
        )
        cls.proc = proc
        if proc.returncode == 0:
            with zipfile.ZipFile(cls.out) as zf:
                cls.names = set(zf.namelist())
                cls.zf_bytes = {n: zf.read(n) for n in zf.namelist()}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_a_build_succeeds(self):
        self.assertEqual(self.proc.returncode, 0,
                         f"build failed:\n{self.proc.stdout}\n{self.proc.stderr}")

    def test_b_first_run_files_present(self):
        need = ["main.py", "requirements.txt", "config.example.json",
                "README.md", "WINDOWS-QUICKSTART.md", "about_me.md",
                "system_prompt.txt",
                "taxonomy/website-library-categories.md",
                "prompts/w01_category.txt", "prompts/w02_subcategory.txt",
                "prompts/w03_analyze.txt", "prompts/01_categorize.txt",
                "prompts/02_summarize.txt", "prompts/03_crosscheck.txt",
                "gitcurator/gui/app.py", "gitcurator/cli.py",
                "gitcurator/core/mirror.py",
                "gitcurator/tools/mirror_manual.py",
                "gitcurator/tools/backfill_websites.py", "VERSION"]
        need += _BATS
        for rel in need:
            self.assertIn(f"{ZIP_ROOT}/{rel}", self.names, rel)

    def test_c_excluded_and_secret_files_absent(self):
        for prefix in ("config.json", "cache.db", "error_outbox.db",
                       "installer.config.json", ".env",
                       "tests/", "_attic/", "cloudflare-bot/",
                       "reports/", ".venv/", "__pycache__/"):
            bad = [n for n in self.names
                   if n.startswith(f"{ZIP_ROOT}/{prefix}")]
            self.assertEqual(bad, [], f"{prefix} leaked: {bad}")
        self.assertNotIn(f"{ZIP_ROOT}/list of changes.txt", self.names)
        # no live session files anywhere in the zip
        sessions = [n for n in self.names if ".session" in n]
        self.assertEqual(sessions, [])

    def test_d_version_file_matches_repo(self):
        with open(os.path.join(_REPO_ROOT, "VERSION"),
                  "r", encoding="utf-8") as fh:
            repo_version = fh.read().strip()
        self.assertEqual(
            self.zf_bytes[f"{ZIP_ROOT}/VERSION"].decode("ascii").strip(),
            repo_version)

    def test_e_bats_ascii_and_crlf(self):
        for bat in _BATS:
            data = self.zf_bytes[f"{ZIP_ROOT}/{bat}"]
            data.decode("ascii")  # raises on any non-ASCII byte
            self.assertIn(b"\r\n", data, f"{bat} missing CRLF")
            self.assertNotIn(b"\r\r", data)  # no double-terminated runs
            bare_lf = data.replace(b"\r\n", b"")
            self.assertNotIn(b"\n", bare_lf, f"{bat} has a bare LF")
            self.assertNotIn(b"\r", bare_lf, f"{bat} has a bare CR")

    def test_f_zip_is_small_and_valid(self):
        self.assertLess(os.path.getsize(self.out), 5 * 1024 * 1024)
        with zipfile.ZipFile(self.out) as zf:
            self.assertIsNone(zf.testzip())


if __name__ == "__main__":
    unittest.main()
