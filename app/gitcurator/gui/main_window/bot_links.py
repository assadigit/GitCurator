#!/usr/bin/env python3
"""gitcurator.gui.main_window.bot_links — the bot link-tools mixin.

Export/verify all bot links, fuzzy GitHub URL matching, missing-URL
resolution + manual resolve dialog (verbatim methods of the original
MainWindow).
"""

import os, re
from datetime import datetime
from gitcurator.gui._qt import *  # noqa: F401,F403 — Qt widget names
from gitcurator.gui.main_window._deps import *  # noqa: F401,F403

__all__ = ["BotLinkToolsMixin"]


class BotLinkToolsMixin:
    """BotLinkToolsMixin — see module docstring (methods are verbatim moves)."""


    def export_all_bot_links(self):
        """Fetch ALL links from the bot and save to a file for manual verification.
        This lets the user compare what the app found vs what they actually forwarded."""
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
        vault = self.vault_combo.currentText()
        if not vault:
            self.log_message("❌ No vault selected — need a place to save the export.", "error")
            self._release_telegram_lock()
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
        self._keep_worker(worker)
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

            vault_path = self.vault_combo.currentText()
            if not vault_path or not os.path.isdir(vault_path):
                self.log_message("❌ No vault selected — cannot verify links.", "error")
                self._release_telegram_lock()
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
                    
                    # Load decommissioned URLs from cache
                    try:
                        cache = CacheDB()
                        decommissioned_urls = set()
                        for row in cache.get_all_decommissioned():
                            decommissioned_urls.add(row[0])
                        cache.close()
                    except Exception:
                        decommissioned_urls = set()
                    
                    # v29.10 — Also load CacheDB to check if URLs were previously processed
                    # This catches cases where the note's source: field has a different URL format
                    # (e.g., embedchain/embedchain was renamed to mem0ai/mem0 on GitHub)
                    try:
                        cache_db = CacheDB()
                        cache_processed_urls = set()
                        for row in cache_db.get_all_processed_urls():
                            cache_processed_urls.add(row[0] if isinstance(row, tuple) else row)
                        cache_db.close()
                    except Exception:
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
                            self.clear_bot_queue()
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
            self._keep_worker(worker)
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
                self._release_telegram_lock()
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
