"""DEAD_LINK_THRESHOLD, dead_link_threshold — moved verbatim from gitcurator/gui/app.py (branch refactor/gui-app-split; see docs/history/REFACTOR_PLAN.md)."""

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

# v0.08 — 404 QUARANTINE THRESHOLD (owner spec: "after 2-3 tries across
# different sessions, system ignore those links"). A GitHub link that 404s
# this many times (counted in cache.db, so the count survives restarts) is
# confirmed dead and silently skipped in EVERY input path.
DEAD_LINK_THRESHOLD = 3

def dead_link_threshold(config=None) -> int:
    """v0.09 (lineage merge) — the quarantine threshold is CONFIGURABLE.

    The v0.08 lineage hardcoded DEAD_LINK_THRESHOLD = 3; the v0.07 lineage
    had a config key (``notfound_strike_threshold``, owner request:
    "auto-ignore after 2–3") with GUI + CLI controls. The merged design
    keeps the v0.08 quarantine machinery but reads the threshold from the
    SAME config key, so the Settings → Dashboard spinbox, ``--strikes N``
    (CLI) and config.json all steer it. Accepts the config dict directly
    (worker/CLI already hold it) or falls back to reading config.json.
    Clamped to >= 2; any error falls back to DEAD_LINK_THRESHOLD."""
    try:
        cfg = config if isinstance(config, dict) else None
        if cfg is None:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r') as _f:
                    cfg = json.load(_f)
        raw = (cfg or {}).get('notfound_strike_threshold',
                              DEAD_LINK_THRESHOLD)
        return max(2, int(raw))
    except (TypeError, ValueError, OSError, json.JSONDecodeError):
        return DEAD_LINK_THRESHOLD
