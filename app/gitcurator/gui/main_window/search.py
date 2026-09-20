#!/usr/bin/env python3
"""gitcurator.gui.main_window.search — the marker/keyword search mixin.

Marker-hash generation/copy, find-by-marker, keyword ID search, message
preview + preview modal (verbatim methods of the original MainWindow).
"""

from gitcurator.gui._qt import *  # noqa: F401,F403 — Qt widget names
from gitcurator.gui.main_window._deps import *  # noqa: F401,F403

__all__ = ["SearchToolsMixin"]


class SearchToolsMixin:
    """SearchToolsMixin — see module docstring (methods are verbatim moves)."""


    # ------------------------------------------------------------------
    # Preview & single-message fetch (also moved off the GUI thread)
    # ------------------------------------------------------------------
    def generate_marker_hash(self):
        """Generate a unique 32-char random hash for marking messages.
        Auto-copies to clipboard after generation."""
        import secrets
        import string
        alphabet = string.ascii_lowercase + string.digits
        alphabet = alphabet.replace('0', '').replace('1', '').replace('l', '').replace('o', '')
        hash_val = ''.join(secrets.choice(alphabet) for _ in range(32))
        self.marker_hash.setText(hash_val)

        # Auto-copy to clipboard
        from PyQt6.QtWidgets import QApplication as _QApp
        clipboard = _QApp.clipboard()
        clipboard.setText(hash_val)

        self.log_message(f"🎲 Generated marker: {hash_val}", "success")
        self.log_message(f"📋 Auto-copied to clipboard — paste it into your Saved Messages now!", "success")

    def copy_marker_hash(self):
        """Copy the generated marker hash to the clipboard."""
        hash_val = self.marker_hash.text().strip()
        if not hash_val:
            self.log_message("No marker generated yet. Click 'Generate' first.", "warning")
            return
        from PyQt6.QtWidgets import QApplication as _QApp
        clipboard = _QApp.clipboard()
        clipboard.setText(hash_val)
        self.log_message(f"📋 Copied to clipboard: {hash_val}", "success")

    def find_by_marker(self):
        """Find message IDs by searching for the marker hash.
        The user pastes the SAME hash into the first and last messages of
        their desired range. The app finds the first occurrence (start) and
        the second occurrence (end).
        """
        if not self._acquire_telegram_lock():
            return
        marker = self.marker_hash.text().strip()
        if not marker:
            self.log_message("No marker code. Click 'Generate' first.", "warning")
            return

        api_id = self.api_id.text()
        api_hash = self.api_hash.text()
        phone = self.phone.text()
        if not api_id or not api_hash or not phone:
            self.log_message("Please fill in API ID, API Hash, and Phone first.", "warning")
            return

        proxy = self._get_proxy_dict()
        if not proxy.get('enabled'):
            self.log_message("⚠️ Proxy is not enabled. Enable it in the Proxy tab.", "warning")

        self.log_message(f"🔍 Searching Saved Messages for marker: {marker}", "info")
        self.log_message("Looking for FIRST occurrence (start) and SECOND occurrence (end)...", "info")

        # Use the keyword job with start=end=marker. The worker will find
        # the FIRST match for start, then continue searching for the NEXT
        # match for end (same string).
        worker = TestWorker(_telegram_keyword_job, "marker_search",
                            api_id, api_hash, phone, proxy, marker, marker, None, None)
        def _job(aid, ahash, ph, px, ks, ke, _ignored_log, _ignored_code):
            return _telegram_keyword_job(aid, ahash, ph, px, ks, ke, worker.log_message, worker.request_code)
        worker._fn = _job

        worker.log_message.connect(self.log_message)
        worker.code_requested.connect(
            lambda pt, w=worker: self._on_telegram_code_requested(pt, w)
        )

        def _on_finished(name, result):
            if result.get('success'):
                start_id = result.get('start_id')
                end_id = result.get('end_id')
                searched = result.get('searched_count', 0)
                self.log_message(f"📊 Searched {searched} messages.", "info")

                if start_id is not None:
                    self.range_from.setText(str(start_id))
                    preview = result.get('start_preview', '')
                    self.log_message(f"✅ Start (1st occurrence): message ID {start_id}", "success")
                    self.log_message(f"   \"{preview}\"", "info")
                else:
                    self.log_message(f"❌ Marker not found in any message.", "error")
                    self.log_message("Make sure you pasted the marker into your Saved Messages.", "warning")

                if end_id is not None:
                    self.range_to.setText(str(end_id))
                    preview = result.get('end_preview', '')
                    self.log_message(f"✅ End (2nd occurrence): message ID {end_id}", "success")
                    self.log_message(f"   \"{preview}\"", "info")
                elif start_id is not None:
                    # Only one occurrence found — user may want single message mode
                    self.log_message("ℹ️ Only one occurrence found (no end marker).", "warning")
                    self.log_message("If you want a SINGLE message, use 'Single Message ID' mode with this ID.", "info")
                    self.log_message("If you want a RANGE, paste the marker into the last message too.", "info")

                if start_id is not None and end_id is not None:
                    self.log_message(f"✅ Range set: {start_id} → {end_id}", "success")
                    self.log_message("Switch to 'Telegram Messages' mode and click Start Processing.", "success")
                    self.mode_telegram.setChecked(True)
            else:
                self.log_message(f"❌ Marker search failed: {result.get('error')}", "error")

        worker.finished_signal.connect(_on_finished)
        self._keep_worker(worker)
        worker.start()

    def find_keyword_ids(self):
        """Search Saved Messages for start/end keywords and fill in the
        From ID / To ID fields automatically. The user edits messages in
        their Saved Messages to contain the keywords, then clicks this button."""
        if not self._acquire_telegram_lock():
            return
        api_id = self.api_id.text()
        api_hash = self.api_hash.text()
        phone = self.phone.text()
        if not api_id or not api_hash or not phone:
            self.log_message("Please fill in API ID, API Hash, and Phone first.", "warning")
            return

        kw_start = self.keyword_start.text().strip()
        kw_end = self.keyword_end.text().strip()
        if not kw_start and not kw_end:
            self.log_message("Please enter at least one keyword (start and/or end).", "warning")
            return

        proxy = self._get_proxy_dict()
        if not proxy.get('enabled'):
            self.log_message("⚠️ Proxy is not enabled. Enable it in the Proxy tab.", "warning")

        self.log_message(f"🔍 Searching Saved Messages for keywords...", "info")
        self.log_message(f"   Start keyword: '{kw_start}'" if kw_start else "   Start keyword: (none)", "info")
        self.log_message(f"   End keyword:   '{kw_end}'" if kw_end else "   End keyword:   (none)", "info")

        worker = TestWorker(_telegram_keyword_job, "keyword_search",
                            api_id, api_hash, phone, proxy, kw_start, kw_end, None, None)
        def _job(aid, ahash, ph, px, ks, ke, _ignored_log, _ignored_code):
            return _telegram_keyword_job(aid, ahash, ph, px, ks, ke, worker.log_message, worker.request_code)
        worker._fn = _job

        worker.log_message.connect(self.log_message)
        worker.code_requested.connect(
            lambda pt, w=worker: self._on_telegram_code_requested(pt, w)
        )

        def _on_finished(name, result):
            if result.get('success'):
                start_id = result.get('start_id')
                end_id = result.get('end_id')
                searched = result.get('searched_count', 0)
                self.log_message(f"📊 Searched {searched} messages.", "info")

                if start_id is not None:
                    self.range_from.setText(str(start_id))
                    preview = result.get('start_preview', '')
                    self.log_message(f"✅ Start: message ID {start_id} \"{preview}\"", "success")
                else:
                    self.log_message(f"❌ Start keyword '{kw_start}' not found.", "error")

                if end_id is not None:
                    self.range_to.setText(str(end_id))
                    preview = result.get('end_preview', '')
                    self.log_message(f"✅ End: message ID {end_id} \"{preview}\"", "success")
                else:
                    self.log_message(f"❌ End keyword '{kw_end}' not found.", "error")

                if start_id is not None and end_id is not None:
                    self.log_message("✅ IDs filled in! Switch to 'Telegram Messages' mode to process.", "success")
                    # Auto-switch to telegram range mode
                    self.mode_telegram.setChecked(True)
            else:
                self.log_message(f"❌ Keyword search failed: {result.get('error')}", "error")

        worker.finished_signal.connect(_on_finished)
        self._keep_worker(worker)
        worker.start()

    def preview_messages(self):
        if not self._acquire_telegram_lock():
            return
        if not self.mode_telegram.isChecked():
            self.log_message("Preview only available in Telegram range mode.", "warning")
            self._release_telegram_lock()
            return
        api_id = self.api_id.text()
        api_hash = self.api_hash.text()
        phone = self.phone.text()
        if not api_id or not api_hash or not phone:
            self.log_message("Please fill in API ID, API Hash, and Phone.", "warning")
            return
        from_id = self.range_from.text()
        to_id = self.range_to.text()
        if not from_id or not to_id:
            self.log_message("Please enter From ID and To ID.", "warning")
            return
        try:
            from_id = int(from_id)
            to_id = int(to_id)
        except ValueError:
            self.log_message("Invalid IDs. Please enter numbers.", "error")
            return

        proxy = self._get_proxy_dict()
        self.log_message("Fetching preview...", "info")

        worker = TestWorker(_telegram_preview_job, "preview",
                            api_id, api_hash, phone, proxy, from_id, to_id, None, None)
        def _job(aid, ahash, ph, px, fid, tid, _ignored_log, _ignored_code):
            return _telegram_preview_job(aid, ahash, ph, px, fid, tid, worker.log_message, worker.request_code)
        worker._fn = _job

        worker.log_message.connect(self.log_message)
        worker.code_requested.connect(
            lambda pt, w=worker: self._on_telegram_code_requested(pt, w)
        )
        def _on_finished(name, result):
            if result.get('success'):
                preview = result.get('preview', {})
                total = preview.get('total_count', 0)
                first = preview.get('first', [])
                last = preview.get('last', [])
                self.log_message(f"Total messages in range: {total}", "info")
                # Show preview in a modal dialog
                self._show_preview_modal(total, first, last)
            else:
                self.log_message(f"Preview failed: {result.get('error')}", "error")
                self._show_custom_message_box("Preview Failed", result.get('error', 'Unknown error'), success=False)
        worker.finished_signal.connect(_on_finished)
        self._keep_worker(worker)
        worker.start()

    def _show_preview_modal(self, total: int, first: list, last: list):
        """Show the preview results in a modal dialog."""
        dialog = QDialog(self)
        dialog.setWindowTitle(f"👁️ Message Preview ({total} messages)")
        
        dialog.setModal(True)
        dialog.setMinimumWidth(600)
        dialog.setMinimumHeight(400)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        # Header
        header = QLabel(f"📊 Found {total} messages in range")
        header.setStyleSheet("font-size: 16px; font-weight: bold; color: #5F54B4;")
        layout.addWidget(header)

        # Preview text
        preview_text = QTextEdit()
        preview_text.setReadOnly(True)
        preview_text.setFont(QFont("Consolas", 9))

        content = ""
        if first:
            content += "=== FIRST MESSAGES ===\n"
            for msg in first:
                content += f"ID {msg['id']} ({msg.get('date', 'N/A')}):\n"
                content += f"  {msg['text']}\n"
                content += f"  Has GitHub links: {'Yes' if msg.get('has_links') else 'No'}\n\n"
        if last:
            content += "=== LAST MESSAGES ===\n"
            for msg in last:
                content += f"ID {msg['id']} ({msg.get('date', 'N/A')}):\n"
                content += f"  {msg['text']}\n"
                content += f"  Has GitHub links: {'Yes' if msg.get('has_links') else 'No'}\n\n"

        preview_text.setPlainText(content)
        layout.addWidget(preview_text)

        # Close button
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setStyleSheet(self._btn_style(COLORS['primary'], COLORS['primary_hover']))
        close_btn.clicked.connect(dialog.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        self._animate_dialog(dialog)
        dialog.exec()
