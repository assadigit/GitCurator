"""_import_telethon_fetcher, fetch_github_urls_sync, TelegramFetcherError — moved verbatim from gitcurator/gui/app.py (branch refactor/gui-app-split; see REFACTOR_PLAN.md at the repo root)."""

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

_APP_DIR = APP_DIR

def _import_telethon_fetcher():
    """Import the telethon fetcher on demand. Returns (fn, error_cls),
    where fn is a failing stub when telethon is not installed."""
    try:
        from gitcurator.integrations.telethon_fetcher import (
            fetch_github_urls_sync, TelegramFetcherError,
        )
        return fetch_github_urls_sync, TelegramFetcherError
    except ImportError as e:
        print(f"Failed to import telethon_fetcher: {e}")
        print("Make sure telethon is installed: pip install telethon")
        def _stub(*args, **kwargs):
            return {"success": False, "error": "Telethon not installed"}
        return _stub, Exception

def fetch_github_urls_sync(*args, **kwargs):
    """Lazy proxy — resolves telethon on first call (headless single-id only)."""
    fn, _ = _import_telethon_fetcher()
    return fn(*args, **kwargs)

TelegramFetcherError = Exception  # lazily replaced by the real class on use
