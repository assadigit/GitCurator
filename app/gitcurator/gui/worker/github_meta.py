"""WorkerGithubMetaMixin — GitHub-side enrichment of a repo note — org reputation, banner download, credibility score, and the _missing-notes backfill.

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
from gitcurator.core import netctx as _netctx
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


class WorkerGithubMetaMixin:
    # ---- moved verbatim; see module docstring ----
    def _get_org_reputation(self, org):
        major = [
            "microsoft", "google", "nvidia", "anthropic", "openai",
            "meta", "amazon", "apple", "ibm", "intel",
            "cloudflare", "aws", "azure", "googlecloud", "gcp",
            "netflix", "uber", "airbnb", "spotify", "twitter", "facebook",
            "github", "gitlab", "docker", "kubernetes", "linux", "redhat"
        ]
        mid = [
            "huggingface", "cohere", "together", "replit", "cursor",
            "vercel", "netlify", "railway", "flyio", "render",
            "supabase", "firebase", "mongodb", "elastic", "datadog"
        ]
        org_lower = org.lower()
        if any(m in org_lower for m in major):
            return 10
        elif any(m in org_lower for m in mid):
            return 7
        elif " " in org and len(org) > 3:
            return 5
        else:
            return 3

    def _download_banner(self, owner, repo_name, folder_path):
        """Download the GitHub social preview banner for a repo.
        Stores banners in a central 'attachments/banners' folder in the vault
        root. Uses retry with backoff for HTTP 429 (rate limit) and caches
        failed downloads to avoid re-trying known failures.
        Returns the local file path if successful, None otherwise."""
        import urllib.request
        import time as _time

        # v25 pre-flight: throttle banner downloads for large batches.
        # opengraph.githubassets.com returns 429 aggressively when we hammer
        # it 200+ times in quick succession. Every 10 banners we pause 2s;
        # every 50 banners we pause 5s. These are best-effort — if we're
        # already rate-limited, the existing 429 backoff handles it.
        try:
            self._banner_count += 1
            if self._banner_count % 50 == 0:
                _time.sleep(self.config.get('banner_throttle_50', 5))
            elif self._banner_count % 10 == 0:
                _time.sleep(self.config.get('banner_throttle_10', 2))
        except Exception:
            pass  # throttle is best-effort — never block on it

        vault_root = self.config.get('vault_path', '')
        if not vault_root:
            vault_root = os.path.dirname(folder_path)
        banners_dir = os.path.join(vault_root, "attachments", "banners")
        # v0.09.5 — Phase 0 (dry-run): recorded, not performed.
        _dryrun.makedirs(banners_dir, exist_ok=True)

        url = f"https://opengraph.githubassets.com/1/{owner}/{repo_name}"
        safe_name = re.sub(r'[^a-zA-Z0-9\-_]+', '_', repo_name)
        banner_filename = f"{safe_name}_banner.png"
        banner_path = os.path.join(banners_dir, banner_filename)

        # Skip if already downloaded
        if os.path.exists(banner_path):
            return banner_path

        # Check if this repo previously failed (cache file marker)
        failed_marker = os.path.join(banners_dir, f"{safe_name}_failed.marker")
        if os.path.exists(failed_marker):
            # Don't re-try known failures (marker auto-expires after 24h)
            marker_age = _time.time() - os.path.getmtime(failed_marker)
            if marker_age < 86400:  # 24 hours
                return None
            else:
                try:
                    # v0.09.5 — Phase 0 (dry-run): recorded, not performed.
                    _dryrun.remove(failed_marker)
                except OSError:
                    pass

        ctx = _netctx.outbound_ssl_context(self.config)

        # Retry with backoff for rate limiting (429) and transient errors
        max_retries = 3
        for attempt in range(max_retries):
            try:
                req = urllib.request.Request(url, headers={
                    'User-Agent': 'Mozilla/5.0',
                    'Accept': 'image/png,image/*',
                })
                with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
                    content_type = resp.headers.get('Content-Type', '')
                    if 'image' in content_type:
                        data = resp.read()
                        if len(data) > 1000:
                            # v30 — Fix (atomic writes): banner written via
                            # tempfile + os.replace — a crash mid-write can
                            # no longer leave a truncated .png that the
                            # "already downloaded" check would then treat as
                            # complete forever.
                            _storage.atomic_write_bytes(banner_path, data)
                            return banner_path
                return None
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    # Rate limited — wait and retry
                    if attempt < max_retries - 1:
                        wait = 5 * (attempt + 1)  # 5s, 10s, 15s
                        self.log_message.emit(
                            f"   ⏳ Banner rate-limited (429), waiting {wait}s before retry {attempt + 2}/{max_retries}...",
                            "warning"
                        )
                        _time.sleep(wait)
                        continue
                    else:
                        # Mark as failed to avoid re-trying
                        try:
                            # v0.09.5 — Phase 0 (dry-run): recorded, not performed.
                            _dryrun.write_text(failed_marker, str(_time.time()))
                        except Exception:
                            pass
                        self.log_message.emit(
                            f"   ⚠️ Banner rate-limited after {max_retries} attempts. Will skip for 24h.",
                            "warning"
                        )
                        return None
                elif e.code == 404:
                    # No banner for this repo — mark as failed (permanent)
                    try:
                        # v0.09.5 — Phase 0 (dry-run): recorded, not performed.
                        _dryrun.write_text(failed_marker, "404")
                    except Exception:
                        pass
                    return None
                else:
                    if attempt < max_retries - 1:
                        _time.sleep(2)
                        continue
                    return None
            except Exception:
                if attempt < max_retries - 1:
                    _time.sleep(2)
                    continue
                return None

        return None

    def _calculate_credibility(self, org_rep, stars, commits, repo):
        if stars > 10000:
            stars_score = 10
        elif stars > 5000:
            stars_score = 8
        elif stars > 1000:
            stars_score = 6
        elif stars > 100:
            stars_score = 4
        elif stars > 10:
            stars_score = 3
        else:
            stars_score = 1

        if commits > 50:
            activity_score = 10
        elif commits > 20:
            activity_score = 7
        elif commits > 5:
            activity_score = 4
        else:
            activity_score = 2

        doc_score = 5
        try:
            if repo.has_issues:
                doc_score += 1
            if repo.license:
                doc_score += 1
            if repo.has_wiki:
                doc_score += 1
            if repo.description and len(repo.description) > 50:
                doc_score += 1
            if repo.get_readme():
                doc_score += 1
        except Exception:
            pass
        doc_score = min(doc_score, 10)

        # Weighted sum on 0-10 scale, then *10 to get 0-100
        raw = (org_rep * 0.4) + (stars_score * 0.25) + (activity_score * 0.2) + (doc_score * 0.15)
        score = round(raw * 10, 1)
        # Famous company special case: minimum 90/100 credibility
        if org_rep >= 10:
            score = max(score, 90.0)
        return score

    def _backfill_missing_notes(self, cache, vault_path, threshold):
        """v0.20.0 — the legacy 404 tail: repos that struck out in earlier
        versions (fail_count below the threshold, no note, no
        confirmation) get their ``_missing`` note NOW, so they stop
        counting as pending and stop burning API calls — the owner's
        "8-9 github addresses that are 404 ... always count them as
        remaining to be processed". Runs once per batch start; idempotent."""
        rows = cache.get_unconfirmed_404s(threshold)
        if not rows:
            return
        written = 0
        for url, strikes in rows:
            if 'github.com/' not in url:
                continue  # defensive: decommissioned rows are github links
            if self._vault_index is not None and self._vault_index.has_url(url):
                # A note already covers it — just confirm the quarantine.
                cache.confirm_dead(
                    url, "404 Not Found (missing-repo note exists)",
                    threshold)
                continue
            tail = url.split('github.com/', 1)[1]
            parts = tail.split('/')
            if len(parts) < 2 or not parts[0] or not parts[1]:
                continue
            owner, repo = parts[0], parts[1]
            if repo.endswith('.git'):
                repo = repo[:-len('.git')]
            if self._record_missing_repo(url, owner, repo, strikes, cache,
                                         threshold):
                written += 1
            else:
                cache.confirm_dead(url, "404 Not Found (backfill)",
                                   threshold)
        if written:
            self.log_message.emit(
                f"🕳️ {written} missing-repo note(s) written (past 404s) — "
                f"those repos no longer count as pending. To re-check one, "
                f"delete its note in _missing/ and reset it in More ▸ View "
                f"404 Quarantine.", "info")
