#!/usr/bin/env python3
"""
test_cli.py — CLI smoke tests (v32.3 CLI hardening).

Locks down the Phase-4 CLI fixes so they cannot regress:

  - build_arg_parser surface: --vault required, --config defaults to the
    APP_DIR-anchored CONFIG_FILE (the root cause of the original
    "Config file not found" failure), --help exits 0 with the usage text
  - resolve_app_path: cwd-first resolution with the APP_DIR fallback
  - `python main.py --help` / `-h` / `--headless` (usage error) via a real
    subprocess of main.py — all answered by argparse BEFORE any PyQt6
    import, so these run on a bare Python install (CI needs zero pip)
  - `--headless --config <missing>`: exits 1 with the as-typed path in
    the error (PyQt6-guarded — the heavy imports happen on that path)

Run:  python -m unittest tests.test_cli -v
   or: python tests/test_cli.py
"""

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest

# Make the app dir importable no matter where we run from.
_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from gitcurator.constants import APP_DIR, CONFIG_FILE, resolve_app_path
from gitcurator.cli import build_arg_parser

try:
    import PyQt6  # noqa: F401
    _HAVE_PYQT = True
except ImportError:
    _HAVE_PYQT = False

_MAIN_PY = os.path.join(_APP_DIR, "main.py")
_REPO_ROOT = os.path.dirname(_APP_DIR)


# ---------------------------------------------------------------------------
# build_arg_parser (pure — runs everywhere, CI included)
# ---------------------------------------------------------------------------

class TestBuildArgParser(unittest.TestCase):

    def test_config_default_is_app_dir_anchored(self):
        """v32.3 root-cause fix: the default was the CWD-relative
        'config.json' — running from any directory other than app/ died
        with 'Config file not found'. It must be the anchored CONFIG_FILE."""
        parsed = build_arg_parser().parse_args(["--vault", "V"])
        self.assertEqual(parsed.config, CONFIG_FILE)
        self.assertTrue(os.path.isabs(parsed.config))
        self.assertEqual(
            os.path.dirname(parsed.config).rstrip(os.sep),
            APP_DIR.rstrip(os.sep),
        )

    def test_vault_is_required(self):
        with self.assertRaises(SystemExit) as cm:
            build_arg_parser().parse_args([])
        self.assertEqual(cm.exception.code, 2)

    def test_headless_flag_and_mode_args(self):
        parsed = build_arg_parser().parse_args([
            "--headless", "--vault", "V", "--from-id", "123",
            "--to-id", "456",
        ])
        self.assertTrue(parsed.headless)
        self.assertEqual(parsed.from_id, 123)
        self.assertEqual(parsed.to_id, 456)

    def test_help_exits_zero_with_usage(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as cm:
                build_arg_parser().parse_args(["--help"])
        self.assertEqual(cm.exception.code, 0)
        text = out.getvalue()
        for token in ("--headless", "--vault", "--config", "--import-file",
                      "--single-id", "--offset-start"):
            self.assertIn(token, text)

    def test_explicit_config_overrides_default(self):
        parsed = build_arg_parser().parse_args(
            ["--vault", "V", "--config", "my.json"])
        self.assertEqual(parsed.config, "my.json")


# ---------------------------------------------------------------------------
# resolve_app_path (pure — runs everywhere)
# ---------------------------------------------------------------------------

class TestResolveAppPath(unittest.TestCase):

    def test_absolute_passthrough(self):
        self.assertEqual(resolve_app_path("/abs/path.json"), "/abs/path.json")
        self.assertEqual(resolve_app_path(""), "")

    def test_relative_existing_in_cwd_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = os.path.join(tmp, "marker_9x7.json")
            with open(marker, "w") as f:
                f.write("{}")
            old = os.getcwd()
            os.chdir(tmp)
            try:
                # exists as typed in the CWD -> the cwd form wins
                got = resolve_app_path("marker_9x7.json")
                self.assertEqual(os.path.abspath(got), marker)
            finally:
                os.chdir(old)

    def test_relative_existing_only_in_app_dir_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = os.getcwd()
            os.chdir(tmp)  # a cwd where no ./config.json exists
            try:
                # config.json exists in APP_DIR -> the anchor kicks in
                got = resolve_app_path("config.json")
                self.assertEqual(got, CONFIG_FILE)
            finally:
                os.chdir(old)

    def test_nonexistent_relative_returns_as_typed(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = os.getcwd()
            os.chdir(tmp)
            try:
                got = resolve_app_path("definitely_not_here_9x7.json")
                self.assertEqual(
                    got, os.path.join(tmp, "definitely_not_here_9x7.json"))
                self.assertFalse(os.path.exists(got))
            finally:
                os.chdir(old)


# ---------------------------------------------------------------------------
# main.py subprocess behavior (argparse answered BEFORE any Qt import —
# these run on a bare Python install, CI included)
# ---------------------------------------------------------------------------

class TestMainPySubprocess(unittest.TestCase):

    def _run(self, *args, cwd=_REPO_ROOT):
        return subprocess.run(
            [sys.executable, _MAIN_PY, *args],
            cwd=cwd, capture_output=True, text=True, timeout=60,
        )

    def test_help_from_repo_root(self):
        """The README promise: 'python main.py --help  :: headless mode
        options'. Pre-fix this launched the GUI (and crashed on
        display-less machines with a Qt platform-plugin error)."""
        proc = self._run("--help")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("--headless", proc.stdout)
        self.assertIn("--vault", proc.stdout)
        self.assertNotIn("Fatal error", proc.stderr)

    def test_short_help_flag(self):
        proc = self._run("-h")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("headless", proc.stdout)

    def test_headless_without_vault_is_usage_error(self):
        proc = self._run("--headless")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--vault", proc.stderr)

    def test_help_does_not_import_qt(self):
        """--help must not even try to load PyQt6 (works on bare Python)."""
        proc = self._run("--help")
        self.assertEqual(proc.returncode, 0)
        self.assertNotIn("PyQt6 is not installed", proc.stderr)
        self.assertNotIn("PyQt6", proc.stderr)


# ---------------------------------------------------------------------------
# End-to-end headless failure path (needs PyQt6 — skips without)
# ---------------------------------------------------------------------------

@unittest.skipUnless(_HAVE_PYQT, "PyQt6 not installed — headless end-to-end needs the pipeline imports")
class TestHeadlessEndToEnd(unittest.TestCase):

    def test_missing_config_exits_1_with_typed_path(self):
        """From the REPO ROOT (the original bug scenario): a missing
        --config must exit 1 and name the path exactly as typed."""
        proc = subprocess.run(
            [sys.executable, _MAIN_PY, "--headless",
             "--import-file", "urls.txt",
             "--vault", os.path.join(_REPO_ROOT, "_cli_test_vault"),
             "--config", "no_such_config_9x7.json"],
            cwd=_REPO_ROOT, capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Config file not found", proc.stdout + proc.stderr)
        # the as-typed (cwd-relative) path stays in the message
        self.assertIn("no_such_config_9x7.json",
                      proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
