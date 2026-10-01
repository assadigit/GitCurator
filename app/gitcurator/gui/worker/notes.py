"""WorkerNotesMixin — writing notes into the vaults — the note body builder, the _missing placeholder, and the _inbox platform notes.

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


class WorkerNotesMixin:
    # ---- moved verbatim; see module docstring ----
    def _build_note(self, url, repo_name, owner, org_name, stars, forks, commit_count,
                    cred_score, org_rep, summary, tags, category_key, confidence,
                    how_it_works, core_value, features, difference, banner_path=None,
                    primary_language="", languages=None, short_summary="",
                    latest_release_date="",
                    quality_issues=None, is_low_quality=False):
        """v30 — Fix (sanitize LLM output into frontmatter, W12/T4): delegated
        to note_builder.build_note. Every value that lands in the YAML
        frontmatter (tags, aliases, org, url, category, languages) is now
        sanitized against YAML injection — the old template interpolated
        raw LLM strings straight into ``tags: [{', '.join(tags)}]``."""
        return _note_builder.build_note(
            url=url, repo_name=repo_name, owner=owner, org_name=org_name,
            stars=stars, forks=forks, commit_count=commit_count,
            cred_score=cred_score, org_rep=org_rep, summary=summary,
            tags=tags, category_key=category_key, confidence=confidence,
            how_it_works=how_it_works, core_value=core_value,
            features=features, difference=difference, banner_path=banner_path,
            primary_language=primary_language, languages=languages,
            short_summary=short_summary,
            latest_release_date=latest_release_date,
            quality_issues=quality_issues,
            is_low_quality=is_low_quality,
        )

    def _rep_to_str(self, rep):
        return _note_builder.rep_to_str(rep)

    def _score_to_rating(self, score):
        return _note_builder.score_to_rating(score)

    def _record_missing_repo(self, url, owner, repo, strikes, cache,
                             threshold):
        """v0.20.0 — write the ``_missing/`` placeholder note for a 404
        repo and immediately confirm-dead the quarantine row.

        The note's ``source:`` frontmatter is the VaultIndex dedupe key —
        with it in the vault the repo stops counting as pending in every
        queue view and never reaches the GitHub API again. Idempotent
        (an existing note is kept); crash-guarded; dry-run records only
        (atomic_write_text gates itself, makedirs via _dryrun).
        Returns the note path, or None when the vault is unset/failed."""
        try:
            if url in getattr(self, '_missing_notes_written', set()):
                return None
            vault_path = self.config.get('vault_path', '')
            note_path = None
            if vault_path:
                from gitcurator.core import note_builder as _nb
                folder = os.path.join(vault_path, '_missing')
                # v0.09.5 — Phase 0 (dry-run): recorded, not performed.
                _dryrun.makedirs(folder, exist_ok=True)
                fname = _storage.safe_filename(
                    f"{owner}_{repo}_missing") + '.md'
                note_path = os.path.join(folder, fname)
                if not os.path.exists(note_path) or _dryrun.is_enabled():
                    _storage.atomic_write_text(
                        note_path,
                        _nb.build_missing_repo_note(url, owner, repo,
                                                    strikes))
                if self._vault_index is not None:
                    self._vault_index.add_url(url, note_path)
            cache.confirm_dead(url, "404 Not Found (missing-repo note)",
                               threshold)
            if not hasattr(self, '_missing_notes_written'):
                self._missing_notes_written = set()
            self._missing_notes_written.add(url)
            return note_path
        except Exception:
            return None

    def _create_inbox_notes(self, non_github_urls, source="Saved"):
        """Classify non-GitHub links by platform and write to per-platform files.

        v25 pre-flight: previously every non-GitHub link landed in a single
        ``_inbox/non_github_links.md`` file. For 200-300 link batches, this
        became an unmanageable wall of mixed-platform URLs. Each platform now
        gets its own .md file (x_twitter_links.md, reddit_links.md, ...).

        The actual work is delegated to the module-level
        ``write_inbox_links_by_platform`` helper so MainWindow.check_bot_queue
        and ProcessingWorker.run() share the exact same code path.

        v0.11.0 — Phase 2: when the Websites pipeline is ON this dead end is
        BYPASSED — the links are processed by core/website_pipeline at the
        end of the batch instead (SPEC §6 Phase 2 routing)."""
        _pipes = (self.config or {}).get('pipelines') or {}
        if _pipes.get('websites', False):
            self.log_message.emit(
                f"🌐 Websites pipeline ON — {len(non_github_urls)} link(s) "
                "will be processed as websites (not written to _inbox).",
                "info")
            return
        # v0.20.0 — the GitHub vault manages ONLY its own domains: the
        # platform tables land in the WEBSITES vault when one is set
        # (fallback: the GitHub vault, the pre-v0.20 behavior, so links
        # are never lost on a vault-less setup).
        write_inbox_links_by_platform(
            _inbox_table_vault(self.config),
            non_github_urls,
            source=source,
            log_callback=self.log_message.emit,
        )
