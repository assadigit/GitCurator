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
__VERSION__ = "32.2 (fix pack: Backup tab vertical scroll + compacted four sections — nothing clips in the fixed 1000x750 window; theme toggle re-themes all three Backup status dots; higher-contrast dark scrollbar; lineage: 401 fallback, MOC sanitizer, always-visible theme toggle, one-click token tester; modular lineage v32)"
import sys as _sys
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
from gitcurator.integrations import vaultseal as _vaultseal
from gitcurator.integrations import goodrepos as _goodrepos
from gitcurator.core.vault import (             # noqa: F401 — back-compat re-export
    VaultIndex, find_obsidian_vaults, _safe_moc_name,
)
from gitcurator.core.cache_db import CacheDB    # noqa: F401
from gitcurator.core.link_tracker import LinkTracker  # noqa: F401
from gitcurator.core.inbox import (             # noqa: F401 — back-compat re-export
    PLATFORM_INFO, classify_platform, write_inbox_links_by_platform,
)
from gitcurator.utils.logging_setup import setup_logging  # noqa: F401
from gitcurator.utils.terminal import Fore, Style, colorama  # noqa: F401
from gitcurator.gui.log_handler import (          # noqa: F401 — back-compat re-export
    _GuiLogHandler, _install_gui_log_handler, _remove_gui_log_handler,
)
from gitcurator.integrations.telegram_jobs import (  # noqa: F401
    _run_telegram_worker, _telegram_test_job, _telegram_preview_job,
    _telegram_single_job, _telegram_keyword_job, _bot_queue_job,
)
from gitcurator.gui.workers import (               # noqa: F401 — back-compat re-export
    ProcessingWorker, TestWorker,
)
from gitcurator.gui.main_window import MainWindow  # noqa: F401

# Historical name kept: the few path resolutions below that used to point
# at the flat main.py directory. APP_DIR is the app/ root, so assets/,
# config.json and the subprocess worker keep resolving exactly as before.
_APP_DIR = APP_DIR

# Third-party imports - with graceful handling
from gitcurator.gui._qt import *  # noqa: F401,F403 — guarded PyQt6 import (single site)
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
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

# Import Telethon fetcher with graceful error
try:
    from gitcurator.integrations.telethon_fetcher import fetch_github_urls_sync, TelegramFetcherError
except ImportError as e:
    print(f"Failed to import telethon_fetcher: {e}")
    print("Make sure telethon is installed: pip install telethon")
    def fetch_github_urls_sync(*args, **kwargs):
        return {"success": False, "error": "Telethon not installed"}
    TelegramFetcherError = Exception

# v28 — Cloudflare bot sync + Google Drive backup (optional, graceful if missing)
try:
    from gitcurator.cloud.cloudflare_manager import CloudflareManager
    from gitcurator.integrations.error_reporter import ErrorReporter, SEVERITY_CRITICAL, SEVERITY_WARNING, SEVERITY_INFO, SEVERITY_DEBUG
    _CLOUDFLARE_AVAILABLE = True
except ImportError as e:
    _CLOUDFLARE_AVAILABLE = False
    print(f"[WARN] Cloudflare modules not available: {e}")
    print("[WARN] Bot sync + GDrive backup disabled. Install cloudflare_sync.py, cloudflare_manager.py, error_reporter.py, gdrive_backup.py")

try:
    from gitcurator.cloud.gdrive_backup import GDriveBackup
    _GDRIVE_AVAILABLE = True
except ImportError:
    _GDRIVE_AVAILABLE = False

# ============================================================================
# Utility
# ============================================================================

def extract_github_urls(text: str) -> List[str]:
    """v30 — Fix (Standardize link parsing): delegated to links.py, the single
    source of truth. The old inline regex here (no-www, no-dots) silently
    dropped valid repos like github.com/john.doe/my.project that the
    telegram worker / backfill manager DID capture — the same message
    produced different link sets depending on which module parsed it."""
    return _links.extract_github_urls(text)

def clean_url(url: str) -> str:
    return _links.clean_url(url)


