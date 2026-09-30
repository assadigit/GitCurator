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
from gitcurator.gui.main_window.lifecycle import LifecycleMixin
from gitcurator.gui.main_window.backup_seal import BackupSealMixin
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


class MainWindow(LifecycleMixin, BackupSealMixin, BotQueueMixin, DashboardMixin, ProcessingControlMixin, TelegramUiMixin, InputProxyMixin, VaultConfigMixin, TestConnectionModalMixin, Phase6Mixin, LlamaCppMixin, ConnectionTestsMixin, HeroMixin, UiMixin, ThemeMixin, QMainWindow):
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


    # ========================================================================
    # v31 — VaultSeal (post-run vault backup to a private GitHub repo)
    # ========================================================================


    # ========================================================================
    # v32 — GoodRepos (post-run publish of the PUBLIC curated directory)
    # ========================================================================


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
