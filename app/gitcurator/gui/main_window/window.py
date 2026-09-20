#!/usr/bin/env python3
"""gitcurator.gui.main_window.window — the MainWindow assembly.

The concrete QMainWindow: ``__init__`` (state setup + initUI), the
graceful-shutdown ``closeEvent`` that unblocks every worker wait before
quitting, and ``_unblock_worker_for_shutdown``. All feature code is
mixed in verbatim from the single-concern mixin modules:

* StyleMixin            — theming, fonts, button styles
* UISetupMixin          — initUI (the tabbed layout)
* SettingsTestsMixin    — per-tab connection tests + About-me wizard
* SearchToolsMixin      — marker / keyword search
* ConfigUiMixin         — config, vaults, proxy helpers
* ProcessingControlMixin— start/stop processing, progress/status relays
* LogPanelMixin         — colored log panel, filters
* VaultOpsMixin         — dashboard stats, verify, recategorize, undo
* BotQueueMixin         — bot-queue workflows
* BotLinkToolsMixin     — link export/verify/resolve
* DialogsMixin          — dialogs + processing_finished handlers
* BackupTabMixin        — the Backup tab
* PublishServicesMixin  — VaultSeal / GoodRepos / dashboard launch

No method bodies changed (md5-verified); the MRO is collision-free
(each method name exists exactly once) and the mixins define no
``__init__``, so ``super().__init__()`` in ``__init__`` reaches
QMainWindow exactly as before.
"""

from gitcurator.gui._qt import *  # noqa: F401,F403 — QMainWindow, Qt, QTimer, …
from gitcurator.gui.main_window._deps import *  # noqa: F401,F403

from gitcurator.gui.main_window.styles import StyleMixin
from gitcurator.gui.main_window.ui_setup import UISetupMixin
from gitcurator.gui.main_window.settings_tests import SettingsTestsMixin
from gitcurator.gui.main_window.search import SearchToolsMixin
from gitcurator.gui.main_window.config_ui import ConfigUiMixin
from gitcurator.gui.main_window.processing_ctl import ProcessingControlMixin
from gitcurator.gui.main_window.log_panel import LogPanelMixin
from gitcurator.gui.main_window.vault_ops import VaultOpsMixin
from gitcurator.gui.main_window.bot_queue import BotQueueMixin
from gitcurator.gui.main_window.bot_links import BotLinkToolsMixin
from gitcurator.gui.main_window.dialogs import DialogsMixin
from gitcurator.gui.main_window.backup_tab import BackupTabMixin
from gitcurator.gui.main_window.publish_services import PublishServicesMixin

__all__ = ["MainWindow"]


