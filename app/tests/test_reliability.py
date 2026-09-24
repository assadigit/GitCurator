#!/usr/bin/env python3
"""
test_reliability.py — regression tests for the v0.06 reliability fixes.

Locks in the two root causes behind the owner-reported v0.05 symptoms
("Another Telegram operation is already running" forever + "Another
instance is already running" on every launch):

  1. TelegramLockManager semantics (owner tracking, owner-scoped release,
     force-release, context manager) — pure stdlib, always runs.
  2. subprocess_runner deadlines (healthy child / hung child killed by the
     idle timeout / interactive-auth grace / hard cap) — pure stdlib,
     always runs. Uses throwaway fake worker scripts in a tempdir; no
     telethon needed.
  3. TestWorker BaseException guarantee (a crashing worker still emits
     finished_signal so the lock cleanup always fires) — needs PyQt6;
     auto-skipped when PyQt6 is absent (CI compiles GUI modules but does
     not install the GUI stack).

Run:  python -m unittest tests.test_reliability -v
"""

import os
import sys
import tempfile
import time
import unittest

# Make the app dir importable no matter where we run from.
_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from gitcurator.gui.telegram_lock import TelegramLockManager
from gitcurator.integrations import subprocess_runner


class _ListLog:
    """Collects (msg, level) pairs like a Qt signal would."""

    def __init__(self):
        self.lines = []

    def emit(self, msg, level):
        self.lines.append((str(msg), level))

    def __call__(self, msg, level):  # plain-callable compatibility
        self.emit(msg, level)


# ---------------------------------------------------------------------------
# 1. TelegramLockManager
# ---------------------------------------------------------------------------

class TestTelegramLockManager(unittest.TestCase):

    def setUp(self):
        self.lock = TelegramLockManager()

    def test_acquire_and_release(self):
        self.assertTrue(self.lock.acquire("bot_check"))
        self.assertTrue(self.lock.busy)
        self.assertEqual(self.lock.owner, "bot_check")
        self.assertTrue(self.lock.release("bot_check"))
        self.assertFalse(self.lock.busy)

    def test_double_acquire_denied(self):
        self.lock.acquire("first")
        self.assertFalse(self.lock.acquire("second"),
                         "a second acquire while held must be refused")
        self.assertEqual(self.lock.owner, "first",
                         "denied acquire must not steal the lock")

    def test_owner_scoped_release_protects_holder(self):
        """The v0.05 over-release bug: a finishing direct-mode batch freed
        an unrelated TestWorker's lock, enabling two telethon children on
        one session file."""
        self.lock.acquire("bot_check")
        self.assertFalse(self.lock.release("batch"),
                         "a different owner must not be able to release")
        self.assertTrue(self.lock.busy, "holder must keep the lock")
        self.assertTrue(self.lock.release("bot_check"))

    def test_release_free_lock_is_noop(self):
        self.assertFalse(self.lock.release("anything"))
        self.assertFalse(self.lock.release())  # unconditional on free lock

    def test_force_release_returns_evicted_owner(self):
        self.lock.acquire("stuck_op")
        evicted = self.lock.force_release("watchdog")
        self.assertEqual(evicted, "stuck_op")
        self.assertFalse(self.lock.busy)
        self.assertIsNone(self.lock.force_release("watchdog"))

    def test_unconditional_release(self):
        self.lock.acquire("owner_a")
        self.assertTrue(self.lock.release(None),
                        "owner=None is the legacy unconditional release")
        self.assertFalse(self.lock.busy)

    def test_context_manager_releases_on_early_return(self):
        """The 15 v0.05 leak paths modeled as one pattern: an early return
        inside the with-block must not leak the lock."""
        def leaky_validation(fail):
            with self.lock.held("preview") as held:
                if not held:
                    return "busy"
                if fail:
                    return "validation error"   # early return!
                return "ok"
        self.assertEqual(leaky_validation(True), "validation error")
        self.assertFalse(self.lock.busy,
                         "early return inside held() must release the lock")
        self.assertEqual(leaky_validation(False), "ok")
        self.assertFalse(self.lock.busy)

    def test_context_manager_releases_on_exception(self):
        try:
            with self.lock.held("worker"):
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        self.assertFalse(self.lock.busy, "exception must release the lock")

    def test_describe_and_age(self):
        self.assertEqual(self.lock.describe(), "free")
        self.lock.acquire("bot_check")
        described = self.lock.describe()
        self.assertIn("bot_check", described)
        self.assertIn("running", described)
        self.assertGreaterEqual(self.lock.age_seconds, 0.0)

    def test_is_stuck(self):
        self.lock.acquire("hung_op")
        self.assertFalse(self.lock.is_stuck(max_age=3600))
        self.assertTrue(self.lock.is_stuck(max_age=0.0))
        self.lock.release("hung_op")
        self.assertFalse(self.lock.is_stuck(max_age=0.0),
                         "a free lock is never stuck")


# ---------------------------------------------------------------------------
# 2. subprocess_runner deadlines
# ---------------------------------------------------------------------------

