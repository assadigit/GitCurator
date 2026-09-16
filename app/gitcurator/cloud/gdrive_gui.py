"""
gdrive_gui.py — PyQt6 settings panel for Google Drive backup
==============================================================
A self-contained QWidget that provides:
  - Enable/disable toggle
  - OAuth authorization flow (opens browser)
  - Status display (authorized, token expiry, last backup, backup count)
  - Backup Now button
  - Restore dialog (list backups, restore/download)
  - Client ID / Secret configuration (advanced)

Usage in main.py:
  from gdrive_gui import GDriveSettingsPanel

  panel = GDriveSettingsPanel(self.config, self.cf_manager, parent=self)
  self.settings_tabs.addTab(panel, "Google Drive")
"""

import os
import threading
from datetime import datetime, timezone

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QLineEdit, QPushButton, QCheckBox, QGroupBox,
    QProgressBar, QTextEdit, QMessageBox, QFrame,
    QDialog, QDialogButtonBox, QListWidget, QListWidgetItem,
    QFileDialog, QSplitter
)

from gitcurator.cloud.gdrive_backup import GDriveBackup


# ========================================
# OAuth Worker Thread (runs browser flow)
# ========================================

class OAuthWorker(QThread):
    """Runs the OAuth flow in a background thread (includes local HTTP server)."""
    result = pyqtSignal(bool, str)

    def __init__(self, gdrive: GDriveBackup):
        super().__init__()
        self.gdrive = gdrive

    def run(self):
        try:
            success, message = self.gdrive.authorize()
            self.result.emit(success, message)
        except Exception as e:
            self.result.emit(False, str(e))


# ========================================
# List Backups Worker Thread
# ========================================

class ListBackupsWorker(QThread):
    result = pyqtSignal(list)

    def __init__(self, gdrive: GDriveBackup):
        super().__init__()
        self.gdrive = gdrive

    def run(self):
        try:
            backups = self.gdrive.list_backups()
            self.result.emit(backups)
        except Exception as e:
            self.result.emit([])


# ========================================
# Restore Worker Thread
# ========================================

class RestoreWorker(QThread):
    result = pyqtSignal(bool, str, str)  # success, message, new_path

    def __init__(self, gdrive: GDriveBackup, file_id: str, mode: str, vault_path: str = ''):
        super().__init__()
        self.gdrive = gdrive
        self.file_id = file_id
        self.mode = mode  # 'new_folder' | 'replace' | 'download'
        self.vault_path = vault_path

    def run(self):
        try:
            if self.mode == 'new_folder':
                parent_dir = os.path.dirname(self.vault_path) if self.vault_path else os.getcwd()
                result = self.gdrive.restore_to_new_folder(self.file_id, parent_dir)
                if result:
                    self.result.emit(True, "Restored to new folder", result)
                else:
                    self.result.emit(False, "Restore failed", "")

            elif self.mode == 'replace':
                if not self.vault_path:
                    self.result.emit(False, "No vault path set", "")
                    return

                reply = True  # Confirmation handled by caller
                success, safety_net = self.gdrive.replace_current_vault(self.file_id, self.vault_path)
                if success:
                    self.result.emit(True, f"Vault replaced. Safety net: {safety_net}", safety_net)
                else:
                    self.result.emit(False, "Replace failed", "")

            elif self.mode == 'download':
                # Ask for save location
                # This is handled in the main thread (needs QFileDialog)
                self.result.emit(False, "Use download dialog instead", "")

        except Exception as e:
            self.result.emit(False, str(e), "")


# ========================================
# Restore Dialog
# ========================================

