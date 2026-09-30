#!/usr/bin/env python3
"""
GitHub Project Curator - Complete GUI Application
Version: 26.0 - CLOUD + VERIFY (cloud LLM API support, Verify All Bot Links button, progress-bar fix on skips)

CHANGES vs v2.1
================
CRITICAL FIX: Telegram fetch now runs in a SEPARATE PROCESS
(telegram_fetch_worker.py) via subprocess, NOT in a QThread.

ROOT CAUSE FINALLY IDENTIFIED:
  - test.py works because it runs in the MAIN THREAD with ProactorEventLoop
  - main.py failed because it ran in a QThread where:
    * ProactorEventLoop -> IOCP deadlock -> WinError 121
    * SelectorEventLoop -> python-socks ignores proxy -> Errno 10060
  - Both event loop types fail in QThread. The GUI framework is irrelevant.

THE FIX:
  - telegram_fetch_worker.py is a standalone script (like test.py)
  - main.py calls it via subprocess.run() from the QThread
  - The worker runs in its own process: main thread, default event loop,
    PySocks proxy -> IDENTICAL to test.py -> GUARANTEED to work
  - Worker stderr streams to GUI log in real time
  - Worker stdout returns JSON result

This is NOT a PyQt vs tkinter issue. Switching GUI frameworks would not help
because the problem is in the asyncio/threading layer, not the GUI layer.
"""

# === VERSION STAMP - printed at import so you can verify the right file loads ===
__VERSION__ = "0.09 (LINEAGE MERGE — one tree again: the owner's v0.08 GUI work + our v0.07.2/3 pipeline fixes unified; kept from v0.08: official Lucide SVG icons everywhere (icons.py, QSvgRenderer, theme-tinted), main-screen redesign (hero lavender CTA row, pipeline strip, log well), 404 QUARANTINE with cross-session attempt counting + batch-start dead-set pre-filter (dead links never reach the GitHub API), aggregate one-line log, notfound record written once at confirmation, More ▸ View 404 Quarantine, proxy pre-flight + DC-rotation connect retries in the Telegram worker, config credential healing; kept from v0.07.2/3: the 3-layer model picker (pre-flight menu BEFORE work, warmup stand-in resolution, mid-batch recovery — a missing Ollama model can never fail or degrade a batch), model_prompt_callback + _pick_best_model (never an embedder), our zero-dependency visual CLI (gitcurator/cli.py via main.py --cli) now also with --list-dead/--reset-dead; UNIFIED: quarantine threshold is CONFIGURABLE again (notfound_strike_threshold — Settings → Dashboard spinbox, CLI --strikes N; v0.08 had it hardcoded), consecutive-miss semantics restored (success resets the counter — v0.08 counted attempts forever), Settings → Dashboard quarantine manager (attempts in progress + confirmed ⛔), v0.07 notfound_strikes data auto-migrates into decommissioned_repos.fail_count; lineage: v0.08 + v0.07.3 + v0.07.2 + v0.07 + v0.06 reliability + v0.05 Ollama auto-start + v0.04 + v0.03 + v0.0.10 upstream, internal v33/v33.1)"
import sys as _sys
# v0.09.1: skip the stamp in CLI mode — the CLI's lazy imports pull this
# module in, and the 2 KB one-line version blob printed mid-output (between
# the banner and the stats) made every `--cli --status` run look broken.
if "--cli" not in _sys.argv:
    print(f"[main] LOADED version {__VERSION__} from {__file__}", file=_sys.stderr, flush=True)
# === END VERSION STAMP ===

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

# v30 — Fix (Extract the testable core): the four pure-stdlib modules that
# used to live inline in this 8.8k-line file. They carry no PyQt import so
# they stay unit-testable headlessly.
#   links.py        — THE single GitHub/non-GitHub link regex + normalization
#   storage.py      — atomic writes (tempfile + os.replace), safe filenames,
#                     config merge
#   note_builder.py — sanitized frontmatter note builder
#   llm_client.py   — timeout-wrapped Ollama chat/list + JSON extraction
# v32 — modular package layout: this file is now gitcurator/gui/app.py.
# Keep the app/ root on sys.path so `import gitcurator` resolves both when
# launched via the thin main.py shim and when this file is run directly.
_APP_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _APP_ROOT not in sys.path:
    sys.path.insert(0, _APP_ROOT)

from gitcurator.constants import (
    APP_DIR, COLORS, CONFIG_FILE, CONFIG_EXAMPLE, CATEGORY_FOLDERS,
    CATEGORY_KEYS, DEFAULT_SYSTEM_PROMPT,
)
from gitcurator.core import links as _links
from gitcurator.core import storage as _storage
from gitcurator.core import note_builder as _note_builder
from gitcurator.core import llm_client as _llm_client
# v0.09.5 — Phase 0 (dry-run): the global log-instead-of-write switch.
# Pure stdlib, no PyQt — see gitcurator/core/dryrun.py.
from gitcurator.core import dryrun as _dryrun
# v0.10.0 — Phase 1 (note state): the per-note record in cache.db that
# makes "moves are corrections" possible (SPEC §4.4). Pure stdlib, no PyQt.
from gitcurator.core import note_state as _note_state
# v0.11.0 — Phase 2 (websites): the whole per-link flow lives in this pure
# module (taxonomy validate, fetch, extract, classify, analyze, write,
# retries, _review). The worker only wires its LLM router + dedupe probe
# into it — new logic stays OUT of this 11k-line file (non-negotiable #8).
from gitcurator.core import website_pipeline as _website_pipeline
# v0.17.0 — Test Connection: the unified four-subsystem prober (vaults /
# Telegram / LLM / GitHub). Pure stdlib + llm_client, no PyQt — the GUI
# battery job below streams its results into the main log.
from gitcurator.core import connection_check as _connection_check
from gitcurator.integrations import vaultseal as _vaultseal
from gitcurator.integrations import goodrepos as _goodrepos
# v0.06 — Fix (stuck Telegram lock, part 1): the single-operation lock now
# lives in its own testable module with owner tracking + watchdog support,
# and the subprocess runner gained a REAL timeout (idle-based + hard cap +
# process registry so closeEvent can kill orphaned telethon children).
from gitcurator.gui.telegram_lock import TelegramLockManager
# v0.07 — ONE unified icon set for the whole UI (design review: the main
# screen mixed four icon languages — pixel-art, full-color emoji, outline,
# flat-solid). All glyphs are now tinted Lucide-style SVGs rendered here.
from gitcurator.gui import icons as _icons
from gitcurator.integrations.subprocess_runner import (
    run_telegram_worker as _run_worker_subprocess,
    kill_all_workers as _kill_all_telegram_workers,
    live_worker_count as _live_telegram_worker_count,
)


# Historical name kept: the few path resolutions below that used to point
# at the flat main.py directory. APP_DIR is the app/ root, so assets/,
# config.json and the subprocess worker keep resolving exactly as before.
_APP_DIR = APP_DIR

# Third-party imports - with graceful handling
try:
    from PyQt6.QtWidgets import *
    from PyQt6.QtCore import *
    from PyQt6.QtGui import *
except ImportError:
    print("PyQt6 is not installed. Please run: pip install PyQt6")
    sys.exit(1)

try:
    import github
    from github import Github, GithubException, Auth
except ImportError:
    print("PyGithub is not installed. Please run: pip install PyGithub")
    sys.exit(1)

try:
    import ollama
except ImportError:
    print("Ollama Python client is not installed. Please run: pip install ollama")
    sys.exit(1)

