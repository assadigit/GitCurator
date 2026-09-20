#!/usr/bin/env python3
"""gitcurator.gui.main_window.backup_tab — the Backup tab mixin.

Backup-tab construction, folder browse, save-config, status refresh,
backup now / result, export-zip / result, restore (verbatim methods of
the original MainWindow).
"""

from gitcurator.gui._qt import *  # noqa: F401,F403 — Qt widget names
from gitcurator.gui.main_window._deps import *  # noqa: F401,F403

__all__ = ["BackupTabMixin"]


class BackupTabMixin:
    """BackupTabMixin — see module docstring (methods are verbatim moves)."""


    # ========================================================================
    # v29.4 — Backup tab UI
    # ========================================================================

    def _create_backup_tab(self):
        """Create the Backup tab (local folder + timestamped zip).

        v32.2 — scroll + compaction. The four sections' natural height
        exceeds the fixed tab pane inside the 1000×750 window, so the tab
        content is wrapped in a QScrollArea (via _wrap_scroll in _build_ui):
        the scroll area's own height matches the tab pane while the inner
        content panel expands to whatever height the sections need —
        vertical scrolling only, horizontal always off. The sections are
        also COMPACTED so most content fits without scrolling:
        - status dots share the action/keep rows (no dedicated status lines)
        - the two settings checkboxes sit side-by-side on one row
        - info notes are single wrapped sentences (no hard line breaks)
        - folder/repo inputs keep their full-width rows
        """
        tab = QWidget()
        tab.setObjectName("backup_tab")
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)

        # ---- 💾 Vault Backup (local zip + FIFO rotation) ----
        status_group = QGroupBox("💾 Vault Backup")
        status_layout = QVBoxLayout(status_group)
        status_layout.setSpacing(6)

        # Backup folder picker — the section's one input row.
        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("Backup folder:"))
        self.backup_folder_input = QLineEdit()
        self.backup_folder_input.setPlaceholderText("e.g., C:\\Users\\You\\OneDrive\\Vault-Backups")
        self.backup_folder_input.setText(self.config.get('backup_folder', ''))
        folder_row.addWidget(self.backup_folder_input, 1)

        browse_btn = QPushButton("📂 Browse")
        browse_btn.clicked.connect(self._backup_browse_folder)
        folder_row.addWidget(browse_btn)
        status_layout.addLayout(folder_row)

        # Rotation + status dot on ONE row (v32.2 compaction).
        keep_row = QHBoxLayout()
        keep_row.addWidget(QLabel("Keep last:"))
        self.backup_max_input = QLineEdit()
        self.backup_max_input.setPlaceholderText("10")
        self.backup_max_input.setMaximumWidth(60)
        self.backup_max_input.setText(str(self.config.get('backup_max', 10)))
        keep_row.addWidget(self.backup_max_input)
        keep_row.addWidget(QLabel("backups (FIFO rotation)"))
        keep_row.addStretch()
        self.backup_status_label = QLabel("● Disabled")
        self.backup_status_label.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {self._status_colors()['error']};")
        keep_row.addWidget(self.backup_status_label)
        status_layout.addLayout(keep_row)

        # Action buttons — v31.1 hierarchy: ONE filled primary (Backup Now)
        # + outlined secondary (Restore); the enable toggle rides the same
        # row, right-aligned (v32.2 compaction). '📤 Export ZIP' lives in the
        # global 'More' overflow menu (export = infrequent action).
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.backup_now_btn = QPushButton("💾 Backup Now")
        self._style_btn(self.backup_now_btn, 'primary')
        self.backup_now_btn.clicked.connect(self._backup_now)
        btn_row.addWidget(self.backup_now_btn)

        self.backup_restore_btn = QPushButton("📂 Restore")
        self._style_btn(self.backup_restore_btn, 'secondary')
        self.backup_restore_btn.clicked.connect(self._backup_restore)
        btn_row.addWidget(self.backup_restore_btn)

        btn_row.addStretch()
        self.backup_enabled_check = QCheckBox("Enable auto-backup after each batch")
        self.backup_enabled_check.setToolTip(
            "Create a timestamped vault ZIP after every processing batch")
        self.backup_enabled_check.setChecked(self.config.get('backup_enabled', False))
        btn_row.addWidget(self.backup_enabled_check)
        status_layout.addLayout(btn_row)

        # One wrapped note (was a 2-line hard-wrapped paragraph).
        info_label = QLabel(
            "💡 Any folder works — a OneDrive/Dropbox/Drive sync folder "
            "uploads your zips to the cloud automatically. Zero setup, no OAuth."
        )
        info_label.setWordWrap(True)
        info_label.setObjectName("info_note")
        status_layout.addWidget(info_label)

        layout.addWidget(status_group)

        # ---- VaultSeal (v31): automatic GitHub mirror ----
        seal_group = QGroupBox("🛡️ VaultSeal — GitHub Mirror")
        seal_layout = QVBoxLayout(seal_group)
        seal_layout.setSpacing(6)

        # Both settings on ONE row (v32.2 compaction).
        seal_checks_row = QHBoxLayout()
        self.vaultseal_enabled_check = QCheckBox("Seal after every run")
        self.vaultseal_enabled_check.setToolTip(
            "Commit the whole vault to git after every run and push it to a "
            "PRIVATE GitHub repository")
        self.vaultseal_enabled_check.setChecked((self.config.get('vaultseal') or {}).get('enabled', True))
        self.vaultseal_enabled_check.toggled.connect(self._vaultseal_refresh_status)
        seal_checks_row.addWidget(self.vaultseal_enabled_check)

        self.vaultseal_push_check = QCheckBox("Push to GitHub")
        self.vaultseal_push_check.setToolTip(
            "Push the seal to GitHub (uses the GitHub token from Settings)")
        self.vaultseal_push_check.setChecked((self.config.get('vaultseal') or {}).get('auto_push', True))
        seal_checks_row.addWidget(self.vaultseal_push_check)
        seal_checks_row.addStretch()
        seal_layout.addLayout(seal_checks_row)

        repo_row = QHBoxLayout()
        repo_row.addWidget(QLabel("Backup repo:"))
        self.vaultseal_repo_input = QLineEdit()
        self.vaultseal_repo_input.setPlaceholderText("auto — derived from the vault folder name")
        self.vaultseal_repo_input.setText((self.config.get('vaultseal') or {}).get('repo_name', ''))
        repo_row.addWidget(self.vaultseal_repo_input, 1)
        seal_layout.addLayout(repo_row)

        # Action + status dot on ONE row (v32.2 compaction).
        seal_btn_row = QHBoxLayout()
        self.vaultseal_now_btn = QPushButton("🛡️ Seal Now")
        # v31.1: Backup Now is the Backup tab's ONE filled primary — Seal Now
        # is the outlined secondary path.
        self._style_btn(self.vaultseal_now_btn, 'secondary')
        self.vaultseal_now_btn.clicked.connect(self._vaultseal_now)
        seal_btn_row.addWidget(self.vaultseal_now_btn)
        seal_btn_row.addStretch()

        self.vaultseal_status_label = QLabel("● —")
        self.vaultseal_status_label.setStyleSheet("font-size: 13px; font-weight: bold; color: #6C6480;")
        seal_btn_row.addWidget(self.vaultseal_status_label)
        seal_layout.addLayout(seal_btn_row)

        # One wrapped note (was a 4-line hard-wrapped paragraph).
        seal_info = QLabel(
            "💡 Obsidian's free tier has no sync — VaultSeal commits the whole "
            "vault after every run and pushes it to a PRIVATE repository (full "
            "history, restore with git clone). Machine state (workspace.json, "
            ".trash) is excluded; an unchanged vault is a no-op."
        )
        seal_info.setWordWrap(True)
        seal_info.setObjectName("info_note")
        seal_layout.addWidget(seal_info)

        layout.addWidget(seal_group)

        self._vaultseal_refresh_status()

        # ---- GoodRepos (v32): public curated directory ----
        good_group = QGroupBox("🌟 Good Repos — Public Directory")
        good_layout = QVBoxLayout(good_group)
        good_layout.setSpacing(6)

        # Both settings on ONE row (v32.2 compaction).
        good_checks_row = QHBoxLayout()
        self.goodrepos_enabled_check = QCheckBox("Publish after every run")
        self.goodrepos_enabled_check.setToolTip(
            "Publish the curated directory to a PUBLIC GitHub repo after "
            "every run")
        self.goodrepos_enabled_check.setChecked((self.config.get('goodrepos') or {}).get('enabled', True))
        self.goodrepos_enabled_check.toggled.connect(self._goodrepos_refresh_status)
        good_checks_row.addWidget(self.goodrepos_enabled_check)

        self.goodrepos_push_check = QCheckBox("Push to GitHub")
        self.goodrepos_push_check.setToolTip(
            "Push the directory to GitHub (uses the GitHub token from Settings)")
        self.goodrepos_push_check.setChecked((self.config.get('goodrepos') or {}).get('auto_push', True))
        good_checks_row.addWidget(self.goodrepos_push_check)
        good_checks_row.addStretch()
        good_layout.addLayout(good_checks_row)

        good_repo_row = QHBoxLayout()
        good_repo_row.addWidget(QLabel("Directory repo:"))
        self.goodrepos_repo_input = QLineEdit()
        self.goodrepos_repo_input.setPlaceholderText("good-repos")
        self.goodrepos_repo_input.setText((self.config.get('goodrepos') or {}).get('repo_name', 'good-repos'))
        good_repo_row.addWidget(self.goodrepos_repo_input, 1)
        good_layout.addLayout(good_repo_row)

        # Action + status dot on ONE row (v32.2 compaction).
        good_btn_row = QHBoxLayout()
        self.goodrepos_now_btn = QPushButton("🌟 Publish Now")
        # v32: outlined secondary — Backup Now stays this tab's one filled primary.
        self._style_btn(self.goodrepos_now_btn, 'secondary')
        self.goodrepos_now_btn.clicked.connect(self._goodrepos_now)
        good_btn_row.addWidget(self.goodrepos_now_btn)
        good_btn_row.addStretch()

        self.goodrepos_status_label = QLabel("● —")
        self.goodrepos_status_label.setStyleSheet("font-size: 13px; font-weight: bold; color: #6C6480;")
        good_btn_row.addWidget(self.goodrepos_status_label)
        good_layout.addLayout(good_btn_row)

        # One wrapped note (was a 4-line hard-wrapped paragraph).
        good_info = QLabel(
            "💡 Every curated repo becomes an entry in a browsable, emoji-rich "
            "README directory (organized like AI → Skills → …) with the full "
            "notes mirrored into category folders. PUBLIC by design — "
            "everyone can browse and benefit from your curation."
        )
        good_info.setWordWrap(True)
        good_info.setObjectName("info_note_indigo")
        good_layout.addWidget(good_info)

        layout.addWidget(good_group)

        self._goodrepos_refresh_status()

        # ---- Dashboard Link ----
        dash_group = QGroupBox("📊 Dashboard")
        dash_layout = QVBoxLayout(dash_group)
        dash_layout.setSpacing(6)

        # URL + action on ONE row (v32.2 compaction — was 2 rows).
        dash_row = QHBoxLayout()
        dash_row.addWidget(QLabel("Worker URL:"))
        self.dash_worker_url_input = QLineEdit()
        self.dash_worker_url_input.setPlaceholderText("https://github-to-obsidian-bot.your-subdomain.workers.dev")
        self.dash_worker_url_input.setText(self.config.get('cloudflare_worker_url', ''))
        dash_row.addWidget(self.dash_worker_url_input, 1)

        open_dash_btn = QPushButton("📊 Open Dashboard")
        self._style_btn(open_dash_btn, 'secondary')
        open_dash_btn.clicked.connect(self._open_dashboard_from_backup_tab)
        dash_row.addWidget(open_dash_btn)
        dash_layout.addLayout(dash_row)

        # One wrapped note (was 2 hard-wrapped lines).
        dash_info = QLabel(
            "📊 Bot stats, pending links and recent activity — served by your "
            "Cloudflare Worker, no separate deployment needed."
        )
        dash_info.setWordWrap(True)
        dash_info.setObjectName("info_note_indigo")
        dash_layout.addWidget(dash_info)

        layout.addWidget(dash_group)

        # v32.2: initialize the backup status dot from config (the seal and
        # goodrepos sections already self-refresh at construction).
        self._backup_refresh_status()

        # v31.1: no filler stretch. v32.2: _wrap_scroll in _build_ui pins this
        # tab's scroll area to the tab pane's fixed visible height while this
        # content panel expands to the height the four sections actually need
        # (vertical-only scrolling, horizontal always off).
        return tab

    def _backup_browse_folder(self):
        """Open folder picker for backup destination."""
        folder = QFileDialog.getExistingDirectory(self, "Select Backup Folder")
        if folder:
            self.backup_folder_input.setText(folder)
            self._backup_save_config()
            self._backup_refresh_status()

    def _backup_save_config(self):
        """Save backup config."""
        self.config['backup_enabled'] = self.backup_enabled_check.isChecked()
        self.config['backup_folder'] = self.backup_folder_input.text().strip()
        try:
            self.config['backup_max'] = max(1, int(self.backup_max_input.text() or '10'))
        except ValueError:
            self.config['backup_max'] = 10
        self.config['cloudflare_worker_url'] = self.dash_worker_url_input.text().strip().rstrip('/')
        # v31 — VaultSeal settings (Backup tab)
        vs = self.config.get('vaultseal')
        if not isinstance(vs, dict):
            vs = {}
        if hasattr(self, 'vaultseal_enabled_check'):
            vs['enabled'] = self.vaultseal_enabled_check.isChecked()
            vs['auto_push'] = self.vaultseal_push_check.isChecked()
            vs['repo_name'] = self.vaultseal_repo_input.text().strip()
        self.config['vaultseal'] = vs
        # v32 — GoodRepos settings (Backup tab)
        gr = self.config.get('goodrepos')
        if not isinstance(gr, dict):
            gr = {}
        if hasattr(self, 'goodrepos_enabled_check'):
            gr['enabled'] = self.goodrepos_enabled_check.isChecked()
            gr['auto_push'] = self.goodrepos_push_check.isChecked()
            gr['repo_name'] = self.goodrepos_repo_input.text().strip() or 'good-repos'
        self.config['goodrepos'] = gr
        self.save_config()

    def _backup_refresh_status(self):
        """Refresh backup status label."""
        import os
        backup_folder = self.backup_folder_input.text().strip()
        backup_enabled = self.backup_enabled_check.isChecked()
        if backup_enabled and backup_folder:
            if os.path.isdir(backup_folder):
                self.backup_status_label.setText("● Ready")
                self.backup_status_label.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {self._status_colors()['success']};")
            else:
                self.backup_status_label.setText("● Folder not found")
                self.backup_status_label.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {self._status_colors()['error']};")
        elif backup_enabled:
            self.backup_status_label.setText("● No folder selected")
            self.backup_status_label.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {self._status_colors()['warning']};")
        else:
            self.backup_status_label.setText("● Disabled")
            self.backup_status_label.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {self._status_colors()['error']};")

    def _backup_now(self):
        """Create a local backup now."""
        self._backup_save_config()
        vault_path = self.config.get('vault_path', '')
        backup_folder = self.config.get('backup_folder', '')

        if not vault_path:
            self._show_custom_message_box("No Vault", "Set a vault path first (📁 Vault tab).", success=False)
            return
        if not backup_folder:
            self._show_custom_message_box("No Backup Folder", "Select a backup folder first.", success=False)
            return

        import os
        if not os.path.isdir(backup_folder):
            try:
                os.makedirs(backup_folder, exist_ok=True)
            except Exception as e:
                self._show_custom_message_box("Error", f"Cannot create folder: {e}", success=False)
                return

        # Run backup in background thread
        class BackupWorker(QThread):
            done = pyqtSignal(bool, str)
            def __init__(self, vault_path, backup_folder, max_backups):
                super().__init__()
                self.vault_path = vault_path
                self.backup_folder = backup_folder
                self.max_backups = max_backups
            def run(self):
                try:
                    import os, zipfile, glob
                    from datetime import datetime, timezone
                    from pathlib import Path
                    timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%M%SZ')
                    file_name = f"vault-{timestamp}.zip"
                    zip_path = os.path.join(self.backup_folder, file_name)

                    EXCLUDE = {'config.json', '.git', '__pycache__'}
                    EXCLUDE_SUFFIX = ('.lock',)
                    EXCLUDE_PREFIX = ('.obsidian/workspace', '.obsidian/app.json')

                    file_count = 0
                    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
                        vault_path_obj = Path(self.vault_path)
                        for root, dirs, files in os.walk(vault_path_obj):
                            root_path = Path(root)
                            dirs[:] = [d for d in dirs if d not in EXCLUDE]
                            for file in files:
                                file_path = root_path / file
                                if file in EXCLUDE:
                                    continue
                                if any(file.endswith(s) for s in EXCLUDE_SUFFIX):
                                    continue
                                if any(p in str(file_path) for p in EXCLUDE_PREFIX):
                                    continue
                                arcname = file_path.relative_to(vault_path_obj.parent)
                                zf.write(file_path, arcname)
                                file_count += 1

                    # FIFO rotation
                    backups = sorted(glob.glob(os.path.join(self.backup_folder, "vault-*.zip")))
                    if len(backups) > self.max_backups:
                        for old_file in backups[:len(backups) - self.max_backups]:
                            try:
                                os.remove(old_file)
                            except:
                                pass

                    size = os.path.getsize(zip_path)
                    size_str = f"{size/1024/1024:.1f} MB" if size >= 1024*1024 else f"{size/1024:.1f} KB"
                    self.done.emit(True, f"{file_name} ({file_count} files, {size_str})")
                except Exception as e:
                    self.done.emit(False, str(e))

        self._backup_worker = BackupWorker(vault_path, backup_folder, self.config.get('backup_max', 10))
        self._backup_worker.done.connect(lambda ok, msg: self._backup_result(ok, msg))
        self._backup_worker.start()
        self.backup_now_btn.setEnabled(False)
        self.backup_now_btn.setText("Backing up...")

    def _backup_result(self, success, message):
        self.backup_now_btn.setEnabled(True)
        self.backup_now_btn.setText("💾 Backup Now")
        if success:
            self.log_message(f"💾 Backup created: {message}", "success")
            self._show_custom_message_box("Backup Complete", message, success=True)
        else:
            self.log_message(f"❌ Backup failed: {message}", "error")
            self._show_custom_message_box("Backup Failed", message, success=False)

    def _backup_export_zip(self):
        """Export a timestamped ZIP to a user-chosen location."""
        vault_path = self.config.get('vault_path', '')
        if not vault_path:
            self._show_custom_message_box("No Vault", "Set a vault path first (📁 Vault tab).", success=False)
            return

        from datetime import datetime
        timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        default_name = f"vault-export-{timestamp}.zip"

        file_path, _ = QFileDialog.getSaveFileName(
            self, "Export Vault ZIP", default_name, "ZIP archives (*.zip)"
        )
        if not file_path:
            return

        # Run in background
        class ExportWorker(QThread):
            done = pyqtSignal(bool, str)
            def __init__(self, vault_path, zip_path):
                super().__init__()
                self.vault_path = vault_path
                self.zip_path = zip_path
            def run(self):
                try:
                    import os, zipfile
                    from pathlib import Path
                    EXCLUDE = {'config.json', '.git', '__pycache__'}
                    EXCLUDE_SUFFIX = ('.lock',)
                    EXCLUDE_PREFIX = ('.obsidian/workspace', '.obsidian/app.json')

                    file_count = 0
                    with zipfile.ZipFile(self.zip_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
                        vault_path_obj = Path(self.vault_path)
                        for root, dirs, files in os.walk(vault_path_obj):
                            root_path = Path(root)
                            dirs[:] = [d for d in dirs if d not in EXCLUDE]
                            for file in files:
                                file_path = root_path / file
                                if file in EXCLUDE:
                                    continue
                                if any(file.endswith(s) for s in EXCLUDE_SUFFIX):
                                    continue
                                if any(p in str(file_path) for p in EXCLUDE_PREFIX):
                                    continue
                                arcname = file_path.relative_to(vault_path_obj.parent)
                                zf.write(file_path, arcname)
                                file_count += 1

                    size = os.path.getsize(self.zip_path)
                    size_str = f"{size/1024/1024:.1f} MB" if size >= 1024*1024 else f"{size/1024:.1f} KB"
                    self.done.emit(True, f"{self.zip_path} ({file_count} files, {size_str})")
                except Exception as e:
                    self.done.emit(False, str(e))

        self._export_worker = ExportWorker(vault_path, file_path)
        self._export_worker.done.connect(lambda ok, msg: self._export_result(ok, msg))
        self._export_worker.start()
        self.backup_export_btn.setEnabled(False)
        self.backup_export_btn.setText("Exporting...")

    def _export_result(self, success, message):
        self.backup_export_btn.setEnabled(True)
        self.backup_export_btn.setText("📤 Export ZIP (timestamped)")
        if success:
            self.log_message(f"📤 Exported: {message}", "success")
            self._show_custom_message_box("Export Complete", f"Saved to:\n{message}", success=True)
        else:
            self.log_message(f"❌ Export failed: {message}", "error")
            self._show_custom_message_box("Export Failed", message, success=False)

    def _backup_restore(self):
        """Open restore dialog showing local backups."""
        self._backup_save_config()
        backup_folder = self.config.get('backup_folder', '')
        if not backup_folder:
            self._show_custom_message_box("No Backup Folder", "Select a backup folder first.", success=False)
            return

        import os, glob
        backups = sorted(glob.glob(os.path.join(backup_folder, "vault-*.zip")), reverse=True)
        if not backups:
            self._show_custom_message_box("No Backups", "No backups found yet.\n\nClick 'Backup Now' first.", success=True)
            return

        # Theme-aware restore dialog
        is_dark = getattr(self, '_dark_mode', False)
        if is_dark:
            bg = "#2B2639"; text_color = "#F2EEE7"; border = "#3B344F"; input_bg = "#241F31"
        else:
            bg = "#FFFFFF"; text_color = "#241F31"; border = "#F2EEE7"; input_bg = "#FDFCF8"

        dialog = QDialog(self)
        dialog.setWindowTitle("📂 Restore from Backup")
        dialog.setMinimumWidth(450)
        dialog.setStyleSheet(
            f"QDialog {{ background-color: {bg}; }} "
            f"QLabel {{ color: {text_color}; }} "
            f"QListWidget {{ background-color: {input_bg}; color: {text_color}; border: 1px solid {border}; border-radius: 4px; }}"
        )
        layout = QVBoxLayout(dialog)

        layout.addWidget(QLabel(f"Found {len(backups)} backup(s):"))
        list_widget = QListWidget()
        for b in backups:
            name = os.path.basename(b)
            size = os.path.getsize(b)
            size_str = f"{size/1024/1024:.1f} MB" if size >= 1024*1024 else f"{size/1024:.1f} KB"
            list_widget.addItem(f"{name}    [ {size_str} ]")
        layout.addWidget(list_widget)

        def do_restore():
            if list_widget.currentRow() < 0:
                return
            backup_path = backups[list_widget.currentRow()]
            reply = self._show_custom_question("⚠️ Restore",
                f"Restore from:\n{os.path.basename(backup_path)}\n\n"
                f"This will extract to a NEW folder next to your current vault\n"
                f"(current vault is NOT touched). Continue?")
            if reply:
                vault_path = self.config.get('vault_path', '')
                import zipfile
                from datetime import datetime
                from pathlib import Path
                ts = datetime.now().strftime('%Y%m%d-%H%M%S')
                new_folder = Path(vault_path).parent / f"vault-restored-{ts}"
                new_folder.mkdir(parents=True, exist_ok=True)
                try:
                    with zipfile.ZipFile(backup_path, 'r') as zf:
                        zf.extractall(new_folder)
                    self.log_message(f"✅ Restored to: {new_folder}", "success")
                    self._show_custom_message_box("Restore Complete",
                        f"Restored to:\n{new_folder}\n\nReview and merge manually.", success=True)
                except Exception as e:
                    self._show_custom_message_box("Restore Failed", str(e), success=False)

        def do_copy():
            if list_widget.currentRow() < 0:
                return
            backup_path = backups[list_widget.currentRow()]
            save_path, _ = QFileDialog.getSaveFileName(dialog, "Copy Backup",
                os.path.basename(backup_path), "ZIP (*.zip)")
            if save_path:
                import shutil
                try:
                    shutil.copy2(backup_path, save_path)
                    self.log_message(f"✅ Copied to: {save_path}", "success")
                except Exception as e:
                    self._show_custom_message_box("Error", str(e), success=False)

        btn_row = QHBoxLayout()
        restore_btn = QPushButton("📂 Restore to New Folder")
        restore_btn.clicked.connect(do_restore)
        btn_row.addWidget(restore_btn)
        copy_btn = QPushButton("⬇️ Copy ZIP to...")
        copy_btn.clicked.connect(do_copy)
        btn_row.addWidget(copy_btn)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        dialog.exec()
