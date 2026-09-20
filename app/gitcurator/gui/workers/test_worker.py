#!/usr/bin/env python3
"""gitcurator.gui.workers.test_worker — TestWorker.

Runs ONE blocking callable on a background QThread, streaming log lines
to the GUI via queued signals; supports interactive Telegram auth
(code_requested -> provide_code). Moved verbatim from ``gui/workers.py``.
"""

import threading
from gitcurator.gui._qt import *  # noqa: F401,F403 — QThread, pyqtSignal, …
from gitcurator.gui.workers._deps import *  # noqa: F401,F403

__all__ = ["TestWorker"]


class TestWorker(QThread):
    log_message = pyqtSignal(str, str)        # (msg, level)
    finished_signal = pyqtSignal(str, dict)   # (test_name, result_dict)
    code_requested = pyqtSignal(str)          # "CODE" or "PASSWORD"

    def __init__(self, fn, test_name: str, *args, **kwargs):
        super().__init__()
        self._fn = fn
        self._test_name = test_name
        self._args = args
        self._kwargs = kwargs
        self._code_event = threading.Event()
        self._code_response = ""

    def provide_code(self, code: str):
        """Called from the GUI thread to deliver the login code/password."""
        self._code_response = code
        self._code_event.set()

    def request_code(self, prompt_type: str = "CODE") -> str:
        """Called from the worker thread. Emits code_requested, then blocks
        until the GUI thread calls provide_code(). Returns the code, or
        empty string if the user cancelled or timed out (5 minutes)."""
        self._code_event.clear()
        self._code_response = ""
        self.code_requested.emit(prompt_type)
        timed_out = not self._code_event.wait(timeout=300)  # 5 minute timeout
        if timed_out:
            # Log the timeout so the worker can handle it
            self.log_message.emit("⏰ Auth code input timed out (5 minutes)", "warning")
        return self._code_response

    def run(self):
        try:
            result = self._fn(*self._args, **self._kwargs)
            self.finished_signal.emit(self._test_name, result or {})
        except Exception as e:
            self.finished_signal.emit(
                self._test_name,
                {"success": False, "error": f"{type(e).__name__}: {e}"}
            )