class TestSubprocessRunner(unittest.TestCase):
    """Fake-worker scenarios — the hung-child case is the v0.05 killer."""

    def _fake(self, tmpdir, body):
        path = os.path.join(tmpdir, "fake_worker.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
        return path

    def test_healthy_worker_returns_json(self):
        with tempfile.TemporaryDirectory() as td:
            fake = self._fake(td, (
                "import sys, time\n"
                "for i in range(2):\n"
                "    print(f'[worker] step {i}', file=sys.stderr, flush=True)\n"
                "    time.sleep(0.1)\n"
                "print('{\"success\": true, \"urls\": [\"x\"]}')\n"
            ))
            res = subprocess_runner.run_telegram_worker(
                {}, _ListLog(), worker_script=fake,
                idle_timeout=30, hard_cap=60)
        self.assertTrue(res.get("success"))
        self.assertEqual(res.get("urls"), ["x"])

    def test_hung_child_killed_by_idle_timeout(self):
        """The forever-bug: a stalled child (dead proxy) must be killed and
        reported, never block the calling thread indefinitely."""
        with tempfile.TemporaryDirectory() as td:
            fake = self._fake(td, (
                "import sys, time\n"
                "print('[worker] connecting...', file=sys.stderr, flush=True)\n"
                "time.sleep(600)\n"
            ))
            t0 = time.monotonic()
            res = subprocess_runner.run_telegram_worker(
                {}, _ListLog(), worker_script=fake,
                idle_timeout=1, hard_cap=300)   # idle floor is 5s
            elapsed = time.monotonic() - t0
        self.assertFalse(res.get("success"))
        self.assertIn("aborted", res.get("error", ""))
        self.assertLess(elapsed, 60, "idle kill must happen promptly")
        self.assertEqual(subprocess_runner.live_worker_count(), 0,
                         "the killed child must not linger in the registry")

    def test_auth_grace_not_killed_while_user_types(self):
        with tempfile.TemporaryDirectory() as td:
            fake = self._fake(td, (
                "import sys, time\n"
                "print('__NEED_CODE__', file=sys.stderr, flush=True)\n"
                "line = sys.stdin.readline().strip()\n"
                "print(f'[worker] got {line}', file=sys.stderr, flush=True)\n"
                "print('{\"success\": true, \"auth\": true}')\n"
            ))
            res = subprocess_runner.run_telegram_worker(
                {}, _ListLog(), code_callback=lambda kind: "12345",
                worker_script=fake,
                idle_timeout=1, auth_grace=30, hard_cap=60)
        self.assertTrue(res.get("success"), res)

    def test_hard_cap_kills_busy_child(self):
        with tempfile.TemporaryDirectory() as td:
            fake = self._fake(td, (
                "import sys, time\n"
                "while True:\n"
                "    print('[worker] busy', file=sys.stderr, flush=True)\n"
                "    time.sleep(0.3)\n"
            ))
            res = subprocess_runner.run_telegram_worker(
                {}, _ListLog(), worker_script=fake,
                idle_timeout=30, hard_cap=2)
        self.assertFalse(res.get("success"))
        self.assertIn("hard cap", res.get("error", ""))

    def test_missing_worker_script(self):
        res = subprocess_runner.run_telegram_worker(
            {}, _ListLog(), worker_script="/nonexistent/worker.py")
        self.assertFalse(res.get("success"))
        self.assertIn("not found", res.get("error", ""))

    def test_garbage_stdout_reported_not_crashed(self):
        with tempfile.TemporaryDirectory() as td:
            fake = self._fake(td, "print('this is not json')\n")
            res = subprocess_runner.run_telegram_worker(
                {}, _ListLog(), worker_script=fake,
                idle_timeout=30, hard_cap=60)
        self.assertFalse(res.get("success"))
        self.assertIn("exited with code", res.get("error", ""))


# ---------------------------------------------------------------------------
# 3. TestWorker always signals (needs PyQt6; skipped without it)
# ---------------------------------------------------------------------------

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    _PYQT = True
except Exception:  # pragma: no cover - CI environment
    _PYQT = False


@unittest.skipUnless(_PYQT, "PyQt6 not installed — GUI-layer check skipped")
class TestWorkerGuaranteedSignal(unittest.TestCase):
    """A worker that raises BaseException (SystemExit/KeyboardInterrupt)
    must STILL emit finished_signal — that signal is what releases the
    Telegram lock in _keep_worker's cleanup."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def _run_worker(self, raiser):
        from gitcurator.gui.app import TestWorker
        collected = []
        w = TestWorker(lambda: None, "crash_probe")
        w._fn = raiser
        w.finished_signal.connect(lambda name, res: collected.append((name, res)))
        w.start()
        w.wait(10000)
        for _ in range(25):
            self.app.processEvents()
            if collected:
                break
            time.sleep(0.02)
        return collected

    def test_system_exit_still_signals(self):
        def raiser():
            raise SystemExit(9)
        collected = self._run_worker(raiser)
        self.assertTrue(collected, "finished_signal MUST fire on SystemExit")
        name, result = collected[0]
        self.assertEqual(name, "crash_probe")
        self.assertFalse(result.get("success"))
        self.assertIn("SystemExit", result.get("error", ""))

    def test_keyboard_interrupt_still_signals(self):
        def raiser():
            raise KeyboardInterrupt()
        collected = self._run_worker(raiser)
        self.assertTrue(collected, "finished_signal MUST fire on KeyboardInterrupt")

    def test_normal_result_signals(self):
        collected = self._run_worker(lambda: {"success": True, "urls": []})
        self.assertTrue(collected)
        self.assertTrue(collected[0][1].get("success"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
