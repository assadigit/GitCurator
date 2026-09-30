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


class MainWindow(DashboardMixin, ProcessingControlMixin, TelegramUiMixin, InputProxyMixin, VaultConfigMixin, TestConnectionModalMixin, Phase6Mixin, LlamaCppMixin, ConnectionTestsMixin, HeroMixin, UiMixin, ThemeMixin, QMainWindow):
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


    def _startup_auto_check(self):
        """Auto-check bot queue on startup — validates proxy first.

        v0.06 — the old "reset the Telegram lock on startup" line was
        removed: the lock object is created fresh in __init__ (it can't be
        stuck), and blindly clearing it 2s after launch could wipe a lock
        legitimately acquired during the first two seconds. The watchdog
        timer now handles genuinely stuck holders."""
        # v0.06 — Fix (zombie process): this fires 2s after launch — if the
        # user already closed the window (or is closing it), never open the
        # modal dialogs below; that invisible modal used to keep the app
        # process alive forever holding app.lock.
        if getattr(self, '_closing', False) or not self.isVisible():
            return
        # Check proxy is enabled
        proxy = self._get_proxy_dict()
        if not proxy.get('enabled'):
            self._show_custom_message_box(
                "Proxy Required",
                "Proxy is not enabled.\n\n"
                "Please enable proxy in the Proxy tab and restart the app.",
                success=False
            )
            return

        # Quick socket check
        import socket
        host = proxy.get('host', '127.0.0.1')
        port = int(proxy.get('port', 10808))
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)
            result = sock.connect_ex((host, port))
            sock.close()
            if result != 0:
                self._show_custom_message_box(
                    "Proxy Unreachable",
                    f"Cannot connect to proxy at {host}:{port}.\n\n"
                    f"Please start v2rayN and restart the app.",
                    success=False
                )
                return
        except Exception:
            self._show_custom_message_box(
                "Proxy Check Failed",
                f"Could not verify proxy at {host}:{port}.\n\n"
                f"Make sure v2rayN is running.",
                success=False
            )
            return

        # Proxy is OK — auto-check bot queue
        bot_username = getattr(self, 'bot_username', QLineEdit()).text().strip().lstrip('@') if hasattr(self, 'bot_username') else ""
        api_id = self.api_id.text()
        api_hash = self.api_hash.text()
        phone = self.phone.text()

        if not bot_username or not api_id or not api_hash or not phone:
            self.log_message("⚠️ Cannot auto-check bot queue — credentials incomplete. Fill them in first.", "warning")
            return

        self.log_message("📬 Auto-checking bot queue on startup...", "info")
        # Show the log panel so user can see progress
        if not self.findChild(QSplitter) or not self.findChild(QSplitter).widget(1).isVisible():
            self._toggle_log_panel()

        self.check_bot_queue()


    # ------------------------------------------------------------------
    # Sources tab — fetch GitHub URLs from RSS / Reddit .json endpoints
    # (free, no API key needed) and feed them into the normal pipeline.
    # ------------------------------------------------------------------
    def fetch_from_sources(self):
        """Fetch GitHub URLs from an RSS feed or Reddit .json endpoint."""
        import urllib.request
        import ssl
        import xml.etree.ElementTree as ET

        url = self.sources_url.text().strip()
        if not url:
            self.log_message("Please enter a URL.", "warning")
            return

        self.log_message(f"🔍 Fetching from: {url}", "info")
        self.sources_results.clear()

        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
                data = resp.read().decode('utf-8', errors='ignore')

            # Extract GitHub URLs using regex
            pattern = re.compile(r'https?://github\.com/([a-zA-Z0-9\-_]+)/([a-zA-Z0-9\-_]+)')
            urls = []

            if '.json' in url:
                # Reddit JSON
                import json as _json
                data_json = _json.loads(data)
                children = data_json.get('data', {}).get('children', [])
                for child in children:
                    text = child.get('data', {}).get('selftext', '') + ' ' + child.get('data', {}).get('url', '') + ' ' + child.get('data', {}).get('title', '')
                    for owner, repo in pattern.findall(text):
                        u = f"https://github.com/{owner}/{repo}"
                        if u not in urls:
                            urls.append(u)
            else:
                # RSS feed (XML)
                for match in pattern.findall(data):
                    owner, repo = match
                    u = f"https://github.com/{owner}/{repo}"
                    if u not in urls:
                        urls.append(u)

            if urls:
                self._sources_urls = urls
                display = '\n'.join(urls)
                self.sources_results.setPlainText(f"Found {len(urls)} GitHub URLs:\n\n{display}")
                self.log_message(f"✅ Found {len(urls)} GitHub URLs from source", "success")
            else:
                self.sources_results.setPlainText("No GitHub URLs found in the source.")
                self.log_message("⚠️ No GitHub URLs found in the source.", "warning")

        except Exception as e:
            self.log_message(f"❌ Failed to fetch: {e}", "error")
            self.sources_results.setPlainText(f"Error: {e}")

    def process_sources_urls(self):
        """Process the URLs fetched from sources."""
        urls = getattr(self, '_sources_urls', [])
        if not urls:
            self.log_message("No URLs to process. Fetch from a source first.", "warning")
            return
        # v31.1 safety gate: confirm before large batches (>10 items).
        if not self._confirm_batch(len(urls), "the fetched sources"):
            self.log_message("⏹️ Batch cancelled — nothing was processed.", "warning")
            return
        self.log_message(f"🚀 Processing {len(urls)} URLs from sources...", "info")
        self._start_worker_with_urls(urls)

    def check_bot_queue(self, on_done=None):
        """Check the bot's Telegram chat for pending GitHub repos.

        v0.03: optional ``on_done(name, result)`` callback — connected AFTER
        the internal _on_finished handler so it observes the updated
        _bot_queue_urls. Returns False when the check bailed early (busy
        Telegram lock / missing bot username / missing credentials), True
        once the fetch worker actually started."""
        if not self._acquire_telegram_lock("bot_check"):
            return False
        bot_username = self.bot_username.text().strip().lstrip('@')
        if not bot_username:
            self.log_message("❌ Please enter the bot username first.", "error")
            self._release_telegram_lock("bot_check")
            return False
        api_id = self.api_id.text()
        api_hash = self.api_hash.text()
        phone = self.phone.text()
        if not api_id or not api_hash or not phone:
            self.log_message("❌ Telegram credentials required.", "error")
            self._release_telegram_lock("bot_check")
            return False

        proxy = self._get_proxy_dict()
        # v0.07.1 — Fix: the queue check used to spawn the worker silently
        # with the proxy DISABLED (user-side checkbox state) and burn 4
        # cryptic ConnectionRefusedError retries. Warn loudly BEFORE the
        # spawn so the cause is visible in the log next to the failure.
        if not proxy.get('enabled'):
            self.log_message(
                "⚠️ Proxy is NOT enabled — Telegram will try a DIRECT connection "
                "(blocked in Iran). Enable it in Settings → Proxy and re-run.",
                "warning")
        self.save_config()
        self.log_message(f"📬 Checking bot queue (@{bot_username})...", "info")
        self.queue_display.clear()

        # v0.06 — Perf: hand the vault path to the background job so the
        # vault filtering (index rebuild + pending classification) happens
        # on the worker thread — the GUI used to freeze 0.5-5s on EVERY
        # queue check, including the startup auto-check.
        _vault_for_filter = self.vault_combo.currentText()
        if _vault_for_filter and not os.path.isdir(_vault_for_filter):
            _vault_for_filter = ''

        worker = TestWorker(_bot_queue_job, "bot_check",
                            api_id, api_hash, phone, proxy, bot_username, None, None, None)
        def _job(aid, ahash, ph, px, bu, _ignored_log, _ignored_code, _ignored_mark):
            return _bot_queue_job(aid, ahash, ph, px, bu, worker.log_message,
                                  worker.request_code,
                                  vault_path=(_vault_for_filter or None),
                                  blocked_domains=_links.blocked_domains_from_config(self.config),
                                  self_domains=_links.self_domains_from_config(self.config))
        worker._fn = _job

        worker.log_message.connect(self.log_message)
        worker.code_requested.connect(
            lambda pt, w=worker: self._on_telegram_code_requested(pt, w)
        )

        def _on_finished(name, result):
            if result.get('success'):
                all_urls = result.get('urls', [])
                non_github = result.get('non_github_urls', [])
                self._bot_queue_non_github = non_github
                self._bot_queue_max_id = result.get('max_message_id', 0)
                self._bot_queue_duplicates = result.get('duplicates_removed', 0)
                self._bot_queue_raw_count = result.get('raw_url_count', len(all_urls) + len(non_github))

                # Add non-GitHub links to inbox
                if non_github:
                    # v0.20.0 — the tables land in the WEBSITES vault when
                    # one is set ("the github vault only manages its
                    # domains"); fallback = the GitHub vault.
                    _table_vault = _inbox_table_vault(self.config)
                    if _table_vault:
                        write_inbox_links_by_platform(
                            _table_vault, non_github, source="Bot",
                            log_callback=lambda msg, lvl: self.log_message(msg, lvl),
                        )

                # Q1/Q6: Filter URLs against VaultIndex + decommissioned —
                # only show NOT-in-vault AND NOT-decommissioned as pending.
                # v0.06 — Perf: the filtering already ran on the WORKER
                # thread (see _bot_queue_job); the GUI thread only reads the
                # precomputed fields. The legacy GUI-thread path remains as
                # a fallback for results that arrive unfiltered.
                if 'pending_urls' in result:
                    pending_urls = result.get('pending_urls', [])
                    in_vault_count = result.get('in_vault_count', 0)
                    decommissioned_count = result.get('decommissioned_count', 0)
                    blocked_count = result.get('blocked_count', 0)
                    self_count = result.get('self_count', 0)
                    if result.get('vault_index_count'):
                        self.log_message(
                            f"📚 Vault index: {result['vault_index_count']} notes indexed", "info")
                else:
                    in_vault_count = 0
                    decommissioned_count = 0
                    blocked_count = 0
                    self_count = 0
                    pending_urls = []
                    # v0.20.0 — the legacy GUI-side filter applies the
                    # same blocked-domain bucket as the worker-side one
                    # (v0.21.0: + the self-domain bucket).
                    _blocked_list = _links.blocked_domains_from_config(self.config)
                    _self_list = _links.self_domains_from_config(self.config)
                    vault_path = self.vault_combo.currentText()
                    if vault_path and os.path.isdir(vault_path):
                        try:
                            vi = VaultIndex(vault_path)
                            vi.rebuild(log_signal=None)
                            self.log_message(f"📚 Vault index: {vi.count} notes indexed", "info")

                            # Load CONFIRMED-dead URLs (v0.08 quarantine:
                            # fail_count >= threshold; unconfirmed 1-2
                            # attempt entries stay pending for their retries)
                            try:
                                cache = CacheDB()
                                decommissioned_urls = cache.get_dead_url_set()
                                cache.close()
                            except Exception:
                                decommissioned_urls = set()

                            for url in all_urls:
                                norm = normalize_url(url)
                                if _links.domain_is_blocked(url, _blocked_list):
                                    blocked_count += 1
                                elif _links.domain_is_self(url, _self_list):
                                    self_count += 1
                                elif vi.has_url(url):
                                    in_vault_count += 1
                                elif norm in decommissioned_urls:
                                    decommissioned_count += 1
                                else:
                                    pending_urls.append(url)
                        except Exception as vi_err:
                            self.log_message(f"⚠️ VaultIndex failed, showing all URLs: {vi_err}", "warning")
                            pending_urls = all_urls
                    else:
                        pending_urls = all_urls

                # Store ONLY pending URLs for processing (Q9: progress bar shows only new repos)
                self._bot_queue_urls = pending_urls

                # Update pending badge (Q15). v31.1: zinc + ⏳ — a pending
                # count is routine, NOT an error; red is reserved for failures.
                if pending_urls:
                    self.pending_badge.setText(f"⏳ {len(pending_urls)} pending")
                    self.pending_badge.setStyleSheet(
                        "background-color: #6C6480; color: white; padding: 4px 8px; "
                        "border-radius: 10px; font-size: 12px; font-weight: bold;"
                    )
                    self.pending_badge.setVisible(True)
                else:
                    self.pending_badge.setText("✅ 0 pending")
                    self.pending_badge.setStyleSheet(
                        "background-color: #B9E3C9; color: #17402B; padding: 4px 8px; "
                        "border-radius: 10px; font-size: 12px; font-weight: bold;"
                    )
                    self.pending_badge.setVisible(True)

                # Build display (Q6: show vault-dedup count)
                display = f"📬 Bot Queue Results\n"
                display += f"{'='*50}\n"
                display += f"Total GitHub URLs in bot:  {len(all_urls)}\n"
                display += f"✅ Already in vault:        {in_vault_count}\n"
                display += f"🗑️ Decommissioned (404):    {decommissioned_count}\n"
                if blocked_count:
                    display += f"🚫 Blocked domains:         {blocked_count}\n"
                if self_count:
                    display += f"🔒 Self domains (own bot):  {self_count}\n"
                display += f"⏳ Pending (not in vault):  {len(pending_urls)}\n"
                display += f"🔗 Non-GitHub links:        {len(non_github)}\n"
                if getattr(self, '_bot_queue_duplicates', 0) > 0:
                    display += f"🔄 Duplicates removed:      {self._bot_queue_duplicates}\n"
                display += f"{'='*50}\n\n"

                if pending_urls:
                    display += "PENDING GITHUB REPOS (need processing):\n"
                    for i, u in enumerate(pending_urls, 1):
                        display += f"  {i}. {u}\n"
                else:
                    display += "🎉 All GitHub repos are already in the vault!\n"
                    display += "Click '✅ Verify All Processed' to confirm.\n"

                if non_github:
                    display += f"\nNON-GITHUB LINKS ({len(non_github)}):\n"
                    display += "(Recorded in _inbox/ per platform)\n"

                self.queue_display.setPlainText(display)

                if pending_urls:
                    self.log_message(
                        f"📬 Queue: {len(pending_urls)} new repos pending ({in_vault_count} already in vault)",
                        "success"
                    )
                else:
                    self.log_message(
                        f"📬 Queue: All {len(all_urls)} GitHub repos already in vault! 0 pending.",
                        "success"
                    )
                    # Hide mark-all-read button until verify passes
                    self.mark_all_read_btn.setVisible(False)
            else:
                self.log_message(f"❌ Bot queue check failed: {result.get('error')}", "error")
                self.queue_display.setPlainText(f"Error: {result.get('error', 'Unknown')}")

        worker.finished_signal.connect(_on_finished)
        if on_done is not None:
            # Connected after _on_finished → runs once _bot_queue_urls is
            # already updated. _keep_worker's _cleanup is connected last, so
            # the Telegram lock is released after on_done, not before.
            worker.finished_signal.connect(on_done)
        self._keep_worker(worker, owner="bot_check")
        worker.start()
        return True

    def process_bot_queue(self):
        """Process all repos in the bot queue."""
        urls = getattr(self, '_bot_queue_urls', [])
        if not urls:
            self.log_message("No repos in queue. Click 'Check Queue' first.", "warning")
            return
        # v31.1 safety gate: confirm before large batches (>10 items).
        if not self._confirm_batch(len(urls), "the bot queue"):
            self.log_message("⏹️ Batch cancelled — nothing was processed.", "warning")
            return
        self.log_message(f"🚀 Processing {len(urls)} repos from bot queue...", "info")
        # Process the URLs using the existing pipeline. v23 — pass
        # bot_source=True and the non-GitHub links so the manifest can
        # track every link through the 5-phase pipeline.
        # v25 pre-flight: forward the intake duplicate stats so the final
        # report can show "🔄 N duplicate URL(s) removed".
        self._start_worker_with_urls(
            urls,
            bot_source=True,
            non_github_urls=getattr(self, '_bot_queue_non_github', []),
            intake_duplicates=getattr(self, '_bot_queue_duplicates', 0),
            raw_url_count=getattr(self, '_bot_queue_raw_count', 0),
        )
        # Note: we don't mark as read here — the user can click "Mark All Read"
        # separately after verifying processing succeeded.

    def process_new_bot_queue(self):
        """v25 pre-flight: Fetch only bot messages newer than the last
        successfully-processed message ID, then process them.

        Workflow:
          1. Read ``last_processed_msg_id`` from config (0 on first run).
          2. Call ``_bot_queue_job(min_id=last_processed_msg_id)`` to fetch
             only messages with id > last_processed_msg_id.
          3. If new URLs are found, start the ProcessingWorker with
             ``bot_source=True`` and stash the new max_message_id.
          4. ``processing_finished`` saves the new last_processed_msg_id
             ONLY after the LinkTracker verifies all links (Phase 5 CLEAR).
             If any link fails verification, the ID is NOT advanced, so the
             next "Process New" run will re-fetch the failed messages and
             retry them.
        """
        if not self._acquire_telegram_lock("bot_process_new"):
            return
        bot_username = self.bot_username.text().strip().lstrip('@')
        if not bot_username:
            self.log_message("❌ Please enter the bot username first.", "error")
            self._release_telegram_lock("bot_process_new")
            return
        api_id = self.api_id.text()
        api_hash = self.api_hash.text()
        phone = self.phone.text()
        if not api_id or not api_hash or not phone:
            self.log_message("❌ Telegram credentials required.", "error")
            self._release_telegram_lock("bot_process_new")
            return

        proxy = self._get_proxy_dict()
        last_id = int(self.config.get('last_processed_msg_id', 0) or 0)
        self.log_message(
            f"📬 Process New: fetching messages newer than ID {last_id}...",
            "info"
        )

        worker = TestWorker(_bot_queue_job, "bot_process_new",
                            api_id, api_hash, phone, proxy, bot_username, None, None, None)
        def _job(aid, ahash, ph, px, bu, _ignored_log, _ignored_code, _ignored_mark):
            return _bot_queue_job(aid, ahash, ph, px, bu, worker.log_message,
                                  worker.request_code, mark_read=False, min_id=last_id)
        worker._fn = _job

        worker.log_message.connect(self.log_message)
        worker.code_requested.connect(
            lambda pt, w=worker: self._on_telegram_code_requested(pt, w)
        )

        def _on_finished(name, result):
            # v0.06 — release early (owner-scoped) so the user can run other
            # Telegram ops while the direct-mode batch processes the URLs.
            self._release_telegram_lock("bot_process_new")
            if not result.get('success'):
                self.log_message(f"❌ Process New failed: {result.get('error')}", "error")
                return
            urls = result.get('urls', [])
            non_github = result.get('non_github_urls', [])
            max_id = result.get('max_message_id', 0)
            dupes = result.get('duplicates_removed', 0)
            raw = result.get('raw_url_count', len(urls) + len(non_github))

            if not urls and not non_github:
                self.log_message(
                    f"✅ All caught up — no new messages since ID {last_id}.",
                    "success"
                )
                self.queue_display.setPlainText(
                    f"📬 No new messages since last processed ID {last_id}.\n\n"
                    f"Last processed message ID: {last_id}\n"
                    f"Forward new GitHub repo URLs to your bot, then click "
                    f"'📬 Process New' again."
                )
                return

            # Stash for processing_finished to commit after verification.
            self._bot_queue_urls = urls
            self._bot_queue_non_github = non_github
            self._bot_queue_max_id = max_id
            self._bot_queue_duplicates = dupes
            self._bot_queue_raw_count = raw
            # v25 pre-flight: this flag tells processing_finished to advance
            # last_processed_msg_id after Phase 5 CLEAR passes.
            self._pending_last_processed_update = max_id

            display = (
                f"📬 Process New: {len(urls)} new GitHub repos + "
                f"{len(non_github)} non-GitHub links (since ID {last_id})\n\n"
            )
            if dupes > 0:
                display += (
                    f"🔄 {dupes} duplicate URL(s) removed "
                    f"({len(urls) + len(non_github)} unique from {raw} total)\n\n"
                )
            if urls:
                display += "GITHUB REPOS:\n"
                for i, u in enumerate(urls, 1):
                    display += f"  {i}. {u}\n"
            if non_github:
                display += "\nNON-GITHUB LINKS:\n"
                for i, u in enumerate(non_github, 1):
                    display += f"  {i}. {u}\n"
            self.queue_display.setPlainText(display)

            self.log_message(
                f"🚀 Process New: starting batch of {len(urls)} GitHub repos...",
                "info"
            )
            # Record non-GitHub links to per-platform inbox files immediately
            # (the worker's Phase 1 intake will also do this, but doing it
            # now means the links are safe even if the user cancels before
            # the worker's intake runs).
            if non_github:
                # v0.20.0 — the tables land in the WEBSITES vault when one
                # is set ("the github vault only manages its domains").
                _table_vault = _inbox_table_vault(self.config)
                if _table_vault:
                    write_inbox_links_by_platform(
                        _table_vault, non_github, source="Bot",
                        log_callback=lambda msg, lvl: self.log_message(msg, lvl),
                    )

            # Start the worker. processing_finished will advance
            # last_processed_msg_id to self._pending_last_processed_update
            # ONLY if Phase 5 CLEAR passes (all links verified).
            # v31.1 safety gate: confirm before large batches (>10 items).
            if not self._confirm_batch(len(urls), "the new bot messages"):
                self.log_message("⏹️ Batch cancelled — nothing was processed.", "warning")
                return
            self._start_worker_with_urls(
                urls,
                bot_source=True,
                non_github_urls=non_github,
                intake_duplicates=dupes,
                raw_url_count=raw,
            )

        worker.finished_signal.connect(_on_finished)
        self._keep_worker(worker, owner="bot_process_new")
        worker.start()

    def export_all_bot_links(self):
        """Fetch ALL links from the bot and save to a file for manual verification.
        This lets the user compare what the app found vs what they actually forwarded."""
        if not self._acquire_telegram_lock("bot_export"):
            return
        bot_username = self.bot_username.text().strip().lstrip('@')
        if not bot_username:
            self.log_message("❌ Please enter the bot username first.", "error")
            self._release_telegram_lock("bot_export")
            return
        api_id = self.api_id.text()
        api_hash = self.api_hash.text()
        phone = self.phone.text()
        if not api_id or not api_hash or not phone:
            self.log_message("❌ Telegram credentials required.", "error")
            self._release_telegram_lock("bot_export")
            return

        proxy = self._get_proxy_dict()
        vault = self.vault_combo.currentText()
        if not vault:
            self.log_message("❌ No vault selected — need a place to save the export.", "error")
            self._release_telegram_lock("bot_export")
            return

        self.log_message("📋 Exporting ALL links from bot (no limit)...", "info")

        worker = TestWorker(_bot_queue_job, "export_links",
                            api_id, api_hash, phone, proxy, bot_username, None, None, None)
        def _job(aid, ahash, ph, px, bu, _ignored_log, _ignored_code, _ignored_mark):
            return _bot_queue_job(aid, ahash, ph, px, bu, worker.log_message, worker.request_code)
        worker._fn = _job

        worker.log_message.connect(self.log_message)
        worker.code_requested.connect(
            lambda pt, w=worker: self._on_telegram_code_requested(pt, w)
        )

        def _on_finished(name, result):
            if result.get('success'):
                urls = result.get('urls', [])
                non_github = result.get('non_github_urls', [])
                total_msgs = result.get('total_messages', 0)
                raw_count = result.get('raw_url_count', 0)
                dups = result.get('duplicates_removed', 0)

                # Save to vault as a verification file
                export_path = os.path.join(vault, f"_bot_links_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md")
                lines = []
                lines.append(f"# 📋 Bot Links Export — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
                lines.append("")
                lines.append("## 📊 Statistics")
                lines.append(f"- 📬 Total messages in bot: **{total_msgs}**")
                lines.append(f"- 🔗 Total raw links found: **{raw_count}**")
                lines.append(f"- 🔄 Duplicates removed: **{dups}**")
                lines.append(f"- ✅ Unique GitHub repos: **{len(urls)}**")
                lines.append(f"- ✅ Unique non-GitHub links: **{len(non_github)}**")
                lines.append(f"- 📝 Total unique links: **{len(urls) + len(non_github)}**")
                lines.append("")
                lines.append("## 🐙 GitHub Repos")
                lines.append("")
                for i, url in enumerate(urls, 1):
                    lines.append(f"{i}. {url}")
                lines.append("")
                lines.append("## 🔗 Non-GitHub Links")
                lines.append("")
                for i, url in enumerate(non_github, 1):
                    lines.append(f"{i}. {url}")
                lines.append("")
                lines.append("---")
                lines.append(f"*Export generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")
                lines.append(f"*Compare this list with what you forwarded to the bot to verify no links are missing.*")

                try:
                    with open(export_path, 'w', encoding='utf-8') as f:
                        f.write('\n'.join(lines))
                    self.log_message(f"📋 Export saved: {export_path}", "success")
                    self.log_message(f"📊 Total messages: {total_msgs} | GitHub: {len(urls)} | Non-GitHub: {len(non_github)} | Duplicates: {dups}", "info")
                    self._show_custom_message_box(
                        "📋 Links Exported",
                        f"Total messages: {total_msgs}\n"
                        f"GitHub repos: {len(urls)}\n"
                        f"Non-GitHub: {len(non_github)}\n"
                        f"Duplicates removed: {dups}\n\n"
                        f"Export saved to:\n{os.path.basename(export_path)}\n\n"
                        f"Open this file in Obsidian to verify all links are accounted for.",
                        success=True
                    )
                except Exception as e:
                    self.log_message(f"❌ Failed to save export: {e}", "error")
            else:
                self.log_message(f"❌ Export failed: {result.get('error')}", "error")

        worker.finished_signal.connect(_on_finished)
        self._keep_worker(worker, owner="bot_export")
        worker.start()

    def verify_all_bot_links(self):
        """v26 — Fix 6: Verify that ALL bot links have been processed into
        the vault.

        Fetches ALL GitHub URLs from the bot (using the same ``_bot_queue_job``
        worker as ``export_all_bot_links``), then checks each GitHub URL
        against the in-memory ``VaultIndex``. Does NOT process anything —
        just reports:
          - Total GitHub links in bot
          - Found in vault ✅
          - Missing from vault ⏳ (with the list of missing URLs)

        The report is shown in the Bot tab's ``queue_display`` text area so
        the user can review it without switching tabs. The whole method is
        wrapped in a try/except so any crash (Telegram auth failure, vault
        read error, etc.) is logged instead of taking down the app."""
        try:
            if not self._acquire_telegram_lock("bot_verify_all"):
                return
            bot_username = self.bot_username.text().strip().lstrip('@')
            if not bot_username:
                self.log_message("❌ Please enter the bot username first.", "error")
                self._release_telegram_lock("bot_verify_all")
                return
            api_id = self.api_id.text()
            api_hash = self.api_hash.text()
            phone = self.phone.text()
            if not api_id or not api_hash or not phone:
                self.log_message("❌ Telegram credentials required.", "error")
                self._release_telegram_lock("bot_verify_all")
                return

            vault_path = self.vault_combo.currentText()
            if not vault_path or not os.path.isdir(vault_path):
                self.log_message("❌ No vault selected — cannot verify links.", "error")
                self._release_telegram_lock("bot_verify_all")
                return

            proxy = self._get_proxy_dict()
            self.save_config()
            self.log_message("✅ Verifying all bot links against vault...", "info")
            self.queue_display.setPlainText(
                "⏳ Fetching ALL links from bot...\n"
                "(This may take a while for large bot queues.)"
            )

            worker = TestWorker(_bot_queue_job, "bot_verify_all",
                                api_id, api_hash, phone, proxy, bot_username, None, None, None)
            def _job(aid, ahash, ph, px, bu, _ignored_log, _ignored_code, _ignored_mark):
                return _bot_queue_job(aid, ahash, ph, px, bu, worker.log_message, worker.request_code)
            worker._fn = _job

            worker.log_message.connect(self.log_message)
            worker.code_requested.connect(
                lambda pt, w=worker: self._on_telegram_code_requested(pt, w)
            )

            def _on_finished(name, result):
                try:
                    if not result.get('success'):
                        err = result.get('error', 'Unknown error')
                        self.log_message(f"❌ Verify All failed: {err}", "error")
                        self.queue_display.setPlainText(f"Error: {err}")
                        return

                    urls = result.get('urls', [])
                    non_github = result.get('non_github_urls', [])

                    # Build the vault index (ground truth for "is this URL processed?")
                    try:
                        vi = VaultIndex(vault_path)
                        vi.rebuild(log_signal=None)
                        self.log_message(f"📚 Vault index: {vi.count} notes indexed", "info")
                    except Exception as vi_err:
                        self.log_message(f"❌ Failed to build vault index: {vi_err}", "error")
                        self.queue_display.setPlainText(f"Error: {vi_err}")
                        return

                    github_in_vault = 0
                    github_decommissioned = 0
                    github_missing = []

                    # v0.06 — Perf: ONE CacheDB connection for both the
                    # decommissioned set and the processed-URL set (the old
                    # code opened and closed two connections back-to-back).
                    try:
                        cache = CacheDB()
                        # v0.08 — verification counts CONFIRMED-dead links
                        # only; unconfirmed (1-2 attempts) fall through to
                        # the missing/pending buckets where they belong.
                        decommissioned_urls = cache.get_dead_url_set()
                        # v29.10 — Also load processed URLs to catch cases
                        # where the note's source: field has a different URL
                        # format (e.g., embedchain/embedchain was renamed to
                        # mem0ai/mem0 on GitHub)
                        cache_processed_urls = set()
                        for row in cache.get_all_processed_urls():
                            cache_processed_urls.add(row[0] if isinstance(row, tuple) else row)
                        cache.close()
                    except Exception:
                        decommissioned_urls = set()
                        cache_processed_urls = set()

                    for url in urls:
                        try:
                            norm = normalize_url(url)
                            if vi.has_url(url):
                                github_in_vault += 1
                            elif norm in decommissioned_urls:
                                github_decommissioned += 1
                            elif norm in cache_processed_urls or url in cache_processed_urls:
                                # v29.10 — Found in CacheDB (was processed before, even if
                                # the note's source: field has a different URL after rename)
                                github_in_vault += 1
                                self.log_message(f"✅ Found in cache (processed before): {url}", "info")
                            else:
                                # v29.9 — Fuzzy match: extract owner/repo and check if any
                                # vault note has that pair in its source URL or filename.
                                fuzzy_match = self._fuzzy_match_github_url(vi, url)
                                if fuzzy_match:
                                    github_in_vault += 1
                                    self.log_message(f"✅ Fuzzy match: {url} → {os.path.basename(fuzzy_match)}", "info")
                                else:
                                    github_missing.append(url)
                        except Exception:
                            github_missing.append(url)

                    # v29.7 — Log the missing URLs so user knows exactly what to process
                    # v31.1: ⏳ — missing-from-vault is PENDING work, not a failure.
                    if github_missing:
                        self.log_message(f"⏳ {len(github_missing)} missing GitHub link(s):", "warning")
                        for i, url in enumerate(github_missing, 1):
                            self.log_message(f"   {i}. {url}", "info")

                    # Build the human-readable report
                    lines = []
                    lines.append("✅ VERIFICATION REPORT")
                    lines.append("=" * 60)
                    lines.append(f"Total GitHub links in bot:      {len(urls)}")
                    lines.append(f"✅ Found in vault:              {github_in_vault}")
                    lines.append(f"🗑️ Decommissioned (404):        {github_decommissioned}")
                    lines.append(f"⏳ Missing from vault:          {len(github_missing)}")
                    lines.append(f"🔗 Non-GitHub links:            {len(non_github)}")
                    lines.append("")
                    lines.append("ALL GITHUB LINKS — DETAILED CHECK:")
                    lines.append("-" * 60)
                    for i, url in enumerate(urls, 1):
                        try:
                            if vi.has_url(url):
                                note_path = vi.get_path(url)
                                note_name = os.path.basename(note_path) if note_path else "?"
                                lines.append(f"  {i:3d}. ✅ {url}")
                                lines.append(f"       → {note_name}")
                            else:
                                lines.append(f"  {i:3d}. ⏳ {url}")
                        except Exception:
                            lines.append(f"  {i:3d}. ❌ {url} (check error)")
                    lines.append("-" * 60)
                    lines.append("")
                    if github_missing:
                        lines.append(f"SUMMARY: {len(github_missing)} link(s) need processing!")
                        lines.append("")
                        lines.append("To process the missing links:")
                        lines.append("  1. Click '📬 Check Queue' to load them")
                        lines.append("  2. Click '🚀 Process All' to process")
                    else:
                        lines.append("🎉 All GitHub links are in the vault!")
                    lines.append("=" * 60)

                    report_text = '\n'.join(lines)
                    self.queue_display.setPlainText(report_text)

                    # Also save to vault for permanent record
                    try:
                        report_path = os.path.join(vault_path, f"_verification_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md")
                        with open(report_path, 'w', encoding='utf-8') as f:
                            f.write(f"# ✅ Verification Report — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")
                            f.write(f"## 📊 Summary\n\n")
                            f.write(f"| Metric | Count |\n|--------|-------|\n")
                            f.write(f"| 📬 Total GitHub links in bot | {len(urls)} |\n")
                            f.write(f"| ✅ Found in vault | {github_in_vault} |\n")
                            f.write(f"| ⏳ Missing from vault | {len(github_missing)} |\n")
                            f.write(f"| 🔗 Non-GitHub links | {len(non_github)} |\n\n")
                            f.write(f"## 📋 Detailed Check\n\n")
                            f.write(f"| # | Status | URL | Note |\n")
                            f.write(f"|---|--------|-----|------|\n")
                            for i, url in enumerate(urls, 1):
                                try:
                                    if vi.has_url(url):
                                        note_path = vi.get_path(url)
                                        note_name = os.path.basename(note_path) if note_path else "?"
                                        f.write(f"| {i} | ✅ | {url} | {note_name} |\n")
                                    else:
                                        f.write(f"| {i} | ⏳ | {url} | — |\n")
                                except Exception:
                                    f.write(f"| {i} | ❌ | {url} | error |\n")
                            f.write(f"\n---\n*Report generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n")
                        self.log_message(f"📄 Verification report saved to vault: {os.path.basename(report_path)}", "success")
                    except Exception:
                        pass

                    summary = (
                        f"✅ Verified: {github_in_vault}/{len(urls)} GitHub links in vault"
                    )
                    if github_missing:
                        summary += f" ({len(github_missing)} missing)"
                        self.log_message(summary, "warning")
                        # v29.11 — Manual Resolve dialog
                        self._show_manual_resolve_dialog(github_missing)
                    else:
                        # 0 truly missing — decommissioned doesn't count as missing
                        self.log_message(
                            f"✅ Verified: {github_in_vault} in vault + {github_decommissioned} decommissioned = {github_in_vault + github_decommissioned}/{len(urls)} accounted for",
                            "success"
                        )
                        # Q5/Q13: Show Mark All Read button after verify passes
                        self.mark_all_read_btn.setVisible(True)
                        # Ask if user wants to mark all as read
                        if self._show_custom_question(
                            "✅ All Verified!",
                            f"All GitHub links accounted for! 🎉\n\n"
                            f"  ✅ In vault: {github_in_vault}\n"
                            f"  🗑️ Decommissioned: {github_decommissioned}\n"
                            f"  ❌ Missing: {len(github_missing)}\n\n"
                            f"Would you like to mark all bot messages as read now?\n"
                            f"This will clear the bot queue for future batches."
                        ):
                            # v0.06 — Fix (signal-ordering self-deadlock):
                            # this handler runs while the verify worker still
                            # holds the Telegram lock (its cleanup is connected
                            # later), so calling clear_bot_queue() directly was
                            # ALWAYS denied with "another Telegram operation is
                            # already running". Defer one event-loop tick.
                            QTimer.singleShot(0, self.clear_bot_queue)
                except Exception as inner_e:
                    import traceback
                    self.log_message(f"❌ Verify All report failed: {inner_e}", "error")
                    try:
                        self.queue_display.setPlainText(
                            f"Error generating report: {inner_e}\n\n{traceback.format_exc()}"
                        )
                    except Exception:
                        pass

            worker.finished_signal.connect(_on_finished)
            self._keep_worker(worker, owner="bot_verify_all")
            worker.start()
        except Exception as e:
            # v26 — Fix 6: never let Verify All take down the app.
            import traceback
            self.log_message(f"❌ Verify All crashed: {e}", "error")
            try:
                self.queue_display.setPlainText(
                    f"Verify All crashed: {e}\n\n{traceback.format_exc()}"
                )
            except Exception:
                pass
            try:
                self._release_telegram_lock("bot_verify_all")
            except Exception:
                pass

    def _fuzzy_match_github_url(self, vault_index, url):
        """v29.9 — Fuzzy match a GitHub URL against the vault index.
        Extracts owner/repo from the URL and checks if any vault note has
        that pair in its source URL. Catches normalization mismatches."""
        try:
            # Extract owner/repo from the URL
            m = re.match(r'https?://(?:www\.)?github\.com/([a-zA-Z0-9\-_.]+)/([a-zA-Z0-9\-_.]+)', url)
            if not m:
                return None
            owner = m.group(1).lower()
            repo = m.group(2).lower()
            # Strip .git suffix if present
            if repo.endswith('.git'):
                repo = repo[:-4]

            # Check all vault URLs for a match
            for vault_url, path in vault_index._url_to_path.items():
                vm = re.match(r'https?://(?:www\.)?github\.com/([a-zA-Z0-9\-_.]+)/([a-zA-Z0-9\-_.]+)', vault_url)
                if vm:
                    v_owner = vm.group(1).lower()
                    v_repo = vm.group(2).lower()
                    if v_repo.endswith('.git'):
                        v_repo = v_repo[:-4]
                    if v_owner == owner and v_repo == repo:
                        return path
        except Exception:
            pass
        return None

    def _process_missing_urls(self, urls):
        """Process missing GitHub URLs directly (from verify dialog)."""
        if not urls:
            return
        self.log_message(f"🚀 Processing {len(urls)} missing URL(s)...", "info")
        # Store the URLs and trigger processing
        self._bot_queue_urls = urls
        self._bot_source = True
        # Show in queue display
        self.queue_display.setPlainText("\n".join(urls))
        # Start processing (reads from self._bot_queue_urls)
        self.start_processing()

    def _show_manual_resolve_dialog(self, missing_urls):
        """v29.11 — Manual resolve dialog for missing links.
        Lets user: mark as processed, decommission, or process each URL."""
        is_dark = getattr(self, '_dark_mode', False)
        if is_dark:
            bg = "#2B2639"; text_color = "#F2EEE7"; border = "#3B344F"; input_bg = "#241F31"; alt_bg = "#352F4A"
        else:
            bg = "#FFFFFF"; text_color = "#423A52"; border = "#EAE3D6"; input_bg = "#FDFCF8"; alt_bg = "#F2EDE3"

        dialog = QDialog(self)
        dialog.setWindowTitle("🔧 Manual Resolve — Missing Links")
        dialog.setMinimumWidth(700)
        dialog.setMinimumHeight(500)
        dialog.setStyleSheet(
            f"QDialog {{ background-color: {bg}; }} "
            f"QLabel {{ color: {text_color}; }} "
            f"QListWidget {{ background-color: {input_bg}; color: {text_color}; border: 1px solid {border}; border-radius: 4px; }} "
            f"QPushButton {{ padding: 6px 12px; border-radius: 4px; }}"
        )
        layout = QVBoxLayout(dialog)
        layout.setSpacing(10)

        # Header
        # v31.1: ⏳ — missing links are PENDING work awaiting a user
        # decision, not a failure.
        header = QLabel(f"⏳ {len(missing_urls)} GitHub link(s) are missing from the vault.\n"
                        f"For each URL, choose an action:")
        header.setWordWrap(True)
        layout.addWidget(header)

        # URL list
        url_list = QListWidget()
        url_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        for url in missing_urls:
            url_list.addItem(url)
        layout.addWidget(url_list)

        # Action buttons — v31.1 hierarchy: ONE filled primary (Process
        # Selected), outlined secondary (Mark as Processed / Mark ALL), ONE
        # filled danger (Decommission).
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        process_btn = QPushButton("🚀 Process Selected")
        process_btn.setStyleSheet(self._btn_style(COLORS['cta'], COLORS['cta_hover'], text=COLORS['cta_text']))
        def do_process():
            if url_list.currentRow() < 0:
                return
            url = missing_urls[url_list.currentRow()]
            dialog.accept()
            self._process_missing_urls([url])
        process_btn.clicked.connect(do_process)
        btn_row.addWidget(process_btn)

        mark_processed_btn = QPushButton("✅ Mark as Processed")
        mark_processed_btn.setStyleSheet(self._btn_style(COLORS['primary'], COLORS['primary_hover'], variant='outline'))
        def do_mark_processed():
            if url_list.currentRow() < 0:
                return
            url = missing_urls[url_list.currentRow()]
            # Add to CacheDB as processed
            try:
                cache = CacheDB()
                m = re.match(r'https?://(?:www\.)?github\.com/([a-zA-Z0-9\-_.]+)/([a-zA-Z0-9\-_.]+)', url)
                owner = m.group(1) if m else ""
                repo = m.group(2) if m else ""
                cache.add_processed(0, url, owner, repo, "(manual resolve)", "Manual")
                cache.close()
            except Exception:
                pass
            self.log_message(f"✅ Marked as processed (manual): {url}", "success")
            url_list.takeItem(url_list.currentRow())
            if url_list.count() == 0:
                dialog.accept()
                self.log_message("✅ All missing links resolved!", "success")
        mark_processed_btn.clicked.connect(do_mark_processed)
        btn_row.addWidget(mark_processed_btn)

        decomm_btn = QPushButton("🗑️ Decommission")
        decomm_btn.setStyleSheet(self._btn_style(COLORS['error'], COLORS['error_hover'], text=COLORS['error_text']))
        def do_decomm():
            if url_list.currentRow() < 0:
                return
            url = missing_urls[url_list.currentRow()]
            try:
                cache = CacheDB()
                cache.decommission(url, "Manual decommission by user")
                cache.close()
            except Exception:
                pass
            self.log_message(f"🗑️ Decommissioned (manual): {url}", "info")
            url_list.takeItem(url_list.currentRow())
            if url_list.count() == 0:
                dialog.accept()
                self.log_message("✅ All missing links resolved!", "success")
        decomm_btn.clicked.connect(do_decomm)
        btn_row.addWidget(decomm_btn)

        # Mark all as processed
        mark_all_btn = QPushButton("✅ Mark ALL as Processed")
        mark_all_btn.setStyleSheet(self._btn_style(COLORS['primary'], COLORS['primary_hover']))
        def do_mark_all():
            try:
                cache = CacheDB()
                for url in missing_urls:
                    m = re.match(r'https?://(?:www\.)?github\.com/([a-zA-Z0-9\-_.]+)/([a-zA-Z0-9\-_.]+)', url)
                    owner = m.group(1) if m else ""
                    repo = m.group(2) if m else ""
                    cache.add_processed(0, url, owner, repo, "(manual resolve)", "Manual")
                cache.close()
            except Exception:
                pass
            self.log_message(f"✅ Marked {len(missing_urls)} URL(s) as processed (manual)", "success")
            dialog.accept()
        mark_all_btn.clicked.connect(do_mark_all)
        btn_row.addWidget(mark_all_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Close button
        close_btn = QPushButton("Close")
        close_btn.setStyleSheet(self._btn_style(COLORS['neutral'], COLORS['neutral_hover'], variant='outline'))
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn)

        dialog.exec()

    def clear_bot_queue(self):
        """Mark all bot messages as read (clears the queue indicator)."""
        if not self._acquire_telegram_lock("bot_clear"):
            return
        bot_username = self.bot_username.text().strip().lstrip('@')
        if not bot_username:
            self.log_message("❌ Please enter the bot username first.", "error")
            self._release_telegram_lock("bot_clear")
            return
        api_id = self.api_id.text()
        api_hash = self.api_hash.text()
        phone = self.phone.text()
        if not api_id or not api_hash or not phone:
            self.log_message("❌ Telegram credentials required.", "error")
            self._release_telegram_lock("bot_clear")
            return

        proxy = self._get_proxy_dict()
        self.log_message("✓ Marking bot messages as read...", "info")

        worker = TestWorker(_bot_queue_job, "bot_clear",
                            api_id, api_hash, phone, proxy, bot_username, None, None, None)
        def _job(aid, ahash, ph, px, bu, _ignored_log, _ignored_code, _ignored_mark):
            return _bot_queue_job(aid, ahash, ph, px, bu, worker.log_message, worker.request_code, mark_read=True)
        worker._fn = _job

        worker.log_message.connect(self.log_message)
        worker.code_requested.connect(
            lambda pt, w=worker: self._on_telegram_code_requested(pt, w)
        )

        def _on_finished(name, result):
            if result.get('success'):
                self.log_message("✓ Bot queue cleared (all messages marked as read)", "success")
                self.queue_display.clear()
                self.queue_display.setPlainText("Queue cleared. Click 'Check Queue' to fetch new messages.")
            else:
                self.log_message(f"❌ Failed to clear queue: {result.get('error')}", "error")

        worker.finished_signal.connect(_on_finished)
        self._keep_worker(worker, owner="bot_clear")
        worker.start()


    def _mark_bot_messages_read(self):
        """Auto-mark all bot messages as read after a successful bot-queue batch
        (v22 Feature 3: Two-Condition Done Check). Non-blocking — runs in a
        TestWorker. Best-effort: failures are logged but don't disrupt the user."""
        bot_username = self.bot_username.text().strip().lstrip('@')
        if not bot_username:
            self.log_message("⚠️ Cannot auto-mark bot messages: no bot username set.", "warning")
            return
        api_id = self.api_id.text()
        api_hash = self.api_hash.text()
        phone = self.phone.text()
        if not api_id or not api_hash or not phone:
            self.log_message("⚠️ Cannot auto-mark bot messages: Telegram credentials incomplete.", "warning")
            return

        # Acquire the Telegram lock (best-effort — if it's held, skip auto-mark)
        if not self._acquire_telegram_lock("bot_auto_mark_read"):
            self.log_message("⚠️ Cannot auto-mark bot messages: another Telegram operation is running.", "warning")
            return

        proxy = self._get_proxy_dict()
        worker = TestWorker(_bot_queue_job, "bot_auto_mark_read",
                            api_id, api_hash, phone, proxy, bot_username, None, None, None)
        def _job(aid, ahash, ph, px, bu, _ignored_log, _ignored_code, _ignored_mark):
            return _bot_queue_job(aid, ahash, ph, px, bu, worker.log_message, worker.request_code, mark_read=True)
        worker._fn = _job

        worker.log_message.connect(self.log_message)
        worker.code_requested.connect(
            lambda pt, w=worker: self._on_telegram_code_requested(pt, w)
        )

        def _on_finished(name, result):
            if result.get('success'):
                self.log_message("✓ Bot messages auto-marked as read", "success")
                try:
                    self.queue_display.clear()
                    self.queue_display.setPlainText("Queue auto-cleared (all repos processed). Click 'Check Queue' to fetch new messages.")
                except Exception:
                    pass
            else:
                self.log_message(f"⚠️ Auto-mark bot messages failed: {result.get('error')}", "warning")

        worker.finished_signal.connect(_on_finished)
        self._keep_worker(worker, owner="bot_auto_mark_read")
        worker.start()


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