class RestoreDialog(QDialog):
    """Dialog for restoring from a Google Drive backup."""

    def __init__(self, gdrive: GDriveBackup, vault_path: str, parent=None):
        super().__init__(parent)
        self.gdrive = gdrive
        self.vault_path = vault_path
        self.selected_file_id = None
        self.setWindowTitle("Restore from Google Drive")
        self.setMinimumWidth(600)
        self.setMinimumHeight(400)

        self._init_ui()
        self._load_backups()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # Header
        header = QLabel("💾 Available Backups")
        header.setStyleSheet("font-size: 16px; font-weight: bold;")
        layout.addWidget(header)

        # Loading label
        self.loading_label = QLabel("Loading backups...")
        layout.addWidget(self.loading_label)

        # Backup list
        self.backup_list = QListWidget()
        self.backup_list.setStyleSheet(
            "QListWidget { border: 1px solid #ddd; border-radius: 6px; }"
            "QListWidget::item { padding: 8px; border-bottom: 1px solid #eee; }"
            "QListWidget::item:selected { background: #6366F1; color: white; }"
        )
        layout.addWidget(self.backup_list)

        # Buttons
        button_layout = QHBoxLayout()

        self.restore_new_btn = QPushButton("📂 Restore to New Folder")
        self.restore_new_btn.setEnabled(False)
        self.restore_new_btn.setStyleSheet(
            "QPushButton { background: #10B981; color: white; padding: 8px 16px; border-radius: 6px; }"
            "QPushButton:hover { background: #059669; }"
            "QPushButton:disabled { background: #ccc; }"
        )
        self.restore_new_btn.clicked.connect(self._on_restore_new)
        button_layout.addWidget(self.restore_new_btn)

        self.replace_btn = QPushButton("⚠️ Replace Current Vault")
        self.replace_btn.setEnabled(False)
        self.replace_btn.setStyleSheet(
            "QPushButton { background: #EF4444; color: white; padding: 8px 16px; border-radius: 6px; }"
            "QPushButton:hover { background: #DC2626; }"
            "QPushButton:disabled { background: #ccc; }"
        )
        self.replace_btn.clicked.connect(self._on_replace)
        button_layout.addWidget(self.replace_btn)

        self.download_btn = QPushButton("⬇️ Download ZIP")
        self.download_btn.setEnabled(False)
        self.download_btn.clicked.connect(self._on_download)
        button_layout.addWidget(self.download_btn)

        button_layout.addStretch()

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        button_layout.addWidget(close_btn)

        layout.addLayout(button_layout)

        # Status label
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #666; font-size: 12px;")
        layout.addWidget(self.status_label)

    def _load_backups(self):
        """Load backups in background thread."""
        self.loading_label.setText("Loading backups...")
        self.backup_list.clear()

        self.list_worker = ListBackupsWorker(self.gdrive)
        self.list_worker.result.connect(self._on_backups_loaded)
        self.list_worker.start()

    def _on_backups_loaded(self, backups: list):
        self.loading_label.setText(f"Found {len(backups)} backup(s)" if backups else "No backups found")

        for backup in backups:
            name = backup.get('name', 'unknown')
            size = int(backup.get('size', 0))
            created = backup.get('createdTime', '')
            file_id = backup.get('id', '')

            # Format size
            if size >= 1024 * 1024:
                size_str = f"{size / (1024 * 1024):.1f} MB"
            elif size >= 1024:
                size_str = f"{size / 1024:.1f} KB"
            else:
                size_str = f"{size} B"

            # Format date
            try:
                dt = datetime.fromisoformat(created.replace('Z', '+00:00'))
                date_str = dt.strftime('%Y-%m-%d %H:%M')
            except:
                date_str = created[:16]

            item_text = f"{name}\n    📦 {size_str}  •  📅 {date_str}"
            item = QListWidgetItem(item_text)
            item.setData(Qt.ItemDataRole.UserRole, file_id)
            self.backup_list.addItem(item)

        if backups:
            self.backup_list.currentItemChanged.connect(self._on_selection_changed)

    def _on_selection_changed(self, current, previous):
        enabled = current is not None
        self.restore_new_btn.setEnabled(enabled)
        self.replace_btn.setEnabled(enabled)
        self.download_btn.setEnabled(enabled)

        if current:
            self.selected_file_id = current.data(Qt.ItemDataRole.UserRole)

    def _on_restore_new(self):
        """Restore to a new folder (safe)."""
        if not self.selected_file_id:
            return

        self.status_label.setText("Restoring to new folder...")
        self._start_restore('new_folder')

    def _on_replace(self):
        """Replace current vault (destructive — with safety net)."""
        if not self.selected_file_id:
            return

        reply = QMessageBox.warning(
            self, "⚠️ Replace Current Vault",
            "This will DELETE your current vault and replace it with the backup.\n\n"
            "A safety net copy of your current vault will be created at:\n"
            "vault-backup-before-restore-{timestamp}/\n\n"
            "The safety net is kept for 7 days.\n\n"
            "Are you sure?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            self.status_label.setText("Replacing vault (with safety net)...")
            self._start_restore('replace')

    def _on_download(self):
        """Download ZIP to a local path."""
        if not self.selected_file_id:
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self, "Download Backup",
            f"vault-backup-{datetime.now().strftime('%Y%m%d')}.zip",
            "ZIP archives (*.zip)"
        )

        if file_path:
            self.status_label.setText("Downloading...")
            # Run in background
            class DownloadWorker(QThread):
                result = pyqtSignal(bool, str)

                def __init__(self, gdrive, file_id, path):
                    super().__init__()
                    self.gdrive = gdrive
                    self.file_id = file_id
                    self.path = path

                def run(self):
                    success = self.gdrive.download_backup(self.file_id, self.path)
                    self.result.emit(success, self.path if success else "Download failed")

            self.download_worker = DownloadWorker(self.gdrive, self.selected_file_id, file_path)
            self.download_worker.result.connect(
                lambda ok, msg: self.status_label.setText(
                    f"✅ Downloaded to {msg}" if ok else f"❌ {msg}"
                )
            )
            self.download_worker.start()

    def _start_restore(self, mode: str):
        """Start restore in background thread."""
        self.restore_worker = RestoreWorker(
            self.gdrive, self.selected_file_id, mode, self.vault_path
        )
        self.restore_worker.result.connect(self._on_restore_complete)
        self.restore_worker.start()

        # Disable buttons during restore
        self.restore_new_btn.setEnabled(False)
        self.replace_btn.setEnabled(False)
        self.download_btn.setEnabled(False)

    def _on_restore_complete(self, success: bool, message: str, new_path: str):
        self.restore_new_btn.setEnabled(True)
        self.replace_btn.setEnabled(True)
        self.download_btn.setEnabled(True)

        if success:
            QMessageBox.information(self, "✅ Restore Complete", message)
            self.status_label.setText(f"✅ {message}")
        else:
            QMessageBox.critical(self, "❌ Restore Failed", message)
            self.status_label.setText(f"❌ {message}")


