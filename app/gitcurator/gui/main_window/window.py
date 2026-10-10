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
from gitcurator.gui.main_window.vault_scan_ui import VaultScanUiMixin

from gitcurator.gui.main_window.theme import ThemeMixin

from gitcurator.gui.main_window.ui import UiMixin

from gitcurator.gui.main_window.vaults_config import VaultConfigMixin

from gitcurator.gui.processing_worker import TestWorker

class MainWindow(LifecycleMixin, BackupSealMixin, BotQueueMixin, DashboardMixin, ProcessingControlMixin, TelegramUiMixin, InputProxyMixin, VaultConfigMixin, TestConnectionModalMixin, VaultScanUiMixin, LinkingToolsMixin, LlamaCppMixin, ConnectionTestsMixin, HeroMixin, UiMixin, ThemeMixin, QMainWindow):
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
        # v0.63.1 — THE TRUTH PASS (the owner's report: "a weird message
        # asks for retrying repos and websites … every repo and website
        # is processed and tidy, and nothing needs retry"). The old check
        # trusted the queue's COUNT alone, and the queue lied: website
        # URLs enqueued by Verify Vault (which the GitHub-only resolver
        # could never clear), repos resolved under drifted spellings,
        # repos that later took the dedupe-skip path — all stayed
        # unresolved forever, crying at every launch while the vault sat
        # tidy. The count is now re-classified against the GITHUB vault's
        # own index (the same ground truth reconciliation uses): stale
        # cries and website rows are resolved out of the queue for good,
        # and only genuinely-missing repos are announced. Silence is the
        # new default on a tidy vault — a cry must be earned.
        _startup_gh_index = None
        try:
            _sv = (self.config.get('vault_path') or '').strip()
            if _sv and os.path.isdir(_sv):
                from gitcurator.gui.vault_index import VaultIndex as _VI
                _startup_gh_index = _VI(_sv)
                _startup_gh_index.rebuild()
        except Exception:
            _startup_gh_index = None   # best-effort — the pass degrades to the old count
        try:
            cache = CacheDB()
            failed_rows = cache.get_failed_urls()
            missing = []
            if failed_rows:
                from gitcurator.gui.cache_db import failed_rows_truth
                _stale, missing, _websites = failed_rows_truth(
                    failed_rows,
                    github_has=_startup_gh_index.has_url
                    if _startup_gh_index is not None else None)
                _resolved_now = cache.resolve_failed_urls(_stale + _websites)
                if _resolved_now:
                    self.log_message(
                        f"♻️ {_resolved_now} stale retry-queue row(s) "
                        f"resolved — their notes are already in the vault "
                        f"(old cries, not missing work; website rows left "
                        f"for the Websites pipeline's own doors).",
                        "success"
                    )
            cache.close()
            if missing:
                self.log_message(
                    f"⚠️ {len(missing)} repo(s) failed in previous runs and "
                    f"are still missing their notes — More ▸ '🔄 Retry "
                    f"Failed' reprocesses them.",
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
                # v0.63.1 — share the startup pass's index (no second
                # vault scan — the same ground truth the truth pass
                # just used).
                if _startup_gh_index is not None:
                    tracker.set_vault_index(_startup_gh_index)
                failed = tracker.get_reconciliation_urls()
                # v0.38.0 — reconciliation heals against the vault: rows
                # left pending/failed by an interrupted batch whose notes
                # are ALREADY stored are closed out right here (and the
                # manifest is updated on disk). Say so — the owner should
                # never wonder why the banner's count shrank.
                _healed = getattr(tracker, 'reconciled_vault_hits', 0)
                if _healed:
                    self.log_message(
                        f"✓ {_healed} link{'s' if _healed != 1 else ''} from the previous batch "
                        f"already stored in the vault — no retry needed.",
                        "success"
                    )
                if failed:
                    self.log_message(
                        f"⚠️ {len(failed)} repo link{'s' if len(failed) != 1 else ''} from previous batch need retry!",
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
                    # v0.63.1 — website rows set aside by the read get
                    # their one honest line (the Websites pipeline's own
                    # doors own them — the master table is their ledger).
                    _aside = getattr(tracker, 'set_aside_websites', 0)
                    if _aside:
                        self.log_message(
                            f"ℹ️ {_aside} website link{'s' if _aside != 1 else ''} "
                            f"from that batch keep waiting in the Websites "
                            f"pipeline's own retry queue — never this "
                            f"retry's business.",
                            "info"
                        )
                    # Only log "all clear" if a manifest actually exists
                    if tracker.load_previous_manifest():
                        self.log_message(
                            "✅ Previous batch verified — all links processed!",
                            "success"
                        )
        except Exception:
            pass  # best-effort — never crash on a manifest read issue

        # v0.32 (five-change pass): the retry banner — the unfinished-link
        # count as a LIVE value (its own LinkTracker read; the log lines
        # above stay as the record).
        self._refresh_retry_banner()

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

        # v0.42.0 — the _review backlog notice (the owner's ask): after the
        # UI settles, scan the Websites vault's _review folder. App-owned
        # fetch-failed placeholders = links the pre-v0.41 honest-bot UA got
        # walled on (403/405). One notice per launch, offered only when
        # there is something to retry, never during a live batch — and
        # self-extinguishing: retry them, they pass, the notice is gone
        # forever. The scan itself is pure frontmatter reads.
        try:
            QTimer.singleShot(2500, self._startup_review_backlog_check)
        except Exception:
            pass  # best-effort — never crash on a timer issue

    # v0.34 (follow-up review): the app opens with NO focus ring anywhere.
    # The gear button is the FIRST tabbable child, so plain Qt behavior
    # hands it the initial focus at every launch — the keyboard-focus
    # outline sat on the gear from the first frame (the reviewer's "stuck
    # purple square"). The WINDOW itself now takes the initial focus;
    # Tab still walks into the children and rings them properly (the
    # icon buttons are TabFocus-only, so a click can never ring them).
    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, '_gc_initial_focus_done', False):
            self._gc_initial_focus_done = True
            try:
                self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
                self.setFocus(Qt.FocusReason.OtherFocusReason)
            except RuntimeError:
                pass  # never crash on a focus issue

    def _open_settings(self):
        """Open the Settings window (every former tab in a sidebar layout).
        Non-modal: batch runs, timers and worker dialogs keep working."""
        self.settings_dialog.show()
        self.settings_dialog.raise_()
        self.settings_dialog.activateWindow()