def normalize_url(url: str) -> str:
    """v30 — delegated to links.normalize_url (single implementation).
    - Lowercase domain
    - Replace twitter.com with x.com
    - Strip query params (?s=20, ?ref=...)
    - Strip #fragment
    - Strip trailing /
    """
    return _links.normalize_url(url)


# Main Entry Point
# ============================================================================

def run_headless(args):
    """Run the app in headless mode (no GUI) for CLI/scripting use.

    Usage:
        python main.py --headless --from-id 123 --to-id 456 --vault "/path/to/vault" --config "config.json"
        python main.py --headless --import-file "urls.txt" --vault "/path/to/vault" --config "config.json"
        python main.py --headless --single-id 12345 --vault "/path/to/vault"
    """
    import argparse

    parser = argparse.ArgumentParser(description="GitHub Project Curator (headless mode)")
    parser.add_argument('--headless', action='store_true', help='Run without GUI')
    parser.add_argument('--from-id', type=int, help='Start message ID (Telegram range mode)')
    parser.add_argument('--to-id', type=int, help='End message ID (Telegram range mode)')
    parser.add_argument('--offset-start', type=int, help='Offset start ID (Telegram offset mode)')
    parser.add_argument('--count', type=int, help='Number of messages (offset mode)')
    parser.add_argument('--import-file', type=str, help='Path to .txt file with URLs')
    parser.add_argument('--vault', type=str, required=True, help='Obsidian vault path')
    parser.add_argument('--config', type=str, default='config.json', help='Config file path')
    parser.add_argument('--single-id', type=int, help='Single Telegram message ID')
    parsed = parser.parse_args(args)

    # Load config
    config_path = parsed.config
    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            config = json.load(f)
    else:
        print(f"Config file not found: {config_path}")
        return 1

    # Override vault path
    config['vault_path'] = parsed.vault

    print(f"[headless] Config loaded from {config_path}")
    print(f"[headless] Vault: {parsed.vault}")

    # Determine mode
    if parsed.import_file:
        mode = 'import'
        import_file = parsed.import_file
        range_from = range_to = offset_start = offset_count = None
        urls = None
        print(f"[headless] Mode: import from file: {import_file}")
    elif parsed.single_id:
        print(f"[headless] Mode: single message ID {parsed.single_id}")
        proxy = config.get('proxy', {})
        api_id = config.get('telegram_api_id', 0)
        api_hash = config.get('telegram_api_hash', '')
        phone = config.get('telegram_phone', '')
        result = fetch_github_urls_sync(
            api_id=api_id, api_hash=api_hash, phone=phone,
            proxy=proxy, from_id=parsed.single_id, to_id=parsed.single_id,
            preview_only=False
        )
        if not result.get('success'):
            print(f"[headless] Failed to fetch: {result.get('error')}")
            return 1
        urls = result.get('urls', [])
        mode = 'direct'
        import_file = None
        range_from = range_to = offset_start = offset_count = None
        print(f"[headless] Fetched {len(urls)} URLs from message {parsed.single_id}")
    elif parsed.from_id is not None and parsed.to_id is not None:
        mode = 'telegram_ids'
        range_from = parsed.from_id
        range_to = parsed.to_id
        offset_start = offset_count = None
        import_file = None
        urls = None
        print(f"[headless] Mode: Telegram range {range_from} to {range_to}")
    elif parsed.offset_start is not None and parsed.count is not None:
        mode = 'telegram_offset'
        offset_start = parsed.offset_start
        offset_count = parsed.count
        range_from = range_to = None
        import_file = None
        urls = None
        print(f"[headless] Mode: Telegram offset {offset_start} count {offset_count}")
    else:
        print("[headless] Error: must specify --from-id + --to-id, --offset-start + --count, --import-file, or --single-id")
        return 1

    # Create a QCoreApplication so QThread works without a GUI
    app = QCoreApplication(sys.argv)

    worker = ProcessingWorker(
        config=config,
        mode=mode,
        range_from=range_from,
        range_to=range_to,
        offset_start=offset_start,
        offset_count=offset_count,
        import_file=import_file,
        urls=urls,
        headless=True,  # v30 — Fix (headless hang-bombs): non-blocking defaults
    )

    def _log(msg, level):
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] [{level}] {msg}")

    def _on_finished(success, message):
        # v31 — VaultSeal: post-run vault backup (best-effort, never raises,
        # runs before the exit print so it can never be cut off). Runs for
        # failed batches too — notes written before a mid-run failure are
        # exactly what we want backed up.
        try:
            vs_summary = {
                "processed": int(getattr(worker, 'processed', 0) or 0),
                "total": int(getattr(worker, 'total', 0) or 0),
            }
            vs_result = _vaultseal.seal_from_config(config, run_summary=vs_summary)
            print(f"[headless] VaultSeal: {vs_result.describe()}")
        except Exception as seal_err:
            print(f"[headless] VaultSeal error: {seal_err}")
        # v32 — GoodRepos: publish the PUBLIC curated directory. Best-effort,
        # never raises — whatever notes exist deserve publication.
        try:
            gr_result = _goodrepos.publish_from_config(config, run_summary=vs_summary)
            print(f"[headless] Good Repos: {gr_result.describe()}")
        except Exception as good_err:
            print(f"[headless] Good Repos error: {good_err}")
        if success:
            print(f"[headless] DONE: {message}")
        else:
            print(f"[headless] ERROR: {message}")
        app.quit()

    # v30 — Fix (headless hang-bombs): belt-and-suspenders receivers for the
    # three signals that previously had NO receiver in headless mode. The
    # worker short-circuits before emitting them (self._headless checks), but
    # these guarantee no emission is ever silently dropped.
    worker.code_requested.connect(
        lambda pt: print(f"[headless] Telegram {pt} requested — no GUI available; the worker fails fast.")
    )
    worker.disk_full_signal.connect(
        lambda p: print(f"[headless] Disk full at {p} — repo skipped and recorded in retry queue.")
    )
    worker.llm_failed_signal.connect(
        lambda repo: print(f"[headless] LLM failed for {repo} — fallback note written, batch continues.")
    )

    def _on_model_changed_headless(provider, model):
        # v30 — persist model auto-switches in headless mode too: the worker
        # already mutated the shared `config` dict in place; write it back.
        print(f"[headless] LLM model switched to '{model}' ({provider}) — saving config.")
        try:
            _storage.write_config_file(config_path, config)
        except Exception as e:
            print(f"[headless] Could not save config: {e}")

    worker.model_changed.connect(_on_model_changed_headless)

    worker.log_message.connect(_log)
    worker.finished_signal.connect(_on_finished)
    worker.start()

    return app.exec()


