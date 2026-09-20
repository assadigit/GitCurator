#!/usr/bin/env python3
"""gitcurator.gui.main_window.bot_queue — the bot-queue workflow mixin.

Fetch from sources, queue check/process/new-process, clear, retry
failed, mark-messages-read (verbatim methods of the original
MainWindow).
"""

import os, re
from gitcurator.gui._qt import *  # noqa: F401,F403 — Qt widget names
from gitcurator.gui.main_window._deps import *  # noqa: F401,F403

__all__ = ["BotQueueMixin"]


class BotQueueMixin:
    """BotQueueMixin — see module docstring (methods are verbatim moves)."""


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

    def check_bot_queue(self):
        """Check the bot's Telegram chat for pending GitHub repos."""
        if not self._acquire_telegram_lock():
            return
        bot_username = self.bot_username.text().strip().lstrip('@')
        if not bot_username:
            self.log_message("❌ Please enter the bot username first.", "error")
            self._release_telegram_lock()
            return
        api_id = self.api_id.text()
        api_hash = self.api_hash.text()
        phone = self.phone.text()
        if not api_id or not api_hash or not phone:
            self.log_message("❌ Telegram credentials required.", "error")
            self._release_telegram_lock()
            return

        proxy = self._get_proxy_dict()
        self.save_config()
        self.log_message(f"📬 Checking bot queue (@{bot_username})...", "info")
        self.queue_display.clear()

        worker = TestWorker(_bot_queue_job, "bot_check",
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
                all_urls = result.get('urls', [])
                non_github = result.get('non_github_urls', [])
                self._bot_queue_non_github = non_github
                self._bot_queue_max_id = result.get('max_message_id', 0)
                self._bot_queue_duplicates = result.get('duplicates_removed', 0)
                self._bot_queue_raw_count = result.get('raw_url_count', len(all_urls) + len(non_github))

                # Add non-GitHub links to inbox
                if non_github:
                    vault_path = self.vault_combo.currentText()
                    if vault_path:
                        write_inbox_links_by_platform(
                            vault_path, non_github, source="Bot",
                            log_callback=lambda msg, lvl: self.log_message(msg, lvl),
                        )

                # Q1/Q6: Filter URLs against VaultIndex + decommissioned — only show NOT-in-vault AND NOT-decommissioned as pending
                in_vault_count = 0
                decommissioned_count = 0
                pending_urls = []
                vault_path = self.vault_combo.currentText()
                if vault_path and os.path.isdir(vault_path):
                    try:
                        vi = VaultIndex(vault_path)
                        vi.rebuild(log_signal=None)
                        self.log_message(f"📚 Vault index: {vi.count} notes indexed", "info")
                        
                        # Load decommissioned URLs
                        try:
                            cache = CacheDB()
                            decommissioned_urls = set()
                            for row in cache.get_all_decommissioned():
                                decommissioned_urls.add(row[0])
                            cache.close()
                        except Exception:
                            decommissioned_urls = set()
                        
                        for url in all_urls:
                            norm = normalize_url(url)
                            if vi.has_url(url):
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

                # Update pending badge (Q15). v31.1: zinc + ⏳ — a pending
                # count is routine, NOT an error; red is reserved for failures.
                if pending_urls:
                    self.pending_badge.setText(f"⏳ {len(pending_urls)} pending")
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
                display = f"📬 Bot Queue Results\n"
                display += f"{'='*50}\n"
                display += f"Total GitHub URLs in bot:  {len(all_urls)}\n"
                display += f"✅ Already in vault:        {in_vault_count}\n"
                display += f"🗑️ Decommissioned (404):    {decommissioned_count}\n"
                display += f"⏳ Pending (not in vault):  {len(pending_urls)}\n"
                display += f"🔗 Non-GitHub links:        {len(non_github)}\n"
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

                if non_github:
                    display += f"\nNON-GITHUB LINKS ({len(non_github)}):\n"
                    display += "(Recorded in _inbox/ per platform)\n"

                self.queue_display.setPlainText(display)

                if pending_urls:
                    self.log_message(
                        f"📬 Queue: {len(pending_urls)} new repos pending ({in_vault_count} already in vault)",
                        "success"
                    )
                else:
                    self.log_message(
                        f"📬 Queue: All {len(all_urls)} GitHub repos already in vault! 0 pending.",
                        "success"
                    )
                    # Hide mark-all-read button until verify passes
                    self.mark_all_read_btn.setVisible(False)
            else:
                self.log_message(f"❌ Bot queue check failed: {result.get('error')}", "error")
                self.queue_display.setPlainText(f"Error: {result.get('error', 'Unknown')}")

        worker.finished_signal.connect(_on_finished)
        self._keep_worker(worker)
        worker.start()

    def process_bot_queue(self):
        """Process all repos in the bot queue."""
        urls = getattr(self, '_bot_queue_urls', [])
        if not urls:
            self.log_message("No repos in queue. Click 'Check Queue' first.", "warning")
            return
        # v31.1 safety gate: confirm before large batches (>10 items).
        if not self._confirm_batch(len(urls), "the bot queue"):
            self.log_message("⏹️ Batch cancelled — nothing was processed.", "warning")
            return
        self.log_message(f"🚀 Processing {len(urls)} repos from bot queue...", "info")
        # Process the URLs using the existing pipeline. v23 — pass
        # bot_source=True and the non-GitHub links so the manifest can
        # track every link through the 5-phase pipeline.
        # v25 pre-flight: forward the intake duplicate stats so the final
        # report can show "🔄 N duplicate URL(s) removed".
        self._start_worker_with_urls(
            urls,
            bot_source=True,
            non_github_urls=getattr(self, '_bot_queue_non_github', []),
            intake_duplicates=getattr(self, '_bot_queue_duplicates', 0),
            raw_url_count=getattr(self, '_bot_queue_raw_count', 0),
        )
        # Note: we don't mark as read here — the user can click "Mark All Read"
        # separately after verifying processing succeeded.

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
        if not self._acquire_telegram_lock():
            return
        bot_username = self.bot_username.text().strip().lstrip('@')
        if not bot_username:
            self.log_message("❌ Please enter the bot username first.", "error")
            self._release_telegram_lock()
            return
        api_id = self.api_id.text()
        api_hash = self.api_hash.text()
        phone = self.phone.text()
        if not api_id or not api_hash or not phone:
            self.log_message("❌ Telegram credentials required.", "error")
            self._release_telegram_lock()
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
            self._release_telegram_lock()
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
                vault_path = self.vault_combo.currentText()
                if vault_path:
                    write_inbox_links_by_platform(
                        vault_path, non_github, source="Bot",
                        log_callback=lambda msg, lvl: self.log_message(msg, lvl),
                    )

            # Start the worker. processing_finished will advance
            # last_processed_msg_id to self._pending_last_processed_update
            # ONLY if Phase 5 CLEAR passes (all links verified).
            # v31.1 safety gate: confirm before large batches (>10 items).
            if not self._confirm_batch(len(urls), "the new bot messages"):
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
        self._keep_worker(worker)
        worker.start()

    def clear_bot_queue(self):
        """Mark all bot messages as read (clears the queue indicator)."""
        if not self._acquire_telegram_lock():
            return
        bot_username = self.bot_username.text().strip().lstrip('@')
        if not bot_username:
            self.log_message("❌ Please enter the bot username first.", "error")
            self._release_telegram_lock()
            return
        api_id = self.api_id.text()
        api_hash = self.api_hash.text()
        phone = self.phone.text()
        if not api_id or not api_hash or not phone:
            self.log_message("❌ Telegram credentials required.", "error")
            self._release_telegram_lock()
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
        self._keep_worker(worker)
        worker.start()

    def retry_failed_repos(self):
        """v22 Feature 4: Fetch unresolved failed URLs from the SQLite cache
        and reprocess them. If the vault path is not set, just shows a message.
        Clears _bot_queue_urls so the auto-mark-read logic in
        processing_finished doesn't fire on a retry batch."""
        vault = self.vault_combo.currentText()
        if not vault or not os.path.isdir(vault):
            self._show_custom_message_box("Error", "Please select a valid Obsidian vault path.", success=False)
            return

        try:
            cache = CacheDB()
            failed = cache.get_failed_urls()
            cache.close()
        except Exception as e:
            self.log_message(f"❌ Failed to read retry queue: {e}", "error")
            return

        if not failed:
            self.log_message("✓ No failed repos to retry.", "success")
            self._show_custom_message_box("Retry Queue Empty", "No failed repos to retry. 🎉", success=True)
            return

        urls = [row[0] for row in failed if row and row[0]]
        if not urls:
            self.log_message("✓ No failed repos to retry.", "success")
            return

        # v31.1 safety gate: confirm before large batches (>10 items).
        if not self._confirm_batch(len(urls), "the retry queue"):
            self.log_message("⏹️ Retry cancelled — nothing was processed.", "warning")
            return

        self.log_message(f"🔄 Retrying {len(urls)} previously-failed repos...", "info")
        # Don't trigger the auto-mark-read flow for retries — these URLs
        # intentionally failed before, so we shouldn't mark the bot queue
        # as read even if they succeed this time (the user might want to
        # verify them first).
        self._bot_queue_urls = []
        self._start_worker_with_urls(urls)

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
        if not self._acquire_telegram_lock():
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
        self._keep_worker(worker)
        worker.start()
