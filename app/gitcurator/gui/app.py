#!/usr/bin/env python3
"""
GitHub Project Curator — application facade (v32.3 modular layout).

Historical note (v26 root-cause analysis, still true of
``integrations/telegram_jobs.py``): the Telegram fetch runs in a SEPARATE
process (telegram_fetch_worker.py) via subprocess, never in a QThread —
running it in a QThread hit ProactorEventLoop IOCP deadlocks
(WinError 121) or python-socks proxy bypass (Errno 10060). The worker
process runs in the main thread with the default event loop, exactly
like tools/test.py.

This module is now a thin BACK-COMPAT FACADE: it re-exports the public
surface the 9,770-line monolith used to expose, so every historical
import keeps working (main.py does ``from gitcurator.gui.app import
main``). The real implementations live in:

    gitcurator/cli.py               run_headless, _is_process_running, main
    gitcurator/gui/main_window.py   MainWindow (the PyQt6 GUI)
    gitcurator/gui/workers.py       ProcessingWorker, TestWorker
    gitcurator/gui/log_handler.py   logging -> Qt signal bridge
    gitcurator/gui/_qt.py           the guarded PyQt6 import (single site)
    gitcurator/core/                links · storage · note_builder · llm_client
                                    vault · cache_db · link_tracker · inbox
    gitcurator/utils/               logging_setup · terminal
    gitcurator/integrations/        telegram_jobs · vaultseal · goodrepos · …

The URL helper delegates below stay here (they are one-line wrappers
around core.links, kept for the historical call surface).
"""

# === VERSION STAMP - printed at import so you can verify the right file loads ===
__VERSION__ = "32.2 (fix pack: Backup tab vertical scroll + compacted four sections — nothing clips in the fixed 1000x750 window; theme toggle re-themes all three Backup status dots; higher-contrast dark scrollbar; lineage: 401 fallback, MOC sanitizer, always-visible theme toggle, one-click token tester; modular lineage v32)"
import sys as _sys
print(f"[main] LOADED version {__VERSION__} from {__file__}", file=_sys.stderr, flush=True)
# === END VERSION STAMP ===

import sys
import os
from typing import List

# v32 — modular package layout. Keep the app/ root on sys.path so
# `import gitcurator` resolves both when launched via the thin main.py
# shim and when this file is run directly.
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
from gitcurator.cli import main, run_headless, _is_process_running  # noqa: F401

# Historical name kept: the few path resolutions below that used to point
# at the flat main.py directory. APP_DIR is the app/ root, so assets/,
# config.json and the subprocess worker keep resolving exactly as before.
_APP_DIR = APP_DIR

# Third-party imports - with graceful handling (kept verbatim so direct
# importers of gitcurator.gui.app keep receiving the same names/messages;
# the modules above run their own copies first)
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
# Utility (historical one-line delegates — kept for the import surface)
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


if __name__ == "__main__":
    main()
