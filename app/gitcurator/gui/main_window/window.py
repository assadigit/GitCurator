"""MainWindow — moved verbatim from gitcurator/gui/app.py (branch refactor/gui-app-split; see docs/history/REFACTOR_PLAN.md)."""

import sys as _sys
import sys
import os
import re
import json
import time
import urllib.error
import sqlite3
import subprocess
import html as _html_module
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Any
import threading
import logging
from logging.handlers import RotatingFileHandler

from gitcurator.constants import (
    APP_DIR, COLORS, CONFIG_FILE, CONFIG_EXAMPLE, CATEGORY_FOLDERS,
    CATEGORY_KEYS, DEFAULT_SYSTEM_PROMPT,
)
from gitcurator.core import links as _links
from gitcurator.core import storage as _storage
from gitcurator.core import note_builder as _note_builder
from gitcurator.core import llm_client as _llm_client
from gitcurator.core import dryrun as _dryrun
from gitcurator.core import note_state as _note_state
from gitcurator.core import website_pipeline as _website_pipeline
from gitcurator.core import connection_check as _connection_check
from gitcurator.integrations import vaultseal as _vaultseal
from gitcurator.integrations import goodrepos as _goodrepos
from gitcurator.gui.telegram_lock import TelegramLockManager
from gitcurator.gui import icons as _icons
from gitcurator.integrations.subprocess_runner import (
    run_telegram_worker as _run_worker_subprocess,
    kill_all_workers as _kill_all_telegram_workers,
    live_worker_count as _live_telegram_worker_count,
)

try:
    from PyQt6.QtWidgets import *
    from PyQt6.QtCore import *
    from PyQt6.QtGui import *
except ImportError:
    print("PyQt6 is not installed. Please run: pip install PyQt6")
    sys.exit(1)

_APP_DIR = APP_DIR

from gitcurator.gui.cache_db import CacheDB

from gitcurator.gui.link_tracker import LinkTracker

from gitcurator.gui.main_window.backup_seal import BackupSealMixin

from gitcurator.gui.main_window.bot_queue import BotQueueMixin

from gitcurator.gui.main_window.connection_tests import ConnectionTestsMixin

from gitcurator.gui.main_window.dashboard import DashboardMixin

from gitcurator.gui.main_window.hero import HeroMixin

from gitcurator.gui.main_window.input_proxy import InputProxyMixin

from gitcurator.gui.main_window.lifecycle import LifecycleMixin

from gitcurator.gui.main_window.llamacpp import LlamaCppMixin

from gitcurator.gui.main_window.linking_tools import LinkingToolsMixin

from gitcurator.gui.main_window.processing_control import ProcessingControlMixin

from gitcurator.gui.main_window.telegram_ui import TelegramUiMixin

from gitcurator.gui.main_window.test_connection_modal import TestConnectionModalMixin

from gitcurator.gui.main_window.theme import ThemeMixin

from gitcurator.gui.main_window.ui import UiMixin

from gitcurator.gui.main_window.vaults_config import VaultConfigMixin

from gitcurator.gui.processing_worker import TestWorker

class MainWindow(LifecycleMixin, BackupSealMixin, BotQueueMixin, DashboardMixin, ProcessingControlMixin, TelegramUiMixin, InputProxyMixin, VaultConfigMixin, TestConnectionModalMixin, LinkingToolsMixin, LlamaCppMixin, ConnectionTestsMixin, HeroMixin, UiMixin, ThemeMixin, QMainWindow):
    # v0.15.1 — startup llama.cpp auto-detect: the detector daemon thread
    # hands its result to the GUI thread through this queued signal (the
    # owner: "the service is running on task manager, the app must
    # automatically catch that!").
    _llamacpp_autodetect_signal = pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        self.config = self.load_config()
        self.worker = None
        # Keep references to background test workers so they aren't GC'd mid-run.
        self._active_test_workers: List[TestWorker] = []
        # Reference to the Ollama server subprocess (if we started it).
        self._ollama_server_proc = None
        # v0.17.0 — Test Connection: the sections collected by the battery
        # worker; the live-Telegram leg appends to them before the verdict.
        self._cc_sections = None
        # Guard: only one Telegram operation at a time (prevents 'database is
        # locked'). v0.06 — now a TelegramLockManager with owner tracking:
        # every acquire names itself, releases are owner-scoped (a finishing
        # worker can no longer free another worker's lock), and the busy
        # message says WHAT holds it and for how long.
        self._tg_lock = TelegramLockManager()
        # A Telegram operation held longer than this is force-released by the
        # watchdog below (every real op is bounded by the subprocess runner's
        # 30-min hard cap, so 35 min means the cleanup chain itself died).
        self._TG_STUCK_SECONDS = 35 * 60
        # UI feature state (dark mode, log filter/search, log entry cache)
        self._dark_mode = False
        self._log_filter = "all"
        self._log_search = ""
        self._all_log_entries: List[Dict[str, str]] = []
        # v0.31.0 (balance pass): the timestamp of the last sync that found
        # NOTHING new — powers the log card's "Everything is up to date"
        # empty state (None = no caught-up sync recorded yet).
        self._log_last_uptodate = None
        # v0.06 — Fix (zombie process / stale app.lock): set as soon as the
        # user closes the window. Every timer-driven callback (startup
        # auto-check, proxy monitor) and every modal helper checks this flag
        # so no dialog can be opened AFTER the main window is gone. The old
        # code opened a "Proxy Required" modal 2s after startup even when
        # the window had already been closed — the invisible dialog kept
        # app.exec() alive forever, the finally-block never removed app.lock,
        # and the NEXT launch refused to start with "Another instance is
        # already running" (the owner's "it does nothing" symptom).
        self._closing = False
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

        # v0.06 — Telegram lock watchdog: if a lock is still held after
        # _TG_STUCK_SECONDS (longer than the subprocess runner's hard cap),
        # the finishing chain itself died — force-release and log loudly so
        # the app stays usable instead of showing "another operation is
        # running" forever (the v0.05 forever-bug).
        try:
            self._tg_watchdog_timer = QTimer(self)
            self._tg_watchdog_timer.timeout.connect(self._tg_lock_watchdog)
            self._tg_watchdog_timer.start(60000)  # check every 60s
        except Exception:
            pass  # best-effort — never crash on a timer issue

        # v0.15.1 — llama.cpp STARTUP auto-detect (owner report: llama-server
        # running in Task Manager but the app never noticed — detection used
        # to live only behind the 🔍 Detect button and the batch preflight).
        # ~1.5s after launch a daemon thread probes the configured URL, the
        # LISTENING PORTS OF THE RUNNING llama-server PROCESS (whatever
        # --port it uses) and the common ports — all proxy-safe — and the
        # result is applied on the GUI thread: auto-switch when the current
        # provider is unusable, otherwise a one-line hint. Never blocks the
        # UI; runs exactly once per session.
        try:
            self._llamacpp_autodetect_done = False
            self._llamacpp_autodetect_signal.connect(
                self._apply_llamacpp_autodetect)
            QTimer.singleShot(1500, self._startup_llamacpp_autodetect)
        except Exception:
            pass  # best-effort — never crash on a timer issue


    def _open_settings(self):
        """Open the Settings window (every former tab in a sidebar layout).
        Non-modal: batch runs, timers and worker dialogs keep working."""
        self.settings_dialog.show()
        self.settings_dialog.raise_()
        self.settings_dialog.activateWindow()