def _is_process_running(pid):
    """Check if a process with the given PID is running AND is a Python process.
    Windows recycles PIDs aggressively, so we verify the process name too."""
    try:
        if sys.platform == 'win32':
            import ctypes
            from ctypes import wintypes
            kernel32 = ctypes.windll.kernel32

            # Open the process with QUERY_INFORMATION + SYNCHRONIZE
            PROCESS_QUERY_INFORMATION = 0x0400
            SYNCHRONIZE = 0x00100000
            handle = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | SYNCHRONIZE, False, pid)
            if not handle:
                return False  # Can't open = process doesn't exist or no access

            try:
                # Get the executable name
                max_path = 260
                buf = ctypes.create_unicode_buffer(max_path)
                # Try QueryFullProcessImageName (more reliable)
                if hasattr(kernel32, 'QueryFullProcessImageNameW'):
                    kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(ctypes.c_uint(max_path)))
                    exe_path = buf.value.lower()
                    # Only treat as "our" process if it's a Python executable
                    if 'python' in exe_path or 'pythonw' in exe_path:
                        return True
                    return False  # PID exists but it's not Python — recycled PID
                return True  # Can't check name — assume running
            finally:
                kernel32.CloseHandle(handle)
        else:
            # Unix: os.kill(pid, 0) works reliably
            os.kill(pid, 0)
            return True
    except (OSError, ProcessLookupError, ValueError, Exception):
        return False

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
