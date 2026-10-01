"""MainWindow BotQueueMixin — methods moved verbatim from the MainWindow in gitcurator/gui/app.py (branch refactor/gui-app-split; see docs/history/REFACTOR_PLAN.md)."""

import sys as _sys
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

_APP_DIR = APP_DIR

from gitcurator.gui.cache_db import CacheDB

from gitcurator.gui.link_helpers import normalize_url

from gitcurator.gui.platform_intake import _inbox_table_vault, write_inbox_links_by_platform

from gitcurator.gui.processing_worker import TestWorker

from gitcurator.gui.vault_index import VaultIndex

from gitcurator.gui.worker_jobs import _bot_queue_job

class BotQueueMixin:
    """BotQueueMixin"""

    # ------------------------------------------------------------------
    # Sources tab — fetch GitHub URLs from RSS / Reddit .json endpoints
    # (free, no API key needed) and feed them into the normal pipeline.
    # ------------------------------------------------------------------
    def fetch_from_sources(self):
        """Fetch GitHub URLs from an RSS feed or Reddit .json endpoint."""
        import urllib.request
        import ssl
        import xml.etree.ElementTree as ET

        url = self.sources_url.text().strip()
        if not url:
            self.log_message("Please enter a URL.", "warning")
            return

        self.log_message(f"🔍 Fetching from: {url}", "info")
        self.sources_results.clear()

        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
                data = resp.read().decode('utf-8', errors='ignore')

            # Extract GitHub URLs using regex
            pattern = re.compile(r'https?://github\.com/([a-zA-Z0-9\-_]+)/([a-zA-Z0-9\-_]+)')
            urls = []

            if '.json' in url:
                # Reddit JSON
                import json as _json
                data_json = _json.loads(data)
                children = data_json.get('data', {}).get('children', [])
                for child in children:
                    text = child.get('data', {}).get('selftext', '') + ' ' + child.get('data', {}).get('url', '') + ' ' + child.get('data', {}).get('title', '')
                    for owner, repo in pattern.findall(text):
                        u = f"https://github.com/{owner}/{repo}"
                        if u not in urls:
                            urls.append(u)
            else:
                # RSS feed (XML)
                for match in pattern.findall(data):
                    owner, repo = match
                    u = f"https://github.com/{owner}/{repo}"
                    if u not in urls:
                        urls.append(u)

            if urls:
                self._sources_urls = urls
                display = '\n'.join(urls)
                self.sources_results.setPlainText(f"Found {len(urls)} GitHub URLs:\n\n{display}")
                self.log_message(f"✅ Found {len(urls)} GitHub URLs from source", "success")
            else:
                self.sources_results.setPlainText("No GitHub URLs found in the source.")
                self.log_message("⚠️ No GitHub URLs found in the source.", "warning")

        except Exception as e:
            self.log_message(f"❌ Failed to fetch: {e}", "error")
            self.sources_results.setPlainText(f"Error: {e}")

    def process_sources_urls(self):
        """Process the URLs fetched from sources."""
        urls = getattr(self, '_sources_urls', [])
        if not urls:
            self.log_message("No URLs to process. Fetch from a source first.", "warning")
            return
        # v31.1 safety gate: confirm before large batches (>10 items).
        if not self._confirm_batch(len(urls), "the fetched sources"):
            self.log_message("⏹️ Batch cancelled — nothing was processed.", "warning")
            return
        self.log_message(f"🚀 Processing {len(urls)} URLs from sources...", "info")
        self._start_worker_with_urls(urls)

    def check_bot_queue(self, on_done=None):
        """Check the bot's Telegram chat for pending GitHub repos.

        v0.03: optional ``on_done(name, result)`` callback — connected AFTER
        the internal _on_finished handler so it observes the updated
        _bot_queue_urls. Returns False when the check bailed early (busy
        Telegram lock / missing bot username / missing credentials), True
        once the fetch worker actually started."""
        if not self._acquire_telegram_lock("bot_check"):
            return False
        bot_username = self.bot_username.text().strip().lstrip('@')
        if not bot_username:
            self.log_message("❌ Please enter the bot username first.", "error")
            self._release_telegram_lock("bot_check")
            return False
        api_id = self.api_id.text()
        api_hash = self.api_hash.text()
        phone = self.phone.text()
        if not api_id or not api_hash or not phone:
            self.log_message("❌ Telegram credentials required.", "error")
            self._release_telegram_lock("bot_check")
            return False

        proxy = self._get_proxy_dict()
        # v0.07.1 — Fix: the queue check used to spawn the worker silently
        # with the proxy DISABLED (user-side checkbox state) and burn 4
        # cryptic ConnectionRefusedError retries. Warn loudly BEFORE the
        # spawn so the cause is visible in the log next to the failure.
        if not proxy.get('enabled'):
            self.log_message(
                "⚠️ Proxy is NOT enabled — Telegram will try a DIRECT connection "
                "(blocked in Iran). Enable it in Settings → Proxy and re-run.",
                "warning")
        self.save_config()
        self.log_message(f"📬 Checking bot queue (@{bot_username})...", "info")
        self.queue_display.clear()

        # v0.06 — Perf: hand the vault path to the background job so the
        # vault filtering (index rebuild + pending classification) happens
        # on the worker thread — the GUI used to freeze 0.5-5s on EVERY
        # queue check, including the startup auto-check.
        _vault_for_filter = self.vault_combo.currentText()
        if _vault_for_filter and not os.path.isdir(_vault_for_filter):
            _vault_for_filter = ''

        worker = TestWorker(_bot_queue_job, "bot_check",
                            api_id, api_hash, phone, proxy, bot_username, None, None, None)
        def _job(aid, ahash, ph, px, bu, _ignored_log, _ignored_code, _ignored_mark):
            return _bot_queue_job(aid, ahash, ph, px, bu, worker.log_message,
                                  worker.request_code,
                                  vault_path=(_vault_for_filter or None),
                                  blocked_domains=_links.blocked_domains_from_config(self.config),
                                  self_domains=_links.self_domains_from_config(self.config),
                                  # v0.24.1 — Fix (websites never sync): hand
                                  # the WEBSITES vault + pipeline switch to the
                                  # job so the non-GitHub links get the same
                                  # pending classification the GitHub ones
                                  # have always had.
                                  website_vault_path=((self.config.get('website_vault_path') or '').strip() or None),
                                  websites_pipeline_on=bool(((self.config or {}).get('pipelines') or {}).get('websites', False)))
        worker._fn = _job

        worker.log_message.connect(self.log_message)
        worker.code_requested.connect(
            lambda pt, w=worker: self._on_telegram_code_requested(pt, w)
        )

        def _on_finished(name, result):
            if result.get('success'):
                all_urls = result.get('urls', [])
                non_github = result.get('non_github_urls', [])
                self._bot_queue_non_github = non_github
                self._bot_queue_max_id = result.get('max_message_id', 0)
                self._bot_queue_duplicates = result.get('duplicates_removed', 0)
                self._bot_queue_raw_count = result.get('raw_url_count', len(all_urls) + len(non_github))

                # Add non-GitHub links to inbox
                if non_github:
                    # v0.20.0 — the tables land in the WEBSITES vault when
                    # one is set ("the github vault only manages its
                    # domains"); fallback = the GitHub vault.
                    _table_vault = _inbox_table_vault(self.config)
                    if _table_vault:
                        write_inbox_links_by_platform(
                            _table_vault, non_github, source="Bot",
                            log_callback=lambda msg, lvl: self.log_message(msg, lvl),
                        )

                # Q1/Q6: Filter URLs against VaultIndex + decommissioned —
                # only show NOT-in-vault AND NOT-decommissioned as pending.
                # v0.06 — Perf: the filtering already ran on the WORKER
                # thread (see _bot_queue_job); the GUI thread only reads the
                # precomputed fields. The legacy GUI-thread path remains as
                # a fallback for results that arrive unfiltered.
                if 'pending_urls' in result:
                    pending_urls = result.get('pending_urls', [])
                    in_vault_count = result.get('in_vault_count', 0)
                    decommissioned_count = result.get('decommissioned_count', 0)
                    blocked_count = result.get('blocked_count', 0)
                    self_count = result.get('self_count', 0)
                    if result.get('vault_index_count'):
                        self.log_message(
                            f"📚 Vault index: {result['vault_index_count']} notes indexed", "info")
                else:
                    in_vault_count = 0
                    decommissioned_count = 0
                    blocked_count = 0
                    self_count = 0
                    pending_urls = []
                    # v0.20.0 — the legacy GUI-side filter applies the
                    # same blocked-domain bucket as the worker-side one
                    # (v0.21.0: + the self-domain bucket).
                    _blocked_list = _links.blocked_domains_from_config(self.config)
                    _self_list = _links.self_domains_from_config(self.config)
                    vault_path = self.vault_combo.currentText()
                    if vault_path and os.path.isdir(vault_path):
                        try:
                            vi = VaultIndex(vault_path)
                            vi.rebuild(log_signal=None)
                            self.log_message(f"📚 Vault index: {vi.count} notes indexed", "info")

                            # Load CONFIRMED-dead URLs (v0.08 quarantine:
                            # fail_count >= threshold; unconfirmed 1-2
                            # attempt entries stay pending for their retries)
                            try:
                                cache = CacheDB()
                                decommissioned_urls = cache.get_dead_url_set()
                                cache.close()
                            except Exception:
                                decommissioned_urls = set()

                            for url in all_urls:
                                norm = normalize_url(url)
                                if _links.domain_is_blocked(url, _blocked_list):
                                    blocked_count += 1
                                elif _links.domain_is_self(url, _self_list):
                                    self_count += 1
                                elif vi.has_url(url):
                                    in_vault_count += 1
                                elif norm in decommissioned_urls:
                                    decommissioned_count += 1
                                else:
                                    pending_urls.append(url)
                        except Exception as vi_err:
                            self.log_message(f"⚠️ VaultIndex failed, showing all URLs: {vi_err}", "warning")
                            pending_urls = all_urls
                    else:
                        pending_urls = all_urls

                # Store ONLY pending URLs for processing (Q9: progress bar shows only new repos)
                self._bot_queue_urls = pending_urls

                # v0.24.1 — Fix (websites never sync): classify the
                # non-GitHub links against the WEBSITES vault too. The
                # worker thread precomputes it (v0.06 perf rule); this
                # GUI-side fallback only fires for results that arrive
                # unclassified. Without this, "pending" was GitHub-only
                # and the app reported "All caught up" while unprocessed
                # website links waited in the queue forever.
                _websites_on = bool(((self.config or {}).get('pipelines') or {}).get('websites', False))
                _web_vault = (self.config.get('website_vault_path') or '').strip()
                if 'pending_website_urls' in result:
                    pending_websites = list(result.get('pending_website_urls') or [])
                    _websites_in_vault = int(result.get('websites_in_vault_count', 0) or 0)
                    _websites_processed = int(result.get('websites_processed_count', 0) or 0)
                    _websites_dismissed = int(result.get('websites_dismissed_count', 0) or 0)
                    _websites_blocked = int(result.get('websites_blocked_count', 0) or 0)
                    _websites_self = int(result.get('websites_self_count', 0) or 0)
                    if result.get('websites_vault_index_count'):
                        self.log_message(
                            f"📚 Websites vault index: {result['websites_vault_index_count']} notes indexed", "info")
                else:
                    pending_websites = []
                    _websites_in_vault = _websites_processed = 0
                    _websites_dismissed = _websites_blocked = _websites_self = 0
                    if _websites_on and _web_vault and non_github:
                        try:
                            _wvi = VaultIndex(_web_vault, normalizer=_links.normalize_website_url)
                            _wvi.rebuild(log_signal=None)
                            _wstate = None
                            try:
                                _wstate = _website_pipeline.WebsiteStateDB()
                            except Exception:
                                _wstate = None
                            _blocked_list = _links.blocked_domains_from_config(self.config)
                            _self_list = _links.self_domains_from_config(self.config)
                            for _u in non_github:
                                _canon = _links.normalize_website_url(_u)
                                if _links.domain_is_blocked(_u, _blocked_list):
                                    _websites_blocked += 1
                                elif _links.domain_is_self(_u, _self_list):
                                    _websites_self += 1
                                elif _wvi.has_url(_u):
                                    _prior = None
                                    if _wstate is not None:
                                        try:
                                            _prior = _wstate.processed_row(_canon)
                                        except Exception:
                                            _prior = None
                                    if _prior and _prior.get('fetch_status') == 'failed':
                                        pending_websites.append(_u)
                                    else:
                                        _websites_in_vault += 1
                                elif _wstate is not None and _wstate.is_dismissed(_canon):
                                    _websites_dismissed += 1
                                elif _wstate is not None and _wstate.is_processed(_canon):
                                    _websites_processed += 1
                                else:
                                    pending_websites.append(_u)
                            if _wstate is not None:
                                try:
                                    _wstate.close()
                                except Exception:
                                    pass
                        except Exception as _wvi_err:
                            self.log_message(
                                f"⚠️ Websites vault classification failed: {_wvi_err}", "warning")
                            pending_websites = list(non_github)
                self._bot_queue_pending_websites = pending_websites
                _total_pending = len(pending_urls) + len(pending_websites)

                # Update pending badge (Q15). v31.1: zinc + ⏳ — a pending
                # count is routine, NOT an error; red is reserved for failures.
                # v0.24.1: the count now includes pending WEBSITES too.
                if _total_pending:
                    self.pending_badge.setText(f"⏳ {_total_pending} pending")
                    self.pending_badge.setStyleSheet(
                        "background-color: #6C6480; color: white; padding: 4px 8px; "
                        "border-radius: 10px; font-size: 12px; font-weight: bold;"
                    )
                    self.pending_badge.setVisible(True)
                else:
                    self.pending_badge.setText("✅ 0 pending")
                    self.pending_badge.setStyleSheet(
                        "background-color: #B9E3C9; color: #17402B; padding: 4px 8px; "
                        "border-radius: 10px; font-size: 12px; font-weight: bold;"
                    )
                    self.pending_badge.setVisible(True)

                # Build display (Q6: show vault-dedup count)
                # v0.24.1: the queue report now shows the WEBSITES side too —
                # both pipelines' pending state, side by side.
                display = f"📬 Bot Queue Results\n"
                display += f"{'='*50}\n"
                display += f"Total GitHub URLs in bot:  {len(all_urls)}\n"
                display += f"✅ Already in vault:        {in_vault_count}\n"
                display += f"🗑️ Decommissioned (404):    {decommissioned_count}\n"
                if blocked_count:
                    display += f"🚫 Blocked domains:         {blocked_count}\n"
                if self_count:
                    display += f"🔒 Self domains (own bot):  {self_count}\n"
                display += f"⏳ Pending (not in vault):  {len(pending_urls)}\n"
                display += f"🔗 Non-GitHub links:        {len(non_github)}\n"
                if _websites_on and _web_vault and non_github:
                    display += f"{'='*50}\n"
                    display += f"🌐 WEBSITES pipeline (vault: {_web_vault})\n"
                    if result.get('websites_vault_index_count') is not None:
                        display += f"📚 Websites vault notes:    {result.get('websites_vault_index_count', 0)}\n"
                    display += f"✅ Websites in vault:       {_websites_in_vault}\n"
                    display += f"✔️ Websites processed:      {_websites_processed}\n"
                    if _websites_dismissed:
                        display += f"🗑️ Websites dismissed:      {_websites_dismissed}\n"
                    if _websites_blocked:
                        display += f"🚫 Blocked domains:         {_websites_blocked}\n"
                    if _websites_self:
                        display += f"🔒 Self domains (own bot):  {_websites_self}\n"
                    display += f"⏳ Websites pending:        {len(pending_websites)}\n"
                elif non_github:
                    if not _websites_on:
                        display += f"{'='*50}\n"
                        display += (f"⚠️ Websites pipeline is OFF — {len(non_github)} non-GitHub "
                                    "link(s) stay as _inbox rows.\n")
                        display += "   Enable it in Settings → 📁 Vault to process them "
                        display += "into the Websites vault.\n"
                    elif not _web_vault:
                        display += f"{'='*50}\n"
                        display += (f"⚠️ Websites pipeline is ON but no Websites vault is "
                                    "set — non-GitHub link(s) stay as _inbox rows.\n")
                        display += "   Pick one in Settings → 📁 Vault.\n"
                if getattr(self, '_bot_queue_duplicates', 0) > 0:
                    display += f"🔄 Duplicates removed:      {self._bot_queue_duplicates}\n"
                display += f"{'='*50}\n\n"

                if pending_urls:
                    display += "PENDING GITHUB REPOS (need processing):\n"
                    for i, u in enumerate(pending_urls, 1):
                        display += f"  {i}. {u}\n"
                else:
                    display += "🎉 All GitHub repos are already in the vault!\n"
                    display += "Click '✅ Verify All Processed' to confirm.\n"

                if _websites_on and _web_vault:
                    if pending_websites:
                        display += f"\nPENDING WEBSITES ({len(pending_websites)} — need processing):\n"
                        _shown = pending_websites[:50]
                        for i, u in enumerate(_shown, 1):
                            display += f"  {i}. {u}\n"
                        if len(pending_websites) > len(_shown):
                            display += f"  … and {len(pending_websites) - len(_shown)} more\n"
                    else:
                        display += "\n🎉 All non-GitHub links are processed into the Websites vault!\n"
                elif non_github:
                    display += f"\nNON-GITHUB LINKS ({len(non_github)}):\n"
                    display += "(Recorded in _inbox/ per platform)\n"

                self.queue_display.setPlainText(display)

                if _total_pending:
                    self.log_message(
                        f"📬 Queue: {len(pending_urls)} new repo(s) + "
                        f"{len(pending_websites)} website link(s) pending "
                        f"({in_vault_count} repos + "
                        f"{_websites_in_vault + _websites_processed} websites already done)",
                        "success"
                    )
                else:
                    self.log_message(
                        f"📬 Queue: all caught up — {len(all_urls)} GitHub repos "
                        f"and {len(non_github)} non-GitHub link(s) are already "
                        "processed.",
                        "success"
                    )
                    # v0.24.1 — actionable hints instead of a silent "caught up"
                    if result.get('websites_pipeline_off') and non_github:
                        self.log_message(
                            f"ℹ️ {len(non_github)} non-GitHub link(s) are waiting as "
                            "_inbox rows — turn the Websites pipeline ON "
                            "(Settings → 📁 Vault) to curate them into the "
                            "Websites vault.", "info")
                    elif result.get('websites_no_vault') and non_github:
                        self.log_message(
                            f"ℹ️ Websites pipeline is ON but no Websites vault is "
                            "set (Settings → 📁 Vault) — non-GitHub links stay in "
                            "_inbox.", "info")
                    # Hide mark-all-read button until verify passes
                    self.mark_all_read_btn.setVisible(False)
            else:
                self.log_message(f"❌ Bot queue check failed: {result.get('error')}", "error")
                self.queue_display.setPlainText(f"Error: {result.get('error', 'Unknown')}")

        worker.finished_signal.connect(_on_finished)
        if on_done is not None:
            # Connected after _on_finished → runs once _bot_queue_urls is
            # already updated. _keep_worker's _cleanup is connected last, so
            # the Telegram lock is released after on_done, not before.
            worker.finished_signal.connect(on_done)
        self._keep_worker(worker, owner="bot_check")
        worker.start()
        return True

    def process_bot_queue(self):
        """Process all repos in the bot queue.

        v0.24.1 — Fix (websites never sync): a batch now starts when EITHER
        pipeline has pending work. Before this, the guard was GitHub-only —
        with every repo already in the vault (0 pending GitHub URLs) the
        button refused to run, so the non-GitHub links sitting in the queue
        NEVER reached the Websites pipeline (the worker has supported
        websites-only batches since v0.11.0 — see _run_impl's
        "not urls and not (_websites_pipeline_on and _website_links)" —
        but no caller ever started one from the queue flow)."""
        urls = getattr(self, '_bot_queue_urls', [])
        websites_pending = getattr(self, '_bot_queue_pending_websites', []) or []
        if not urls and not websites_pending:
            self.log_message("No items in queue. Click 'Check Queue' first.", "warning")
            return
        # v31.1 safety gate: confirm before large batches (>10 items).
        # v0.24.1: the count covers BOTH pipelines' pending items.
        if not self._confirm_batch(len(urls) + len(websites_pending), "the bot queue"):
            self.log_message("⏹️ Batch cancelled — nothing was processed.", "warning")
            return
        if urls and websites_pending:
            self.log_message(
                f"🚀 Processing {len(urls)} repo(s) + {len(websites_pending)} "
                f"website link(s) from bot queue...", "info")
        elif urls:
            self.log_message(f"🚀 Processing {len(urls)} repos from bot queue...", "info")
        else:
            self.log_message(
                f"🚀 Processing {len(websites_pending)} website link(s) from bot "
                "queue (Websites pipeline)...", "info")
        # Process the URLs using the existing pipeline. v23 — pass
        # bot_source=True and the non-GitHub links so the manifest can
        # track every link through the 5-phase pipeline.
        # v25 pre-flight: forward the intake duplicate stats so the final
        # report can show "🔄 N duplicate URL(s) removed".
        # v0.24.1: the full non-GitHub list is passed (the Websites
        # pipeline's own dedupe skips the already-done links) — exactly the
        # payload a GitHub+websites batch has always carried.
        self._start_worker_with_urls(
            urls,
            bot_source=True,
            non_github_urls=getattr(self, '_bot_queue_non_github', []),
            intake_duplicates=getattr(self, '_bot_queue_duplicates', 0),
            raw_url_count=getattr(self, '_bot_queue_raw_count', 0),
        )

    def process_new_bot_queue(self):
        """v25 pre-flight: Fetch only bot messages newer than the last
        successfully-processed message ID, then process them.

        Workflow:
          1. Read ``last_processed_msg_id`` from config (0 on first run).
          2. Call ``_bot_queue_job(min_id=last_processed_msg_id)`` to fetch
             only messages with id > last_processed_msg_id.
          3. If new URLs are found, start the ProcessingWorker with
             ``bot_source=True`` and stash the new max_message_id.
          4. ``processing_finished`` saves the new last_processed_msg_id
             ONLY after the LinkTracker verifies all links (Phase 5 CLEAR).
             If any link fails verification, the ID is NOT advanced, so the
             next "Process New" run will re-fetch the failed messages and
             retry them.
        """
        if not self._acquire_telegram_lock("bot_process_new"):
            return
        bot_username = self.bot_username.text().strip().lstrip('@')
        if not bot_username:
            self.log_message("❌ Please enter the bot username first.", "error")
            self._release_telegram_lock("bot_process_new")
            return
        api_id = self.api_id.text()
        api_hash = self.api_hash.text()
        phone = self.phone.text()
        if not api_id or not api_hash or not phone:
            self.log_message("❌ Telegram credentials required.", "error")
            self._release_telegram_lock("bot_process_new")
            return

        proxy = self._get_proxy_dict()
        last_id = int(self.config.get('last_processed_msg_id', 0) or 0)
        self.log_message(
            f"📬 Process New: fetching messages newer than ID {last_id}...",
            "info"
        )

        worker = TestWorker(_bot_queue_job, "bot_process_new",
                            api_id, api_hash, phone, proxy, bot_username, None, None, None)
        def _job(aid, ahash, ph, px, bu, _ignored_log, _ignored_code, _ignored_mark):
            return _bot_queue_job(aid, ahash, ph, px, bu, worker.log_message,
                                  worker.request_code, mark_read=False, min_id=last_id)
        worker._fn = _job

        worker.log_message.connect(self.log_message)
        worker.code_requested.connect(
            lambda pt, w=worker: self._on_telegram_code_requested(pt, w)
        )

        def _on_finished(name, result):
            # v0.06 — release early (owner-scoped) so the user can run other
            # Telegram ops while the direct-mode batch processes the URLs.
            self._release_telegram_lock("bot_process_new")
            if not result.get('success'):
                self.log_message(f"❌ Process New failed: {result.get('error')}", "error")
                return
            urls = result.get('urls', [])
            non_github = result.get('non_github_urls', [])
            max_id = result.get('max_message_id', 0)
            dupes = result.get('duplicates_removed', 0)
            raw = result.get('raw_url_count', len(urls) + len(non_github))

            if not urls and not non_github:
                self.log_message(
                    f"✅ All caught up — no new messages since ID {last_id}.",
                    "success"
                )
                self.queue_display.setPlainText(
                    f"📬 No new messages since last processed ID {last_id}.\n\n"
                    f"Last processed message ID: {last_id}\n"
                    f"Forward new GitHub repo URLs to your bot, then click "
                    f"'📬 Process New' again."
                )
                return

            # Stash for processing_finished to commit after verification.
            self._bot_queue_urls = urls
            self._bot_queue_non_github = non_github
            self._bot_queue_max_id = max_id
            self._bot_queue_duplicates = dupes
            self._bot_queue_raw_count = raw
            # v25 pre-flight: this flag tells processing_finished to advance
            # last_processed_msg_id after Phase 5 CLEAR passes.
            self._pending_last_processed_update = max_id

            display = (
                f"📬 Process New: {len(urls)} new GitHub repos + "
                f"{len(non_github)} non-GitHub links (since ID {last_id})\n\n"
            )
            if dupes > 0:
                display += (
                    f"🔄 {dupes} duplicate URL(s) removed "
                    f"({len(urls) + len(non_github)} unique from {raw} total)\n\n"
                )
            if urls:
                display += "GITHUB REPOS:\n"
                for i, u in enumerate(urls, 1):
                    display += f"  {i}. {u}\n"
            if non_github:
                display += "\nNON-GITHUB LINKS:\n"
                for i, u in enumerate(non_github, 1):
                    display += f"  {i}. {u}\n"
            self.queue_display.setPlainText(display)

            self.log_message(
                f"🚀 Process New: starting batch of {len(urls)} GitHub repos...",
                "info"
            )
            # Record non-GitHub links to per-platform inbox files immediately
            # (the worker's Phase 1 intake will also do this, but doing it
            # now means the links are safe even if the user cancels before
            # the worker's intake runs).
            if non_github:
                # v0.20.0 — the tables land in the WEBSITES vault when one
                # is set ("the github vault only manages its domains").
                _table_vault = _inbox_table_vault(self.config)
                if _table_vault:
                    write_inbox_links_by_platform(
                        _table_vault, non_github, source="Bot",
                        log_callback=lambda msg, lvl: self.log_message(msg, lvl),
                    )

            # Start the worker. processing_finished will advance
            # last_processed_msg_id to self._pending_last_processed_update
            # ONLY if Phase 5 CLEAR passes (all links verified).
            # v31.1 safety gate: confirm before large batches (>10 items).
            # v0.24.1: the count covers repos AND website links.
            if not self._confirm_batch(len(urls) + len(non_github), "the new bot messages"):
                self.log_message("⏹️ Batch cancelled — nothing was processed.", "warning")
                return
            self._start_worker_with_urls(
                urls,
                bot_source=True,
                non_github_urls=non_github,
                intake_duplicates=dupes,
                raw_url_count=raw,
            )

        worker.finished_signal.connect(_on_finished)
        self._keep_worker(worker, owner="bot_process_new")
        worker.start()

    def export_all_bot_links(self):
        """Fetch ALL links from the bot and save to a file for manual verification.
        This lets the user compare what the app found vs what they actually forwarded."""
        if not self._acquire_telegram_lock("bot_export"):
            return
        bot_username = self.bot_username.text().strip().lstrip('@')
        if not bot_username:
            self.log_message("❌ Please enter the bot username first.", "error")
            self._release_telegram_lock("bot_export")
            return
        api_id = self.api_id.text()
        api_hash = self.api_hash.text()
        phone = self.phone.text()
        if not api_id or not api_hash or not phone:
            self.log_message("❌ Telegram credentials required.", "error")
            self._release_telegram_lock("bot_export")
            return

        proxy = self._get_proxy_dict()
        vault = self.vault_combo.currentText()
        if not vault:
            self.log_message("❌ No vault selected — need a place to save the export.", "error")
            self._release_telegram_lock("bot_export")
            return

        self.log_message("📋 Exporting ALL links from bot (no limit)...", "info")

        worker = TestWorker(_bot_queue_job, "export_links",
                            api_id, api_hash, phone, proxy, bot_username, None, None, None)
        def _job(aid, ahash, ph, px, bu, _ignored_log, _ignored_code, _ignored_mark):
            return _bot_queue_job(aid, ahash, ph, px, bu, worker.log_message, worker.request_code)
        worker._fn = _job

        worker.log_message.connect(self.log_message)
        worker.code_requested.connect(
            lambda pt, w=worker: self._on_telegram_code_requested(pt, w)
        )

        def _on_finished(name, result):
            if result.get('success'):
                urls = result.get('urls', [])
                non_github = result.get('non_github_urls', [])
                total_msgs = result.get('total_messages', 0)
                raw_count = result.get('raw_url_count', 0)
                dups = result.get('duplicates_removed', 0)

                # Save to vault as a verification file
                export_path = os.path.join(vault, f"_bot_links_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md")
                lines = []
                lines.append(f"# 📋 Bot Links Export — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
                lines.append("")
                lines.append("## 📊 Statistics")
                lines.append(f"- 📬 Total messages in bot: **{total_msgs}**")
                lines.append(f"- 🔗 Total raw links found: **{raw_count}**")
                lines.append(f"- 🔄 Duplicates removed: **{dups}**")
                lines.append(f"- ✅ Unique GitHub repos: **{len(urls)}**")
                lines.append(f"- ✅ Unique non-GitHub links: **{len(non_github)}**")
                lines.append(f"- 📝 Total unique links: **{len(urls) + len(non_github)}**")
                lines.append("")
                lines.append("## 🐙 GitHub Repos")
                lines.append("")
                for i, url in enumerate(urls, 1):
                    lines.append(f"{i}. {url}")
                lines.append("")
                lines.append("## 🔗 Non-GitHub Links")
                lines.append("")
                for i, url in enumerate(non_github, 1):
                    lines.append(f"{i}. {url}")
                lines.append("")
                lines.append("---")
                lines.append(f"*Export generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")
                lines.append(f"*Compare this list with what you forwarded to the bot to verify no links are missing.*")

                try:
                    with open(export_path, 'w', encoding='utf-8') as f:
                        f.write('\n'.join(lines))
                    self.log_message(f"📋 Export saved: {export_path}", "success")
                    self.log_message(f"📊 Total messages: {total_msgs} | GitHub: {len(urls)} | Non-GitHub: {len(non_github)} | Duplicates: {dups}", "info")
                    self._show_custom_message_box(
                        "📋 Links Exported",
                        f"Total messages: {total_msgs}\n"
                        f"GitHub repos: {len(urls)}\n"
                        f"Non-GitHub: {len(non_github)}\n"
                        f"Duplicates removed: {dups}\n\n"
                        f"Export saved to:\n{os.path.basename(export_path)}\n\n"
                        f"Open this file in Obsidian to verify all links are accounted for.",
                        success=True
                    )
                except Exception as e:
                    self.log_message(f"❌ Failed to save export: {e}", "error")
            else:
                self.log_message(f"❌ Export failed: {result.get('error')}", "error")

        worker.finished_signal.connect(_on_finished)
        self._keep_worker(worker, owner="bot_export")
        worker.start()

    def verify_all_bot_links(self):
        """v26 — Fix 6: Verify that ALL bot links have been processed into
        the vault.

        Fetches ALL GitHub URLs from the bot (using the same ``_bot_queue_job``
        worker as ``export_all_bot_links``), then checks each GitHub URL
        against the in-memory ``VaultIndex``. Does NOT process anything —
        just reports:
          - Total GitHub links in bot
          - Found in vault ✅
          - Missing from vault ⏳ (with the list of missing URLs)

        The report is shown in the Bot tab's ``queue_display`` text area so
        the user can review it without switching tabs. The whole method is
        wrapped in a try/except so any crash (Telegram auth failure, vault
        read error, etc.) is logged instead of taking down the app."""
        try:
            if not self._acquire_telegram_lock("bot_verify_all"):
                return
            bot_username = self.bot_username.text().strip().lstrip('@')
            if not bot_username:
                self.log_message("❌ Please enter the bot username first.", "error")
                self._release_telegram_lock("bot_verify_all")
                return
            api_id = self.api_id.text()
            api_hash = self.api_hash.text()
            phone = self.phone.text()
            if not api_id or not api_hash or not phone:
                self.log_message("❌ Telegram credentials required.", "error")
                self._release_telegram_lock("bot_verify_all")
                return

            vault_path = self.vault_combo.currentText()
            if not vault_path or not os.path.isdir(vault_path):
                self.log_message("❌ No vault selected — cannot verify links.", "error")
                self._release_telegram_lock("bot_verify_all")
                return

            proxy = self._get_proxy_dict()
            self.save_config()
            self.log_message("✅ Verifying all bot links against vault...", "info")
            self.queue_display.setPlainText(
                "⏳ Fetching ALL links from bot...\n"
                "(This may take a while for large bot queues.)"
            )

            worker = TestWorker(_bot_queue_job, "bot_verify_all",
                                api_id, api_hash, phone, proxy, bot_username, None, None, None)
            def _job(aid, ahash, ph, px, bu, _ignored_log, _ignored_code, _ignored_mark):
                return _bot_queue_job(aid, ahash, ph, px, bu, worker.log_message, worker.request_code)
            worker._fn = _job

            worker.log_message.connect(self.log_message)
            worker.code_requested.connect(
                lambda pt, w=worker: self._on_telegram_code_requested(pt, w)
            )

            def _on_finished(name, result):
                try:
                    if not result.get('success'):
                        err = result.get('error', 'Unknown error')
                        self.log_message(f"❌ Verify All failed: {err}", "error")
                        self.queue_display.setPlainText(f"Error: {err}")
                        return

                    urls = result.get('urls', [])
                    non_github = result.get('non_github_urls', [])

                    # Build the vault index (ground truth for "is this URL processed?")
                    try:
                        vi = VaultIndex(vault_path)
                        vi.rebuild(log_signal=None)
                        self.log_message(f"📚 Vault index: {vi.count} notes indexed", "info")
                    except Exception as vi_err:
                        self.log_message(f"❌ Failed to build vault index: {vi_err}", "error")
                        self.queue_display.setPlainText(f"Error: {vi_err}")
                        return

                    github_in_vault = 0
                    github_decommissioned = 0
                    github_missing = []

                    # v0.06 — Perf: ONE CacheDB connection for both the
                    # decommissioned set and the processed-URL set (the old
                    # code opened and closed two connections back-to-back).
                    try:
                        cache = CacheDB()
                        # v0.08 — verification counts CONFIRMED-dead links
                        # only; unconfirmed (1-2 attempts) fall through to
                        # the missing/pending buckets where they belong.
                        decommissioned_urls = cache.get_dead_url_set()
                        # v29.10 — Also load processed URLs to catch cases
                        # where the note's source: field has a different URL
                        # format (e.g., embedchain/embedchain was renamed to
                        # mem0ai/mem0 on GitHub)
                        cache_processed_urls = set()
                        for row in cache.get_all_processed_urls():
                            cache_processed_urls.add(row[0] if isinstance(row, tuple) else row)
                        cache.close()
                    except Exception:
                        decommissioned_urls = set()
                        cache_processed_urls = set()

                    for url in urls:
                        try:
                            norm = normalize_url(url)
                            if vi.has_url(url):
                                github_in_vault += 1
                            elif norm in decommissioned_urls:
                                github_decommissioned += 1
                            elif norm in cache_processed_urls or url in cache_processed_urls:
                                # v29.10 — Found in CacheDB (was processed before, even if
                                # the note's source: field has a different URL after rename)
                                github_in_vault += 1
                                self.log_message(f"✅ Found in cache (processed before): {url}", "info")
                            else:
                                # v29.9 — Fuzzy match: extract owner/repo and check if any
                                # vault note has that pair in its source URL or filename.
                                fuzzy_match = self._fuzzy_match_github_url(vi, url)
                                if fuzzy_match:
                                    github_in_vault += 1
                                    self.log_message(f"✅ Fuzzy match: {url} → {os.path.basename(fuzzy_match)}", "info")
                                else:
                                    github_missing.append(url)
                        except Exception:
                            github_missing.append(url)

                    # v29.7 — Log the missing URLs so user knows exactly what to process
                    # v31.1: ⏳ — missing-from-vault is PENDING work, not a failure.
                    if github_missing:
                        self.log_message(f"⏳ {len(github_missing)} missing GitHub link(s):", "warning")
                        for i, url in enumerate(github_missing, 1):
                            self.log_message(f"   {i}. {url}", "info")

                    # Build the human-readable report
                    lines = []
                    lines.append("✅ VERIFICATION REPORT")
                    lines.append("=" * 60)
                    lines.append(f"Total GitHub links in bot:      {len(urls)}")
                    lines.append(f"✅ Found in vault:              {github_in_vault}")
                    lines.append(f"🗑️ Decommissioned (404):        {github_decommissioned}")
                    lines.append(f"⏳ Missing from vault:          {len(github_missing)}")
                    lines.append(f"🔗 Non-GitHub links:            {len(non_github)}")
                    lines.append("")
                    lines.append("ALL GITHUB LINKS — DETAILED CHECK:")
                    lines.append("-" * 60)
                    for i, url in enumerate(urls, 1):
                        try:
                            if vi.has_url(url):
                                note_path = vi.get_path(url)
                                note_name = os.path.basename(note_path) if note_path else "?"
                                lines.append(f"  {i:3d}. ✅ {url}")
                                lines.append(f"       → {note_name}")
                            else:
                                lines.append(f"  {i:3d}. ⏳ {url}")
                        except Exception:
                            lines.append(f"  {i:3d}. ❌ {url} (check error)")
                    lines.append("-" * 60)
                    lines.append("")
                    if github_missing:
                        lines.append(f"SUMMARY: {len(github_missing)} link(s) need processing!")
                        lines.append("")
                        lines.append("To process the missing links:")
                        lines.append("  1. Click '📬 Check Queue' to load them")
                        lines.append("  2. Click '🚀 Process All' to process")
                    else:
                        lines.append("🎉 All GitHub links are in the vault!")
                    lines.append("=" * 60)

                    report_text = '\n'.join(lines)
                    self.queue_display.setPlainText(report_text)

                    # Also save to vault for permanent record
                    try:
                        report_path = os.path.join(vault_path, f"_verification_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md")
                        with open(report_path, 'w', encoding='utf-8') as f:
                            f.write(f"# ✅ Verification Report — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")
                            f.write(f"## 📊 Summary\n\n")
                            f.write(f"| Metric | Count |\n|--------|-------|\n")
                            f.write(f"| 📬 Total GitHub links in bot | {len(urls)} |\n")
                            f.write(f"| ✅ Found in vault | {github_in_vault} |\n")
                            f.write(f"| ⏳ Missing from vault | {len(github_missing)} |\n")
                            f.write(f"| 🔗 Non-GitHub links | {len(non_github)} |\n\n")
                            f.write(f"## 📋 Detailed Check\n\n")
                            f.write(f"| # | Status | URL | Note |\n")
                            f.write(f"|---|--------|-----|------|\n")
                            for i, url in enumerate(urls, 1):
                                try:
                                    if vi.has_url(url):
                                        note_path = vi.get_path(url)
                                        note_name = os.path.basename(note_path) if note_path else "?"
                                        f.write(f"| {i} | ✅ | {url} | {note_name} |\n")
                                    else:
                                        f.write(f"| {i} | ⏳ | {url} | — |\n")
                                except Exception:
                                    f.write(f"| {i} | ❌ | {url} | error |\n")
                            f.write(f"\n---\n*Report generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n")
                        self.log_message(f"📄 Verification report saved to vault: {os.path.basename(report_path)}", "success")
                    except Exception:
                        pass

                    summary = (
                        f"✅ Verified: {github_in_vault}/{len(urls)} GitHub links in vault"
                    )
                    if github_missing:
                        summary += f" ({len(github_missing)} missing)"
                        self.log_message(summary, "warning")
                        # v29.11 — Manual Resolve dialog
                        self._show_manual_resolve_dialog(github_missing)
                    else:
                        # 0 truly missing — decommissioned doesn't count as missing
                        self.log_message(
                            f"✅ Verified: {github_in_vault} in vault + {github_decommissioned} decommissioned = {github_in_vault + github_decommissioned}/{len(urls)} accounted for",
                            "success"
                        )
                        # Q5/Q13: Show Mark All Read button after verify passes
                        self.mark_all_read_btn.setVisible(True)
                        # Ask if user wants to mark all as read
                        if self._show_custom_question(
                            "✅ All Verified!",
                            f"All GitHub links accounted for! 🎉\n\n"
                            f"  ✅ In vault: {github_in_vault}\n"
                            f"  🗑️ Decommissioned: {github_decommissioned}\n"
                            f"  ❌ Missing: {len(github_missing)}\n\n"
                            f"Would you like to mark all bot messages as read now?\n"
                            f"This will clear the bot queue for future batches."
                        ):
                            # v0.06 — Fix (signal-ordering self-deadlock):
                            # this handler runs while the verify worker still
                            # holds the Telegram lock (its cleanup is connected
                            # later), so calling clear_bot_queue() directly was
                            # ALWAYS denied with "another Telegram operation is
                            # already running". Defer one event-loop tick.
                            QTimer.singleShot(0, self.clear_bot_queue)
                except Exception as inner_e:
                    import traceback
                    self.log_message(f"❌ Verify All report failed: {inner_e}", "error")
                    try:
                        self.queue_display.setPlainText(
                            f"Error generating report: {inner_e}\n\n{traceback.format_exc()}"
                        )
                    except Exception:
                        pass

            worker.finished_signal.connect(_on_finished)
            self._keep_worker(worker, owner="bot_verify_all")
            worker.start()
        except Exception as e:
            # v26 — Fix 6: never let Verify All take down the app.
            import traceback
            self.log_message(f"❌ Verify All crashed: {e}", "error")
            try:
                self.queue_display.setPlainText(
                    f"Verify All crashed: {e}\n\n{traceback.format_exc()}"
                )
            except Exception:
                pass
            try:
                self._release_telegram_lock("bot_verify_all")
            except Exception:
                pass

    def _fuzzy_match_github_url(self, vault_index, url):
        """v29.9 — Fuzzy match a GitHub URL against the vault index.
        Extracts owner/repo from the URL and checks if any vault note has
        that pair in its source URL. Catches normalization mismatches."""
        try:
            # Extract owner/repo from the URL
            m = re.match(r'https?://(?:www\.)?github\.com/([a-zA-Z0-9\-_.]+)/([a-zA-Z0-9\-_.]+)', url)
            if not m:
                return None
            owner = m.group(1).lower()
            repo = m.group(2).lower()
            # Strip .git suffix if present
            if repo.endswith('.git'):
                repo = repo[:-4]

            # Check all vault URLs for a match
            for vault_url, path in vault_index._url_to_path.items():
                vm = re.match(r'https?://(?:www\.)?github\.com/([a-zA-Z0-9\-_.]+)/([a-zA-Z0-9\-_.]+)', vault_url)
                if vm:
                    v_owner = vm.group(1).lower()
                    v_repo = vm.group(2).lower()
                    if v_repo.endswith('.git'):
                        v_repo = v_repo[:-4]
                    if v_owner == owner and v_repo == repo:
                        return path
        except Exception:
            pass
        return None

    def _process_missing_urls(self, urls):
        """Process missing GitHub URLs directly (from verify dialog)."""
        if not urls:
            return
        self.log_message(f"🚀 Processing {len(urls)} missing URL(s)...", "info")
        # Store the URLs and trigger processing
        self._bot_queue_urls = urls
        self._bot_source = True
        # Show in queue display
        self.queue_display.setPlainText("\n".join(urls))
        # Start processing (reads from self._bot_queue_urls)
        self.start_processing()

    def _show_manual_resolve_dialog(self, missing_urls):
        """v29.11 — Manual resolve dialog for missing links.
        Lets user: mark as processed, decommission, or process each URL."""
        is_dark = getattr(self, '_dark_mode', False)
        if is_dark:
            bg = "#2B2639"; text_color = "#F2EEE7"; border = "#3B344F"; input_bg = "#241F31"; alt_bg = "#352F4A"
        else:
            bg = "#FFFFFF"; text_color = "#423A52"; border = "#EAE3D6"; input_bg = "#FDFCF8"; alt_bg = "#F2EDE3"

        dialog = QDialog(self)
        dialog.setWindowTitle("🔧 Manual Resolve — Missing Links")
        dialog.setMinimumWidth(700)
        dialog.setMinimumHeight(500)
        dialog.setStyleSheet(
            f"QDialog {{ background-color: {bg}; }} "
            f"QLabel {{ color: {text_color}; }} "
            f"QListWidget {{ background-color: {input_bg}; color: {text_color}; border: 1px solid {border}; border-radius: 4px; }} "
            f"QPushButton {{ padding: 6px 12px; border-radius: 4px; }}"
        )
        layout = QVBoxLayout(dialog)
        layout.setSpacing(10)

        # Header
        # v31.1: ⏳ — missing links are PENDING work awaiting a user
        # decision, not a failure.
        header = QLabel(f"⏳ {len(missing_urls)} GitHub link(s) are missing from the vault.\n"
                        f"For each URL, choose an action:")
        header.setWordWrap(True)
        layout.addWidget(header)

        # URL list
        url_list = QListWidget()
        url_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        for url in missing_urls:
            url_list.addItem(url)
        layout.addWidget(url_list)

        # Action buttons — v31.1 hierarchy: ONE filled primary (Process
        # Selected), outlined secondary (Mark as Processed / Mark ALL), ONE
        # filled danger (Decommission).
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        process_btn = QPushButton("🚀 Process Selected")
        process_btn.setStyleSheet(self._btn_style(COLORS['cta'], COLORS['cta_hover'], text=COLORS['cta_text']))
        def do_process():
            if url_list.currentRow() < 0:
                return
            url = missing_urls[url_list.currentRow()]
            dialog.accept()
            self._process_missing_urls([url])
        process_btn.clicked.connect(do_process)
        btn_row.addWidget(process_btn)

        mark_processed_btn = QPushButton("✅ Mark as Processed")
        mark_processed_btn.setStyleSheet(self._btn_style(COLORS['primary'], COLORS['primary_hover'], variant='outline'))
        def do_mark_processed():
            if url_list.currentRow() < 0:
                return
            url = missing_urls[url_list.currentRow()]
            # Add to CacheDB as processed
            try:
                cache = CacheDB()
                m = re.match(r'https?://(?:www\.)?github\.com/([a-zA-Z0-9\-_.]+)/([a-zA-Z0-9\-_.]+)', url)
                owner = m.group(1) if m else ""
                repo = m.group(2) if m else ""
                cache.add_processed(0, url, owner, repo, "(manual resolve)", "Manual")
                cache.close()
            except Exception:
                pass
            self.log_message(f"✅ Marked as processed (manual): {url}", "success")
            url_list.takeItem(url_list.currentRow())
            if url_list.count() == 0:
                dialog.accept()
                self.log_message("✅ All missing links resolved!", "success")
        mark_processed_btn.clicked.connect(do_mark_processed)
        btn_row.addWidget(mark_processed_btn)

        decomm_btn = QPushButton("🗑️ Decommission")
        decomm_btn.setStyleSheet(self._btn_style(COLORS['error'], COLORS['error_hover'], text=COLORS['error_text']))
        def do_decomm():
            if url_list.currentRow() < 0:
                return
            url = missing_urls[url_list.currentRow()]
            try:
                cache = CacheDB()
                cache.decommission(url, "Manual decommission by user")
                cache.close()
            except Exception:
                pass
            self.log_message(f"🗑️ Decommissioned (manual): {url}", "info")
            url_list.takeItem(url_list.currentRow())
            if url_list.count() == 0:
                dialog.accept()
                self.log_message("✅ All missing links resolved!", "success")
        decomm_btn.clicked.connect(do_decomm)
        btn_row.addWidget(decomm_btn)

        # Mark all as processed
        mark_all_btn = QPushButton("✅ Mark ALL as Processed")
        mark_all_btn.setStyleSheet(self._btn_style(COLORS['primary'], COLORS['primary_hover']))
        def do_mark_all():
            try:
                cache = CacheDB()
                for url in missing_urls:
                    m = re.match(r'https?://(?:www\.)?github\.com/([a-zA-Z0-9\-_.]+)/([a-zA-Z0-9\-_.]+)', url)
                    owner = m.group(1) if m else ""
                    repo = m.group(2) if m else ""
                    cache.add_processed(0, url, owner, repo, "(manual resolve)", "Manual")
                cache.close()
            except Exception:
                pass
            self.log_message(f"✅ Marked {len(missing_urls)} URL(s) as processed (manual)", "success")
            dialog.accept()
        mark_all_btn.clicked.connect(do_mark_all)
        btn_row.addWidget(mark_all_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Close button
        close_btn = QPushButton("Close")
        close_btn.setStyleSheet(self._btn_style(COLORS['neutral'], COLORS['neutral_hover'], variant='outline'))
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn)

        dialog.exec()

    def clear_bot_queue(self):
        """Mark all bot messages as read (clears the queue indicator)."""
        if not self._acquire_telegram_lock("bot_clear"):
            return
        bot_username = self.bot_username.text().strip().lstrip('@')
        if not bot_username:
            self.log_message("❌ Please enter the bot username first.", "error")
            self._release_telegram_lock("bot_clear")
            return
        api_id = self.api_id.text()
        api_hash = self.api_hash.text()
        phone = self.phone.text()
        if not api_id or not api_hash or not phone:
            self.log_message("❌ Telegram credentials required.", "error")
            self._release_telegram_lock("bot_clear")
            return

        proxy = self._get_proxy_dict()
        self.log_message("✓ Marking bot messages as read...", "info")

        worker = TestWorker(_bot_queue_job, "bot_clear",
                            api_id, api_hash, phone, proxy, bot_username, None, None, None)
        def _job(aid, ahash, ph, px, bu, _ignored_log, _ignored_code, _ignored_mark):
            return _bot_queue_job(aid, ahash, ph, px, bu, worker.log_message, worker.request_code, mark_read=True)
        worker._fn = _job

        worker.log_message.connect(self.log_message)
        worker.code_requested.connect(
            lambda pt, w=worker: self._on_telegram_code_requested(pt, w)
        )

        def _on_finished(name, result):
            if result.get('success'):
                self.log_message("✓ Bot queue cleared (all messages marked as read)", "success")
                self.queue_display.clear()
                self.queue_display.setPlainText("Queue cleared. Click 'Check Queue' to fetch new messages.")
            else:
                self.log_message(f"❌ Failed to clear queue: {result.get('error')}", "error")

        worker.finished_signal.connect(_on_finished)
        self._keep_worker(worker, owner="bot_clear")
        worker.start()

    def _mark_bot_messages_read(self):
        """Auto-mark all bot messages as read after a successful bot-queue batch
        (v22 Feature 3: Two-Condition Done Check). Non-blocking — runs in a
        TestWorker. Best-effort: failures are logged but don't disrupt the user."""
        bot_username = self.bot_username.text().strip().lstrip('@')
        if not bot_username:
            self.log_message("⚠️ Cannot auto-mark bot messages: no bot username set.", "warning")
            return
        api_id = self.api_id.text()
        api_hash = self.api_hash.text()
        phone = self.phone.text()
        if not api_id or not api_hash or not phone:
            self.log_message("⚠️ Cannot auto-mark bot messages: Telegram credentials incomplete.", "warning")
            return

        # Acquire the Telegram lock (best-effort — if it's held, skip auto-mark)
        if not self._acquire_telegram_lock("bot_auto_mark_read"):
            self.log_message("⚠️ Cannot auto-mark bot messages: another Telegram operation is running.", "warning")
            return

        proxy = self._get_proxy_dict()
        worker = TestWorker(_bot_queue_job, "bot_auto_mark_read",
                            api_id, api_hash, phone, proxy, bot_username, None, None, None)
        def _job(aid, ahash, ph, px, bu, _ignored_log, _ignored_code, _ignored_mark):
            return _bot_queue_job(aid, ahash, ph, px, bu, worker.log_message, worker.request_code, mark_read=True)
        worker._fn = _job

        worker.log_message.connect(self.log_message)
        worker.code_requested.connect(
            lambda pt, w=worker: self._on_telegram_code_requested(pt, w)
        )

        def _on_finished(name, result):
            if result.get('success'):
                self.log_message("✓ Bot messages auto-marked as read", "success")
                try:
                    self.queue_display.clear()
                    self.queue_display.setPlainText("Queue auto-cleared (all repos processed). Click 'Check Queue' to fetch new messages.")
                except Exception:
                    pass
            else:
                self.log_message(f"⚠️ Auto-mark bot messages failed: {result.get('error')}", "warning")

        worker.finished_signal.connect(_on_finished)
        self._keep_worker(worker, owner="bot_auto_mark_read")
        worker.start()

    def _startup_auto_check(self):
        """Auto-check bot queue on startup — validates proxy first.

        v0.06 — the old "reset the Telegram lock on startup" line was
        removed: the lock object is created fresh in __init__ (it can't be
        stuck), and blindly clearing it 2s after launch could wipe a lock
        legitimately acquired during the first two seconds. The watchdog
        timer now handles genuinely stuck holders."""
        # v0.06 — Fix (zombie process): this fires 2s after launch — if the
        # user already closed the window (or is closing it), never open the
        # modal dialogs below; that invisible modal used to keep the app
        # process alive forever holding app.lock.
        if getattr(self, '_closing', False) or not self.isVisible():
            return
        # Check proxy is enabled
        proxy = self._get_proxy_dict()
        if not proxy.get('enabled'):
            self._show_custom_message_box(
                "Proxy Required",
                "Proxy is not enabled.\n\n"
                "Please enable proxy in the Proxy tab and restart the app.",
                success=False
            )
            return

        # Quick socket check
        import socket
        host = proxy.get('host', '127.0.0.1')
        port = int(proxy.get('port', 10808))
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)
            result = sock.connect_ex((host, port))
            sock.close()
            if result != 0:
                self._show_custom_message_box(
                    "Proxy Unreachable",
                    f"Cannot connect to proxy at {host}:{port}.\n\n"
                    f"Please start v2rayN and restart the app.",
                    success=False
                )
                return
        except Exception:
            self._show_custom_message_box(
                "Proxy Check Failed",
                f"Could not verify proxy at {host}:{port}.\n\n"
                f"Make sure v2rayN is running.",
                success=False
            )
            return

        # Proxy is OK — auto-check bot queue
        bot_username = getattr(self, 'bot_username', QLineEdit()).text().strip().lstrip('@') if hasattr(self, 'bot_username') else ""
        api_id = self.api_id.text()
        api_hash = self.api_hash.text()
        phone = self.phone.text()

        if not bot_username or not api_id or not api_hash or not phone:
            self.log_message("⚠️ Cannot auto-check bot queue — credentials incomplete. Fill them in first.", "warning")
            return

        self.log_message("📬 Auto-checking bot queue on startup...", "info")
        # Show the log panel so user can see progress
        if not self.findChild(QSplitter) or not self.findChild(QSplitter).widget(1).isVisible():
            self._toggle_log_panel()

        self.check_bot_queue()

