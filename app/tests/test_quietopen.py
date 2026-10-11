"""tests/test_quietopen.py — v0.66.0 THE QUIET OPEN.

The owner's law (session, verbatim): "the system, in the startup must
not try to connect itself, it must wait for user to click scan or
fetch."

The old behavior: 2 seconds after the window appeared,
``_startup_auto_check`` fired — a proxy socket probe with MODAL error
dialogs ("Proxy Required" / "Proxy Unreachable"), then a full Telegram
bot-queue fetch (a live network login) — before the owner had touched
anything.

The new law: the app opens Idle and silent. Every connection is a
click: SYNC (the bot fetch), Scan (the vault pass), Test Connection.
This suite pins the retirement so it can never creep back.
"""

import inspect
import unittest

from gitcurator.gui import app as gui_app
from gitcurator.gui.main_window import bot_queue as _bq


class TestTheQuietOpen(unittest.TestCase):

    def test_the_startup_auto_check_method_is_gone(self):
        self.assertFalse(
            hasattr(gui_app.MainWindow, '_startup_auto_check'),
            "MainWindow._startup_auto_check must not exist — the app "
            "must not connect itself on startup (the owner's law)")

    def test_initui_schedules_no_startup_connection(self):
        src = inspect.getsource(gui_app.MainWindow.initUI)
        # the SCHEDULING must be gone (a comment naming the retired
        # method is fine — a singleShot wiring it is not). The only
        # legal callers of check_bot_queue are CLICK handlers.
        self.assertNotIn('QTimer.singleShot(2000, self._startup_auto_check)',
                         src)
        self.assertNotIn('singleShot', src,
                         "initUI must never schedule deferred work — the "
                         "app opens quiet; connections are clicks only "
                         "(the _run_mirror_timer is a GUI-state mirror, "
                         "wired above via QTimer(self), not singleShot)")

    def test_the_quiet_open_note_lives_in_initui(self):
        src = inspect.getsource(gui_app.MainWindow.initUI)
        self.assertIn('THE QUIET OPEN', src)

    def test_bot_queue_module_no_longer_carries_the_auto_check(self):
        src = inspect.getsource(_bq)
        self.assertNotIn('def _startup_auto_check', src)

    def test_user_clicks_still_reach_check_bot_queue(self):
        # the manual doors stay wired: the Settings queue button and the
        # hero SYNC flow both call check_bot_queue (a click, never a timer)
        ui_src = inspect.getsource(gui_app.MainWindow.initUI)
        self.assertIn('check_queue_btn.clicked.connect(self.check_bot_queue)',
                      ui_src)
        self.assertTrue(callable(getattr(gui_app.MainWindow,
                                         'check_bot_queue', None)))


if __name__ == '__main__':
    unittest.main()