try:
    import colorama
    from colorama import Fore, Style
    colorama.init(autoreset=True)
except ImportError:
    # Fallback if colorama missing
    class Fore:
        GREEN = ''; YELLOW = ''; RED = ''; CYAN = ''; WHITE = ''; RESET = ''
    Style = Fore
    print("colorama not installed; colored output disabled.")

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

# v0.06 — Perf (startup): the Telethon in-process fetcher is imported
# LAZILY now. It is used ONLY by the headless --single-id path
# (run_headless below); every GUI fetch goes through the subprocess worker
# (telegram_fetch_worker.py), which imports telethon in ITS own process.
# Importing telethon here cost ~0.5-1s of cold-start for every GUI launch
# and dragged the whole asyncio/telethon stack into the GUI process for
# nothing. The lazy shims below preserve the old names for any external
# callers.


# v28 — Cloudflare bot sync + Google Drive backup (optional, graceful if missing)
try:
    from gitcurator.cloud.cloudflare_manager import CloudflareManager
    from gitcurator.integrations.error_reporter import ErrorReporter, SEVERITY_CRITICAL, SEVERITY_WARNING, SEVERITY_INFO, SEVERITY_DEBUG
    _CLOUDFLARE_AVAILABLE = True
except ImportError as e:
    _CLOUDFLARE_AVAILABLE = False
    print(f"[WARN] Cloudflare modules not available: {e}")
    print("[WARN] Bot sync + GDrive backup disabled. Install cloudflare_sync.py, cloudflare_manager.py, error_reporter.py, gdrive_backup.py")
from gitcurator.gui.main_window.bot_queue import BotQueueMixin
from gitcurator.gui.main_window.dashboard import DashboardMixin
from gitcurator.gui.main_window.processing_control import ProcessingControlMixin
from gitcurator.gui.main_window.telegram_ui import TelegramUiMixin
from gitcurator.gui.main_window.input_proxy import InputProxyMixin
from gitcurator.gui.main_window.vaults_config import VaultConfigMixin
from gitcurator.gui.main_window.test_connection_modal import TestConnectionModalMixin
from gitcurator.gui.main_window.phase6 import Phase6Mixin
from gitcurator.gui.main_window.llamacpp import LlamaCppMixin
from gitcurator.gui.main_window.connection_tests import ConnectionTestsMixin
from gitcurator.gui.main_window.hero import HeroMixin
from gitcurator.gui.main_window.ui import UiMixin
from gitcurator.gui.main_window.theme import ThemeMixin
from gitcurator.gui.headless import run_headless, _is_process_running
from gitcurator.gui.dialogs import SettingsDialog, ConnectionTestDialog
from gitcurator.gui.processing_worker import ProcessingWorker, TestWorker
from gitcurator.gui.worker_jobs import (
    _run_telegram_worker,
    _telegram_test_job,
    _bot_queue_job,
    _connection_battery_job,
    _quick_detect_job,
)
from gitcurator.gui.log_bridge import (
    _GuiLogHandler,
    _install_gui_log_handler,
    _remove_gui_log_handler,
)
from gitcurator.gui.link_tracker import LinkTracker
from gitcurator.gui.cache_db import CacheDB
from gitcurator.gui.vault_index import VaultIndex, find_obsidian_vaults, _safe_moc_name
from gitcurator.gui.platform_intake import (
    PLATFORM_INFO,
    _inbox_table_vault,
    classify_platform,
    write_inbox_links_by_platform,
)
from gitcurator.gui.telegram_lazy import (
    _import_telethon_fetcher,
    fetch_github_urls_sync,
    TelegramFetcherError,
)
from gitcurator.gui.dead_links import DEAD_LINK_THRESHOLD, dead_link_threshold
from gitcurator.gui.link_helpers import extract_github_urls, clean_url, normalize_url

try:
    from gitcurator.cloud.gdrive_backup import GDriveBackup
    _GDRIVE_AVAILABLE = True
except ImportError:
    _GDRIVE_AVAILABLE = False


# ============================================================================
# Logging Setup
# ============================================================================

def setup_logging(log_level="INFO", log_dir="logs"):
    os.makedirs(log_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file_md = os.path.join(log_dir, f"processing_{timestamp}.md")
    log_file_txt = os.path.join(log_dir, f"processing_{timestamp}.txt")

    logger = logging.getLogger()
    logger.setLevel(getattr(logging, log_level.upper()))

    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG)
    logger.addHandler(console)

    file_md = RotatingFileHandler(log_file_md, maxBytes=5*1024*1024, backupCount=5)
    file_md.setLevel(logging.INFO)
    logger.addHandler(file_md)

    file_txt = RotatingFileHandler(log_file_txt, maxBytes=5*1024*1024, backupCount=5)
    file_txt.setLevel(logging.DEBUG)
    logger.addHandler(file_txt)

    return logger

# ============================================================================
# Utility
# ============================================================================


# ============================================================================
# Database (SQLite Cache)
# ============================================================================


# ============================================================================
# v23 — Link Tracker (No Link Left Behind)
# ============================================================================
# A 5-phase link-tracking pipeline that guarantees every URL discovered by
# the app is either turned into an Obsidian note (GitHub links) or recorded
# in the inbox table (non-GitHub links). The JSON manifest is the source of
# truth — it survives app crashes and is reconciled on the next launch.
#
# Phases:
#   1. INTAKE        — record EVERY link before any processing (atomic save)
#   2. PROCESS       — update each link's status as it is processed
#   3. VERIFY        — check that every "processed" link has a real note file
#                      and every "recorded" link is actually in the inbox table
#   4. RECONCILE     — on next launch, surface links that failed/never finished
#   5. CLEAR         — only mark bot messages as read if ALL links verified
# ============================================================================


# ============================================================================
# v32.1 — MOC filename sanitizer (Windows crash fix)
# ============================================================================


# ============================================================================
# Processing Worker (QThread)
# ============================================================================


# ============================================================================
# Test Worker (QThread)
# ----------------------------------------------------------------------------
# Runs a single blocking callable on a background thread and streams log lines
# back to the GUI via signals (queued connections -> UI updates immediately,
# even while the network call is still running). This is what makes the log
# panel feel real-time: the GUI thread never blocks on network I/O.
#
# Also supports interactive Telegram auth: when the subprocess worker prints
# __NEED_CODE__ or __NEED_PASSWORD__, _run_telegram_worker calls
# self.request_code() which emits code_requested -> GUI shows a dialog ->
# GUI calls self.provide_code(code) -> worker thread unblocks and sends the
# code to the subprocess via stdin.
# ============================================================================


# ============================================================================
# Background jobs (run inside TestWorker). Each accepts a log_signal so it can
# stream progress from the worker thread to the GUI via a queued signal.
# ============================================================================


# ============================================================================
# SUBPROCESS-BASED TELEGRAM FETCH
# ----------------------------------------------------------------------------
# Runs the Telegram fetch in a SEPARATE Python process (telegram_fetch_worker.py),
# identical to running test.py. This avoids ALL asyncio/threading issues:
#   - ProactorEventLoop IOCP deadlock in QThread (WinError 121)
#   - python-socks proxy silently ignored with SelectorEventLoop (Errno 10060)
# The worker process runs in the main thread with the default event loop,
# exactly like test.py -> GUARANTEED to work.
# ============================================================================

import subprocess as _subprocess