# ========================================
# GDrive Settings Panel
# ========================================

class GDriveSettingsPanel(QWidget):
    """
    Settings panel for Google Drive backup.
    Embed in a QTabWidget.
    """

    def __init__(self, config: dict, cf_manager=None, parent=None):
        super().__init__(parent)
        self.config = config
        self.cf_manager = cf_manager
        self._oauth_worker = None

        self._init_ui()
        self._load_config()

        # Status refresh timer
        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self._refresh_status)
        self.status_timer.start(5000)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # ========================================
        # Connection Status Card
        # ========================================
        status_group = QGroupBox("Google Drive Status")
        status_layout = QVBoxLayout(status_group)

        self.status_label = QLabel("● Checking...")
        self.status_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        status_layout.addWidget(self.status_label)

        self.details_label = QLabel("")
        self.details_label.setStyleSheet("color: #666; font-size: 12px;")
        self.details_label.setWordWrap(True)
        status_layout.addWidget(self.details_label)

        layout.addWidget(status_group)

        # ========================================
        # Configuration Card
        # ========================================
        config_group = QGroupBox("Configuration")
        config_form = QFormLayout(config_group)

        # Enable checkbox
        self.enabled_check = QCheckBox("Enable Google Drive backup")
        config_form.addRow(self.enabled_check)

        # Max backups
        self.max_backups_input = QLineEdit()
        self.max_backups_input.setPlaceholderText("10")
        self.max_backups_input.setMaxLength(3)
        config_form.addRow("Max backups to keep:", self.max_backups_input)

        layout.addWidget(config_group)

        # ========================================
        # OAuth Credentials Card (Advanced)
        # ========================================
        oauth_group = QGroupBox("OAuth Credentials (Advanced)")
        oauth_form = QFormLayout(oauth_group)

        self.client_id_input = QLineEdit()
        self.client_id_input.setPlaceholderText("xxxxx.apps.googleusercontent.com")
        self.client_id_input.setEchoMode(QLineEdit.EchoMode.Password)
        oauth_form.addRow("Client ID:", self.client_id_input)

        self.client_secret_input = QLineEdit()
        self.client_secret_input.setPlaceholderText("GOCSPX-xxxxx")
        self.client_secret_input.setEchoMode(QLineEdit.EchoMode.Password)
        oauth_form.addRow("Client Secret:", self.client_secret_input)

        help_label = QLabel(
            "Get these from: Google Cloud Console → APIs & Services → Credentials\n"
            "Create OAuth 2.0 Client ID (Desktop app type)\n"
            "Add http://localhost:8765/callback to authorized redirect URIs"
        )
        help_label.setStyleSheet("color: #666; font-size: 11px;")
        help_label.setWordWrap(True)
        oauth_form.addRow(help_label)

        layout.addWidget(oauth_group)

        # ========================================
        # Actions Card
        # ========================================
        actions_group = QGroupBox("Actions")
        actions_layout = QHBoxLayout(actions_group)

        self.authorize_btn = QPushButton("🔐 Authorize")
        self.authorize_btn.setStyleSheet(
            "QPushButton { background: #6366F1; color: white; padding: 8px 16px; border-radius: 6px; }"
            "QPushButton:hover { background: #4F46E5; }"
        )
        self.authorize_btn.clicked.connect(self._on_authorize)
        actions_layout.addWidget(self.authorize_btn)

        self.backup_now_btn = QPushButton("💾 Backup Now")
        self.backup_now_btn.setStyleSheet(
            "QPushButton { background: #10B981; color: white; padding: 8px 16px; border-radius: 6px; }"
            "QPushButton:hover { background: #059669; }"
        )
        self.backup_now_btn.clicked.connect(self._on_backup_now)
        actions_layout.addWidget(self.backup_now_btn)

        self.restore_btn = QPushButton("📂 Restore")
        self.restore_btn.clicked.connect(self._on_restore)
        actions_layout.addWidget(self.restore_btn)

        actions_layout.addStretch()

        layout.addWidget(actions_group)

        # ========================================
        # Activity Log
        # ========================================
        log_group = QGroupBox("Backup Activity")
        log_layout = QVBoxLayout(log_group)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(120)
        self.log_text.setStyleSheet(
            "QTextEdit { background: #1a1a2e; color: #e0e0e0; "
            "font-family: 'JetBrains Mono', 'Consolas', monospace; font-size: 11px; }"
        )
        log_layout.addWidget(self.log_text)

        layout.addWidget(log_group)

        layout.addStretch()

    def _load_config(self):
        """Load config values into UI."""
        self.enabled_check.setChecked(self.config.get('gdrive_enabled', False))
        self.max_backups_input.setText(str(self.config.get('gdrive_max_backups', 10)))
        self.client_id_input.setText(self.config.get('gdrive_client_id', ''))
        self.client_secret_input.setText(self.config.get('gdrive_client_secret', ''))

    def _save_config(self):
        """Save UI values to config."""
        self.config['gdrive_enabled'] = self.enabled_check.isChecked()
        try:
            self.config['gdrive_max_backups'] = max(1, int(self.max_backups_input.text() or '10'))
        except ValueError:
            self.config['gdrive_max_backups'] = 10
        self.config['gdrive_client_id'] = self.client_id_input.text().strip()
        self.config['gdrive_client_secret'] = self.client_secret_input.text().strip()

    # ========================================
    # UI Event Handlers
    # ========================================

    def _on_authorize(self):
        """Start OAuth flow."""
        self._save_config()

        if not self.config.get('gdrive_client_id') or not self.config.get('gdrive_client_secret'):
            QMessageBox.warning(
                self, "Missing Credentials",
                "Please enter your Google OAuth Client ID and Secret first."
            )
            return

        self.authorize_btn.setEnabled(False)
        self.authorize_btn.setText("Authorizing... (check browser)")
        self._log("Starting OAuth flow...")

        # Run OAuth in background thread
        gdrive = self.cf_manager.gdrive if self.cf_manager else GDriveBackup(self.config)
        self._oauth_worker = OAuthWorker(gdrive)
        self._oauth_worker.result.connect(self._on_authorize_result)
        self._oauth_worker.start()

    def _on_authorize_result(self, success: bool, message: str):
        self.authorize_btn.setEnabled(True)
        self.authorize_btn.setText("🔐 Authorize")

        if success:
            self._log("✅ Authorized successfully!")
            self._refresh_status()
        else:
            self._log(f"❌ Authorization failed: {message}")
            QMessageBox.critical(self, "Authorization Failed", message)

    def _on_backup_now(self):
        """Force immediate backup."""
        self._save_config()

        if not self.config.get('gdrive_enabled'):
            QMessageBox.warning(self, "Not Enabled", "Enable Google Drive backup first.")
            return

        if self.cf_manager:
            self.cf_manager.force_backup_now()
            self._log("💾 Backup triggered")
        else:
            self._log("⚠️ CloudflareManager not connected")

    def _on_restore(self):
        """Open restore dialog."""
        if not self.cf_manager or not self.cf_manager.gdrive.is_authorized():
            QMessageBox.warning(self, "Not Authorized", "Authorize Google Drive first.")
            return

        vault_path = self.config.get('vault_path', '')
        if not vault_path:
            QMessageBox.warning(self, "No Vault", "Set a vault path in Settings first.")
            return

        dialog = RestoreDialog(self.cf_manager.gdrive, vault_path, self)
        dialog.exec()

    # ========================================
    # Status refresh
    # ========================================

    def _refresh_status(self):
        """Refresh the status display."""
        gdrive = self.cf_manager.gdrive if self.cf_manager else GDriveBackup(self.config)

        if gdrive.is_enabled() and gdrive.is_authorized():
            if gdrive.is_token_expired():
                self.status_label.setText("● Token expired (will refresh)")
                self.status_label.setStyleSheet(
                    "font-size: 14px; font-weight: bold; color: #F59E0B;"
                )
            else:
                self.status_label.setText("● Connected")
                self.status_label.setStyleSheet(
                    "font-size: 14px; font-weight: bold; color: #10B981;"
                )
        elif gdrive.is_enabled():
            self.status_label.setText("● Enabled (not authorized)")
            self.status_label.setStyleSheet(
                "font-size: 14px; font-weight: bold; color: #F59E0B;"
            )
        else:
            self.status_label.setText("● Disabled")
            self.status_label.setStyleSheet(
                "font-size: 14px; font-weight: bold; color: #EF4444;"
            )

        details_parts = []
        if gdrive.is_authorized():
            # Token expiry
            expiry = self.config.get('gdrive_token_expiry', 0)
            if expiry:
                remaining = expiry - datetime.now(timezone.utc).timestamp()
                if remaining > 0:
                    details_parts.append(f"Token: {int(remaining / 60)}m left")

        self.details_label.setText("  •  ".join(details_parts) if details_parts else "—")

    # ========================================
    # Logging
    # ========================================

    def _log(self, message: str):
        """Append to the activity log."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.append(f"[{timestamp}] {message}")

    def save_config(self):
        """Call this before closing to save UI state."""
        self._save_config()
