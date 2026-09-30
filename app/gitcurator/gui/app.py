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


class MainWindow(LlamaCppMixin, ConnectionTestsMixin, HeroMixin, UiMixin, ThemeMixin, QMainWindow):
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
    # v0.16.0 — Phase 6 (Linking): the recall-hook and link-suggestion
    # tools, run in-process through TestWorker with stdout captured and
    # streamed to the GUI log on completion. The SAFE defaults only —
    # the bulk/apply/collect steps stay on the command line where the
    # SPEC's owner-approval flow lives.
    # ------------------------------------------------------------------
    def _run_phase6_tool(self, tool_main, argv, label):
        """Run one Phase-6 tool's main() in a TestWorker, stdout captured;
        the tail lands in the GUI log when it finishes."""
        if self.worker is not None and not self.worker.isFinished():
            self.log_message(
                "⏳ A batch is running — wait for it to finish before "
                "running the linking tools.", "warning")
            return
        import contextlib
        import io as _io

        def _job():
            buf = _io.StringIO()
            try:
                with contextlib.redirect_stdout(buf), \
                        contextlib.redirect_stderr(buf):
                    rc = tool_main(argv)
            except SystemExit as exc:            # argparse / provider aborts
                rc = int(exc.code or 0)
            except Exception as exc:              # noqa: BLE001
                buf.write(f"\n💥 {type(exc).__name__}: {exc}\n")
                rc = 2
            return {'success': rc == 0, 'rc': rc,
                    'output': buf.getvalue()}

        worker = TestWorker(_job, label)
        worker.log_message.connect(self.log_message)

        def _on_finished(_name, result):
            lines = (result.get('output') or '').splitlines()
            tail = lines[-40:]
            for line in tail:
                self.log_message(line, "info")
            if result.get('success'):
                self.log_message(f"✅ {label} finished.", "success")
            else:
                self.log_message(
                    f"❌ {label} finished with exit code "
                    f"{result.get('rc')} (see the lines above — the "
                    "command-line tools print the exact next step).",
                    "error")

        worker.finished_signal.connect(_on_finished)
        self._active_test_workers.append(worker)
        self.log_message(f"⏳ {label} running… (the GUI stays usable)",
                         "info")
        worker.start()

    def run_recall_hooks_dryrun(self):
        """🪝 Recall hooks (dry-run): the Phase-6 step-1 tool in its
        default dry-run mode — nothing is written, the diff lands in the
        log + app/reports/recall/. Approve samples and apply from the
        command line (the SPEC flow)."""
        from gitcurator.tools import add_recall_hooks
        self._run_phase6_tool(add_recall_hooks.main, [],
                              "Recall hooks (dry-run)")

    def run_link_suggestions(self):
        """🔗 Build link suggestions: embeddings → neighbors → LLM
        confirm → the Suggestions note under the manual vault's Library/
        (its only write). Tick boxes there, then run the tool with
        --collect on the command line to apply."""
        from gitcurator.tools import build_links
        self._run_phase6_tool(build_links.main, [],
                              "Link suggestions")


    def test_all(self):
        """🔌 Test Connection — the owner's four-subsystem readiness check,
        v0.23.0: a MODAL with live per-subsystem status (owner spec: "a
        modal must open with a loading, then everything that is connected
        gets an emoji check; a close button and a Start Syncing button
        that turns green after everything is connected"):

          [1/4] 📁 Vaults    — found + writable (ready to receive notes)
          [2/4] 🧠 LLM       — the ACTIVE provider: cloud (OpenAI-compatible
                               or Claude) / Ollama / llama.cpp
          [3/4] 🐙 GitHub    — token valid + the backup repos ready
          [4/4] ✈️ Telegram  — credentials/session/bot/proxy + a LIVE
                               connection test through the same subprocess
                               a batch uses

        The battery (1-4 local) runs in one background TestWorker whose
        section/result signals drive the modal's rows (and still stream
        the log line by line); the LIVE Telegram leg starts only after it
        (session.session is single-user — serialized like every other
        Telegram button) and finalizes the Telegram row. The verdict
        enables 🚀 Start Syncing (→ the hero SYNC flow). The interactive
        login dialog still works: the live leg wires code_requested, so a
        first-run user can complete the account login during the test.
        """
        if self.worker is not None and not self.worker.isFinished():
            self.log_message(
                "⏳ A batch is running — Test Connection is available when "
                "it finishes.", "warning")
            return
        if not self._acquire_telegram_lock("connection_check"):
            self.log_message(
                "⏳ Another Telegram operation is already running — try Test "
                "Connection again in a moment.", "warning")
            return
        self.log_message(
            "🔍 Test Connection — checking vaults, LLM, GitHub and "
            "Telegram…", "info")

        # v0.23.0 — the modal (kept on self so every leg can route into it;
        # guarded everywhere with isVisible() — a closed dialog never
        # crashes a late result).
        self._cc_dialog = ConnectionTestDialog(self)

        # Snapshot: saved config + the live credential widgets (the same
        # values the per-test buttons read — unsaved edits get tested too).
        snapshot = dict(self.config)
        try:
            tok = self.github_token.text().strip()
            if tok:
                snapshot['github_token'] = tok
            aid = self.api_id.text().strip()
            if aid:
                snapshot['telegram_api_id'] = aid
            ahash = self.api_hash.text().strip()
            if ahash:
                snapshot['telegram_api_hash'] = ahash
            ph = self.phone.text().strip()
            if ph:
                snapshot['telegram_phone'] = ph
            bot = self.bot_username.text().strip().lstrip('@')
            if bot:
                snapshot['bot_username'] = bot
            snapshot['proxy'] = self._get_proxy_dict()
        except Exception:
            pass  # widget access must never break the check
        self._cc_sections = None

        worker = TestWorker(_connection_battery_job, "connection_battery",
                            snapshot)

        def _job(cfg):
            return _connection_battery_job(
                cfg, worker.log_message,
                on_section=worker.section_signal.emit,
                on_result=worker.result_signal.emit)
        worker._fn = _job
        worker.log_message.connect(self.log_message)

        # v0.23.0 — structured progress into the modal's rows.
        def _on_section(idx, _title, _total):
            dlg = getattr(self, '_cc_dialog', None)
            if dlg is not None and dlg.isVisible():
                dlg.set_row_checking(idx)
        worker.section_signal.connect(_on_section)

        def _on_result(idx, r):
            dlg = getattr(self, '_cc_dialog', None)
            if dlg is not None and dlg.isVisible():
                dlg.add_row_detail(idx, _connection_check.render_line(r))
        worker.result_signal.connect(_on_result)

        def _on_battery_done(_name, result):
            if getattr(self, '_closing', False):
                self._release_telegram_lock("connection_check")
                return
            sections = (result or {}).get("sections")
            if not sections:
                # The battery itself crashed — NEVER report 'ALL SYSTEMS
                # READY' from an empty result; surface the crash instead.
                err = (result or {}).get("error") or "the check crashed"
                sections = [["Check", [{"name": "Test battery",
                                        "level": "error",
                                        "detail": str(err)[:300]}]]]
            self._cc_sections = sections
            self._cc_dialog_sync_rows(sections)
            self._cc_telegram_leg(snapshot)

        worker.finished_signal.connect(_on_battery_done)
        self._active_test_workers.append(worker)
        worker.start()
        self._animate_dialog(self._cc_dialog)
        self._cc_dialog.exec()

    def _cc_dialog_sync_rows(self, sections):
        """v0.23.0 — settle the modal's rows from the battery's sections
        (rows 1-3 final; the Telegram row stays spinning through the live
        leg — unless the section list is degenerate, in which case every
        mapped row settles with its own verdict)."""
        dlg = getattr(self, '_cc_dialog', None)
        if dlg is None or not dlg.isVisible():
            return
        for i, (_title, results) in enumerate(sections or [], start=1):
            levels = [r.get("level") for r in (results or [])] or ["info"]
            verdict = ("error" if "error" in levels
                       else "warn" if "warning" in levels else "ok")
            dlg.finalize_row(i, verdict)

    def _cc_telegram_leg(self, snapshot):
        """The LIVE Telegram test — runs after the battery (never two
        Telethon children at once). Bot queue when a bot is configured
        (proves account login AND the bot chat through the same session),
        else the Saved-Messages preview (account login only)."""
        api_id = str(snapshot.get('telegram_api_id', '') or '').strip()
        api_hash = str(snapshot.get('telegram_api_hash', '') or '').strip()
        phone = str(snapshot.get('telegram_phone', '') or '').strip()
        bot = str(snapshot.get('bot_username', '') or '').strip().lstrip('@')
        if not (api_id and api_hash and phone):
            self.log_message(
                "⏭️ Live Telegram test skipped — credentials incomplete "
                "(see the Telegram lines above).", "info")
            self._release_telegram_lock("connection_check")
            self._cc_finish()
            return
        proxy = snapshot.get('proxy') or {}
        if bot:
            self.log_message(
                f"📡 Live Telegram test — reading the @{bot} queue through "
                f"your session…", "info")
            worker = TestWorker(_bot_queue_job, "connection_telegram",
                                api_id, api_hash, phone, proxy, bot,
                                None, None)

            def _job(aid, ahash, ph, px, _b, _ignored_log, _ignored_code):
                return _bot_queue_job(aid, ahash, ph, px, bot,
                                      worker.log_message, worker.request_code,
                                      mark_read=False, min_id=0,
                                      vault_path=None)
            mode = "bot"
        else:
            self.log_message(
                "📡 Live Telegram test — no bot configured; testing the "
                "account login (Saved Messages)…", "info")
            worker = TestWorker(_telegram_test_job, "connection_telegram",
                                api_id, api_hash, phone, proxy, None, None)

            def _job(aid, ahash, ph, px, _ignored_log, _ignored_code):
                return _telegram_test_job(aid, ahash, ph, px,
                                          worker.log_message,
                                          worker.request_code)
            mode = "account"
        worker._fn = _job
        worker.log_message.connect(self.log_message)
        worker.code_requested.connect(
            lambda pt, w=worker: self._on_telegram_code_requested(pt, w))

        def _on_done(_name, result):
            line = _connection_check.telegram_live_result(result, mode)
            self.log_message("   " + _connection_check.render_line(line),
                             _connection_check.GUI_LEVELS.get(line["level"],
                                                              "info"))
            if isinstance(self._cc_sections, list) and self._cc_sections:
                self._cc_sections[-1][1].append(line)
            self._cc_finish()

        worker.finished_signal.connect(_on_done)
        # _keep_worker releases the connection_check lock when this finishes
        self._keep_worker(worker, owner="connection_check")
        worker.start()

    def _cc_finish(self):
        """The one-line verdict — the 'user is ensured everything is up'.
        v0.23.0 — also settles the modal: every section's verdict lands on
        its row and Start Syncing unlocks (green) only when ALL are ok."""
        s = _connection_check.summarize(self._cc_sections or [])
        level = _connection_check.GUI_LEVELS.get(s["level"], "info")
        self.log_message(f"🏁 Test Connection — {s['headline']}", level)
        dlg = getattr(self, '_cc_dialog', None)
        if dlg is not None and dlg.isVisible():
            self._cc_dialog_sync_rows(self._cc_sections or [])
            dlg.finish_all()
        self._cc_sections = None

    # ------------------------------------------------------------------
    # v0.23.0 — the legacy Input-mode handlers are GONE: generate_marker_
    # hash / copy_marker_hash / find_by_marker / find_keyword_ids /
    # preview_messages / _show_preview_modal served the ID Range, Markers
    # and Single Msg modes removed at the owner's request ("remove ID
    # range + single msg + markers options and its codes inside code
    # base"). The bot-queue SYNC (main view) and Import txt file
    # (Settings → 📥 Input) are the two input paths now.
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # UI Helpers
    # ------------------------------------------------------------------
    def populate_vaults(self):
        self.vault_combo.clear()
        history = self.config.get('vaults_history', [])
        current = self.config.get('vault_path', '')
        if current and current not in history:
            history.insert(0, current)
        discovered = find_obsidian_vaults()
        all_vaults = list(dict.fromkeys(history + discovered))
        for v in all_vaults:
            self.vault_combo.addItem(v)
        if current:
            index = self.vault_combo.findText(current)
            if index >= 0:
                self.vault_combo.setCurrentIndex(index)
            else:
                self.vault_combo.insertItem(0, current)
                self.vault_combo.setCurrentIndex(0)

    def on_vault_changed(self, text):
        if text:
            self.config['vault_path'] = text
            history = self.config.get('vaults_history', [])
            if text not in history:
                history.insert(0, text)
                self.config['vaults_history'] = history
            self.save_config()
            # v0.07: the PROCESSED counter reads the ACTIVE vault's manifest
            # — keep it truthful when the vault selection changes.
            self._refresh_pipeline_counter()

    def browse_vault(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Obsidian Vault")
        if folder:
            if self.vault_combo.findText(folder) == -1:
                self.vault_combo.insertItem(0, folder)
            self.vault_combo.setCurrentText(folder)
            self.on_vault_changed(folder)

    # v0.10.0 — Phase 1: browse / live-status / save handlers for the new
    # vault pickers on the 📁 Vault settings page.
    def browse_website_vault(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select Websites Vault (a folder that does not exist yet is fine)")
        if folder:
            self.website_vault_input.setText(folder)
            self._save_vault_page()

    def browse_manual_vault(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select Manual Notes Vault")
        if folder:
            self.manual_vault_input.setText(folder)
            self._save_vault_page()

    def _vault_path_status(self, path, kind):
        """(text, color) for a vault path — the live status line under each
        picker. Websites folders that don't exist yet are 'will be created'
        (its pipeline creates them); the GitHub vault must exist; the Manual
        vault is the owner's to create."""
        colors = self._status_colors()
        path = (path or '').strip()
        if not path:
            return "● not set", colors['warning']
        if os.path.isdir(path):
            return "● found", colors['success']
        if kind == 'websites':
            return "● will be created (folder does not exist yet)", colors['warning']
        if kind == 'github':
            return "● missing on disk", colors['error']
        return "● not found — you create this vault yourself", colors['warning']

    def _refresh_vault_page_status(self, *args):
        """Update the two live status labels (no saving — cheap, per keystroke)."""
        if not hasattr(self, 'website_vault_status'):
            return
        text, color = self._vault_path_status(
            self.website_vault_input.text(), 'websites')
        self.website_vault_status.setText(text)
        self.website_vault_status.setStyleSheet(
            f"font-size: 12px; font-weight: bold; color: {color};")
        text, color = self._vault_path_status(
            self.manual_vault_input.text(), 'manual')
        self.manual_vault_status.setText(text)
        self.manual_vault_status.setStyleSheet(
            f"font-size: 12px; font-weight: bold; color: {color};")

    def _save_vault_page(self, *args):
        """Commit the Vault page (paths + repo + switches) via the ONE
        merge-based save path — same as every other settings page."""
        self._refresh_vault_page_status()
        self.save_config()

    def remove_vault(self):
        current = self.vault_combo.currentText()
        if not current:
            return
        reply = QMessageBox.StandardButton.Yes if self._show_custom_question("Remove Vault", f"Remove '{current}' from the list? (This will not delete the folder.)") else QMessageBox.StandardButton.No
        if reply == QMessageBox.StandardButton.Yes:
            self.vault_combo.removeItem(self.vault_combo.currentIndex())
            history = self.config.get('vaults_history', [])
            if current in history:
                history.remove(current)
                self.config['vaults_history'] = history
            if self.config.get('vault_path') == current:
                self.config['vault_path'] = ''
            self.save_config()

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r') as f:
                    config = json.load(f)
            except (json.JSONDecodeError, OSError):
                return CONFIG_EXAMPLE.copy()
            # v0.07.1 — Fix: heal hand-edited configs. Credentials pasted
            # with surrounding whitespace (typically a trailing newline)
            # broke PyGithub ("Invalid ... character(s) in header value:
            # 'token ghp_…\n'") even though the token itself was valid.
            # Strip the credential-bearing flat keys at load so a saved or
            # hand-edited config can never poison the app again.
            for k in ('telegram_api_id', 'telegram_api_hash', 'telegram_phone',
                      'github_token', 'bot_token', 'cloud_api_key',
                      'cloudflare_worker_url'):
                v = config.get(k)
                if isinstance(v, str):
                    config[k] = v.strip()
            px = config.get('proxy')
            if isinstance(px, dict) and isinstance(px.get('host'), str):
                px['host'] = px['host'].strip()
            return config
        else:
            return CONFIG_EXAMPLE.copy()

    def save_config(self):
        """v30 — Fix (save_config was destroying data): MERGE, never
        whitelist-rebuild.

        What was wrong (all fixed here):
          1. The old body rebuilt config.json from ~25 hardcoded keys — every
             OTHER key was silently DESTROYED on save: cloudflare_install_id,
             cloudflare_shared_secret, gdrive_*, hmac_secret, user-added keys.
             Pairing with the Cloudflare worker / GDrive backup was wiped the
             first time the app closed.
          2. timeout_per_repo / max_retries / delay_between_api_calls were
             hardcoded (60 / 3 / 0.5) — user-tuned values were stomped on every
             save. They are now only DEFAULTED when absent (see
             storage.merge_config).
          3. ``self.config = config`` REBOUND the attribute — the running
             ProcessingWorker kept the OLD dict, so mid-batch config changes
             (like the LLM model fix) were invisible to it, and two divergent
             configs drifted until restart. self.config is now mutated IN
             PLACE so every holder sees the same data.
          4. The file write is now atomic (tempfile + os.replace) — a crash
             mid-save can no longer truncate config.json.
        """
        ui_values = {
            "telegram_api_id": int(self.api_id.text()) if self.api_id.text().isdigit() else 0,
            # v0.07.1 — credential fields strip()d at the source so a
            # pasted trailing newline can never reach the file (or PyGithub
            # headers) again.
            "telegram_api_hash": self.api_hash.text().strip(),
            "telegram_phone": self.phone.text().strip(),
            "proxy": {
                "enabled": self.proxy_enabled.isChecked(),
                "type": self.proxy_type.currentText(),
                "host": self.proxy_host.text().strip(),
                "port": int(self.proxy_port.text()) if self.proxy_port.text().isdigit() else 10808,
                # v0.19.0 — the Websites pipeline rides the same proxy.
                "use_for_web": self.proxy_use_for_web.isChecked()
            },
            "ollama": {
                "base_url": self.ollama_url.text(),
                "model": self.ollama_model.currentText()
            },
            # v26 — Fix 4: persist the cloud LLM provider selection + creds
            # so ProcessingWorker can pick the right backend on next launch.
            # Defaults to 'ollama' for backward compatibility — existing users
            # won't notice anything changed unless they explicitly switch.
            # v0.15.0 — llama.cpp engine detection: the third provider value
            # 'llamacpp' + its llamacpp_* keys (model empty = auto-detected
            # from the running llama-server).
            # v0.23.0 — the two-level radios: the host radio (local/cloud)
            # and, inside local, the engine radio (Ollama/llama.cpp). The
            # stored value keeps the same three strings as always.
            "llm_provider": (
                "cloud" if getattr(self, 'llm_host_cloud', None)
                and self.llm_host_cloud.isChecked()
                else "llamacpp" if getattr(self, 'llm_provider_llamacpp', None)
                and self.llm_provider_llamacpp.isChecked() else "ollama"),
            "cloud_api_url": getattr(self, 'cloud_api_url', QLineEdit()).text() if hasattr(self, 'cloud_api_url') else self.config.get('cloud_api_url', 'https://api.openai.com/v1'),
            "cloud_api_key": getattr(self, 'cloud_api_key', QLineEdit()).text().strip() if hasattr(self, 'cloud_api_key') else self.config.get('cloud_api_key', ''),
            "cloud_model": getattr(self, 'cloud_model', QLineEdit()).text().strip() if hasattr(self, 'cloud_model') else self.config.get('cloud_model', 'gpt-4o-mini'),
            "llamacpp_api_url": (self.llamacpp_api_url.text().strip()
                                  if hasattr(self, 'llamacpp_api_url')
                                  else self.config.get(
                                      'llamacpp_api_url',
                                      _llm_client.LLAMACPP_DEFAULT_BASE + '/v1')),
            "llamacpp_api_key": (self.llamacpp_api_key.text().strip()
                                  if hasattr(self, 'llamacpp_api_key')
                                  else self.config.get('llamacpp_api_key', '')),
            "llamacpp_model": (self.llamacpp_model.currentText().strip()
                                if hasattr(self, 'llamacpp_model')
                                else self.config.get('llamacpp_model', '')),
            # v0.13.0 — Phase 4: the explicit context window (llm_num_ctx).
            # v0.23.0 — plus the OUTPUT half (llm_max_output_tokens):
            # num_ctx/num_predict on Ollama, warning-budget/max_tokens on
            # the cloud paths, max_tokens (required) on Claude.
            "llm_num_ctx": self._llm_num_ctx_value(),
            "llm_max_output_tokens": self._llm_max_output_tokens_value(),
            "github_token": self.github_token.text().strip(),
            "bot_token": getattr(self, 'bot_token', QLineEdit()).text().strip() if hasattr(self, 'bot_token') else "",
            "bot_username": getattr(self, 'bot_username', QLineEdit()).text() if hasattr(self, 'bot_username') else "githubfetcherbot",
            "vault_path": self.vault_combo.currentText() if self.vault_combo.currentText() else "",
            "vaults_history": self.config.get('vaults_history', []),
            "log_level": "INFO",
            "dark_mode": getattr(self, '_dark_mode', False),
            # v25 pre-flight: persist the last-processed bot message ID so
            # "📬 Process New" can skip already-processed messages on the
            # next run. Also persist the configurable delays so the user
            # can tune rate-limit handling from config.json.
            "last_processed_msg_id": int(self.config.get('last_processed_msg_id', 0) or 0),
            "large_batch_extra_delay": float(self.config.get('large_batch_extra_delay', 1.5)),
            "banner_throttle_10": float(self.config.get('banner_throttle_10', 2)),
            "banner_throttle_50": float(self.config.get('banner_throttle_50', 5)),
            # v29.4 — Cloudflare worker URL (for dashboard) + local backup.
            # NOTE: cloudflare_install_id / cloudflare_shared_secret / other
            # cloudflare_* and gdrive_* keys live in self.config and are
            # PRESERVED by the merge — they are no longer wiped on save.
            "cloudflare_worker_url": getattr(self, 'dash_worker_url_input', QLineEdit()).text().strip() if hasattr(self, 'dash_worker_url_input') else self.config.get('cloudflare_worker_url', ''),
            "backup_enabled": getattr(self, 'backup_enabled_check', None) and self.backup_enabled_check.isChecked() if hasattr(self, 'backup_enabled_check') else self.config.get('backup_enabled', False),
            "backup_folder": getattr(self, 'backup_folder_input', QLineEdit()).text().strip() if hasattr(self, 'backup_folder_input') else self.config.get('backup_folder', ''),
            "backup_max": self.config.get('backup_max', 10),
            # v0.09 (merge) — 404 quarantine threshold (Settings → Dashboard
            # spinbox; defensive hasattr pattern like every optional widget).
            "notfound_strike_threshold": (
                self.quarantine_threshold_spin.value()
                if hasattr(self, 'quarantine_threshold_spin')
                else self.config.get('notfound_strike_threshold', 3)),
            # v31 — VaultSeal (GitHub mirror of the vault). Defensive hasattr
            # pattern: the widgets live in the Backup tab and always exist by
            # the time the main window saves — but never bet on widget order.
            "vaultseal": {
                "enabled": (self.vaultseal_enabled_check.isChecked()
                            if hasattr(self, 'vaultseal_enabled_check')
                            else (self.config.get('vaultseal') or {}).get('enabled', True)),
                "auto_push": (self.vaultseal_push_check.isChecked()
                              if hasattr(self, 'vaultseal_push_check')
                              else (self.config.get('vaultseal') or {}).get('auto_push', True)),
                "repo_name": (self.vaultseal_repo_input.text().strip()
                              if hasattr(self, 'vaultseal_repo_input')
                              else (self.config.get('vaultseal') or {}).get('repo_name', '')),
            },
            # v32 — GoodRepos (public curated directory). Same defensive
            # hasattr pattern as VaultSeal above.
            "goodrepos": {
                "enabled": (self.goodrepos_enabled_check.isChecked()
                            if hasattr(self, 'goodrepos_enabled_check')
                            else (self.config.get('goodrepos') or {}).get('enabled', True)),
                "auto_push": (self.goodrepos_push_check.isChecked()
                              if hasattr(self, 'goodrepos_push_check')
                              else (self.config.get('goodrepos') or {}).get('auto_push', True)),
                "repo_name": (self.goodrepos_repo_input.text().strip()
                              if hasattr(self, 'goodrepos_repo_input')
                              else (self.config.get('goodrepos') or {}).get('repo_name', 'good-repos')),
            },
            # v0.10.0 — Phase 1: vault settings + pipeline switches. Same
            # defensive hasattr pattern; a missing widget keeps the config
            # value (merge_config never drops keys).
            "website_vault_path": (self.website_vault_input.text().strip()
                                   if hasattr(self, 'website_vault_input')
                                   else self.config.get('website_vault_path', '')),
            "manual_vault_path": (self.manual_vault_input.text().strip()
                                  if hasattr(self, 'manual_vault_input')
                                  else self.config.get('manual_vault_path', '')),
            "website_repo_name": (self.website_repo_input.text().strip()
                                  if hasattr(self, 'website_repo_input')
                                  else self.config.get('website_repo_name', '')),
            # v0.20.0 — blocked domains for the Websites pipeline (the X
            # fix). Comma-separated text → clean list; an EMPTY field is a
            # deliberate opt-out (list []), a missing widget keeps config.
            "web_blocked_domains": (
                [d.strip().lower() for d in
                 self.web_blocked_input.text().split(',') if d.strip()]
                if hasattr(self, 'web_blocked_input')
                else self.config.get('web_blocked_domains',
                                     list(_links.DEFAULT_BLOCKED_DOMAINS))),
            # v0.21.0 — self domains (the app's own bot): same text→list
            # contract as the blocked list; empty field = deliberate opt-out.
            "web_self_domains": (
                [d.strip().lower() for d in
                 self.web_self_input.text().split(',') if d.strip()]
                if hasattr(self, 'web_self_input')
                else self.config.get('web_self_domains',
                                     list(_links.DEFAULT_SELF_DOMAINS))),
            "taxonomy_path": self.config.get('taxonomy_path', ''),
            "pipelines": {
                "github": (self.pipeline_github_check.isChecked()
                           if hasattr(self, 'pipeline_github_check')
                           else (self.config.get('pipelines') or {}).get('github', True)),
                "websites": (self.pipeline_websites_check.isChecked()
                             if hasattr(self, 'pipeline_websites_check')
                             else (self.config.get('pipelines') or {}).get('websites', False)),
            },
        }

        # Merge UI values into the EXISTING config — unknown keys survive,
        # nested dicts merge key-by-key, tuning keys are only defaulted.
        merged = _storage.merge_config(self.config, ui_values)

        # Atomic write — a crash mid-save can no longer truncate config.json.
        _storage.write_config_file(CONFIG_FILE, merged)

        # v30 — NEVER rebind self.config. Mutate in place so the running
        # worker (which holds this exact dict) sees updates live.
        self.config.clear()
        self.config.update(merged)

    def load_ui_config(self):
        pass

    def select_import_file(self):
        """v0.23.0 — the Import txt file picker: .txt AND .md (one URL per
        line; # comments and blank lines skipped by the worker's importer)."""
        file, _ = QFileDialog.getOpenFileName(
            self, "Import txt file", "",
            "Text & Markdown (*.txt *.md);;Text Files (*.txt);;Markdown (*.md);;All files (*)")
        if file:
            self.import_file.setText(file)
            self.log_message(
                f"📄 Import file selected: {file}", "info")

    def show_input_help(self):
        """Popup with concise usage instructions for the Input tab (the
        single Import txt file mode)."""
        self._show_custom_message_box(
            "Input — How to Use",
            "Import txt file: pick a .txt or .md file with one URL per line "
            "(lines starting with # are comments; blank lines are skipped), "
            "then click PROCESS on the main view.\n\n"
            "GitHub repos are noted into the GitHub vault; every other "
            "website into the Websites vault — exactly like a fetched "
            "batch.\n\n"
            "To fetch from Telegram instead, click SYNC — it pulls every "
            "undone item from the bot queue.",
            success=True
        )

    def _get_proxy_dict(self):
        return {
            "enabled": self.proxy_enabled.isChecked(),
            "type": self.proxy_type.currentText(),
            "host": self.proxy_host.text(),
            "port": int(self.proxy_port.text()) if self.proxy_port.text().isdigit() else 10808,
            # v0.19.0 — carried along so live snapshots (Test Connection,
            # batch runs) see the same setting the Websites pipeline uses.
            "use_for_web": self.proxy_use_for_web.isChecked()
        }

    def _check_proxy_health(self):
        """v22 Feature 7: Poll the configured proxy port and update the
        status-label dot in the action button row. Non-blocking — uses a
        2-second socket connect_ex timeout. Runs on the GUI thread (the
        socket call returns well within 2s whether the host is up or down)."""
        # Guard: widget might not exist yet (early init) or might've been
        # destroyed during shutdown.
        if not hasattr(self, 'proxy_status_label'):
            return
        try:
            proxy = self._get_proxy_dict()
        except Exception:
            return
        try:
            if not proxy.get('enabled'):
                self.proxy_status_label.setPixmap(
                    _icons.pixmap('dot', '#8E8A90', 12))
                self.proxy_status_label.setToolTip("Proxy disabled")
                self._set_proxy_status_text("Idle", "Proxy disabled")
                return
            host = proxy.get('host', '127.0.0.1') or '127.0.0.1'
            try:
                port = int(proxy.get('port', 10808))
            except (TypeError, ValueError):
                port = 10808
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            try:
                result = sock.connect_ex((host, port))
            finally:
                try:
                    sock.close()
                except Exception:
                    pass
            if result == 0:
                self.proxy_status_label.setPixmap(
                    _icons.pixmap('dot', '#42B36B', 12))
                self.proxy_status_label.setToolTip(f"Proxy OK ({host}:{port})")
                self._set_proxy_status_text("Connected", f"Proxy OK ({host}:{port})")
            else:
                self.proxy_status_label.setPixmap(
                    _icons.pixmap('dot', '#E85D75', 12))
                self.proxy_status_label.setToolTip(f"Proxy unreachable ({host}:{port})")
                self._set_proxy_status_text("Error", f"Proxy unreachable ({host}:{port})")
        except Exception:
            try:
                self.proxy_status_label.setPixmap(
                    _icons.pixmap('dot', '#E85D75', 12))
                self.proxy_status_label.setToolTip("Proxy check failed")
                self._set_proxy_status_text("Error", "Proxy check failed")
            except Exception:
                pass

    def _set_proxy_status_text(self, state: str, tooltip: str):
        """v31.1 (WCAG 1.4.1): the status DOT is always accompanied by a TEXT
        label — color alone never conveys state ("Connected/Idle/Error").
        v0.07: the label also carries the matching SEMANTIC text color from
        the design system (success/error/muted), so the state is legible at
        a glance without reading the word."""
        if not hasattr(self, 'proxy_status_text'):
            return
        self.proxy_status_text.setText(state)
        self.proxy_status_text.setToolTip(tooltip)
        self.proxy_status_text.setAccessibleName(f"Proxy status: {state}")
        semantic = {
            'Connected': 'success',
            'Error':     'error',
            'Idle':      'muted',
            'Checking…': 'muted',
        }.get(state, 'muted')
        self.proxy_status_text.setStyleSheet(
            f"color: {self._status_colors()[semantic]}; font-size: 12px;")

    def _acquire_telegram_lock(self, owner: str = "telegram") -> bool:
        """Try to acquire the Telegram busy lock. Returns True if acquired,
        False if another Telegram operation is already running.

        v0.06 — the lock is a TelegramLockManager with owner tracking; the
        busy message now names the holder and its age so "please wait"
        becomes an actionable diagnostic instead of a dead end."""
        if not self._tg_lock.acquire(owner):
            held = self._tg_lock.describe()
            age = int(self._tg_lock.age_seconds)
            stuck_hint = (
                " It looks stuck — it will be force-released automatically "
                "if it doesn't finish, or restart the app."
                if age > 300 else
                " Please wait for it to finish."
            )
            self.log_message(
                f"⏳ Another Telegram operation is already running: {held}.{stuck_hint}",
                "warning"
            )
            return False
        return True

    def _release_telegram_lock(self, owner: Optional[str] = None):
        """Release the Telegram busy lock.

        v0.06 — owner-scoped: when ``owner`` is given the release only takes
        effect if that owner still holds the lock (a finished worker can no
        longer free a lock a DIFFERENT worker now holds — the v0.05
        over-release bug that enabled two telethon children on one session
        file). ``owner=None`` releases unconditionally (shutdown paths)."""
        self._tg_lock.release(owner)

    def _tg_lock_watchdog(self):
        """v0.06 — auto-release a Telegram lock held implausibly long.

        Every real operation is bounded by the subprocess runner's hard cap
        (30 min), so a lock still held after _TG_STUCK_SECONDS means the
        worker-finished cleanup chain itself died. Force-release so the app
        stays usable — exactly the scenario that used to show "another
        operation is running" forever."""
        try:
            if self._tg_lock.is_stuck(self._TG_STUCK_SECONDS):
                held_for = int(self._tg_lock.age_seconds)
                evicted = self._tg_lock.force_release("watchdog")
                self.log_message(
                    f"🔓 Watchdog: Telegram lock held by '{evicted}' for "
                    f"{held_for}s looks stuck — force-released. "
                    f"If Telegram misbehaves, restart the app.",
                    "warning"
                )
        except Exception:
            pass  # best-effort — never crash the timer

    def _keep_worker(self, worker: TestWorker, owner: Optional[str] = None):
        """Hold a reference so the QThread isn't garbage-collected mid-run.
        Also releases the Telegram busy lock when the worker finishes.

        v0.06 — owner-scoped release: the cleanup releases the lock only if
        THIS worker's owner still holds it. Previously any finishing worker
        unconditionally cleared the flag, which could free a lock that a
        newer operation had legitimately acquired."""
        self._active_test_workers.append(worker)
        def _cleanup(*_a):
            try:
                self._active_test_workers.remove(worker)
            except ValueError:
                pass
            self._tg_lock.release(owner)
        worker.finished_signal.connect(_cleanup)

    def _on_telegram_code_requested(self, prompt_type: str, worker: TestWorker):
        """Called when the Telegram worker needs a login code or 2FA password.
        Shows a theme-aware modal dialog with clear instructions."""
        dialog = QDialog(self)
        dialog.setModal(True)
        dialog.raise_()
        dialog.activateWindow()

        # Theme-aware colors
        is_dark = getattr(self, '_dark_mode', False)
        if is_dark:
            bg_color = "#2B2639"
            text_color = "#F2EEE7"
            help_bg = "#2E2A3C"
            border_color = "#4A4263"
            focus_color = "#C4BCF5"
        else:
            bg_color = "#FFFFFF"
            text_color = "#423A52"
            help_bg = "#F2EDE3"
            border_color = "#D8D0BE"
            focus_color = "#5F54B4"  # v32 pastel: violet accent

        dialog.setStyleSheet(f"""
            QDialog {{ background-color: {bg_color}; }}
            QLabel {{ color: {text_color}; background: transparent; }}
        """)

        if prompt_type == "PASSWORD":
            dialog.setWindowTitle("🔒 Telegram 2FA Password")
            label_text = "Enter your Telegram cloud password (2FA):"
            echo_mode = QLineEdit.EchoMode.Password
            placeholder = ""
            help_text = ""
        else:
            dialog.setWindowTitle("📲 Telegram Login Code")
            label_text = "Telegram has sent a login code to your account."
            echo_mode = QLineEdit.EchoMode.Normal
            placeholder = "e.g. 12345"
            help_text = (
                "WHERE TO FIND THE CODE:\n"
                "• Telegram app: Look for a 'Login Code' notification at the top\n"
                "  of your chat list (it's NOT in Saved Messages)\n"
                "• SMS: Check text messages on your phone\n"
                "• Other devices: Check any other device logged into Telegram\n\n"
                "The code expires in a few minutes. If you don't receive it,\n"
                "cancel and try again."
            )

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)

        # Title label
        title_label = QLabel(label_text)
        title_label.setStyleSheet(f"font-size: 14px; font-weight: bold; color: {text_color}; background: transparent;")
        title_label.setWordWrap(True)
        layout.addWidget(title_label)

        # Input field
        input_field = QLineEdit()
        input_field.setEchoMode(echo_mode)
        input_field.setPlaceholderText(placeholder)
        input_field.setMinimumWidth(360)
        input_field.setStyleSheet(
            f"QLineEdit {{ padding: 8px; border: 2px solid {border_color}; border-radius: 6px; font-size: 14px; background: {bg_color}; color: {text_color}; }}"
            f"QLineEdit:focus {{ border-color: {focus_color}; }}"
        )
        layout.addWidget(input_field)

        # Help text for code input
        if help_text:
            help_label = QLabel(help_text)
            help_label.setStyleSheet(
                f"font-size: 11px; color: {text_color}; background-color: {help_bg}; "
                f"padding: 10px; border-radius: 4px; font-family: Consolas, monospace;"
            )
            help_label.setWordWrap(True)
            layout.addWidget(help_label)

        # Buttons
        button_row = QHBoxLayout()
        button_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet(
            "padding: 8px 20px; font-size: 13px; border: 1px solid #ccc; border-radius: 4px; background: #f5f5f5;"
        )

        ok_btn = QPushButton("✓ Submit Code")
        ok_btn.setDefault(True)
        ok_btn.setStyleSheet(self._btn_style(COLORS['primary'], COLORS['primary_hover']))

        button_row.addWidget(cancel_btn)
        button_row.addWidget(ok_btn)
        layout.addLayout(button_row)

        def _on_accept():
            worker.provide_code(input_field.text().strip())
            dialog.accept()

        def _on_reject():
            worker.provide_code("")
            dialog.reject()

        ok_btn.clicked.connect(_on_accept)
        cancel_btn.clicked.connect(_on_reject)
        input_field.returnPressed.connect(_on_accept)

        # Focus the input field and auto-resize
        input_field.setFocus()
        dialog.adjustSize()
        self._animate_dialog(dialog)
        dialog.exec()

    def start_processing(self):
        """v0.23.0 — the simplified PROCESS dispatcher. Two input paths
        remain: the fetched bot queue (SYNC) and the Import txt file
        (Settings → 📥 Input). The ID Range / Markers / Single Msg
        branches were removed with their modes."""
        self.save_config()
        vault = self.vault_combo.currentText()
        if not vault or not os.path.isdir(vault):
            self._show_custom_message_box("Error", "Please select a valid Obsidian vault path.", success=False)
            return

        # Fetched bot queue first (the SYNC flow's fetched items).
        bot_urls = getattr(self, '_bot_queue_urls', [])
        if bot_urls:
            # v31.1 safety gate: confirm before large batches (>10 items).
            if not self._confirm_batch(len(bot_urls), "the bot queue"):
                self.log_message("⏹️ Batch cancelled — nothing was processed.", "warning")
                return
            self.log_message(f"🚀 Processing {len(bot_urls)} repos from bot queue...", "info")
            # v23 — pass bot_source=True so the manifest records the source
            # as 'bot' and Phase 5 auto-mark-read can fire on success. Also
            # pass the non-GitHub links so they are tracked in the manifest
            # (they were already added to the inbox table by check_bot_queue).
            self._start_worker_with_urls(
                bot_urls,
                bot_source=True,
                non_github_urls=getattr(self, '_bot_queue_non_github', [])
            )
            return

        # Import txt file (the ONE input mode): .txt or .md, one URL per
        # line — GitHub repos to the GitHub pipeline, everything else to
        # the Websites pipeline.
        import_file = self.import_file.text().strip()
        if import_file and os.path.exists(import_file):
            self._start_worker('import', None, None, None, None, import_file, None)
            return

        # Nothing fetched and no file picked — the helpful message.
        self._show_custom_message_box(
            "Nothing to Process",
            "No undone items fetched and no import file picked.\n\n"
            "Click SYNC first — it fetches every undone item from the "
            "Telegram bot, then becomes PROCESS.\n"
            "Or pick a .txt / .md file in Settings → Input.",
            success=False
        )

    def _start_worker_with_urls(self, urls, bot_source=False, non_github_urls=None,
                                intake_duplicates=0, raw_url_count=0):
        """Start a ProcessingWorker in 'direct' mode with a pre-fetched list
        of URLs. Used by the bot-queue, sources, and retry flows.

        v23 — No Link Left Behind:
          * ``bot_source=True``  labels the manifest source as 'bot' (so the
            Phase 5 auto-mark-read flow can fire on success).
          * ``non_github_urls`` is the list of non-GitHub links discovered
            alongside the GitHub URLs (typically from the bot queue). They
            are recorded in the manifest and the inbox table so they are
            never silently dropped.

        v25 pre-flight:
          * ``intake_duplicates`` / ``raw_url_count`` are surfaced in the
            final report so the user can see how many duplicate URLs were
            deduped during intake ("🔄 15 duplicates removed (200 unique
            from 215 total)").
        """
        self._start_worker(
            'direct', None, None, None, None, None, urls,
            bot_source=bot_source,
            non_github_urls=non_github_urls,
            intake_duplicates=intake_duplicates,
            raw_url_count=raw_url_count,
        )

    def _start_worker(self, mode, range_from, range_to, offset_start, offset_count, import_file, urls,
                      bot_source=False, non_github_urls=None, intake_duplicates=0, raw_url_count=0):
        # Acquire Telegram lock for modes that access the session file.
        # 'direct' and 'import' modes don't use Telegram, so no lock needed.
        # v0.06 — owner "batch": processing_finished releases exactly this
        # owner, so a finishing direct/import batch can no longer free a
        # lock held by an unrelated Telegram operation (v0.05 over-release).
        if mode in ('telegram_ids', 'telegram_offset'):
            if not self._acquire_telegram_lock("batch"):
                return

        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.progress_bar.setValue(0)
        # v31.1 spec: the determinate progress bar is visible ONLY while a
        # batch job runs — show it now, label it with the current item as
        # the batch progresses (update_progress / update_status).
        self.progress_bar.setVisible(True)
        self.progress_bar.setFormat("Processing…")
        self.log_text.clear()
        # Track processing start time for elapsed display
        self._processing_start_time = datetime.now()

        self.worker = ProcessingWorker(
            config=self.config,
            mode=mode,
            range_from=range_from,
            range_to=range_to,
            offset_start=offset_start,
            offset_count=offset_count,
            import_file=import_file,
            urls=urls
        )
        # v23 — populate link-tracker flags BEFORE the worker thread starts
        # so run() can read them during Phase 1 intake. non_github_urls is
        # only relevant for 'direct' mode (bot queue / sources / retry).
        self.worker._bot_source = bool(bot_source)
        if non_github_urls:
            self.worker._non_github_urls = list(non_github_urls)
        # v25 pre-flight: surface duplicate-removal stats in the final
        # report. Set from the bot-queue worker's dedup pass.
        self.worker._intake_duplicates = int(intake_duplicates or 0)
        self.worker._raw_url_count = int(raw_url_count or 0)
        self.worker.progress_updated.connect(self.update_progress)
        self.worker.status_updated.connect(self.update_status)
        self.worker.log_message.connect(self.log_message)
        self.worker.code_requested.connect(
            lambda pt, w=self.worker: self._on_telegram_code_requested(pt, w)
        )
        self.worker.finished_signal.connect(self.processing_finished)
        self.worker.disk_full_signal.connect(self._on_disk_full)
        # Pass the worker ref to _on_llm_failed so it can deliver the user's
        # decision back to the worker via resolve_llm_failure(). The signal
        # is emitted from the worker thread but Qt delivers it on the GUI
        # thread via a queued connection, so showing a modal dialog is safe.
        self.worker.llm_failed_signal.connect(
            lambda repo_name, w=self.worker: self._on_llm_failed(repo_name, w)
        )
        # v30 — Fix (model persistence): keep Settings + config.json in sync
        # when the worker's auto-switch or dialog-driven model change fires.
        self.worker.model_changed.connect(self._on_model_changed)
        self.worker.start()

    def stop_processing(self):
        if self.worker:
            self.worker.stop()
            self.log_message("⏹️ Stopping...", "warning")
            self.stop_btn.setEnabled(False)

    def update_progress(self, current, total):
        """Update the progress bar value and show `Processing X of Y` in its
        text (v31.1 spec: 'Processing 7 of 30 — repo-name').

        The format string is intentionally short so it fits on the narrow
        progress bar; the human-readable status (current repo name) is set
        separately by `update_status` which overwrites this format with a
        longer `Processing X of Y — owner/repo` string while a repo is being
        processed.
        """
        if total > 0:
            self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
        # v33: standalone X / Y counter beside the bar (wireframe: "10/20").
        # v0.07: never shows "– / –" — a determinate bar always has numbers.
        if hasattr(self, 'progress_count'):
            self.progress_count.setText(f"{current} / {total}" if total > 0 else "0 / 0")
            self.progress_count.setToolTip(
                f"Processing {current} of {total}" if total > 0
                else "No batch running")
        # Show "Processing X of Y" format in the progress bar
        if total > 0:
            self.progress_bar.setFormat(f"Processing {current} of {total}")
        else:
            self.progress_bar.setFormat("Ready")

    def update_status(self, text):
        """Show the current status text in the progress bar's format string.

        Format: `Processing X of Y — owner/repo (elapsed)`
        Elapsed time is calculated from `self._processing_start_time`.
        """
        # Status shown in progress bar format (status label removed)
        if text and text != "Ready":
            # Calculate elapsed time
            elapsed_str = ""
            if hasattr(self, '_processing_start_time'):
                elapsed = datetime.now() - self._processing_start_time
                total_secs = int(elapsed.total_seconds())
                if total_secs >= 60:
                    elapsed_str = f" ({total_secs // 60}m {total_secs % 60}s)"
                else:
                    elapsed_str = f" ({total_secs}s)"

            # Extract repo name from URL if it's a GitHub URL
            if 'github.com/' in text:
                parts = text.replace('https://github.com/', '').split('/')
                if len(parts) >= 2:
                    repo_name = f"{parts[0]}/{parts[1]}"
                    current = self.progress_bar.value()
                    total = self.progress_bar.maximum()
                    self.progress_bar.setFormat(f"Processing {current} of {total} — {repo_name[:40]}{elapsed_str}")
                    return
            self.progress_bar.setFormat(f"Processing: {text[:60]}{elapsed_str}")
        else:
            self.progress_bar.setFormat("Ready")


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


    def update_dashboard(self):
        """Scan the vault and display statistics in the Dashboard tab.

        Only reads the first ~1KB of each .md file (frontmatter) so it stays
        fast even on large vaults.
        """
        vault = self.vault_combo.currentText()
        if not vault or not os.path.isdir(vault):
            self.dashboard_text.setPlainText("No vault selected.")
            return

        self.log_message("📊 Refreshing dashboard...", "info")

        stats = {
            'total_notes': 0,
            'categories': {},
            'languages': {},
            'avg_credibility': 0.0,
            'credibility_scores': [],
            'banners': 0,
            'review_queue': 0,
        }

        for root, dirs, files in os.walk(vault):
            for fname in files:
                if fname.endswith('.md'):
                    fpath = os.path.join(root, fname)
                    stats['total_notes'] += 1

                    # Check if in _review folder
                    if '_review' in root:
                        stats['review_queue'] += 1

                    # Read frontmatter (only the first 1KB for speed)
                    try:
                        with open(fpath, 'r', encoding='utf-8') as f:
                            content = f.read(1000)

                        cat_match = re.search(r'category:\s*(.+)', content)
                        if cat_match:
                            cat = cat_match.group(1).strip()
                            stats['categories'][cat] = stats['categories'].get(cat, 0) + 1

                        cred_match = re.search(r'credibility_score:\s*([\d.]+)', content)
                        if cred_match:
                            try:
                                cred = float(cred_match.group(1))
                                stats['credibility_scores'].append(cred)
                            except ValueError:
                                pass

                        lang_match = re.search(r'primary_language:\s*(.+)', content)
                        if lang_match:
                            lang = lang_match.group(1).strip()
                            stats['languages'][lang] = stats['languages'].get(lang, 0) + 1
                    except Exception:
                        pass

                elif fname.endswith('.png') and 'banner' in fname:
                    stats['banners'] += 1

        # Calculate average credibility
        if stats['credibility_scores']:
            stats['avg_credibility'] = sum(stats['credibility_scores']) / len(stats['credibility_scores'])

        # Build dashboard text
        lines = []
        lines.append("=" * 50)
        lines.append("📊 VAULT DASHBOARD")
        lines.append("=" * 50)
        lines.append(f"Vault: {vault}")
        lines.append(f"Total notes: {stats['total_notes']}")
        lines.append(f"Banners downloaded: {stats['banners']}")
        lines.append(f"Review queue: {stats['review_queue']}")
        lines.append(f"Average credibility: {stats['avg_credibility']:.1f}/100")
        lines.append("")
        lines.append("📁 NOTES BY CATEGORY:")
        lines.append("-" * 50)
        for cat, count in sorted(stats['categories'].items(), key=lambda x: -x[1]):
            lines.append(f"  {cat:40s} {count:3d}")
        lines.append("")
        lines.append("💻 NOTES BY LANGUAGE:")
        lines.append("-" * 50)
        for lang, count in sorted(stats['languages'].items(), key=lambda x: -x[1]):
            lines.append(f"  {lang:40s} {count:3d}")
        lines.append("")
        lines.append("=" * 50)

        self.dashboard_text.setPlainText('\n'.join(lines))
        self.log_message(
            f"✅ Dashboard updated: {stats['total_notes']} notes, "
            f"{len(stats['categories'])} categories",
            "success"
        )
        self.refresh_quarantine_view()

    # ------------------------------------------------------------------
    # v0.09 (lineage merge) — 404 quarantine manager (Settings → Dashboard)
    # ------------------------------------------------------------------
    def refresh_quarantine_view(self):
        """Render the 404 quarantine table into the Dashboard page.

        Reads cache.db (not the vault), so it works even without a vault
        selected. Shows in-progress attempts AND confirmed-dead rows
        (⛔), unlike the More-menu viewer which lists confirmed only.
        Best-effort: a DB error shows an empty table, never a dialog."""
        try:
            cache = CacheDB()
            try:
                rows = cache.get_quarantine_stats()
            finally:
                cache.close()
        except Exception:
            rows = []
        if not rows:
            self.quarantine_text.setPlainText(
                "✅ No 404 attempts on record. Deleted repos will appear "
                "here after a run reports them missing.")
            return
        threshold = dead_link_threshold(self.config)
        lines = [f"{'URL':56s} {'attempts':>8s}  last seen"]
        lines.append("-" * 88)
        for url, _reason, attempts, last_seen in rows[:30]:
            flag = "  ⛔ quarantined" if attempts >= threshold else ""
            lines.append(f"{url:56s} {attempts:8d}  {str(last_seen)[:16]}{flag}")
        if len(rows) > 30:
            lines.append(f"... and {len(rows) - 30} more (see CLI --status / --list-dead)")
        lines.append("")
        lines.append(
            f"Threshold: {threshold} consecutive 404s → the repo is "
            "quarantined and skipped. Reset the quarantine to re-check it.")
        self.quarantine_text.setPlainText('\n'.join(lines))

    def clear_all_quarantine(self):
        """Reset every 404 attempt counter so known-dead repos are
        re-checked on the next run (e.g. after a takedown was reverted or a
        private repo became public again)."""
        reply = (QMessageBox.StandardButton.Yes
                 if self._show_custom_question(
                     "♻️ Reset 404 Quarantine",
                     "Reset the attempt counter for EVERY recorded URL?\n"
                     "Quarantined repos will be re-checked on the next run "
                     "instead of being auto-ignored.")
                 else QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            cache = CacheDB()
            try:
                removed = cache.reset_dead_links()
            finally:
                cache.close()
        except Exception as exc:
            self.log_message(f"⚠️ Could not reset the 404 quarantine: {exc}", "warning")
            return
        self.log_message(
            f"♻️ 404 quarantine reset — {removed} link(s) will be processed again.",
            "success")
        self.refresh_quarantine_view()

    def _save_quarantine_threshold(self, value: int):
        """Persist the spinbox value to config.json (merge-safe, live for
        the next batch — the worker reads the key per run)."""
        self.config['notfound_strike_threshold'] = int(value)
        self.save_config()

    def undo_last_batch(self):
        """v22 Feature 6: Delete the .md files written by the most recent
        processing batch. The list is stored at `<vault>/_undo_last_batch.txt`
        by ProcessingWorker.run() after a batch completes.

        Asks for confirmation, deletes each file (best-effort), and removes
        the corresponding entries from the SQLite cache + vault index."""
        vault = self.vault_combo.currentText()
        if not vault or not os.path.isdir(vault):
            self._show_custom_message_box("Error", "Please select a valid Obsidian vault path.", success=False)
            return

        undo_path = os.path.join(vault, '_undo_last_batch.txt')
        if not os.path.isfile(undo_path):
            self._show_custom_message_box("No Batch to Undo", "No _undo_last_batch.txt file found in the vault.\n"
                                    "Nothing to undo.", success=True)
            return

        # Read the list of files (one path per line)
        try:
            with open(undo_path, 'r', encoding='utf-8') as uf:
                file_list = [line.strip() for line in uf if line.strip()]
        except Exception as e:
            self._show_custom_message_box("Error", f"Failed to read undo list: {e}", success=False)
            return

        if not file_list:
            self._show_custom_message_box("No Batch to Undo", "The undo list is empty. Nothing to undo.", success=True)
            return

        existing = [f for f in file_list if os.path.isfile(f)]
        if not existing:
            self._show_custom_message_box("Nothing to Undo", "All files from the last batch have already been removed.", success=True)
            try:
                os.remove(undo_path)
            except OSError:
                pass
            return

        reply = QMessageBox.StandardButton.Yes if self._show_custom_question("↩️ Undo Last Batch", f"This will DELETE {len(existing)} note file(s) written by the last batch:\n\n"
            + "\n".join(f"• {os.path.basename(f)}" for f in existing[:10])
            + ("\n..." if len(existing) > 10 else "")
            + "\n\nProceed?") else QMessageBox.StandardButton.No
        if reply != QMessageBox.StandardButton.Yes:
            return

        deleted = 0
        errors = 0
        try:
            cache = CacheDB()
        except Exception:
            cache = None
        for fpath in existing:
            try:
                os.remove(fpath)
                deleted += 1
                # Also remove from the SQLite cache by note_path match.
                if cache is not None:
                    try:
                        cache.cursor.execute(
                            "DELETE FROM processed_repos WHERE note_path = ?", (fpath,)
                        )
                        cache.conn.commit()
                    except Exception:
                        pass
            except Exception:
                errors += 1
        if cache is not None:
            try:
                cache.close()
            except Exception:
                pass

        self.log_message(f"↩️ Undo: deleted {deleted} file(s), {errors} error(s).", "success" if errors == 0 else "warning")
        # Remove the undo file so the same batch can't be undone twice.
        try:
            os.remove(undo_path)
        except OSError:
            pass
        # Refresh the dashboard to reflect the deletion.
        try:
            self.update_dashboard()
        except Exception:
            pass

    def verify_vault(self):
        """v23 — Phase 3 manual verification — read the manifest, check every
        GitHub link has a non-empty note file on disk, and every non-GitHub
        link is in the inbox table. Shows a detailed report in the dashboard
        text area. Offers to retry any failed links via the existing retry
        flow (cache.add_failed).

        v26 — Fix 2: the entire method is wrapped in a try/except so a crash
        anywhere (missing manifest file, malformed JSON, unexpected attribute
        on the tracker) is reported to the user instead of taking down the
        whole app. The old ``QMessageBox.question`` retry prompt has been
        replaced with a theme-aware custom dialog (no native modal that can
        hide behind the main window on some WMs) AND the failed URLs are
        added to the retry queue unconditionally — the user no longer has to
        click 'Yes' to enqueue them."""
        try:
            vault = self.vault_combo.currentText()
            if not vault or not os.path.isdir(vault):
                try:
                    self.dashboard_text.setPlainText("No vault selected.")
                except Exception:
                    pass
                return

            tracker = LinkTracker(vault)
            prev = tracker.load_previous_manifest()
            if not prev:
                try:
                    self.dashboard_text.setPlainText(
                        "No previous manifest found in this vault.\n"
                        "Process a batch first — the manifest is created at the start of every batch."
                    )
                except Exception:
                    pass
                self.log_message("ℹ️ No manifest to verify — process a batch first.", "info")
                return

            # Load the previous manifest into the tracker so verify() can
            # re-check every link against the current state of the vault.
            tracker.manifest = prev
            # v29 fix: log_signal must be a Qt signal (with .emit()), not a method.
            # self.log_message is a method in MainWindow, so pass None.
            # v0.21.0 — look for _inbox rows in the Websites vault too
            # (the tables live there since v0.20.0 vault separation).
            _extra_dirs = []
            _wv = ((self.config or {}).get('website_vault_path') or '').strip()
            if _wv:
                _extra_dirs.append(os.path.join(_wv, "_inbox"))
            report = tracker.verify(log_signal=None, extra_inbox_dirs=_extra_dirs)

            # Log the summary manually
            self.log_message(f"🔍 Verify: {report.get('github_processed', 0)} processed, {report.get('github_failed', 0)} failed", "info")

            # Render a detailed report in the dashboard text area
            lines = []
            lines.append("=" * 60)
            lines.append("🔍 VAULT VERIFICATION REPORT")
            lines.append("=" * 60)
            lines.append(f"Batch:    {prev.get('batch_id', 'unknown')}")
            lines.append(f"Source:   {prev.get('source', 'unknown')}")
            lines.append(f"Created:  {prev.get('created_at', 'unknown')}")
            lines.append(f"Total links: {report['total']}")
            lines.append("")
            lines.append(f"✅ GitHub processed:    {report['github_processed']}")
            lines.append(f"⏭️ GitHub skipped:      {report['github_skipped']}  (dedup — already in vault)")
            if report.get('github_pending'):
                lines.append(f"⏳ GitHub pending:      {report['github_pending']}  (unfinished — will retry)")
            lines.append(f"❌ GitHub failed:       {report['github_failed']}")
            if report.get('non_github_recorded'):
                lines.append(f"✅ Non-GitHub recorded: {report['non_github_recorded']}  (inbox tables)")
            if report.get('websites_processed') or report.get('websites_review') or report.get('websites_skipped'):
                lines.append(f"🌐 Websites notes:      {report.get('websites_processed', 0)}")
                lines.append(f"🗂️ Websites in _review: {report.get('websites_review', 0)}  (retry scheduled)")
                lines.append(f"⏭️ Websites skipped:    {report.get('websites_skipped', 0)}  (dedup)")
            if report.get('blocked_recorded'):
                lines.append(f"🚫 Blocked/self domains: {report.get('blocked_recorded')}  (recorded in _inbox)")
            if report.get('non_github_pending'):
                lines.append(f"⏳ Non-GitHub pending:  {report.get('non_github_pending')}  (websites pipeline off / no vault)")
            if report.get('non_github_failed'):
                lines.append(f"❌ Non-GitHub failed:   {report.get('non_github_failed', 0)}")
            acc = report.get('accounted', 0)
            if report.get('accounting_ok'):
                lines.append(f"🧮 Accounting:          {acc}/{report['total']} accounted for ✓")
            else:
                lines.append(f"🧮 Accounting:          only {acc}/{report['total']} accounted for — {report.get('unaccounted', 0)} escaped every bucket!")
            lines.append("")

            if report["verification_passed"] and report.get('accounting_ok'):
                lines.append("🎉 ALL LINKS VERIFIED — NO DATA LOSS!")
            else:
                lines.append(f"⚠️ {len(report['failed_links'])} link(s) need retry:")
                lines.append("")
                for fl in report["failed_links"]:
                    lines.append(f"  ❌ {fl.get('url', '?')}")
                    lines.append(f"     Type:   {fl.get('type', '?')}")
                    lines.append(f"     Status: {fl.get('status', '?')}")
                    lines.append(f"     Error:  {fl.get('error', 'unknown')}")
                    lines.append("")

            lines.append("=" * 60)
            try:
                self.dashboard_text.setPlainText('\n'.join(lines))
            except Exception:
                pass

            # v26 — Fix 2: failed URLs are enqueued directly (no Yes/No prompt
            # that could crash if the parent window is being torn down) and
            # the user is shown a custom theme-aware dialog telling them what
            # happened. The retry queue is the canonical source of "links to
            # reprocess" so this is safe even if the user clicks the Verify
            # Vault button many times.
            failed_urls = [fl.get('url') for fl in report['failed_links'] if fl.get('url')]
            if failed_urls:
                self._show_custom_message_box(
                    "Failed Links Found",
                    f"{len(failed_urls)} link(s) failed verification.\n\n"
                    "They have been added to the retry queue.\n"
                    "Click '🔄 Retry Failed' in the Bot tab to reprocess them.",
                    success=False
                )
                # Add to retry queue directly (no question dialog).
                try:
                    cache = CacheDB()
                    for u in failed_urls:
                        try:
                            cache.add_failed(u, "failed Phase 3 verification")
                        except Exception:
                            pass
                    cache.close()
                    self.log_message(
                        f"📥 Added {len(failed_urls)} failed link(s) to the retry queue. "
                        f"Click '🔄 Retry Failed' in the Bot tab to reprocess them.",
                        "info"
                    )
                except Exception as e:
                    self.log_message(f"❌ Failed to enqueue retries: {e}", "error")
        except Exception as e:
            # v26 — Fix 2: never let a Verify Vault crash take down the app.
            import traceback
            error_msg = f"Verify Vault crashed: {e}\n\n{traceback.format_exc()}"
            try:
                self.dashboard_text.setPlainText(error_msg)
            except Exception:
                pass
            self.log_message(f"❌ Verify Vault crashed: {e}", "error")

    def recategorize_notes(self):
        """Show a table of notes and let the user bulk-reassign categories.

        For each row the user picks a new category from a dropdown. On
        'Apply Changes', notes whose category changed are rewritten with the
        new `category:` frontmatter value and moved to the corresponding
        category folder. The old file is removed only if the new path differs.
        """
        vault = self.vault_combo.currentText()
        if not vault or not os.path.isdir(vault):
            self._show_custom_message_box("Error", "Please select a valid vault first.", success=False)
            return

        # Collect all notes with their current categories
        notes = []
        locked_count = 0
        for root, dirs, files in os.walk(vault):
            for fname in files:
                if not fname.endswith('.md'):
                    continue
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, 'r', encoding='utf-8') as f:
                        content = f.read(1000)
                    # v0.12.0 — Phase 3 (§4.4): locked notes (the owner
                    # moved them by hand — a correction) are never
                    # re-categorized, not even listed here.
                    if re.search(r'^category_locked:\s*true', content,
                                 re.MULTILINE):
                        locked_count += 1
                        continue
                    cat_match = re.search(r'category:\s*(.+)', content)
                    cat = cat_match.group(1).strip() if cat_match else "Unknown"
                    notes.append({'path': fpath, 'name': fname, 'category': cat})
                except Exception:
                    pass

        if locked_count:
            self.log_message.emit(
                f"🔒 {locked_count} locked note(s) skipped — your own moves "
                "are never re-categorized.", "info")

        if not notes:
            self._show_custom_message_box("Recategorize", "No notes found in vault.", success=True)
            return

        # Create dialog with table
        dialog = QDialog(self)
        dialog.setWindowTitle(f"📁 Recategorize Notes ({len(notes)} notes)")
        
        dialog.setMinimumWidth(800)
        dialog.setMinimumHeight(500)

        layout = QVBoxLayout(dialog)

        # Table
        from PyQt6.QtWidgets import QTableWidget, QTableWidgetItem, QComboBox, QHeaderView
        table = QTableWidget(len(notes), 3)
        table.setHorizontalHeaderLabels(["Note", "Current Category", "New Category"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)

        for i, note in enumerate(notes):
            table.setItem(i, 0, QTableWidgetItem(note['name']))
            table.setItem(i, 1, QTableWidgetItem(note['category']))
            combo = QComboBox()
            combo.addItems(CATEGORY_KEYS)
            idx = combo.findText(note['category'])
            if idx >= 0:
                combo.setCurrentIndex(idx)
            table.setCellWidget(i, 2, combo)

        layout.addWidget(table)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        apply_btn = QPushButton("✓ Apply Changes")
        apply_btn.setStyleSheet(self._btn_style(COLORS['cta'], COLORS['cta_hover'], text=COLORS['cta_text']))
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet(
            "padding: 8px 20px; border: 1px solid #ccc; border-radius: 5px;"
        )
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(apply_btn)
        layout.addLayout(btn_row)

        def _apply():
            moved = 0
            for i, note in enumerate(notes):
                combo = table.cellWidget(i, 2)
                new_cat = combo.currentText()
                if new_cat != note['category']:
                    try:
                        with open(note['path'], 'r', encoding='utf-8') as f:
                            full_content = f.read()
                        new_content = re.sub(
                            r'category:\s*.+',
                            f'category: {new_cat}',
                            full_content
                        )
                        # Write to new location
                        new_folder = os.path.join(
                            vault, CATEGORY_FOLDERS.get(new_cat, "Uncategorized")
                        )
                        os.makedirs(new_folder, exist_ok=True)
                        new_path = os.path.join(new_folder, note['name'])

                        with open(new_path, 'w', encoding='utf-8') as f:
                            f.write(new_content)

                        # Delete old file if different location
                        if os.path.abspath(new_path) != os.path.abspath(note['path']):
                            os.remove(note['path'])

                        # Update SQLite cache so dedup check finds the note at the new path
                        cache = None
                        try:
                            cache = CacheDB()
                            # Find the repo_id by matching the old note_path
                            cache.cursor.execute(
                                "SELECT repo_id FROM processed_repos WHERE note_path = ?",
                                (note['path'],)
                            )
                            row = cache.cursor.fetchone()
                            if row:
                                repo_id = row[0]
                                cache.cursor.execute(
                                    "UPDATE processed_repos SET note_path = ?, category = ? WHERE repo_id = ?",
                                    (new_path, new_cat, repo_id)
                                )
                                cache.conn.commit()
                        except Exception as cache_err:
                            self.log_message(
                                f"   ⚠️ Cache update failed for {note['name']}: {cache_err}",
                                "warning"
                            )
                        finally:
                            if cache:
                                try:
                                    cache.close()
                                except Exception:
                                    pass

                        moved += 1
                    except Exception as e:
                        self.log_message(
                            f"Failed to recategorize {note['name']}: {e}", "error"
                        )

            self.log_message(f"📁 Recategorized {moved} notes", "success")
            dialog.accept()
            if moved > 0:
                self.update_dashboard()

        apply_btn.clicked.connect(_apply)
        cancel_btn.clicked.connect(dialog.reject)

        self._animate_dialog(dialog)
        dialog.exec()

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

    def retry_failed_repos(self):
        """v22 Feature 4: Fetch unresolved failed URLs from the SQLite cache
        and reprocess them. If the vault path is not set, just shows a message.
        Clears _bot_queue_urls so the auto-mark-read logic in
        processing_finished doesn't fire on a retry batch."""
        vault = self.vault_combo.currentText()
        if not vault or not os.path.isdir(vault):
            self._show_custom_message_box("Error", "Please select a valid Obsidian vault path.", success=False)
            return

        try:
            cache = CacheDB()
            failed = cache.get_failed_urls()
            cache.close()
        except Exception as e:
            self.log_message(f"❌ Failed to read retry queue: {e}", "error")
            return

        if not failed:
            self.log_message("✓ No failed repos to retry.", "success")
            self._show_custom_message_box("Retry Queue Empty", "No failed repos to retry. 🎉", success=True)
            return

        urls = [row[0] for row in failed if row and row[0]]
        if not urls:
            self.log_message("✓ No failed repos to retry.", "success")
            return

        # v31.1 safety gate: confirm before large batches (>10 items).
        if not self._confirm_batch(len(urls), "the retry queue"):
            self.log_message("⏹️ Retry cancelled — nothing was processed.", "warning")
            return

        self.log_message(f"🔄 Retrying {len(urls)} previously-failed repos...", "info")
        # Don't trigger the auto-mark-read flow for retries — these URLs
        # intentionally failed before, so we shouldn't mark the bot queue
        # as read even if they succeed this time (the user might want to
        # verify them first).
        self._bot_queue_urls = []
        self._start_worker_with_urls(urls)

    # ------------------------------------------------------------------
    # v0.08 — 404 quarantine management (More ▸ View 404 Quarantine)
    # ------------------------------------------------------------------
    def view_dead_links(self):
        """List every CONFIRMED-dead link (>= DEAD_LINK_THRESHOLD consecutive
        404s, counted across sessions in cache.db) with attempts + date, and
        offer a one-click Reset — for false positives (a repo that went
        PRIVATE reads as 404 to an unauthorized token, but processes fine
        again once it is public / the token has access)."""
        try:
            cache = CacheDB()
            dead = cache.get_dead_urls(dead_link_threshold(self.config))
            cache.close()
        except Exception as e:
            self.log_message(f"❌ Failed to read the 404 quarantine: {e}", "error")
            return

        if not dead:
            self.log_message("✓ 404 quarantine is empty — no confirmed-dead links.", "success")
            self._show_custom_message_box(
                "404 Quarantine",
                "The quarantine is empty — no links have been confirmed dead.\n\n"
                f"(A link enters the quarantine after "
                f"{dead_link_threshold(self.config)} "
                "consecutive 404s across sessions.)",
                success=True)
            return

        rows = "\n".join(
            f"• {url}\n     attempts: {count} · since: {(at or '')[:10]}"
            for url, reason, count, at in dead)
        self.log_message(f"🚫 404 quarantine: {len(dead)} confirmed-dead link(s).", "warning")
        if self._show_custom_question(
                "404 Quarantine — confirmed-dead links",
                f"{len(dead)} link(s) are quarantined and skipped in every batch:\n\n"
                f"{rows}\n\n"
                "Reset the quarantine? Every link gets a fresh set of "
                "attempts — use this if a repo was private or renamed and "
                "is back."):
            self._reset_dead_links_now()

    def _reset_dead_links_now(self):
        """Clear the whole 404 quarantine table (see view_dead_links)."""
        try:
            cache = CacheDB()
            removed = cache.reset_dead_links()
            cache.close()
        except Exception as e:
            self.log_message(f"❌ Failed to reset the 404 quarantine: {e}", "error")
            return
        self.log_message(
            f"♻️ 404 quarantine reset — {removed} link(s) will be processed again.",
            "success")

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


    def _schedule_progress_hide(self, delay_ms: int = 2500):
        """v31.1 spec: the progress bar is visible ONLY while a batch job
        runs — after a finish/failure we flash the result briefly, then hide.
        Guarded so a freshly-started batch is never hidden by a stale timer."""
        QTimer.singleShot(delay_ms, self._hide_progress_bar)

    def _hide_progress_bar(self):
        if not self.start_btn.isEnabled():
            return  # a NEW batch is already running — keep the bar live
        # v33: the progress row is a permanent fixture of the main view
        # (wireframe) — reset to Ready instead of hiding.
        self.progress_bar.setFormat("Ready")
        self.progress_bar.setValue(0)
        # v0.07: fall back to the manifest's REAL totals instead of "– / –".
        self._refresh_pipeline_counter()

    def processing_finished(self, success, message):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        # v0.07: the PROCESSED counter falls back to the manifest's real
        # totals the moment a batch ends (never back to a blank "– / –").
        self._refresh_pipeline_counter()
        # v0.06 — owner-scoped release: only frees the lock when THIS batch
        # actually holds it (telegram modes). A direct/import batch finishing
        # while an unrelated Telegram operation runs must NOT steal its lock
        # — that race used to enable two telethon children on one session.
        self._release_telegram_lock("batch")

        # v29.4 — Auto-backup after batch (if enabled)
        if success and self.config.get('backup_enabled', False) and self.config.get('backup_folder', ''):
            if hasattr(self, '_backup_now'):
                try:
                    self._backup_now()
                except Exception as e:
                    self.log_message(f"⚠️ Auto-backup failed: {e}", "warning")

        # v31 — VaultSeal: seal the vault into its private GitHub mirror.
        # Deliberately runs for FAILED batches too: a mid-run failure may
        # still have written notes, and those are exactly what we want
        # backed up. An unchanged vault is a no-op ("vault unchanged since
        # the last seal"). Never blocks the GUI — runs in a QThread.
        try:
            vs_cfg = self.config.get('vaultseal') or {}
            if vs_cfg.get('enabled', True) and self.config.get('vault_path'):
                self._start_vault_seal()
        except Exception as seal_err:
            self.log_message(f"⚠️ VaultSeal could not start: {seal_err}", "warning")

        # v32 — GoodRepos: publish the PUBLIC curated directory (emoji-rich
        # README + mirrored notes) right after the private seal. Unchanged
        # content is a no-op; never blocks the GUI — its own QThread.
        try:
            gr_cfg = self.config.get('goodrepos') or {}
            if gr_cfg.get('enabled', True) and self.config.get('vault_path'):
                self._start_goodrepos_publish()
        except Exception as good_err:
            self.log_message(f"⚠️ Good Repos could not start: {good_err}", "warning")

        # v0.10.0 — Phase 1: the WEBSITES vault gets its own private mirror
        # (a second, independent VaultSeal). A silent no-op while the
        # websites pipeline is OFF (the default) or no websites vault is
        # configured — exactly the Phase 1 acceptance behavior.
        try:
            _pipes_cfg = self.config.get('pipelines') or {}
            if (_pipes_cfg.get('websites', False)
                    and (self.config.get('website_vault_path') or '').strip()):
                self._start_websites_seal()
        except Exception as ws_err:
            self.log_message(f"⚠️ Websites vault seal could not start: {ws_err}", "warning")

        # Calculate total elapsed time
        elapsed_str = ""
        if hasattr(self, '_processing_start_time'):
            elapsed = datetime.now() - self._processing_start_time
            total_secs = int(elapsed.total_seconds())
            if total_secs >= 60:
                elapsed_str = f" in {total_secs // 60}m {total_secs % 60}s"
            else:
                elapsed_str = f" in {total_secs}s"
            del self._processing_start_time

        if success:
            self.log_message(f"✅ {message}{elapsed_str}", "success")
            # Hint: new messages may have arrived during processing
            self.log_message("💡 Tip: New messages may have arrived during processing — click Check Queue again.", "info")
            self.progress_bar.setFormat(f"✅ Done{elapsed_str}")
            self._schedule_progress_hide()

            # v23 — Phase 5: CLEAR — only mark bot messages as read if ALL
            # links verified (no failures, no pending/processing leftovers).
            # This supersedes the v22 vault-index check, which only verified
            # GitHub URLs and ignored non-GitHub links. The LinkTracker's
            # manifest is now the single source of truth.
            #
            # Fallback: if there is no link_tracker (e.g. vault_path was
            # empty when the worker started), fall back to the v22 vault
            # index check so we don't regress.
            bot_urls = getattr(self, '_bot_queue_urls', [])
            all_clear = False
            if bot_urls:
                worker_lt = getattr(self.worker, 'link_tracker', None) if self.worker else None
                if worker_lt:
                    if worker_lt.get_all_clear():
                        all_clear = True
                        self.log_message("✅ All links verified — marking bot messages as read...", "info")
                        self._mark_bot_messages_read()
                    else:
                        # Show which links are causing the failure
                        pending_links = [l for l in worker_lt.manifest["links"] if l["status"] in ("failed", "processing", "pending")]
                        self.log_message(
                            f"⚠️ {len(pending_links)} link(s) not verified — bot messages NOT marked as read",
                            "warning"
                        )
                        for pl in pending_links[:5]:
                            self.log_message(
                                f"   • [{pl['status']}] {pl['url']}" + (f" — {pl.get('error','')}" if pl.get('error') else ""),
                                "info"
                            )
                        if len(pending_links) > 5:
                            self.log_message(f"   ... and {len(pending_links) - 5} more", "info")
                else:
                    # No link tracker — fall back to the v22 vault-index check
                    all_in_vault = True
                    worker_vi = getattr(self.worker, '_vault_index', None) if self.worker else None
                    if worker_vi:
                        for url in bot_urls:
                            try:
                                norm = normalize_url(url)
                                if not worker_vi.has_url(norm):
                                    all_in_vault = False
                                    break
                            except Exception:
                                all_in_vault = False
                                break
                    if all_in_vault:
                        all_clear = True
                        self.log_message("✅ All repos processed — marking bot messages as read...", "info")
                        self._mark_bot_messages_read()
                    else:
                        self.log_message("⚠️ Some repos failed — bot messages NOT marked as read (retry next time)", "warning")

            # v25 pre-flight: advance last_processed_msg_id ONLY if the
            # batch came from "📬 Process New" AND Phase 5 CLEAR passed.
            # If verification failed, we keep the old ID so the next
            # "Process New" run re-fetches the failed messages and retries
            # them — no link is ever lost.
            pending_update = getattr(self, '_pending_last_processed_update', 0)
            if pending_update and all_clear:
                old_id = int(self.config.get('last_processed_msg_id', 0) or 0)
                if pending_update > old_id:
                    self.config['last_processed_msg_id'] = int(pending_update)
                    try:
                        self.save_config()
                        self.log_message(
                            f"📌 last_processed_msg_id advanced: {old_id} → {pending_update} "
                            f"(next 'Process New' will skip up to ID {pending_update})",
                            "success"
                        )
                    except Exception as save_err:
                        self.log_message(
                            f"⚠️ Failed to persist last_processed_msg_id ({save_err}) — "
                            f"this run is verified but the next 'Process New' will re-fetch these messages.",
                            "warning"
                        )
            elif pending_update and not all_clear:
                self.log_message(
                    f"⏸️ last_processed_msg_id NOT advanced (some links failed verification) — "
                    f"next 'Process New' will re-fetch messages newer than ID "
                    f"{int(self.config.get('last_processed_msg_id', 0) or 0)} and retry failed links.",
                    "warning"
                )
            # Clear the pending flag regardless — it only applies to this batch.
            self._pending_last_processed_update = 0

            self._show_custom_message_box("Processing Complete", f"{message}{elapsed_str}", success=True)
        else:
            self.log_message(f"❌ {message}", "error")
            self.progress_bar.setFormat("❌ Failed")
            self._schedule_progress_hide()
            self._show_custom_message_box("Processing Error", message, success=False)

    def _on_disk_full(self, path):
        """Handle disk full — show dialog, wait for user to free space, then resume."""
        msg = f"Disk is full!\n\nCannot write:\n{path}\n\nFree up disk space, then click OK to resume processing."
        self.log_message("💾 DISK FULL — processing paused. Free space and click OK.", "error")
        # Use theme-aware question dialog
        if self._show_custom_question("💾 Disk Full", msg):
            self.log_message("▶️ Resuming processing after disk full...", "info")
            if self.worker:
                self.worker._disk_full_paused = False
        else:
            self.log_message("⏹️ Aborting due to disk full.", "error")
            if self.worker:
                self.worker.stop()
                self.worker._disk_full_paused = False

    def _on_llm_failed(self, repo_name, worker):
        """LLM failed for a repo — show a custom blocking dialog with buttons
        + a model dropdown. The worker thread is BLOCKED on
        `_llm_retry_event` until we call `worker.resolve_llm_failure(response)`.

        Response values sent back to the worker:
          - 'skip'   — skip this repo, continue batch
          - 'retry'  — retry with the same model
          - '<name>' — retry with this model name (from the dropdown)
          - 'stop'   — stop the batch

        v30 — Fix (model persistence): when a DIFFERENT model is chosen (and
        "Remember" is checked, default ON), the worker persists it via
        _apply_model_choice(): it applies to the rest of the batch, the
        Settings combo, and config.json. This dialog used to reappear for
        EVERY link because the choice was only used for a single retry.
        """
        # v0.15.0 — llama.cpp engine detection: the failed-model name and
        # the pick-a-model list are provider-aware (llamacpp reads the
        # llamacpp_* keys and lists the llama-server's own /v1/models).
        _provider = self.config.get('llm_provider', 'ollama')
        if _provider == 'ollama':
            failed_model = (self.config.get('ollama', {}) or {}).get('model', '')
        elif _provider == 'llamacpp':
            failed_model = str(self.config.get('llamacpp_model', '') or '')
        else:
            failed_model = self.config.get('cloud_model', '')
        self.log_message(
            f"❌ LLM failed for '{repo_name}' after 3 attempts (model '{failed_model}').",
            "error"
        )

        dialog = QDialog(self)
        dialog.setWindowTitle("🤖 LLM Analysis Failed")
        dialog.setModal(True)
        dialog.setMinimumWidth(460)

        # Theme-aware colors (matches _show_custom_message_box)
        is_dark = getattr(self, '_dark_mode', False)
        if is_dark:
            bg_color = "#2B2639"
            text_color = "#F2EEE7"
            border_color = "#3B344F"
            input_bg = "#241F31"
        else:
            bg_color = "#FFFFFF"
            text_color = "#423A52"
            border_color = "#F2EEE7"
            input_bg = "#FDFCF8"

        dialog.setStyleSheet(f"""
            QDialog {{ background-color: {bg_color}; }}
            QLabel {{ color: {text_color}; background: transparent; }}
            QComboBox, QComboBox QAbstractItemView {{
                background-color: {input_bg};
                color: {text_color};
                border: 1px solid {border_color};
                border-radius: 4px;
                padding: 6px 10px;
            }}
        """)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)

        # Header
        header = QHBoxLayout()
        icon_label = QLabel("🤖")
        icon_label.setStyleSheet("font-size: 28px; background: transparent;")
        header.addWidget(icon_label)
        title_label = QLabel(f" LLM Failed: {repo_name}")
        title_color = "#DC2626" if not is_dark else "#F87171"
        title_label.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {title_color}; background: transparent;")
        header.addWidget(title_label)
        header.addStretch()
        layout.addLayout(header)

        # Description
        desc = QLabel(
            f"The LLM failed to analyze this repo after 3 attempts.\n"
            f"Failed model: <b>{failed_model or 'unknown'}</b>\n"
            "Choose how to proceed:"
        )
        desc.setStyleSheet(f"font-size: 13px; color: {text_color}; background: transparent;")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # Fetch available models (best-effort, may be empty/slow).
        # v0.15.0 — provider-aware: Ollama lists its own models; llama.cpp
        # lists the llama-server's /v1/models; the cloud endpoint keeps the
        # legacy behavior (Ollama list — switching models there is rare).
        server_down = False
        if _provider == 'llamacpp':
            _probe = {}
            try:
                _probe = _llm_client.probe_llamacpp(
                    self.llamacpp_api_url.text().strip()
                    if hasattr(self, 'llamacpp_api_url')
                    else self.config.get('llamacpp_api_url', ''),
                    self.llamacpp_api_key.text().strip()
                    if hasattr(self, 'llamacpp_api_key')
                    else self.config.get('llamacpp_api_key', ''))
                model_names = list(_probe.get('models') or [])
                server_down = not _probe.get('found')
            except Exception:
                model_names = []
                server_down = True
            if _probe.get('props_model') and _probe['props_model'] not in model_names:
                model_names.append(_probe['props_model'])
        else:
            try:
                model_names, _err = self._get_ollama_model_names(self.ollama_url.text())
                server_down = _err is not None
            except Exception:
                model_names = []
                server_down = True
        if not model_names:
            model_names = []

        # v0.05 — Fix (owner report: model-switching cannot fix a dead
        # server): when the server is unreachable, the model dropdown is
        # useless — say WHY the analysis failed and what actually helps,
        # right inside the dialog.
        if server_down:
            server_hint = QLabel(
                "⚠️ The LLM server is not reachable right now — a different "
                "model will NOT fix this.\n"
                "Start it first: Settings → LLM → 🚀 Start Server (or run "
                "'ollama serve' in a terminal), then choose Retry."
            )
            server_hint.setWordWrap(True)
            server_hint.setStyleSheet(
                f"font-size: 12px; color: {'#B45309' if not is_dark else '#F2DCA8'}; "
                f"background: transparent; border: 1px solid "
                f"{'#F5E3C0' if not is_dark else '#4A3F28'}; border-radius: 6px; "
                f"padding: 8px;"
            )
            layout.addWidget(server_hint)

        # Model dropdown
        model_row = QHBoxLayout()
        model_row.addWidget(QLabel("Retry with:"))
        model_combo = QComboBox()
        model_combo.addItem("— Same model —", "retry")
        for name in model_names:
            model_combo.addItem(name, name)
        model_row.addWidget(model_combo, 1)
        layout.addLayout(model_row)

        # v30 — Fix (model persistence): default the dropdown to a DIFFERENT
        # model when one exists — the same model just failed 3 times.
        for idx in range(model_combo.count()):
            data = model_combo.itemData(idx)
            if data and data != "retry" and data != failed_model:
                model_combo.setCurrentIndex(idx)
                break

        # v30 — "Remember" checkbox: save the chosen model as the default
        # (Settings + config.json + rest of batch). ON by default — that is
        # what users expect: pick once, keep going.
        remember_check = QCheckBox("Remember this model (apply to the rest of the batch and save to Settings)")
        remember_check.setChecked(True)
        remember_check.setStyleSheet(f"color: {text_color}; background: transparent; font-size: 12px;")
        layout.addWidget(remember_check)

        # Buttons
        btn_row = QHBoxLayout()
        skip_btn = QPushButton("⏭️ Skip")
        skip_btn.setStyleSheet(self._btn_style(COLORS['neutral'], COLORS['neutral_hover'], variant='outline'))
        retry_same_btn = QPushButton("🔁 Retry Same")
        retry_same_btn.setStyleSheet(self._btn_style(COLORS['primary'], COLORS['primary_hover']))
        retry_with_btn = QPushButton("🔁 Retry With ▾")
        retry_with_btn.setStyleSheet(self._btn_style(COLORS['cta'], COLORS['cta_hover'], text=COLORS['cta_text']))
        stop_btn = QPushButton("⏹ Stop Batch")
        stop_btn.setStyleSheet(self._btn_style(COLORS['error_deep'], '#8F1B2E', variant='outline'))
        btn_row.addWidget(skip_btn)
        btn_row.addWidget(retry_same_btn)
        btn_row.addWidget(retry_with_btn)
        btn_row.addWidget(stop_btn)
        layout.addLayout(btn_row)

        # Capture the decision in a closure so we can deliver it to the worker
        response_holder = {"value": None, "remember": True}

        def _respond(value: str):
            response_holder["value"] = value
            response_holder["remember"] = remember_check.isChecked()
            dialog.accept()

        skip_btn.clicked.connect(lambda: _respond("skip"))
        retry_same_btn.clicked.connect(lambda: _respond("retry"))
        retry_with_btn.clicked.connect(lambda: _respond(model_combo.currentData() or "retry"))
        stop_btn.clicked.connect(lambda: _respond("stop"))

        self._animate_dialog(dialog)
        dialog.exec()

        # If user closed the dialog with the X button (no decision), default to skip
        decision = response_holder["value"] or "skip"
        remember = response_holder["remember"]
        self.log_message(f"👉 User chose: '{decision}'" + (" (remember)" if remember else ""), "info")

        # v30 — Fix (model persistence): persist the model choice immediately
        # (config + Settings combo + config.json). The worker ALSO applies it
        # to self.config, but doing it here guarantees the GUI + disk state
        # even if the worker finishes first.
        if remember and decision and decision not in ("skip", "retry", "stop"):
            self._on_model_changed(
                self.config.get('llm_provider', 'ollama'), decision
            )
        # Unblock the worker thread
        worker.resolve_llm_failure(decision)

    def _on_model_changed(self, provider: str, model_name: str):
        """v30 — Fix (model persistence): a new LLM model was selected
        (from the failure dialog, the auto-switch, or the worker). Sync the
        Settings combo + self.config (in place) + config.json so the choice
        survives the batch AND the next app launch."""
        if not model_name:
            return
        try:
            if provider == 'cloud':
                self.config['cloud_model'] = model_name
                if hasattr(self, 'cloud_model'):
                    self.cloud_model.setText(model_name)
            elif provider == 'llamacpp':
                # v0.15.0 — llama.cpp engine detection: keep the llamacpp
                # key + the Settings combo in sync with the auto-detected /
                # re-picked model.
                self.config['llamacpp_model'] = model_name
                if hasattr(self, 'llamacpp_model'):
                    combo = self.llamacpp_model
                    idx = combo.findText(model_name)
                    if idx < 0:
                        combo.insertItem(0, model_name)
                        idx = 0
                    combo.setCurrentIndex(idx)
            else:
                ollama_cfg = self.config.get('ollama')
                if not isinstance(ollama_cfg, dict):
                    ollama_cfg = {}
                    self.config['ollama'] = ollama_cfg
                ollama_cfg['model'] = model_name
                # Sync the Settings dropdown so the user SEES the change.
                if hasattr(self, 'ollama_model'):
                    combo = self.ollama_model
                    idx = combo.findText(model_name)
                    if idx < 0:
                        combo.insertItem(0, model_name)
                        idx = 0
                    combo.setCurrentIndex(idx)
            # Persist to disk (save_config now MERGES — this can no longer
            # wipe cloudflare_*/gdrive_* keys).
            self.save_config()
            self.log_message(
                f"✅ LLM model set to '{model_name}' for the rest of the batch "
                f"and saved to Settings.",
                "success"
            )
        except Exception as e:
            self.log_message(f"⚠️ Could not save model choice: {e}", "warning")

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