# v0.23.0 — _telegram_preview_job / _telegram_single_job /
# _telegram_keyword_job are GONE with the Input modes they served (Preview,
# Single Msg, Markers/custom keywords). _telegram_test_job stays (the
# account-login leg of Test Connection) and _bot_queue_job stays (the SYNC
# hero flow's bot-queue fetch).


# ============================================================================
# Main GUI (PyQt6) with Tabbed Layout and Per-tab Test Buttons
# ============================================================================


class MainWindow(BotQueueMixin, DashboardMixin, ProcessingControlMixin, TelegramUiMixin, InputProxyMixin, VaultConfigMixin, TestConnectionModalMixin, Phase6Mixin, LlamaCppMixin, ConnectionTestsMixin, HeroMixin, UiMixin, ThemeMixin, QMainWindow):
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


                                     # keyword / range / import)


    # ------------------------------------------------------------------
    # About Me Wizard — generates about_me.md to give the LLM context
    # ------------------------------------------------------------------
    def show_about_me_wizard(self):
        """Show a multi-step interview wizard to generate about_me.md."""
        dialog = QDialog(self)
        dialog.setWindowTitle("📝 About Me Wizard")
        
        dialog.setModal(True)
        dialog.setMinimumWidth(600)
        dialog.setMinimumHeight(500)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # Intro
        intro = QLabel(
            "📝 About Me Wizard\n\n"
            "This generates an 'about_me.md' file that gives the LLM context about you.\n"
            "The LLM uses this to personalize its analysis and explain how each project\n"
            "might specifically help YOU.\n\n"
            "Fill in the fields below (leave blank if you prefer not to answer):"
        )
        intro.setWordWrap(True)
        # v0.23.0 — no hardcoded text color: the themed QWidget rule colors
        # it (the old #423A52 was unreadable on the dark plum dialog).
        intro.setStyleSheet("font-size: 13px; padding: 8px;")
        intro.setObjectName("info_header")
        layout.addWidget(intro)

        # Form fields
        from PyQt6.QtWidgets import QFormLayout as _QFormLayout
        form = _QFormLayout()
        form.setSpacing(10)

        name_input = QLineEdit()
        name_input.setPlaceholderText("e.g. Software Engineer, Researcher, Student")
        role_input = QLineEdit()
        role_input.setPlaceholderText("e.g. AI/ML Engineer, Full-stack Developer")
        interests_input = QTextEdit()
        interests_input.setPlaceholderText("e.g. AI agents, developer tools, self-hosted software, automation...")
        interests_input.setMaximumHeight(80)
        objectives_input = QTextEdit()
        objectives_input.setPlaceholderText("e.g. Find tools for my workflow, learn new frameworks, build a knowledge base...")
        objectives_input.setMaximumHeight(80)
        tech_input = QLineEdit()
        tech_input.setPlaceholderText("e.g. Python, Rust, TypeScript, Docker, Kubernetes")
        vault_input = QTextEdit()
        vault_input.setPlaceholderText("e.g. I organize projects by domain (AI, Tools, Infrastructure) and use notes for quick recall.")
        vault_input.setMaximumHeight(60)

        form.addRow("Who are you:", name_input)
        form.addRow("Your role:", role_input)
        form.addRow("Your interests:", interests_input)
        form.addRow("Your objectives:", objectives_input)
        form.addRow("Technologies you use:", tech_input)
        form.addRow("How you use this vault:", vault_input)
        layout.addLayout(form)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        # v0.23.0 — theme-aware design-system buttons (the hardcoded light
        # stylesheet painted a light-bordered button on the dark theme), and
        # _style_btn tracks them so a theme flip restyles them too.
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(dialog.reject)
        self._style_btn(cancel_btn, 'secondary')
        generate_btn = QPushButton("✓ Generate about_me.md")
        self._style_btn(generate_btn, 'primary')
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(generate_btn)
        layout.addLayout(btn_row)

        def _generate():
            name = name_input.text().strip()
            role = role_input.text().strip()
            interests = interests_input.toPlainText().strip()
            objectives = objectives_input.toPlainText().strip()
            tech = tech_input.text().strip()
            vault = vault_input.toPlainText().strip()

            content = "# About Me\n\n"
            if name:
                content += f"## Who I am\nI am a {name}.\n\n"
            if role:
                content += f"## My role\n{role}\n\n"
            if interests:
                content += f"## My interests\n{interests}\n\n"
            if objectives:
                content += f"## My objectives\n{objectives}\n\n"
            if tech:
                content += f"## Technologies I use\n{tech}\n\n"
            if vault:
                content += f"## How I use this knowledge base\n{vault}\n\n"

            # Add default if nothing was filled
            if not any([name, role, interests, objectives, tech, vault]):
                content += "## Who I am\nI am a software engineer who curates GitHub projects.\n\n"
                content += "## My objectives\nDiscover useful tools and frameworks for my development workflow.\n\n"

            try:
                with open("about_me.md", 'w', encoding='utf-8') as f:
                    f.write(content)
                self.log_message(f"📝 Generated about_me.md ({len(content)} chars)", "success")
                self.log_message("The LLM will now personalize analyses based on your profile.", "info")
                self._show_custom_message_box("Success", "about_me.md generated successfully!", success=True)
                dialog.accept()
            except Exception as e:
                self._show_custom_message_box("Error", f"Failed to write about_me.md: {e}", success=False)

        generate_btn.clicked.connect(_generate)

        self._animate_dialog(dialog)
        dialog.exec()


    # ------------------------------------------------------------------
    # v0.15.0 — llama.cpp engine detection (owner request): detect the
    # llama-server and its model AUTOMATICALLY, like the Ollama group.
    # ------------------------------------------------------------------


    # ------------------------------------------------------------------
    # v0.23.0 — the legacy Input-mode handlers are GONE: generate_marker_
    # hash / copy_marker_hash / find_by_marker / find_keyword_ids /
    # preview_messages / _show_preview_modal served the ID Range, Markers
    # and Single Msg modes removed at the owner's request ("remove ID
    # range + single msg + markers options and its codes inside code
    # base"). The bot-queue SYNC (main view) and Import txt file
    # (Settings → 📥 Input) are the two input paths now.
    # ------------------------------------------------------------------


        # Note: we don't mark as read here — the user can click "Mark All Read"
        # separately after verifying processing succeeded.


    # ========================================================================
    # v29.4 — Backup tab UI
    # ========================================================================

    def _create_backup_tab(self):
        """Create the Backup tab (local folder + timestamped zip).

        v32.2 — scroll + compaction. The four sections' natural height
        exceeds the fixed tab pane inside the 1000×750 window, so the tab
        content is wrapped in a QScrollArea (via _wrap_scroll in _build_ui):
        the scroll area's own height matches the tab pane while the inner
        content panel expands to whatever height the sections need —
        vertical scrolling only, horizontal always off. The sections are
        also COMPACTED so most content fits without scrolling:
        - status dots share the action/keep rows (no dedicated status lines)
        - the two settings checkboxes sit side-by-side on one row
        - info notes are single wrapped sentences (no hard line breaks)
        - folder/repo inputs keep their full-width rows
        """
        tab = QWidget()
        tab.setObjectName("backup_tab")
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)

        # ---- 💾 Vault Backup (local zip + FIFO rotation) ----
        status_group = QGroupBox("💾 Vault Backup")
        status_layout = QVBoxLayout(status_group)
        status_layout.setSpacing(6)

        # Backup folder picker — the section's one input row.
        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("Backup folder:"))
        self.backup_folder_input = QLineEdit()
        self.backup_folder_input.setPlaceholderText("e.g., C:\\Users\\You\\OneDrive\\Vault-Backups")
        self.backup_folder_input.setText(self.config.get('backup_folder', ''))
        folder_row.addWidget(self.backup_folder_input, 1)

        browse_btn = QPushButton("📂 Browse")
        browse_btn.clicked.connect(self._backup_browse_folder)
        folder_row.addWidget(browse_btn)
        status_layout.addLayout(folder_row)

        # Rotation + status dot on ONE row (v32.2 compaction).
        keep_row = QHBoxLayout()
        keep_row.addWidget(QLabel("Keep last:"))
        self.backup_max_input = QLineEdit()
        self.backup_max_input.setPlaceholderText("10")
        self.backup_max_input.setMaximumWidth(60)
        self.backup_max_input.setText(str(self.config.get('backup_max', 10)))
        keep_row.addWidget(self.backup_max_input)
        keep_row.addWidget(QLabel("backups (FIFO rotation)"))
        keep_row.addStretch()
        self.backup_status_label = QLabel("● Disabled")
        self.backup_status_label.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {self._status_colors()['error']};")
        keep_row.addWidget(self.backup_status_label)
        status_layout.addLayout(keep_row)

        # Action buttons — v31.1 hierarchy: ONE filled primary (Backup Now)
        # + outlined secondary (Restore); the enable toggle rides the same
        # row, right-aligned (v32.2 compaction). '📤 Export ZIP' lives in the
        # global 'More' overflow menu (export = infrequent action).
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.backup_now_btn = QPushButton("💾 Backup Now")
        self._style_btn(self.backup_now_btn, 'primary')
        self.backup_now_btn.clicked.connect(self._backup_now)
        btn_row.addWidget(self.backup_now_btn)

        self.backup_restore_btn = QPushButton("📂 Restore")
        self._style_btn(self.backup_restore_btn, 'secondary')
        self.backup_restore_btn.clicked.connect(self._backup_restore)
        btn_row.addWidget(self.backup_restore_btn)

        btn_row.addStretch()
        self.backup_enabled_check = QCheckBox("Enable auto-backup after each batch")
        self.backup_enabled_check.setToolTip(
            "Create a timestamped vault ZIP after every processing batch")
        self.backup_enabled_check.setChecked(self.config.get('backup_enabled', False))
        btn_row.addWidget(self.backup_enabled_check)
        status_layout.addLayout(btn_row)

        # One wrapped note (was a 2-line hard-wrapped paragraph).
        info_label = QLabel(
            "💡 Any folder works — a OneDrive/Dropbox/Drive sync folder "
            "uploads your zips to the cloud automatically. Zero setup, no OAuth."
        )
        info_label.setWordWrap(True)
        info_label.setObjectName("info_note")
        status_layout.addWidget(info_label)

        layout.addWidget(status_group)

        # ---- VaultSeal (v31): automatic GitHub mirror ----
        seal_group = QGroupBox("🛡️ VaultSeal — GitHub Mirror")
        seal_layout = QVBoxLayout(seal_group)
        seal_layout.setSpacing(6)

        # Both settings on ONE row (v32.2 compaction).
        seal_checks_row = QHBoxLayout()
        self.vaultseal_enabled_check = QCheckBox("Seal after every run")
        self.vaultseal_enabled_check.setToolTip(
            "Commit the whole vault to git after every run and push it to a "
            "PRIVATE GitHub repository")
        self.vaultseal_enabled_check.setChecked((self.config.get('vaultseal') or {}).get('enabled', True))
        self.vaultseal_enabled_check.toggled.connect(self._vaultseal_refresh_status)
        seal_checks_row.addWidget(self.vaultseal_enabled_check)

        self.vaultseal_push_check = QCheckBox("Push to GitHub")
        self.vaultseal_push_check.setToolTip(
            "Push the seal to GitHub (uses the GitHub token from Settings)")
        self.vaultseal_push_check.setChecked((self.config.get('vaultseal') or {}).get('auto_push', True))
        seal_checks_row.addWidget(self.vaultseal_push_check)
        seal_checks_row.addStretch()
        seal_layout.addLayout(seal_checks_row)

        repo_row = QHBoxLayout()
        repo_row.addWidget(QLabel("Backup repo:"))
        self.vaultseal_repo_input = QLineEdit()
        self.vaultseal_repo_input.setPlaceholderText("auto — derived from the vault folder name")
        self.vaultseal_repo_input.setText((self.config.get('vaultseal') or {}).get('repo_name', ''))
        repo_row.addWidget(self.vaultseal_repo_input, 1)
        seal_layout.addLayout(repo_row)

        # Action + status dot on ONE row (v32.2 compaction).
        seal_btn_row = QHBoxLayout()
        self.vaultseal_now_btn = QPushButton("🛡️ Seal Now")
        # v31.1: Backup Now is the Backup tab's ONE filled primary — Seal Now
        # is the outlined secondary path.
        self._style_btn(self.vaultseal_now_btn, 'secondary')
        self.vaultseal_now_btn.clicked.connect(self._vaultseal_now)
        seal_btn_row.addWidget(self.vaultseal_now_btn)
        seal_btn_row.addStretch()

        self.vaultseal_status_label = QLabel("● —")
        self.vaultseal_status_label.setStyleSheet("font-size: 13px; font-weight: bold; color: #6C6480;")
        seal_btn_row.addWidget(self.vaultseal_status_label)
        seal_layout.addLayout(seal_btn_row)

        # One wrapped note (was a 4-line hard-wrapped paragraph).
        seal_info = QLabel(
            "💡 Obsidian's free tier has no sync — VaultSeal commits the whole "
            "vault after every run and pushes it to a PRIVATE repository (full "
            "history, restore with git clone). Machine state (workspace.json, "
            ".trash) is excluded; an unchanged vault is a no-op."
        )
        seal_info.setWordWrap(True)
        seal_info.setObjectName("info_note")
        seal_layout.addWidget(seal_info)

        layout.addWidget(seal_group)

        self._vaultseal_refresh_status()

        # ---- GoodRepos (v32): public curated directory ----
        good_group = QGroupBox("🌟 Good Repos — Public Directory")
        good_layout = QVBoxLayout(good_group)
        good_layout.setSpacing(6)

        # Both settings on ONE row (v32.2 compaction).
        good_checks_row = QHBoxLayout()
        self.goodrepos_enabled_check = QCheckBox("Publish after every run")
        self.goodrepos_enabled_check.setToolTip(
            "Publish the curated directory to a PUBLIC GitHub repo after "
            "every run")
        self.goodrepos_enabled_check.setChecked((self.config.get('goodrepos') or {}).get('enabled', True))
        self.goodrepos_enabled_check.toggled.connect(self._goodrepos_refresh_status)
        good_checks_row.addWidget(self.goodrepos_enabled_check)

        self.goodrepos_push_check = QCheckBox("Push to GitHub")
        self.goodrepos_push_check.setToolTip(
            "Push the directory to GitHub (uses the GitHub token from Settings)")
        self.goodrepos_push_check.setChecked((self.config.get('goodrepos') or {}).get('auto_push', True))
        good_checks_row.addWidget(self.goodrepos_push_check)
        good_checks_row.addStretch()
        good_layout.addLayout(good_checks_row)

        good_repo_row = QHBoxLayout()
        good_repo_row.addWidget(QLabel("Directory repo:"))
        self.goodrepos_repo_input = QLineEdit()
        self.goodrepos_repo_input.setPlaceholderText("good-repos")
        self.goodrepos_repo_input.setText((self.config.get('goodrepos') or {}).get('repo_name', 'good-repos'))
        good_repo_row.addWidget(self.goodrepos_repo_input, 1)
        good_layout.addLayout(good_repo_row)

        # Action + status dot on ONE row (v32.2 compaction).
        good_btn_row = QHBoxLayout()
        self.goodrepos_now_btn = QPushButton("🌟 Publish Now")
        # v32: outlined secondary — Backup Now stays this tab's one filled primary.
        self._style_btn(self.goodrepos_now_btn, 'secondary')
        self.goodrepos_now_btn.clicked.connect(self._goodrepos_now)
        good_btn_row.addWidget(self.goodrepos_now_btn)
        good_btn_row.addStretch()

        self.goodrepos_status_label = QLabel("● —")
        self.goodrepos_status_label.setStyleSheet("font-size: 13px; font-weight: bold; color: #6C6480;")
        good_btn_row.addWidget(self.goodrepos_status_label)
        good_layout.addLayout(good_btn_row)

        # One wrapped note (was a 4-line hard-wrapped paragraph).
        good_info = QLabel(
            "💡 Every curated repo becomes an entry in a browsable, emoji-rich "
            "README directory (organized like AI → Skills → …) with the full "
            "notes mirrored into category folders. PUBLIC by design — "
            "everyone can browse and benefit from your curation."
        )
        good_info.setWordWrap(True)
        good_info.setObjectName("info_note_indigo")
        good_layout.addWidget(good_info)

        layout.addWidget(good_group)

        self._goodrepos_refresh_status()

        # ---- Dashboard Link ----
        dash_group = QGroupBox("📊 Dashboard")
        dash_layout = QVBoxLayout(dash_group)
        dash_layout.setSpacing(6)

        # URL + action on ONE row (v32.2 compaction — was 2 rows).
        dash_row = QHBoxLayout()
        dash_row.addWidget(QLabel("Worker URL:"))
        self.dash_worker_url_input = QLineEdit()
        self.dash_worker_url_input.setPlaceholderText("https://github-to-obsidian-bot.your-subdomain.workers.dev")
        self.dash_worker_url_input.setText(self.config.get('cloudflare_worker_url', ''))
        dash_row.addWidget(self.dash_worker_url_input, 1)

        open_dash_btn = QPushButton("📊 Open Dashboard")
        self._style_btn(open_dash_btn, 'secondary')
        open_dash_btn.clicked.connect(self._open_dashboard_from_backup_tab)
        dash_row.addWidget(open_dash_btn)
        dash_layout.addLayout(dash_row)

        # One wrapped note (was 2 hard-wrapped lines).
        dash_info = QLabel(
            "📊 Bot stats, pending links and recent activity — served by your "
            "Cloudflare Worker, no separate deployment needed."
        )
        dash_info.setWordWrap(True)
        dash_info.setObjectName("info_note_indigo")
        dash_layout.addWidget(dash_info)

        layout.addWidget(dash_group)

        # v32.2: initialize the backup status dot from config (the seal and
        # goodrepos sections already self-refresh at construction).
        self._backup_refresh_status()

        # v31.1: no filler stretch. v32.2: _wrap_scroll in _build_ui pins this
        # tab's scroll area to the tab pane's fixed visible height while this
        # content panel expands to the height the four sections actually need
        # (vertical-only scrolling, horizontal always off).
        return tab

    def _backup_browse_folder(self):
        """Open folder picker for backup destination."""
        folder = QFileDialog.getExistingDirectory(self, "Select Backup Folder")
        if folder:
            self.backup_folder_input.setText(folder)
            self._backup_save_config()
            self._backup_refresh_status()

    def _backup_save_config(self):
        """Save backup config."""
        self.config['backup_enabled'] = self.backup_enabled_check.isChecked()
        self.config['backup_folder'] = self.backup_folder_input.text().strip()
        try:
            self.config['backup_max'] = max(1, int(self.backup_max_input.text() or '10'))
        except ValueError:
            self.config['backup_max'] = 10
        self.config['cloudflare_worker_url'] = self.dash_worker_url_input.text().strip().rstrip('/')
        # v31 — VaultSeal settings (Backup tab)
        vs = self.config.get('vaultseal')
        if not isinstance(vs, dict):
            vs = {}
        if hasattr(self, 'vaultseal_enabled_check'):
            vs['enabled'] = self.vaultseal_enabled_check.isChecked()
            vs['auto_push'] = self.vaultseal_push_check.isChecked()
            vs['repo_name'] = self.vaultseal_repo_input.text().strip()
        self.config['vaultseal'] = vs
        # v32 — GoodRepos settings (Backup tab)
        gr = self.config.get('goodrepos')
        if not isinstance(gr, dict):
            gr = {}
        if hasattr(self, 'goodrepos_enabled_check'):
            gr['enabled'] = self.goodrepos_enabled_check.isChecked()
            gr['auto_push'] = self.goodrepos_push_check.isChecked()
            gr['repo_name'] = self.goodrepos_repo_input.text().strip() or 'good-repos'
        self.config['goodrepos'] = gr
        self.save_config()

    def _backup_refresh_status(self):
        """Refresh backup status label."""
        import os
        backup_folder = self.backup_folder_input.text().strip()
        backup_enabled = self.backup_enabled_check.isChecked()
        if backup_enabled and backup_folder:
            if os.path.isdir(backup_folder):
                self.backup_status_label.setText("● Ready")
                self.backup_status_label.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {self._status_colors()['success']};")
            else:
                self.backup_status_label.setText("● Folder not found")
                self.backup_status_label.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {self._status_colors()['error']};")
        elif backup_enabled:
            self.backup_status_label.setText("● No folder selected")
            self.backup_status_label.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {self._status_colors()['warning']};")
        else:
            self.backup_status_label.setText("● Disabled")
            self.backup_status_label.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {self._status_colors()['error']};")

    def _backup_now(self):
        """Create a local backup now."""
        self._backup_save_config()
        vault_path = self.config.get('vault_path', '')
        backup_folder = self.config.get('backup_folder', '')

        if not vault_path:
            self._show_custom_message_box("No Vault", "Set a vault path first (📁 Vault tab).", success=False)
            return
        if not backup_folder:
            self._show_custom_message_box("No Backup Folder", "Select a backup folder first.", success=False)
            return

        import os
        if not os.path.isdir(backup_folder):
            try:
                os.makedirs(backup_folder, exist_ok=True)
            except Exception as e:
                self._show_custom_message_box("Error", f"Cannot create folder: {e}", success=False)
                return

        # Run backup in background thread
        class BackupWorker(QThread):
            done = pyqtSignal(bool, str)
            def __init__(self, vault_path, backup_folder, max_backups):
                super().__init__()
                self.vault_path = vault_path
                self.backup_folder = backup_folder
                self.max_backups = max_backups
            def run(self):
                try:
                    import os, zipfile, glob
                    from datetime import datetime, timezone
                    from pathlib import Path
                    timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%M%SZ')
                    file_name = f"vault-{timestamp}.zip"
                    zip_path = os.path.join(self.backup_folder, file_name)

                    EXCLUDE = {'config.json', '.git', '__pycache__'}
                    EXCLUDE_SUFFIX = ('.lock',)
                    EXCLUDE_PREFIX = ('.obsidian/workspace', '.obsidian/app.json')

                    file_count = 0
                    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
                        vault_path_obj = Path(self.vault_path)
                        for root, dirs, files in os.walk(vault_path_obj):
                            root_path = Path(root)
                            dirs[:] = [d for d in dirs if d not in EXCLUDE]
                            for file in files:
                                file_path = root_path / file
                                if file in EXCLUDE:
                                    continue
                                if any(file.endswith(s) for s in EXCLUDE_SUFFIX):
                                    continue
                                if any(p in str(file_path) for p in EXCLUDE_PREFIX):
                                    continue
                                arcname = file_path.relative_to(vault_path_obj.parent)
                                zf.write(file_path, arcname)
                                file_count += 1

                    # FIFO rotation
                    backups = sorted(glob.glob(os.path.join(self.backup_folder, "vault-*.zip")))
                    if len(backups) > self.max_backups:
                        for old_file in backups[:len(backups) - self.max_backups]:
                            try:
                                os.remove(old_file)
                            except:
                                pass

                    size = os.path.getsize(zip_path)
                    size_str = f"{size/1024/1024:.1f} MB" if size >= 1024*1024 else f"{size/1024:.1f} KB"
                    self.done.emit(True, f"{file_name} ({file_count} files, {size_str})")
                except Exception as e:
                    self.done.emit(False, str(e))

        self._backup_worker = BackupWorker(vault_path, backup_folder, self.config.get('backup_max', 10))
        self._backup_worker.done.connect(lambda ok, msg: self._backup_result(ok, msg))
        self._backup_worker.start()
        self.backup_now_btn.setEnabled(False)
        self.backup_now_btn.setText("Backing up...")

    def _backup_result(self, success, message):
        self.backup_now_btn.setEnabled(True)
        self.backup_now_btn.setText("💾 Backup Now")
        if success:
            self.log_message(f"💾 Backup created: {message}", "success")
            self._show_custom_message_box("Backup Complete", message, success=True)
        else:
            self.log_message(f"❌ Backup failed: {message}", "error")
            self._show_custom_message_box("Backup Failed", message, success=False)

    # ========================================================================
    # v31 — VaultSeal (post-run vault backup to a private GitHub repo)
    # ========================================================================

    def _start_vault_seal(self):
        """Seal the vault in a background thread: commit + best-effort push.

        Mirrors the v29.4 BackupWorker pattern — the GUI never blocks on git
        or the network; the result lands in the log panel and refreshes the
        Backup tab status label.
        """
        run_summary = {}
        try:
            run_summary = {
                "processed": int(getattr(self.worker, 'processed', 0) or 0),
                "total": int(getattr(self.worker, 'total', 0) or 0),
            }
        except Exception:
            run_summary = {}

        class VaultSealWorker(QThread):
            done = pyqtSignal(bool, str)

            def __init__(self, config, summary):
                super().__init__()
                self.config = config
                self.summary = summary

            def run(self):
                try:
                    result = _vaultseal.seal_from_config(
                        self.config, run_summary=self.summary)
                    self.done.emit(result.ok, result.describe())
                except Exception as e:  # belt & suspenders — seal() never raises
                    self.done.emit(False, str(e))

        self.log_message("🛡️ VaultSeal: sealing vault → private GitHub mirror…", "info")
        self._vaultseal_worker = VaultSealWorker(self.config, run_summary)
        self._vaultseal_worker.done.connect(self._vault_seal_result)
        self._vaultseal_worker.start()

    def _vault_seal_result(self, ok, message):
        if hasattr(self, 'vaultseal_now_btn'):
            self.vaultseal_now_btn.setEnabled(True)
            self.vaultseal_now_btn.setText("🛡️ Seal Now")
        icon = "✅" if ok else "⚠️"
        level = "success" if ok else "warning"
        self.log_message(f"{icon} VaultSeal: {message}", level)
        self._vaultseal_refresh_status()

    def _start_websites_seal(self):
        """Seal the WEBSITES vault in a background thread (Phase 1, v0.10.0).

        Only ever called when the websites pipeline is ON and a websites
        vault path is set — with the defaults (off / empty) this never
        runs. Same QThread pattern as _start_vault_seal: the GUI never
        blocks on git or the network.
        """
        run_summary = {}
        try:
            run_summary = {
                "processed": int(getattr(self.worker, 'processed', 0) or 0),
                "total": int(getattr(self.worker, 'total', 0) or 0),
            }
        except Exception:
            run_summary = {}

        class WebsitesSealWorker(QThread):
            done = pyqtSignal(bool, str)

            def __init__(self, config, summary):
                super().__init__()
                self.config = config
                self.summary = summary

            def run(self):
                try:
                    result = _vaultseal.websites_seal_from_config(
                        self.config, run_summary=self.summary)
                    self.done.emit(result.ok, result.describe())
                except Exception as e:  # belt & suspenders — seal() never raises
                    self.done.emit(False, str(e))

        def _on_websites_seal_done(ok, message):
            icon = "✅" if ok else "⚠️"
            level = "success" if ok else "warning"
            self.log_message(f"{icon} Websites vault seal: {message}", level)

        self.log_message("🌐 Websites vault seal: sealing → its private GitHub mirror…", "info")
        self._websites_seal_worker = WebsitesSealWorker(self.config, run_summary)
        self._websites_seal_worker.done.connect(_on_websites_seal_done)
        self._websites_seal_worker.start()

    def _vaultseal_now(self):
        """Manual seal — the exact code path the post-run hook uses."""
        self._backup_save_config()
        if not self.config.get('vault_path'):
            self._show_custom_message_box("No Vault", "Set a vault path first (📁 Vault tab).", success=False)
            return
        if hasattr(self, 'vaultseal_now_btn'):
            self.vaultseal_now_btn.setEnabled(False)
            self.vaultseal_now_btn.setText("Sealing…")
        self._start_vault_seal()

    def _vaultseal_refresh_status(self):
        """Cheap status line — config only, no git subprocesses."""
        if not hasattr(self, 'vaultseal_status_label'):
            return
        if hasattr(self, 'vaultseal_enabled_check'):
            enabled = self.vaultseal_enabled_check.isChecked()
        else:
            enabled = (self.config.get('vaultseal') or {}).get('enabled', True)
        has_vault = bool(self.config.get('vault_path'))
        has_token = bool((self.config.get('github_token') or '').strip())
        if not enabled:
            text, color = "● Disabled", self._status_colors()['error']
        elif not has_vault:
            text, color = "● No vault selected", self._status_colors()['warning']
        elif not has_token:
            text, color = "● Local-only (no GitHub token — commits, no push)", self._status_colors()['warning']
        else:
            text, color = "● Ready — auto-seal after every run", self._status_colors()['success']
        self.vaultseal_status_label.setText(text)
        self.vaultseal_status_label.setStyleSheet(
            f"font-size: 13px; font-weight: bold; color: {color};")

    # ========================================================================
    # v32 — GoodRepos (post-run publish of the PUBLIC curated directory)
    # ========================================================================

    def _start_goodrepos_publish(self):
        """Publish the public directory in a background thread (never blocks).

        Mirrors the VaultSealWorker pattern exactly — the GUI never blocks
        on git or the network; the result lands in the log panel and
        refreshes the Backup tab status label.
        """
        run_summary = {}
        try:
            run_summary = {
                "processed": int(getattr(self.worker, 'processed', 0) or 0),
                "total": int(getattr(self.worker, 'total', 0) or 0),
            }
        except Exception:
            run_summary = {}

        class GoodReposWorker(QThread):
            done = pyqtSignal(bool, str)

            def __init__(self, config, summary):
                super().__init__()
                self.config = config
                self.summary = summary

            def run(self):
                try:
                    result = _goodrepos.publish_from_config(
                        self.config, run_summary=self.summary)
                    self.done.emit(result.ok, result.describe())
                except Exception as e:  # publish() never raises — belt & suspenders
                    self.done.emit(False, str(e))

        self.log_message("🌟 Good Repos: publishing the curated directory → public repo…", "info")
        self._goodrepos_worker = GoodReposWorker(self.config, run_summary)
        self._goodrepos_worker.done.connect(self._goodrepos_result)
        self._goodrepos_worker.start()

    def _goodrepos_result(self, ok, message):
        if hasattr(self, 'goodrepos_now_btn'):
            self.goodrepos_now_btn.setEnabled(True)
            self.goodrepos_now_btn.setText("🌟 Publish Now")
        icon = "✅" if ok else "⚠️"
        level = "success" if ok else "warning"
        self.log_message(f"{icon} Good Repos: {message}", level)
        self._goodrepos_refresh_status()

    def _goodrepos_now(self):
        """Manual publish — the exact code path the post-run hook uses."""
        self._backup_save_config()
        if not self.config.get('vault_path'):
            self._show_custom_message_box("No Vault", "Set a vault path first (📁 Vault tab).", success=False)
            return
        if hasattr(self, 'goodrepos_now_btn'):
            self.goodrepos_now_btn.setEnabled(False)
            self.goodrepos_now_btn.setText("Publishing…")
        self._start_goodrepos_publish()

    def _goodrepos_refresh_status(self):
        """Cheap status line — config only, no git subprocesses."""
        if not hasattr(self, 'goodrepos_status_label'):
            return
        if hasattr(self, 'goodrepos_enabled_check'):
            enabled = self.goodrepos_enabled_check.isChecked()
        else:
            enabled = (self.config.get('goodrepos') or {}).get('enabled', True)
        has_vault = bool(self.config.get('vault_path'))
        has_token = bool((self.config.get('github_token') or '').strip())
        if not enabled:
            text, color = "● Disabled", self._status_colors()['error']
        elif not has_vault:
            text, color = "● No vault selected", self._status_colors()['warning']
        elif not has_token:
            text, color = "● Local-only (no GitHub token — README built, no push)", self._status_colors()['warning']
        else:
            text, color = "● Ready — auto-publish after every run", self._status_colors()['success']
        self.goodrepos_status_label.setText(text)
        self.goodrepos_status_label.setStyleSheet(
            f"font-size: 13px; font-weight: bold; color: {color};")

    def _backup_export_zip(self):
        """Export a timestamped ZIP to a user-chosen location."""
        vault_path = self.config.get('vault_path', '')
        if not vault_path:
            self._show_custom_message_box("No Vault", "Set a vault path first (📁 Vault tab).", success=False)
            return

        from datetime import datetime
        timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        default_name = f"vault-export-{timestamp}.zip"

        file_path, _ = QFileDialog.getSaveFileName(
            self, "Export Vault ZIP", default_name, "ZIP archives (*.zip)"
        )
        if not file_path:
            return

        # Run in background
        class ExportWorker(QThread):
            done = pyqtSignal(bool, str)
            def __init__(self, vault_path, zip_path):
                super().__init__()
                self.vault_path = vault_path
                self.zip_path = zip_path
            def run(self):
                try:
                    import os, zipfile
                    from pathlib import Path
                    EXCLUDE = {'config.json', '.git', '__pycache__'}
                    EXCLUDE_SUFFIX = ('.lock',)
                    EXCLUDE_PREFIX = ('.obsidian/workspace', '.obsidian/app.json')

                    file_count = 0
                    with zipfile.ZipFile(self.zip_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
                        vault_path_obj = Path(self.vault_path)
                        for root, dirs, files in os.walk(vault_path_obj):
                            root_path = Path(root)
                            dirs[:] = [d for d in dirs if d not in EXCLUDE]
                            for file in files:
                                file_path = root_path / file
                                if file in EXCLUDE:
                                    continue
                                if any(file.endswith(s) for s in EXCLUDE_SUFFIX):
                                    continue
                                if any(p in str(file_path) for p in EXCLUDE_PREFIX):
                                    continue
                                arcname = file_path.relative_to(vault_path_obj.parent)
                                zf.write(file_path, arcname)
                                file_count += 1

                    size = os.path.getsize(self.zip_path)
                    size_str = f"{size/1024/1024:.1f} MB" if size >= 1024*1024 else f"{size/1024:.1f} KB"
                    self.done.emit(True, f"{self.zip_path} ({file_count} files, {size_str})")
                except Exception as e:
                    self.done.emit(False, str(e))

        self._export_worker = ExportWorker(vault_path, file_path)
        self._export_worker.done.connect(lambda ok, msg: self._export_result(ok, msg))
        self._export_worker.start()
        self.backup_export_btn.setEnabled(False)
        self.backup_export_btn.setText("Exporting...")

    def _export_result(self, success, message):
        self.backup_export_btn.setEnabled(True)
        self.backup_export_btn.setText("📤 Export ZIP (timestamped)")
        if success:
            self.log_message(f"📤 Exported: {message}", "success")
            self._show_custom_message_box("Export Complete", f"Saved to:\n{message}", success=True)
        else:
            self.log_message(f"❌ Export failed: {message}", "error")
            self._show_custom_message_box("Export Failed", message, success=False)

    def _backup_restore(self):
        """Open restore dialog showing local backups."""
        self._backup_save_config()
        backup_folder = self.config.get('backup_folder', '')
        if not backup_folder:
            self._show_custom_message_box("No Backup Folder", "Select a backup folder first.", success=False)
            return

        import os, glob
        backups = sorted(glob.glob(os.path.join(backup_folder, "vault-*.zip")), reverse=True)
        if not backups:
            self._show_custom_message_box("No Backups", "No backups found yet.\n\nClick 'Backup Now' first.", success=True)
            return

        # Theme-aware restore dialog
        is_dark = getattr(self, '_dark_mode', False)
        if is_dark:
            bg = "#2B2639"; text_color = "#F2EEE7"; border = "#3B344F"; input_bg = "#241F31"
        else:
            bg = "#FFFFFF"; text_color = "#241F31"; border = "#F2EEE7"; input_bg = "#FDFCF8"

        dialog = QDialog(self)
        dialog.setWindowTitle("📂 Restore from Backup")
        dialog.setMinimumWidth(450)
        dialog.setStyleSheet(
            f"QDialog {{ background-color: {bg}; }} "
            f"QLabel {{ color: {text_color}; }} "
            f"QListWidget {{ background-color: {input_bg}; color: {text_color}; border: 1px solid {border}; border-radius: 4px; }}"
        )
        layout = QVBoxLayout(dialog)

        layout.addWidget(QLabel(f"Found {len(backups)} backup(s):"))
        list_widget = QListWidget()
        for b in backups:
            name = os.path.basename(b)
            size = os.path.getsize(b)
            size_str = f"{size/1024/1024:.1f} MB" if size >= 1024*1024 else f"{size/1024:.1f} KB"
            list_widget.addItem(f"{name}    [ {size_str} ]")
        layout.addWidget(list_widget)

        def do_restore():
            if list_widget.currentRow() < 0:
                return
            backup_path = backups[list_widget.currentRow()]
            reply = self._show_custom_question("⚠️ Restore",
                f"Restore from:\n{os.path.basename(backup_path)}\n\n"
                f"This will extract to a NEW folder next to your current vault\n"
                f"(current vault is NOT touched). Continue?")
            if reply:
                vault_path = self.config.get('vault_path', '')
                import zipfile
                from datetime import datetime
                from pathlib import Path
                ts = datetime.now().strftime('%Y%m%d-%H%M%S')
                new_folder = Path(vault_path).parent / f"vault-restored-{ts}"
                new_folder.mkdir(parents=True, exist_ok=True)
                try:
                    with zipfile.ZipFile(backup_path, 'r') as zf:
                        zf.extractall(new_folder)
                    self.log_message(f"✅ Restored to: {new_folder}", "success")
                    self._show_custom_message_box("Restore Complete",
                        f"Restored to:\n{new_folder}\n\nReview and merge manually.", success=True)
                except Exception as e:
                    self._show_custom_message_box("Restore Failed", str(e), success=False)

        def do_copy():
            if list_widget.currentRow() < 0:
                return
            backup_path = backups[list_widget.currentRow()]
            save_path, _ = QFileDialog.getSaveFileName(dialog, "Copy Backup",
                os.path.basename(backup_path), "ZIP (*.zip)")
            if save_path:
                import shutil
                try:
                    shutil.copy2(backup_path, save_path)
                    self.log_message(f"✅ Copied to: {save_path}", "success")
                except Exception as e:
                    self._show_custom_message_box("Error", str(e), success=False)

        btn_row = QHBoxLayout()
        restore_btn = QPushButton("📂 Restore to New Folder")
        restore_btn.clicked.connect(do_restore)
        btn_row.addWidget(restore_btn)
        copy_btn = QPushButton("⬇️ Copy ZIP to...")
        copy_btn.clicked.connect(do_copy)
        btn_row.addWidget(copy_btn)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        dialog.exec()

    def _open_dashboard_from_backup_tab(self):
        """Open the web dashboard from the Backup tab."""
        import webbrowser
        worker_url = self.dash_worker_url_input.text().strip().rstrip('/')
        if not worker_url:
            worker_url = self.config.get('cloudflare_worker_url', '').rstrip('/')
        if not worker_url:
            self._show_custom_message_box(
                "No Worker URL",
                "Enter your Worker URL first.\n\n"
                "Example: https://github-to-obsidian-bot.your-subdomain.workers.dev",
                success=False
            )
            return
        self._backup_save_config()
        webbrowser.open(f"{worker_url}/dashboard")

    def _open_dashboard_browser(self):
        """Open dashboard from toolbar button."""
        import webbrowser
        worker_url = ''
        if hasattr(self, 'dash_worker_url_input'):
            worker_url = self.dash_worker_url_input.text().strip().rstrip('/')
        if not worker_url:
            worker_url = self.config.get('cloudflare_worker_url', '').rstrip('/')
        if not worker_url:
            self._show_custom_message_box(
                "Dashboard Not Configured",
                "Enter your Worker URL in the 💾 Backup tab first.\n\n"
                "Example: https://github-to-obsidian-bot.your-subdomain.workers.dev",
                success=False
            )
            return
        webbrowser.open(f"{worker_url}/dashboard")

    def closeEvent(self, event):
        # If processing is active, ask for confirmation BEFORE anything else
        # (the question helper must still be allowed to show its modal, so
        # the _closing flag is only set after the user confirms).
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

        # v0.06 — Fix (zombie process): the user confirmed the close (or
        # nothing was running) — shutdown begins. Mark closing so every
        # timer-driven callback (startup auto-check, proxy monitor) and
        # modal helper knows never to open a dialog from here on, and stop
        # the recurring timers so nothing fires after this point.
        self._closing = True
        for timer_name in ('_proxy_timer', '_tg_watchdog_timer'):
            timer = getattr(self, timer_name, None)
            if timer is not None:
                try:
                    timer.stop()
                except Exception:
                    pass

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
        # v0.06 — Fix (orphaned telethon children): kill every live
        # telegram worker subprocess. An orphan kept the session.session
        # SQLite file locked, which made the NEXT launch's auto bot-check
        # stall — and with the old unbounded stderr reader that stall was
        # permanent, reproducing the forever-bug on every restart.
        try:
            killed = _kill_all_telegram_workers()
            if killed:
                print(f"[closeEvent] killed {killed} telegram worker subprocess(es)",
                      file=sys.stderr, flush=True)
        except Exception:
            pass
        # Release the lock so a crashed shutdown never leaves bookkeeping
        # in a busy state (harmless at exit, correct if the app is reused).
        try:
            self._tg_lock.force_release("shutdown")
        except Exception:
            pass
        event.accept()
        # v0.06 — Fix (zombie process): force the application loop to exit.
        # Hidden parented dialogs (SettingsDialog and friends) survive the
        # main window's close; with them technically "open" Qt did NOT emit
        # lastWindowClosed, app.exec() never returned, the finally-block in
        # main() never deleted app.lock, and the process lingered as a
        # zombie — which then made every subsequent launch print "Another
        # instance is already running" and exit. An explicit quit()
        # guarantees exec() returns, the lock file is removed, and the
        # process actually dies.
        try:
            QApplication.instance().quit()
        except Exception:
            pass

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

# ============================================================================
# Main Entry Point
# ============================================================================


def main():
    # Check for headless mode
    if '--headless' in sys.argv:
        sys.exit(run_headless(sys.argv[1:]))

    # Single instance check — detects stale locks from crashed sessions
    lock_file = "app.lock"
    lock_fd = None
    try:
        lock_fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(lock_fd, str(os.getpid()).encode())
    except FileExistsError:
        # Lock file exists — check if the PID is still running
        old_pid = None
        try:
            with open(lock_file, 'r') as f:
                old_pid = int(f.read().strip())
        except (ValueError, IOError):
            pass

        if old_pid and _is_process_running(old_pid):
            print(f"Another instance is already running (PID {old_pid}). Exiting.")
            sys.exit(1)

        # Process is dead — stale lock, force remove and retry
        print(f"Removing stale lock file (PID {old_pid or 'unknown'} is no longer running)...")
        try:
            os.remove(lock_file)
        except PermissionError:
            # Windows: file may be held by a zombie handle — retry with small delay
            import time
            time.sleep(0.5)
            try:
                os.remove(lock_file)
            except PermissionError:
                # Last resort: overwrite without O_EXCL
                pass
        try:
            lock_fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(lock_fd, str(os.getpid()).encode())
        except FileExistsError:
            # O_EXCL still failing — just overwrite the file content
            try:
                if lock_fd:
                    os.close(lock_fd)
            except:
                pass
            with open(lock_file, 'w') as f:
                f.write(str(os.getpid()))
            lock_fd = None  # We don't have an exclusive lock, but that's OK
    except OSError as e:
        print(f"Cannot create lock file ({lock_file}): {e}")
        # Don't exit — just continue without lock
        lock_fd = None

    try:
        # Enable high-DPI scaling for sharp rendering on 4K displays
        try:
            QApplication.setHighDpiScaleFactorRoundingPolicy(
                Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
            )
        except AttributeError:
            pass  # Older PyQt6 versions
        app = QApplication(sys.argv)
        app.setStyle('Fusion')
        window = MainWindow()
        window.show()
        exit_code = app.exec()
    except Exception as e:
        print(f"Fatal error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        exit_code = 1
    finally:
        if lock_fd is not None:
            try:
                os.close(lock_fd)
            except OSError:
                pass
        try:
            os.remove(lock_file)
        except OSError:
            pass
        sys.exit(exit_code)


if __name__ == "__main__":
    main()
