"""extract_github_urls, clean_url, normalize_url — moved verbatim from gitcurator/gui/app.py (branch refactor/gui-app-split; see REFACTOR_PLAN.md at the repo root)."""

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
