#!/usr/bin/env python3
"""gitcurator.gui.main_window.processing_ctl — the processing control mixin.

Batch confirm gate, Telegram busy-lock, worker keep-alive, code-request
dialog, start/stop processing, progress/status relays and the startup
auto-check (verbatim methods of the original MainWindow).
"""

import os
from datetime import datetime
from gitcurator.gui._qt import *  # noqa: F401,F403 — Qt widget names
from gitcurator.gui.main_window._deps import *  # noqa: F401,F403

__all__ = ["ProcessingControlMixin"]


class ProcessingControlMixin:
    """ProcessingControlMixin — see module docstring (methods are verbatim moves)."""


    def _confirm_batch(self, count: int, source: str) -> bool:
        """v31.1 safety gate: confirm before starting any batch operation on
        MORE THAN 10 items, stating the exact item count."""
        if count <= 10:
            return True
        return self._show_custom_question(
            "Confirm Large Batch",
            f"This will process {count} items ({source}).\n\nContinue?"
        )

    def _acquire_telegram_lock(self) -> bool:
        """Try to acquire the Telegram busy lock. Returns True if acquired,
        False if another Telegram operation is already running."""
        if self._telegram_busy:
            self.log_message(
                "⏳ Another Telegram operation is already running. Please wait for it to finish.",
                "warning"
            )
            return False
        self._telegram_busy = True
        return True

    def _release_telegram_lock(self):
        """Release the Telegram busy lock."""
        self._telegram_busy = False

    def _keep_worker(self, worker: TestWorker):
        """Hold a reference so the QThread isn't garbage-collected mid-run.
        Also releases the Telegram busy lock when the worker finishes."""
        self._active_test_workers.append(worker)
        def _cleanup(*_a):
            try:
                self._active_test_workers.remove(worker)
            except ValueError:
                pass
            self._release_telegram_lock()
        worker.finished_signal.connect(_cleanup)

    def _on_telegram_code_requested(self, prompt_type: str, worker: TestWorker):
        """Called when the Telegram worker needs a login code or 2FA password.
        Shows a theme-aware modal dialog with clear instructions."""
        dialog = QDialog(self)
        dialog.setModal(True)
        dialog.raise_()
        dialog.activateWindow()

        # Theme-aware colors
        is_dark = getattr(self, '_dark_mode', False)
        if is_dark:
            bg_color = "#2B2639"
            text_color = "#F2EEE7"
            help_bg = "#2E2A3C"
            border_color = "#4A4263"
            focus_color = "#C4BCF5"
        else:
            bg_color = "#FFFFFF"
            text_color = "#423A52"
            help_bg = "#F2EDE3"
            border_color = "#D8D0BE"
            focus_color = "#5F54B4"  # v32 pastel: violet accent

        dialog.setStyleSheet(f"""
            QDialog {{ background-color: {bg_color}; }}
            QLabel {{ color: {text_color}; background: transparent; }}
        """)

        if prompt_type == "PASSWORD":
            dialog.setWindowTitle("🔒 Telegram 2FA Password")
            label_text = "Enter your Telegram cloud password (2FA):"
            echo_mode = QLineEdit.EchoMode.Password
            placeholder = ""
            help_text = ""
        else:
            dialog.setWindowTitle("📲 Telegram Login Code")
            label_text = "Telegram has sent a login code to your account."
            echo_mode = QLineEdit.EchoMode.Normal
            placeholder = "e.g. 12345"
            help_text = (
                "WHERE TO FIND THE CODE:\n"
                "• Telegram app: Look for a 'Login Code' notification at the top\n"
                "  of your chat list (it's NOT in Saved Messages)\n"
                "• SMS: Check text messages on your phone\n"
                "• Other devices: Check any other device logged into Telegram\n\n"
                "The code expires in a few minutes. If you don't receive it,\n"
                "cancel and try again."
            )

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)

        # Title label
        title_label = QLabel(label_text)
        title_label.setStyleSheet(f"font-size: 14px; font-weight: bold; color: {text_color}; background: transparent;")
        title_label.setWordWrap(True)
        layout.addWidget(title_label)

        # Input field
        input_field = QLineEdit()
        input_field.setEchoMode(echo_mode)
        input_field.setPlaceholderText(placeholder)
        input_field.setMinimumWidth(360)
        input_field.setStyleSheet(
            f"QLineEdit {{ padding: 8px; border: 2px solid {border_color}; border-radius: 6px; font-size: 14px; background: {bg_color}; color: {text_color}; }}"
            f"QLineEdit:focus {{ border-color: {focus_color}; }}"
        )
        layout.addWidget(input_field)

        # Help text for code input
        if help_text:
            help_label = QLabel(help_text)
            help_label.setStyleSheet(
                f"font-size: 11px; color: {text_color}; background-color: {help_bg}; "
                f"padding: 10px; border-radius: 4px; font-family: Consolas, monospace;"
            )
            help_label.setWordWrap(True)
            layout.addWidget(help_label)

        # Buttons
        button_row = QHBoxLayout()
        button_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet(
            "padding: 8px 20px; font-size: 13px; border: 1px solid #ccc; border-radius: 4px; background: #f5f5f5;"
        )

        ok_btn = QPushButton("✓ Submit Code")
        ok_btn.setDefault(True)
        ok_btn.setStyleSheet(self._btn_style(COLORS['primary'], COLORS['primary_hover']))

        button_row.addWidget(cancel_btn)
        button_row.addWidget(ok_btn)
        layout.addLayout(button_row)

        def _on_accept():
            worker.provide_code(input_field.text().strip())
            dialog.accept()

        def _on_reject():
            worker.provide_code("")
            dialog.reject()

        ok_btn.clicked.connect(_on_accept)
        cancel_btn.clicked.connect(_on_reject)
        input_field.returnPressed.connect(_on_accept)

        # Focus the input field and auto-resize
        input_field.setFocus()
        dialog.adjustSize()
        self._animate_dialog(dialog)
        dialog.exec()

    def start_processing(self):
        self.save_config()
        vault = self.vault_combo.currentText()
        if not vault or not os.path.isdir(vault):
            self._show_custom_message_box("Error", "Please select a valid Obsidian vault path.", success=False)
            return

        # If no input mode is checked but we have bot queue URLs, process those
        bot_urls = getattr(self, '_bot_queue_urls', [])
        if bot_urls and not any([
            self.mode_single.isChecked(),
            self.mode_keyword.isChecked(),
            self.mode_telegram.isChecked(),
            self.mode_import.isChecked(),
        ]):
            # v31.1 safety gate: confirm before large batches (>10 items).
            if not self._confirm_batch(len(bot_urls), "the bot queue"):
                self.log_message("⏹️ Batch cancelled — nothing was processed.", "warning")
                return
            self.log_message(f"🚀 Processing {len(bot_urls)} repos from bot queue...", "info")
            # v23 — pass bot_source=True so the manifest records the source
            # as 'bot' and Phase 5 auto-mark-read can fire on success. Also
            # pass the non-GitHub links so they are tracked in the manifest
            # (they were already added to the inbox table by check_bot_queue).
            self._start_worker_with_urls(
                bot_urls,
                bot_source=True,
                non_github_urls=getattr(self, '_bot_queue_non_github', [])
            )
            return

        if self.mode_single.isChecked():
            if not self._acquire_telegram_lock():
                return
            single_id = self.single_id.text()
            if not single_id:
                self._show_custom_message_box("Error", "Please enter a message ID.", success=False)
                return
            try:
                single_id = int(single_id)
            except ValueError:
                self._show_custom_message_box("Error", "Invalid message ID. Must be an integer.", success=False)
                return
            proxy = self._get_proxy_dict()
            api_id = self.api_id.text()
            api_hash = self.api_hash.text()
            phone = self.phone.text()
            if not api_id or not api_hash or not phone:
                self._show_custom_message_box("Error", "Please fill in Telegram credentials.", success=False)
                return
            # Single-message fetch on a background thread so the GUI stays
            # responsive and the log streams in real time.
            worker = TestWorker(_telegram_single_job, "single_fetch",
                                api_id, api_hash, phone, proxy, single_id, None, None)
            def _job(aid, ahash, ph, px, sid, _ignored_log, _ignored_code):
                return _telegram_single_job(aid, ahash, ph, px, sid, worker.log_message, worker.request_code)
            worker._fn = _job

            worker.log_message.connect(self.log_message)
            worker.code_requested.connect(
                lambda pt, w=worker: self._on_telegram_code_requested(pt, w)
            )
            def _on_finished(name, result):
                if result.get('success'):
                    urls = result.get('urls', [])
                    if not urls:
                        self.log_message("No GitHub URLs found in that message.", "warning")
                        return
                    # v31.1 safety gate (exact item count stated).
                    if not self._confirm_batch(len(urls), "the single message fetch"):
                        self.log_message("⏹️ Batch cancelled — nothing was processed.", "warning")
                        return
                    self._start_worker_with_urls(urls)
                else:
                    self.log_message(f"Failed to fetch message: {result.get('error')}", "error")
            worker.finished_signal.connect(_on_finished)
            self._keep_worker(worker)
            worker.start()
            return

        if self.mode_keyword.isChecked():
            # Keyword mode: first find IDs by keywords, then process the range
            if not self._acquire_telegram_lock():
                return
            kw_start = self.keyword_start.text().strip()
            kw_end = self.keyword_end.text().strip()
            if not kw_start or not kw_end:
                self._show_custom_message_box("Error", "Please enter both start and end keywords.", success=False)
                return
            api_id = self.api_id.text()
            api_hash = self.api_hash.text()
            phone = self.phone.text()
            if not api_id or not api_hash or not phone:
                self._show_custom_message_box("Error", "Please fill in Telegram credentials.", success=False)
                return
            proxy = self._get_proxy_dict()
            self.log_message("🔍 Finding message IDs by keywords before processing...", "info")

            worker = TestWorker(_telegram_keyword_job, "keyword_find_process",
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
                    if start_id is None or end_id is None:
                        self.log_message("❌ Could not find both keywords. Cannot process range.", "error")
                        return
                    # Fill in the IDs and start processing
                    self.range_from.setText(str(start_id))
                    self.range_to.setText(str(end_id))
                    self.log_message(f"✅ Found range: {start_id} to {end_id}. Starting processing...", "success")
                    self._start_worker('telegram_ids', start_id, end_id, None, None, None, None)
                else:
                    self.log_message(f"❌ Keyword search failed: {result.get('error')}", "error")

            worker.finished_signal.connect(_on_finished)
            self._keep_worker(worker)
            worker.start()
            return

        if self.mode_telegram.isChecked():
            from_id = self.range_from.text()
            to_id = self.range_to.text()
            offset_start = self.offset_start.text()
            offset_count = self.offset_count.text()

            if from_id and to_id:
                mode = 'telegram_ids'
                range_from = int(from_id)
                range_to = int(to_id)
                offset_start = None
                offset_count = None
            elif offset_start and offset_count:
                mode = 'telegram_offset'
                range_from = None
                range_to = None
                offset_start = int(offset_start)
                offset_count = int(offset_count)
            else:
                self._show_custom_message_box("Error", "Please provide either Message ID range or Offset parameters.", success=False)
                return
            import_file = None
            urls = None
        else:  # import
            mode = 'import'
            range_from = None
            range_to = None
            offset_start = None
            offset_count = None
            import_file = self.import_file.text()
            if not import_file or not os.path.exists(import_file):
                # No mode selected and no import file — show helpful message
                self._show_custom_message_box(
                    "Nothing to Process",
                    "No processing mode selected and no bot queue loaded.\n\n"
                    "Go to the 🤖 Bot tab and click '📬 Check Queue' first, "
                    "or select an input mode in the 📥 Input tab.",
                    success=False
                )
                return
            urls = None

        self._start_worker(mode, range_from, range_to, offset_start, offset_count, import_file, urls)

    def _start_worker_with_urls(self, urls, bot_source=False, non_github_urls=None,
                                intake_duplicates=0, raw_url_count=0):
        """Start a ProcessingWorker in 'direct' mode with a pre-fetched list
        of URLs. Used by the bot-queue, sources, and retry flows.

        v23 — No Link Left Behind:
          * ``bot_source=True``  labels the manifest source as 'bot' (so the
            Phase 5 auto-mark-read flow can fire on success).
          * ``non_github_urls`` is the list of non-GitHub links discovered
            alongside the GitHub URLs (typically from the bot queue). They
            are recorded in the manifest and the inbox table so they are
            never silently dropped.

        v25 pre-flight:
          * ``intake_duplicates`` / ``raw_url_count`` are surfaced in the
            final report so the user can see how many duplicate URLs were
            deduped during intake ("🔄 15 duplicates removed (200 unique
            from 215 total)").
        """
        self._start_worker(
            'direct', None, None, None, None, None, urls,
            bot_source=bot_source,
            non_github_urls=non_github_urls,
            intake_duplicates=intake_duplicates,
            raw_url_count=raw_url_count,
        )

    def _start_worker(self, mode, range_from, range_to, offset_start, offset_count, import_file, urls,
                      bot_source=False, non_github_urls=None, intake_duplicates=0, raw_url_count=0):
        # Acquire Telegram lock for modes that access the session file.
        # 'direct' and 'import' modes don't use Telegram, so no lock needed.
        if mode in ('telegram_ids', 'telegram_offset'):
            if not self._acquire_telegram_lock():
                return

        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.progress_bar.setValue(0)
        # v31.1 spec: the determinate progress bar is visible ONLY while a
        # batch job runs — show it now, label it with the current item as
        # the batch progresses (update_progress / update_status).
        self.progress_bar.setVisible(True)
        self.progress_bar.setFormat("Processing…")
        self.log_text.clear()
        # Track processing start time for elapsed display
        self._processing_start_time = datetime.now()

        self.worker = ProcessingWorker(
            config=self.config,
            mode=mode,
            range_from=range_from,
            range_to=range_to,
            offset_start=offset_start,
            offset_count=offset_count,
            import_file=import_file,
            urls=urls
        )
        # v23 — populate link-tracker flags BEFORE the worker thread starts
        # so run() can read them during Phase 1 intake. non_github_urls is
        # only relevant for 'direct' mode (bot queue / sources / retry).
        self.worker._bot_source = bool(bot_source)
        if non_github_urls:
            self.worker._non_github_urls = list(non_github_urls)
        # v25 pre-flight: surface duplicate-removal stats in the final
        # report. Set from the bot-queue worker's dedup pass.
        self.worker._intake_duplicates = int(intake_duplicates or 0)
        self.worker._raw_url_count = int(raw_url_count or 0)
        self.worker.progress_updated.connect(self.update_progress)
        self.worker.status_updated.connect(self.update_status)
        self.worker.log_message.connect(self.log_message)
        self.worker.code_requested.connect(
            lambda pt, w=self.worker: self._on_telegram_code_requested(pt, w)
        )
        self.worker.finished_signal.connect(self.processing_finished)
        self.worker.disk_full_signal.connect(self._on_disk_full)
        # Pass the worker ref to _on_llm_failed so it can deliver the user's
        # decision back to the worker via resolve_llm_failure(). The signal
        # is emitted from the worker thread but Qt delivers it on the GUI
        # thread via a queued connection, so showing a modal dialog is safe.
        self.worker.llm_failed_signal.connect(
            lambda repo_name, w=self.worker: self._on_llm_failed(repo_name, w)
        )
        # v30 — Fix (model persistence): keep Settings + config.json in sync
        # when the worker's auto-switch or dialog-driven model change fires.
        self.worker.model_changed.connect(self._on_model_changed)
        self.worker.start()

    def stop_processing(self):
        if self.worker:
            self.worker.stop()
            self.log_message("⏹️ Stopping...", "warning")
            self.stop_btn.setEnabled(False)

    def update_progress(self, current, total):
        """Update the progress bar value and show `Processing X of Y` in its
        text (v31.1 spec: 'Processing 7 of 30 — repo-name').

        The format string is intentionally short so it fits on the narrow
        progress bar; the human-readable status (current repo name) is set
        separately by `update_status` which overwrites this format with a
        longer `Processing X of Y — owner/repo` string while a repo is being
        processed.
        """
        if total > 0:
            self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
        # Show "Processing X of Y" format in the progress bar
        if total > 0:
            self.progress_bar.setFormat(f"Processing {current} of {total}")
        else:
            self.progress_bar.setFormat("Ready")

    def update_status(self, text):
        """Show the current status text in the progress bar's format string.

        Format: `Processing X of Y — owner/repo (elapsed)`
        Elapsed time is calculated from `self._processing_start_time`.
        """
        # Status shown in progress bar format (status label removed)
        if text and text != "Ready":
            # Calculate elapsed time
            elapsed_str = ""
            if hasattr(self, '_processing_start_time'):
                elapsed = datetime.now() - self._processing_start_time
                total_secs = int(elapsed.total_seconds())
                if total_secs >= 60:
                    elapsed_str = f" ({total_secs // 60}m {total_secs % 60}s)"
                else:
                    elapsed_str = f" ({total_secs}s)"

            # Extract repo name from URL if it's a GitHub URL
            if 'github.com/' in text:
                parts = text.replace('https://github.com/', '').split('/')
                if len(parts) >= 2:
                    repo_name = f"{parts[0]}/{parts[1]}"
                    current = self.progress_bar.value()
                    total = self.progress_bar.maximum()
                    self.progress_bar.setFormat(f"Processing {current} of {total} — {repo_name[:40]}{elapsed_str}")
                    return
            self.progress_bar.setFormat(f"Processing: {text[:60]}{elapsed_str}")
        else:
            self.progress_bar.setFormat("Ready")

    def _startup_auto_check(self):
        """Auto-check bot queue on startup — validates proxy first."""
        # Reset the Telegram lock on startup (in case it was stuck from a crash)
        self._telegram_busy = False

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