class MainWindow(
    QMainWindow,
    StyleMixin,
    UISetupMixin,
    SettingsTestsMixin,
    SearchToolsMixin,
    ConfigUiMixin,
    ProcessingControlMixin,
    LogPanelMixin,
    VaultOpsMixin,
    BotQueueMixin,
    BotLinkToolsMixin,
    DialogsMixin,
    BackupTabMixin,
    PublishServicesMixin
):
    """MainWindow — the GitCurator main window (see module docstring)."""

    def __init__(self):
        super().__init__()
        self.config = self.load_config()
        self.worker = None
        # Keep references to background test workers so they aren't GC'd mid-run.
        self._active_test_workers: List[TestWorker] = []
        # Reference to the Ollama server subprocess (if we started it).
        self._ollama_server_proc = None
        # State for sequential test_all (avoids 'database is locked')
        self._test_all_chain_step = None
        # Guard: only one Telegram operation at a time (prevents 'database is locked')
        self._telegram_busy = False
        # UI feature state (dark mode, log filter/search, log entry cache)
        self._dark_mode = False
        self._log_filter = "all"
        self._log_search = ""
        self._all_log_entries: List[Dict[str, str]] = []
        self.initUI()

        # v29.4 — Cloudflare sync is optional (disabled by default, no tab)
        self.cf_manager = None

        # v22 Feature 4: On launch, check for repos that failed in a previous
        # run and prompt the user to retry them. Best-effort — if the DB
        # can't be opened, silently skip.
        try:
            cache = CacheDB()
            failed_count = cache.get_failed_count()
            cache.close()
            if failed_count > 0:
                self.log_message(
                    f"⚠️ {failed_count} repos failed in previous runs. "
                    f"Click '🔄 Retry Failed' in the Bot tab to reprocess them.",
                    "warning"
                )
        except Exception:
            pass

        # v23 — Phase 4: RECONCILE — read the previous manifest (if any) and
        # surface any links that failed/never finished. Non-blocking: just
        # logs a warning so the user knows there's outstanding work. The
        # failed URLs are stored on self._reconciliation_urls for the
        # (future) retry button to consume.
        try:
            vault_path = self.config.get('vault_path', '')
            if vault_path and os.path.isdir(vault_path):
                tracker = LinkTracker(vault_path)
                failed = tracker.get_reconciliation_urls()
                if failed:
                    self.log_message(
                        f"⚠️ {len(failed)} links from previous batch need retry!",
                        "warning"
                    )
                    for url in failed[:5]:
                        self.log_message(f"   • {url}", "warning")
                    if len(failed) > 5:
                        self.log_message(
                            f"   ... and {len(failed) - 5} more (use Dashboard → 🔍 Verify Vault)",
                            "warning"
                        )
                    self._reconciliation_urls = failed
                else:
                    # Only log "all clear" if a manifest actually exists
                    if tracker.load_previous_manifest():
                        self.log_message(
                            "✅ Previous batch verified — all links processed!",
                            "success"
                        )
        except Exception:
            pass  # best-effort — never crash on a manifest read issue

        # v22 Feature 7: Proxy Health Monitor — poll the proxy port every 60s
        # and update self.proxy_status_label. The check is non-blocking (2s
        # socket timeout). First check fires immediately so the dot isn't
        # stuck on "checking..." for a full minute after launch.
        try:
            self._proxy_timer = QTimer(self)
            self._proxy_timer.timeout.connect(self._check_proxy_health)
            self._proxy_timer.start(60000)  # 60 seconds
            # Fire an immediate first check
            QTimer.singleShot(0, self._check_proxy_health)
        except Exception:
            pass  # best-effort — never crash on a timer issue

    def closeEvent(self, event):
        # If processing is active, ask for confirmation before terminating
        if self.worker and self.worker.is_running:
            processed = self.worker.processed
            total = self.worker.total
            msg = (
                f"Processing in progress: {processed}/{total} repos extracted.\n\n"
                f"Do you really want to terminate?"
            )
            reply = QMessageBox.StandardButton.Yes if self._show_custom_question("⚠️ Terminate Processing?", msg) else QMessageBox.StandardButton.No
            if reply == QMessageBox.StandardButton.No:
                event.ignore()
                return

        self.save_config()
        if self.worker:
            # v30 — Fix (graceful close, W10): before waiting, UNBLOCK every
            # wait the worker could be parked on. Previously, if the worker
            # was sitting in _llm_retry_event.wait(600) (LLM failure dialog
            # pending) or the disk-full spin loop, worker.wait(5000) expired
            # and the app quit with the thread still running — random crashes
            # / half-written state on exit.
            self._unblock_worker_for_shutdown(self.worker)
            self.worker.stop()
            self.worker.wait(5000)  # wait up to 5 seconds for graceful shutdown
        # Wait for any in-flight test workers too.
        for w in list(self._active_test_workers):
            try:
                self._unblock_worker_for_shutdown(w)
                w.wait(2000)
            except Exception:
                pass
        event.accept()

    @staticmethod
    def _unblock_worker_for_shutdown(worker) -> None:
        """v30 — release every blocking wait a worker may be parked on so a
        pending dialog/timeout can't hold the thread past app exit:
          - resolve_llm_failure('stop')  -> unblocks _llm_retry_event
          - provide_code('')             -> unblocks _code_event
          - _disk_full_paused = False    -> exits the disk-full spin loop
        All calls are hasattr-guarded so it also works for TestWorker."""
        if worker is None:
            return
        try:
            if hasattr(worker, 'resolve_llm_failure'):
                worker.resolve_llm_failure("stop")
            if hasattr(worker, 'provide_code'):
                worker.provide_code("")
            if hasattr(worker, '_disk_full_paused'):
                worker._disk_full_paused = False
        except Exception:
            pass
