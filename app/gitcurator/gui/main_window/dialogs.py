#!/usr/bin/env python3
"""gitcurator.gui.main_window.dialogs — the dialog + completion mixin.

Custom message box / question helpers, progress-bar hide scheduling,
``processing_finished`` summary, disk-full, LLM-failure and
model-changed handlers (verbatim methods of the original MainWindow).
"""

from datetime import datetime
from gitcurator.gui._qt import *  # noqa: F401,F403 — Qt widget names
from gitcurator.gui.main_window._deps import *  # noqa: F401,F403

__all__ = ["DialogsMixin"]


class DialogsMixin:
    """DialogsMixin — see module docstring (methods are verbatim moves)."""


    def _show_custom_message_box(self, title: str, message: str, success: bool = True):
        """Show a custom message box with theme-aware colors.
        Works in both light and dark mode."""
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setModal(True)
        dialog.setMinimumWidth(400)

        # Determine theme-appropriate colors
        is_dark = getattr(self, '_dark_mode', False)
        if is_dark:
            bg_color = "#2B2639"       # zinc-800
            text_color = "#F2EEE7"      # warm white
            border_color = "#3B344F"    # plum border
        else:
            bg_color = "#FFFFFF"
            text_color = "#423A52"      # charcoal
            border_color = "#F2EEE7"    # zinc-200

        # Apply background + border to the dialog itself
        dialog.setStyleSheet(f"""
            QDialog {{
                background-color: {bg_color};
            }}
            QLabel {{
                color: {text_color};
                background: transparent;
            }}
        """)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # Icon + title row
        header = QHBoxLayout()
        if success:
            icon_label = QLabel("✅")
            title_text = " Success!"
            title_color = "#16A34A" if not is_dark else "#4ADE80"  # green-600 / green-400
        else:
            icon_label = QLabel("❌")
            title_text = " Error"
            title_color = "#DC2626" if not is_dark else "#F87171"  # red-600 / red-400
        icon_label.setStyleSheet("font-size: 32px; background: transparent;")
        header.addWidget(icon_label)

        title_label = QLabel(title_text)
        title_label.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {title_color}; background: transparent;")
        header.addWidget(title_label)
        header.addStretch()
        layout.addLayout(header)

        # Message
        msg_label = QLabel(message)
        msg_label.setStyleSheet(f"font-size: 13px; color: {text_color}; background: transparent;")
        msg_label.setWordWrap(True)
        layout.addWidget(msg_label)

        # OK button
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton("OK")
        ok_btn.setStyleSheet(self._btn_style(COLORS['primary'], COLORS['primary_hover']))
        ok_btn.clicked.connect(dialog.accept)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

        self._animate_dialog(dialog)
        dialog.exec()

    def _show_custom_question(self, title: str, message: str) -> bool:
        """Show a theme-aware Yes/No question dialog.
        Returns True if user clicks Yes, False otherwise."""
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setModal(True)
        dialog.setMinimumWidth(400)

        is_dark = getattr(self, '_dark_mode', False)
        if is_dark:
            bg_color = "#2B2639"
            text_color = "#F2EEE7"
        else:
            bg_color = "#FFFFFF"
            text_color = "#423A52"

        dialog.setStyleSheet(f"""
            QDialog {{ background-color: {bg_color}; }}
            QLabel {{ color: {text_color}; background: transparent; }}
        """)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # Icon + title
        header = QHBoxLayout()
        icon_label = QLabel("⚠️")
        icon_label.setStyleSheet("font-size: 32px; background: transparent;")
        header.addWidget(icon_label)
        title_label = QLabel(title)
        title_color = "#EA580C" if not is_dark else "#FB923C"  # orange
        title_label.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {title_color}; background: transparent;")
        header.addWidget(title_label)
        header.addStretch()
        layout.addLayout(header)

        msg_label = QLabel(message)
        msg_label.setStyleSheet(f"font-size: 13px; color: {text_color}; background: transparent;")
        msg_label.setWordWrap(True)
        layout.addWidget(msg_label)

        # Yes/No buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        no_btn = QPushButton("No")
        no_btn.setStyleSheet(self._btn_style(COLORS['neutral'], COLORS['neutral_hover'], variant='outline'))
        no_btn.clicked.connect(dialog.reject)
        btn_row.addWidget(no_btn)
        yes_btn = QPushButton("Yes")
        yes_btn.setStyleSheet(self._btn_style(COLORS['error'], COLORS['error_hover'], text=COLORS['error_text']))
        yes_btn.clicked.connect(dialog.accept)
        btn_row.addWidget(yes_btn)
        layout.addLayout(btn_row)

        self._animate_dialog(dialog)
        result = dialog.exec()
        return result == QDialog.DialogCode.Accepted

    def _schedule_progress_hide(self, delay_ms: int = 2500):
        """v31.1 spec: the progress bar is visible ONLY while a batch job
        runs — after a finish/failure we flash the result briefly, then hide.
        Guarded so a freshly-started batch is never hidden by a stale timer."""
        QTimer.singleShot(delay_ms, self._hide_progress_bar)

    def _hide_progress_bar(self):
        if not self.start_btn.isEnabled():
            return  # a NEW batch is already running — keep the bar visible
        self.progress_bar.setVisible(False)
        self.progress_bar.setFormat("Ready")
        self.progress_bar.setValue(0)

    def processing_finished(self, success, message):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self._release_telegram_lock()

        # v29.4 — Auto-backup after batch (if enabled)
        if success and self.config.get('backup_enabled', False) and self.config.get('backup_folder', ''):
            if hasattr(self, '_backup_now'):
                try:
                    self._backup_now()
                except Exception as e:
                    self.log_message(f"⚠️ Auto-backup failed: {e}", "warning")

        # v31 — VaultSeal: seal the vault into its private GitHub mirror.
        # Deliberately runs for FAILED batches too: a mid-run failure may
        # still have written notes, and those are exactly what we want
        # backed up. An unchanged vault is a no-op ("vault unchanged since
        # the last seal"). Never blocks the GUI — runs in a QThread.
        try:
            vs_cfg = self.config.get('vaultseal') or {}
            if vs_cfg.get('enabled', True) and self.config.get('vault_path'):
                self._start_vault_seal()
        except Exception as seal_err:
            self.log_message(f"⚠️ VaultSeal could not start: {seal_err}", "warning")

        # v32 — GoodRepos: publish the PUBLIC curated directory (emoji-rich
        # README + mirrored notes) right after the private seal. Unchanged
        # content is a no-op; never blocks the GUI — its own QThread.
        try:
            gr_cfg = self.config.get('goodrepos') or {}
            if gr_cfg.get('enabled', True) and self.config.get('vault_path'):
                self._start_goodrepos_publish()
        except Exception as good_err:
            self.log_message(f"⚠️ Good Repos could not start: {good_err}", "warning")

        # Calculate total elapsed time
        elapsed_str = ""
        if hasattr(self, '_processing_start_time'):
            elapsed = datetime.now() - self._processing_start_time
            total_secs = int(elapsed.total_seconds())
            if total_secs >= 60:
                elapsed_str = f" in {total_secs // 60}m {total_secs % 60}s"
            else:
                elapsed_str = f" in {total_secs}s"
            del self._processing_start_time

        if success:
            self.log_message(f"✅ {message}{elapsed_str}", "success")
            # Hint: new messages may have arrived during processing
            self.log_message("💡 Tip: New messages may have arrived during processing — click Check Queue again.", "info")
            self.progress_bar.setFormat(f"✅ Done{elapsed_str}")
            self._schedule_progress_hide()

            # v23 — Phase 5: CLEAR — only mark bot messages as read if ALL
            # links verified (no failures, no pending/processing leftovers).
            # This supersedes the v22 vault-index check, which only verified
            # GitHub URLs and ignored non-GitHub links. The LinkTracker's
            # manifest is now the single source of truth.
            #
            # Fallback: if there is no link_tracker (e.g. vault_path was
            # empty when the worker started), fall back to the v22 vault
            # index check so we don't regress.
            bot_urls = getattr(self, '_bot_queue_urls', [])
            all_clear = False
            if bot_urls:
                worker_lt = getattr(self.worker, 'link_tracker', None) if self.worker else None
                if worker_lt:
                    if worker_lt.get_all_clear():
                        all_clear = True
                        self.log_message("✅ All links verified — marking bot messages as read...", "info")
                        self._mark_bot_messages_read()
                    else:
                        # Show which links are causing the failure
                        pending_links = [l for l in worker_lt.manifest["links"] if l["status"] in ("failed", "processing", "pending")]
                        self.log_message(
                            f"⚠️ {len(pending_links)} link(s) not verified — bot messages NOT marked as read",
                            "warning"
                        )
                        for pl in pending_links[:5]:
                            self.log_message(
                                f"   • [{pl['status']}] {pl['url']}" + (f" — {pl.get('error','')}" if pl.get('error') else ""),
                                "info"
                            )
                        if len(pending_links) > 5:
                            self.log_message(f"   ... and {len(pending_links) - 5} more", "info")
                else:
                    # No link tracker — fall back to the v22 vault-index check
                    all_in_vault = True
                    worker_vi = getattr(self.worker, '_vault_index', None) if self.worker else None
                    if worker_vi:
                        for url in bot_urls:
                            try:
                                norm = normalize_url(url)
                                if not worker_vi.has_url(norm):
                                    all_in_vault = False
                                    break
                            except Exception:
                                all_in_vault = False
                                break
                    if all_in_vault:
                        all_clear = True
                        self.log_message("✅ All repos processed — marking bot messages as read...", "info")
                        self._mark_bot_messages_read()
                    else:
                        self.log_message("⚠️ Some repos failed — bot messages NOT marked as read (retry next time)", "warning")

            # v25 pre-flight: advance last_processed_msg_id ONLY if the
            # batch came from "📬 Process New" AND Phase 5 CLEAR passed.
            # If verification failed, we keep the old ID so the next
            # "Process New" run re-fetches the failed messages and retries
            # them — no link is ever lost.
            pending_update = getattr(self, '_pending_last_processed_update', 0)
            if pending_update and all_clear:
                old_id = int(self.config.get('last_processed_msg_id', 0) or 0)
                if pending_update > old_id:
                    self.config['last_processed_msg_id'] = int(pending_update)
                    try:
                        self.save_config()
                        self.log_message(
                            f"📌 last_processed_msg_id advanced: {old_id} → {pending_update} "
                            f"(next 'Process New' will skip up to ID {pending_update})",
                            "success"
                        )
                    except Exception as save_err:
                        self.log_message(
                            f"⚠️ Failed to persist last_processed_msg_id ({save_err}) — "
                            f"this run is verified but the next 'Process New' will re-fetch these messages.",
                            "warning"
                        )
            elif pending_update and not all_clear:
                self.log_message(
                    f"⏸️ last_processed_msg_id NOT advanced (some links failed verification) — "
                    f"next 'Process New' will re-fetch messages newer than ID "
                    f"{int(self.config.get('last_processed_msg_id', 0) or 0)} and retry failed links.",
                    "warning"
                )
            # Clear the pending flag regardless — it only applies to this batch.
            self._pending_last_processed_update = 0

            self._show_custom_message_box("Processing Complete", f"{message}{elapsed_str}", success=True)
        else:
            self.log_message(f"❌ {message}", "error")
            self.progress_bar.setFormat("❌ Failed")
            self._schedule_progress_hide()
            self._show_custom_message_box("Processing Error", message, success=False)

    def _on_disk_full(self, path):
        """Handle disk full — show dialog, wait for user to free space, then resume."""
        msg = f"Disk is full!\n\nCannot write:\n{path}\n\nFree up disk space, then click OK to resume processing."
        self.log_message("💾 DISK FULL — processing paused. Free space and click OK.", "error")
        # Use theme-aware question dialog
        if self._show_custom_question("💾 Disk Full", msg):
            self.log_message("▶️ Resuming processing after disk full...", "info")
            if self.worker:
                self.worker._disk_full_paused = False
        else:
            self.log_message("⏹️ Aborting due to disk full.", "error")
            if self.worker:
                self.worker.stop()
                self.worker._disk_full_paused = False

    def _on_llm_failed(self, repo_name, worker):
        """LLM failed for a repo — show a custom blocking dialog with buttons
        + a model dropdown. The worker thread is BLOCKED on
        `_llm_retry_event` until we call `worker.resolve_llm_failure(response)`.

        Response values sent back to the worker:
          - 'skip'   — skip this repo, continue batch
          - 'retry'  — retry with the same model
          - '<name>' — retry with this model name (from the dropdown)
          - 'stop'   — stop the batch

        v30 — Fix (model persistence): when a DIFFERENT model is chosen (and
        "Remember" is checked, default ON), the worker persists it via
        _apply_model_choice(): it applies to the rest of the batch, the
        Settings combo, and config.json. This dialog used to reappear for
        EVERY link because the choice was only used for a single retry.
        """
        failed_model = (self.config.get('ollama', {}) or {}).get('model', '') \
            if self.config.get('llm_provider', 'ollama') == 'ollama' \
            else self.config.get('cloud_model', '')
        self.log_message(
            f"❌ LLM failed for '{repo_name}' after 3 attempts (model '{failed_model}').",
            "error"
        )

        dialog = QDialog(self)
        dialog.setWindowTitle("🤖 LLM Analysis Failed")
        dialog.setModal(True)
        dialog.setMinimumWidth(460)

        # Theme-aware colors (matches _show_custom_message_box)
        is_dark = getattr(self, '_dark_mode', False)
        if is_dark:
            bg_color = "#2B2639"
            text_color = "#F2EEE7"
            border_color = "#3B344F"
            input_bg = "#241F31"
        else:
            bg_color = "#FFFFFF"
            text_color = "#423A52"
            border_color = "#F2EEE7"
            input_bg = "#FDFCF8"

        dialog.setStyleSheet(f"""
            QDialog {{ background-color: {bg_color}; }}
            QLabel {{ color: {text_color}; background: transparent; }}
            QComboBox, QComboBox QAbstractItemView {{
                background-color: {input_bg};
                color: {text_color};
                border: 1px solid {border_color};
                border-radius: 4px;
                padding: 6px 10px;
            }}
        """)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)

        # Header
        header = QHBoxLayout()
        icon_label = QLabel("🤖")
        icon_label.setStyleSheet("font-size: 28px; background: transparent;")
        header.addWidget(icon_label)
        title_label = QLabel(f" LLM Failed: {repo_name}")
        title_color = "#DC2626" if not is_dark else "#F87171"
        title_label.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {title_color}; background: transparent;")
        header.addWidget(title_label)
        header.addStretch()
        layout.addLayout(header)

        # Description
        desc = QLabel(
            f"The LLM failed to analyze this repo after 3 attempts.\n"
            f"Failed model: <b>{failed_model or 'unknown'}</b>\n"
            "Choose how to proceed:"
        )
        desc.setStyleSheet(f"font-size: 13px; color: {text_color}; background: transparent;")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # Fetch available models from Ollama (best-effort, may be empty/slow)
        try:
            model_names, _err = self._get_ollama_model_names(self.ollama_url.text())
        except Exception:
            model_names = []
        if not model_names:
            model_names = []

        # Model dropdown
        model_row = QHBoxLayout()
        model_row.addWidget(QLabel("Retry with:"))
        model_combo = QComboBox()
        model_combo.addItem("— Same model —", "retry")
        for name in model_names:
            model_combo.addItem(name, name)
        model_row.addWidget(model_combo, 1)
        layout.addLayout(model_row)

        # v30 — Fix (model persistence): default the dropdown to a DIFFERENT
        # model when one exists — the same model just failed 3 times.
        for idx in range(model_combo.count()):
            data = model_combo.itemData(idx)
            if data and data != "retry" and data != failed_model:
                model_combo.setCurrentIndex(idx)
                break

        # v30 — "Remember" checkbox: save the chosen model as the default
        # (Settings + config.json + rest of batch). ON by default — that is
        # what users expect: pick once, keep going.
        remember_check = QCheckBox("Remember this model (apply to the rest of the batch and save to Settings)")
        remember_check.setChecked(True)
        remember_check.setStyleSheet(f"color: {text_color}; background: transparent; font-size: 12px;")
        layout.addWidget(remember_check)

        # Buttons
        btn_row = QHBoxLayout()
        skip_btn = QPushButton("⏭️ Skip")
        skip_btn.setStyleSheet(self._btn_style(COLORS['neutral'], COLORS['neutral_hover'], variant='outline'))
        retry_same_btn = QPushButton("🔁 Retry Same")
        retry_same_btn.setStyleSheet(self._btn_style(COLORS['primary'], COLORS['primary_hover']))
        retry_with_btn = QPushButton("🔁 Retry With ▾")
        retry_with_btn.setStyleSheet(self._btn_style(COLORS['cta'], COLORS['cta_hover'], text=COLORS['cta_text']))
        stop_btn = QPushButton("⏹ Stop Batch")
        stop_btn.setStyleSheet(self._btn_style(COLORS['error_deep'], '#8F1B2E', variant='outline'))
        btn_row.addWidget(skip_btn)
        btn_row.addWidget(retry_same_btn)
        btn_row.addWidget(retry_with_btn)
        btn_row.addWidget(stop_btn)
        layout.addLayout(btn_row)

        # Capture the decision in a closure so we can deliver it to the worker
        response_holder = {"value": None, "remember": True}

        def _respond(value: str):
            response_holder["value"] = value
            response_holder["remember"] = remember_check.isChecked()
            dialog.accept()

        skip_btn.clicked.connect(lambda: _respond("skip"))
        retry_same_btn.clicked.connect(lambda: _respond("retry"))
        retry_with_btn.clicked.connect(lambda: _respond(model_combo.currentData() or "retry"))
        stop_btn.clicked.connect(lambda: _respond("stop"))

        self._animate_dialog(dialog)
        dialog.exec()

        # If user closed the dialog with the X button (no decision), default to skip
        decision = response_holder["value"] or "skip"
        remember = response_holder["remember"]
        self.log_message(f"👉 User chose: '{decision}'" + (" (remember)" if remember else ""), "info")

        # v30 — Fix (model persistence): persist the model choice immediately
        # (config + Settings combo + config.json). The worker ALSO applies it
        # to self.config, but doing it here guarantees the GUI + disk state
        # even if the worker finishes first.
        if remember and decision and decision not in ("skip", "retry", "stop"):
            self._on_model_changed(
                self.config.get('llm_provider', 'ollama'), decision
            )
        # Unblock the worker thread
        worker.resolve_llm_failure(decision)

    def _on_model_changed(self, provider: str, model_name: str):
        """v30 — Fix (model persistence): a new LLM model was selected
        (from the failure dialog, the auto-switch, or the worker). Sync the
        Settings combo + self.config (in place) + config.json so the choice
        survives the batch AND the next app launch."""
        if not model_name:
            return
        try:
            if provider == 'cloud':
                self.config['cloud_model'] = model_name
                if hasattr(self, 'cloud_model'):
                    self.cloud_model.setText(model_name)
            else:
                ollama_cfg = self.config.get('ollama')
                if not isinstance(ollama_cfg, dict):
                    ollama_cfg = {}
                    self.config['ollama'] = ollama_cfg
                ollama_cfg['model'] = model_name
                # Sync the Settings dropdown so the user SEES the change.
                if hasattr(self, 'ollama_model'):
                    combo = self.ollama_model
                    idx = combo.findText(model_name)
                    if idx < 0:
                        combo.insertItem(0, model_name)
                        idx = 0
                    combo.setCurrentIndex(idx)
            # Persist to disk (save_config now MERGES — this can no longer
            # wipe cloudflare_*/gdrive_* keys).
            self.save_config()
            self.log_message(
                f"✅ LLM model set to '{model_name}' for the rest of the batch "
                f"and saved to Settings.",
                "success"
            )
        except Exception as e:
            self.log_message(f"⚠️ Could not save model choice: {e}", "warning")
