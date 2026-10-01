"""WorkerInputsMixin — where a batch's links come from — the Telegram fetch and the import file.

Moved verbatim from gitcurator/gui/processing_worker.py at
v0.25.0 (hygiene pass); processing_worker.py composes this mixin
onto the ProcessingWorker shell. Bodies are byte-identical.
"""


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

_APP_DIR = APP_DIR

from gitcurator.gui.cache_db import CacheDB

from gitcurator.gui.dead_links import DEAD_LINK_THRESHOLD, dead_link_threshold

from gitcurator.gui.link_helpers import clean_url, normalize_url

from gitcurator.gui.link_tracker import LinkTracker

from gitcurator.gui.platform_intake import (
    PLATFORM_INFO,
    _inbox_table_vault,
    classify_platform,
    write_inbox_links_by_platform,
)

from gitcurator.gui.vault_index import VaultIndex, _safe_moc_name

from gitcurator.gui.worker_jobs import _run_telegram_worker


class WorkerInputsMixin:
    # ---- moved verbatim; see module docstring ----
    def _fetch_from_telegram(self):
        # Back up session file before use
        try:
            import shutil
            session_path = 'session.session'
            if os.path.exists(session_path):
                backup_path = session_path + '.bak'
                shutil.copy2(session_path, backup_path)
        except Exception:
            pass  # backup is best-effort

        api_id = self.config.get('telegram_api_id', 0)
        api_hash = self.config.get('telegram_api_hash', '')
        phone = self.config.get('telegram_phone', '')
        proxy = self.config.get('proxy', {})

        if not api_id or not api_hash or not phone:
            self.log_message.emit("Telegram credentials missing.", "error")
            return []

        try:
            # Build config for the subprocess worker (same as test buttons)
            config = {
                'api_id': int(api_id),
                'api_hash': api_hash,
                'phone': phone,
                'proxy': proxy,
                'session_file': 'session',
                'preview_only': False,
            }
            if self.mode == 'telegram_ids':
                from_id = self.range_from
                to_id = self.range_to
                if from_id is None or to_id is None:
                    self.log_message.emit("Invalid message ID range.", "error")
                    return []
                config['from_id'] = int(from_id)
                config['to_id'] = int(to_id)
            else:
                offset_start = self.offset_start
                offset_count = self.offset_count
                if offset_start is None or offset_count is None:
                    self.log_message.emit("Invalid offset parameters.", "error")
                    return []
                config['offset_start'] = int(offset_start)
                config['count'] = int(offset_count)

            # Run the subprocess worker (identical to test.py execution context)
            self.log_message.emit("Starting Telegram fetch via subprocess...", "info")
            result = _run_telegram_worker(config, self.log_message, code_callback=self.request_code)

            if result.get('success'):
                urls = result.get('urls', [])
                non_github = result.get('non_github_urls', [])
                self.log_message.emit(
                    f"📥 Fetched {len(urls)} GitHub URLs + {len(non_github)} non-GitHub links "
                    f"from {result.get('total_messages', 0)} messages.",
                    "info"
                )
                # Add non-GitHub links to the review table
                if non_github:
                    self._create_inbox_notes(non_github, source="Saved")
                # Store non-GitHub links for the summary report
                self._non_github_urls = non_github
                return urls
            else:
                error = result.get('error', 'Unknown error')
                self.log_message.emit(f"Telegram fetch failed: {error}", "error")
                return []

        except Exception as e:
            self.log_message.emit(f"Exception in Telegram fetch: {e}", "error")
            return []

    def _fetch_from_import(self):
        urls = []
        if not self.import_file or not os.path.exists(self.import_file):
            self.log_message.emit("Import file not found.", "error")
            return []
        # v0.29.0 — faithful batch imports (the owner's fidelity test):
        # the file is parsed by core/links.parse_import_text — the SAME
        # link grammar as every other intake — instead of treating each
        # raw line as a URL. Markdown links, bullets, trailing
        # descriptions, scheme-less addresses, http://, www., BOM'd
        # files and duplicates are all handled; lines that contain no
        # recognizable address are REPORTED (never silently dropped).
        try:
            text = _links.read_import_file(self.import_file)
        except Exception as e:
            self.log_message.emit(f"Could not read import file: {e}", "error")
            return []
        parsed = _links.parse_import_text(text)
        github_urls = parsed['github_urls']
        non_github_urls = parsed['website_urls']

        # v23 — No Link Left Behind: non-GitHub URLs are recorded in the
        # inbox table so they are never silently dropped. The processing
        # loop only sees GitHub URLs. (v0.29.0: they now always carry a
        # scheme, so the fetcher and THE LAW's domain check both work.)
        if non_github_urls:
            try:
                self._create_inbox_notes(non_github_urls, source="Import")
            except Exception as e:
                self.log_message.emit(f"⚠️ Failed to record non-GitHub links from import: {e}", "warning")
            # Store for the manifest intake in run()
            self._non_github_urls = non_github_urls

        # Intake bookkeeping — feeds the run report's "N duplicate URL(s)
        # removed during intake" line (Feature 7 counters).
        try:
            self._intake_duplicates = int(parsed['duplicates'])
            self._raw_url_count = int(parsed['raw_count'])
        except Exception:
            pass

        if parsed['duplicates']:
            self.log_message.emit(
                f"🔄 {parsed['duplicates']} duplicate address(es) in the "
                f"file ignored ({parsed['raw_count']} total → "
                f"{len(github_urls) + len(non_github_urls)} unique)",
                "info")
        if parsed['unparsed']:
            self.log_message.emit(
                f"⚠️ {len(parsed['unparsed'])} line(s) with no recognizable "
                "address skipped:", "warning")
            for line in parsed['unparsed'][:20]:
                self.log_message.emit(f"     · {line[:100]}", "warning")
            if len(parsed['unparsed']) > 20:
                self.log_message.emit(
                    f"     … and {len(parsed['unparsed']) - 20} more",
                    "warning")

        self.log_message.emit(
            f"📄 Loaded {len(github_urls)} GitHub URLs + "
            f"{len(non_github_urls)} website URLs from import file.",
            "info")
        if not github_urls and not non_github_urls:
            self.log_message.emit(
                "The import file contained no addresses — only blank "
                "lines and # comments were found.", "warning")
        return github_urls
