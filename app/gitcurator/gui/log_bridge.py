"""_GuiLogHandler, _install_gui_log_handler, _remove_gui_log_handler — moved verbatim from gitcurator/gui/app.py (branch refactor/gui-app-split; see REFACTOR_PLAN.md at the repo root)."""

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

class _GuiLogHandler(logging.Handler):
    """Bridges Python logging -> Qt signal so the fetcher's internal log
    lines (session path, proxy tuple, attempt details) appear in the GUI log,
    not just the terminal."""
    def __init__(self, log_signal):
        super().__init__()
        self._log_signal = log_signal

    def emit(self, record):
        try:
            msg = self.format(record)
            level = record.levelname.lower()
            if level == 'warning':
                level = 'warning'
            elif level in ('error', 'critical'):
                level = 'error'
            else:
                level = 'info'
            self._log_signal.emit(msg, level)
        except Exception:
            pass

def _install_gui_log_handler(log_signal):
    """Attach a _GuiLogHandler to the root logger for the duration of a job.
    Returns the handler so it can be removed afterwards.

    CRITICAL: also set the root logger's LEVEL to INFO. By default the root
    logger level is WARNING, which means logger.info() calls are silently
    dropped BEFORE they ever reach the handler. This was why the Session/Proxy/
    Attempt diagnostics weren't appearing in the GUI log."""
    handler = _GuiLogHandler(log_signal)
    handler.setLevel(logging.INFO)
    root = logging.getLogger()
    root.setLevel(logging.INFO)   # <-- THIS WAS MISSING
    root.addHandler(handler)
    return handler

def _remove_gui_log_handler(handler):
    try:
        logging.getLogger().removeHandler(handler)
    except Exception:
        pass
