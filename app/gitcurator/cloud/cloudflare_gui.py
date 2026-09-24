"""
cloudflare_gui.py — PyQt6 settings panel for Cloudflare sync
=============================================================
A self-contained QWidget that provides:
  - Enable/disable toggle
  - Worker URL input
  - Pairing code entry (from /pair bot command)
  - Pair/Unpair buttons
  - Status display (paired, last sync, last poll, pending count)
  - Sync Now / Force Poll buttons
  - Poll interval slider (1-60 min)
  - Backfill/Cutover button (if not yet done)

Usage in main.py:
  from cloudflare_gui import CloudflareSettingsPanel

  panel = CloudflareSettingsPanel(self.config, self.cf_manager, parent=self)
  self.settings_tabs.addTab(panel, "Cloudflare")

The panel reads/writes config keys directly and connects to the
CloudflareManager's signals for live status updates.
"""

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QFont, QColor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QLineEdit, QPushButton, QCheckBox, QSpinBox, QGroupBox,
    QProgressBar, QTextEdit, QMessageBox, QFrame, QSlider,
    QDialog, QDialogButtonBox
)

from gitcurator.cloud.cloudflare_sync import CloudflareSync


# ========================================
# Pairing Dialog
# ========================================

class PairingDialog(QDialog):
    """Dialog for entering the pairing code from /pair bot command."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Pair with Cloudflare Bot")
        self.setModal(True)
        self.setMinimumWidth(450)

        layout = QVBoxLayout(self)

        # Instructions
        info = QLabel(
            "To pair the desktop app with your Cloudflare bot:\n\n"
            "1. Open Telegram and send /pair to your bot\n"
            "2. Copy the 8-character pairing code\n"
            "3. Paste it below and click Pair\n\n"
            "The code expires in 10 minutes."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        # Worker URL
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://github-curator-bot.your-subdomain.workers.dev")
        layout.addWidget(QLabel("Worker URL:"))
        layout.addWidget(self.url_input)

        # Pairing code
        self.code_input = QLineEdit()
        self.code_input.setPlaceholderText("e.g., ABC12345")
        self.code_input.setMaxLength(8)
        self.code_input.textChanged.connect(self._on_code_changed)
        layout.addWidget(QLabel("Pairing Code:"))
        layout.addWidget(self.code_input)

        # Buttons
        buttons = QHBoxLayout()
        self.pair_btn = QPushButton("Pair")
        self.pair_btn.setEnabled(False)
        self.pair_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(self.pair_btn)
        buttons.addWidget(cancel_btn)
        layout.addLayout(buttons)

    def _on_code_changed(self, text):
        self.pair_btn.setEnabled(len(text.strip()) == 8)

    def get_url(self) -> str:
        return self.url_input.text().strip().rstrip('/')

    def get_code(self) -> str:
        return self.code_input.text().strip().upper()


# ========================================
# Backfill Progress Dialog
# ========================================

class BackfillProgressDialog(QDialog):
    """Dialog showing backfill progress."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Backfill Progress")
        self.setModal(True)
        self.setMinimumWidth(500)

        layout = QVBoxLayout(self)

        self.status_label = QLabel("Starting backfill...")
        layout.addWidget(self.status_label)

        self.progress = QProgressBar()
        self.progress.setMinimum(0)
        self.progress.setMaximum(100)
        layout.addWidget(self.progress)

        self.detail_label = QLabel("")
        self.detail_label.setWordWrap(True)
        layout.addWidget(self.detail_label)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self._on_cancel)
        layout.addWidget(self.cancel_btn)

        self._cancelled = False

    def _on_cancel(self):
        self._cancelled = True
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setText("Cancelling...")

    def is_cancelled(self) -> bool:
        return self._cancelled

    def update_progress(self, current: int, total: int, message: str):
        if total > 0:
            pct = int((current / total) * 100)
            self.progress.setValue(pct)
        self.status_label.setText(message)
        self.detail_label.setText(f"{current} / {total} links")


# ========================================
# Cloudflare Settings Panel
# ========================================

class CloudflareSettingsPanel(QWidget):
    """
    Settings panel for Cloudflare bot integration.
    Embed in a QTabWidget.
    """

    def __init__(self, config: dict, cf_manager=None, parent=None):
        super().__init__(parent)
        self.config = config
        self.cf_manager = cf_manager

        self._init_ui()
        self._load_config()
        self._connect_signals()

        # Status refresh timer
        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self._refresh_status)
        self.status_timer.start(5000)  # Refresh every 5s

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # ========================================
        # Connection Status Card
        # ========================================
        status_group = QGroupBox("Connection Status")
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
        self.enabled_check = QCheckBox("Enable Cloudflare sync")
        config_form.addRow(self.enabled_check)

        # Worker URL
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://github-curator-bot.your-subdomain.workers.dev")
        config_form.addRow("Worker URL:", self.url_input)

        # Poll interval
        poll_layout = QHBoxLayout()
        self.poll_slider = QSlider(Qt.Orientation.Horizontal)
        self.poll_slider.setMinimum(1)
        self.poll_slider.setMaximum(60)
        self.poll_slider.setValue(5)
        self.poll_label = QLabel("5 min")
        self.poll_label.setMinimumWidth(60)
        self.poll_slider.valueChanged.connect(
            lambda v: self.poll_label.setText(f"{v} min")
        )
        poll_layout.addWidget(self.poll_slider)
        poll_layout.addWidget(self.poll_label)
        config_form.addRow("Poll interval:", poll_layout)

        layout.addWidget(config_group)

        # ========================================
        # Pairing Card
        # ========================================
        pairing_group = QGroupBox("Desktop Pairing")
        pairing_layout = QHBoxLayout(pairing_group)

        self.pair_btn = QPushButton("🔗 Pair Desktop")
        self.pair_btn.clicked.connect(self._on_pair)
        pairing_layout.addWidget(self.pair_btn)

        self.unpair_btn = QPushButton("Unpair")
        self.unpair_btn.clicked.connect(self._on_unpair)
        self.unpair_btn.setEnabled(False)
        pairing_layout.addWidget(self.unpair_btn)

        pairing_layout.addStretch()

        layout.addWidget(pairing_group)

        # ========================================
        # Actions Card
        # ========================================
        actions_group = QGroupBox("Actions")
        actions_layout = QHBoxLayout(actions_group)

        self.sync_now_btn = QPushButton("🔄 Sync Now")
        self.sync_now_btn.clicked.connect(self._on_sync_now)
        actions_layout.addWidget(self.sync_now_btn)

        self.poll_now_btn = QPushButton("📥 Poll Now")
        self.poll_now_btn.clicked.connect(self._on_poll_now)
        actions_layout.addWidget(self.poll_now_btn)

        actions_layout.addStretch()

        layout.addWidget(actions_group)

        # ========================================
        # Cutover / Backfill Card
        # ========================================
        self.cutover_group = QGroupBox("Cutover (One-time Migration)")
        cutover_layout = QVBoxLayout(self.cutover_group)

        cutover_info = QLabel(
            "If you have existing links in your Telegram bot queue, "
            "run the backfill to migrate them to Cloudflare. "
            "After cutover, the desktop app will poll Cloudflare instead of Telegram."
        )
        cutover_info.setWordWrap(True)
        cutover_info.setStyleSheet("color: #666; font-size: 12px;")
        cutover_layout.addWidget(cutover_info)

        self.cutover_btn = QPushButton("🚀 Start Backfill & Cutover")
        self.cutover_btn.clicked.connect(self._on_start_backfill)
        cutover_layout.addWidget(self.cutover_btn)

        layout.addWidget(self.cutover_group)

        # ========================================
        # Activity Log
        # ========================================
        log_group = QGroupBox("Recent Activity")
        log_layout = QVBoxLayout(log_group)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(150)
        self.log_text.setStyleSheet(
            "QTextEdit { background: #1a1a2e; color: #e0e0e0; "
            "font-family: 'JetBrains Mono', 'Consolas', monospace; font-size: 11px; }"
        )
        log_layout.addWidget(self.log_text)

        layout.addWidget(log_group)

        layout.addStretch()

    def _connect_signals(self):
        """Connect to CloudflareManager signals."""
        if self.cf_manager:
            self.cf_manager.sync_completed.connect(self._on_sync_completed)
            self.cf_manager.poll_completed.connect(self._on_poll_completed)
            self.cf_manager.pending_links_received.connect(self._on_pending_received)
            self.cf_manager.error_occurred.connect(self._on_error)
            self.cf_manager.auth_required.connect(self._on_auth_required)

    def _load_config(self):
        """Load config values into UI."""
        self.enabled_check.setChecked(self.config.get('cloudflare_enabled', False))
        self.url_input.setText(self.config.get('cloudflare_worker_url', ''))
        interval = self.config.get('cloudflare_poll_interval', 300)
        self.poll_slider.setValue(max(1, interval // 60))
        self.poll_label.setText(f"{max(1, interval // 60)} min")

        paired = bool(self.config.get('cloudflare_install_id'))
        self.unpair_btn.setEnabled(paired)
        self.pair_btn.setEnabled(not paired)

    def _save_config(self):
        """Save UI values to config."""
        self.config['cloudflare_enabled'] = self.enabled_check.isChecked()
        self.config['cloudflare_worker_url'] = self.url_input.text().strip().rstrip('/')
        self.config['cloudflare_poll_interval'] = self.poll_slider.value() * 60

    # ========================================
    # UI Event Handlers
    # ========================================

    def _on_pair(self):
        """Open pairing dialog."""
        self._save_config()

        dialog = PairingDialog(self)
        dialog.url_input.setText(self.url_input.text())

        if dialog.exec() == QDialog.DialogCode.Accepted:
            url = dialog.get_url()
            code = dialog.get_code()

            if not url or not code:
                return

            self.url_input.setText(url)
            self.config['cloudflare_worker_url'] = url

            # Run pairing in background thread
            self._run_pairing(code)

    def _run_pairing(self, code: str):
        """Run pairing in a background thread to avoid blocking UI."""
        self.pair_btn.setEnabled(False)
        self.pair_btn.setText("Pairing...")

        class PairWorker(QThread):
            result = pyqtSignal(bool, str)

            def __init__(self, sync, code):
                super().__init__()
                self.sync = sync
                self.code = code

            def run(self):
                success, msg = self.sync.pair(self.code)
                self.result.emit(success, msg)

        worker = PairWorker(self.cf_manager.sync if self.cf_manager else CloudflareSync(self.config), code)
        worker.result.connect(lambda ok, msg: self._on_pair_result(ok, msg, worker))
        worker.start()
        self._pair_worker = worker  # Keep reference

    def _on_pair_result(self, success: bool, message: str, worker):
        self.pair_btn.setText("🔗 Pair Desktop")
        self._load_config()

        if success:
            self._log("✅ Paired successfully!")
            if self.cf_manager:
                self.cf_manager.force_sync_now()
        else:
            self._log(f"❌ Pairing failed: {message}")

    def _on_unpair(self):
        """Unpair the desktop install."""
        reply = QMessageBox.question(
            self, "Unpair",
            "Are you sure you want to unpair?\n\n"
            "The desktop will stop syncing with Cloudflare.\n"
            "Your bot data is not affected.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            if self.cf_manager:
                self.cf_manager.sync.unpair()
            self._save_config()
            self._load_config()
            self._log("Unpaired")

    def _on_sync_now(self):
        """Force immediate vault_mirror push."""
        self._save_config()
        if self.cf_manager:
            self.cf_manager.force_sync_now()
            self._log("🔄 Sync triggered")
        else:
            self._log("⚠️ CloudflareManager not connected")

    def _on_poll_now(self):
        """Force immediate poll."""
        self._save_config()
        if self.cf_manager:
            self.cf_manager.force_poll_now()
            self._log("📥 Poll triggered")
        else:
            self._log("⚠️ CloudflareManager not connected")

    def _on_start_backfill(self):
        """Start the backfill/cutover flow."""
        if not self.cf_manager or not self.cf_manager.sync.is_paired():
            self._log("❌ Pair with Cloudflare first")
            return

        # Confirm
        reply = QMessageBox.question(
            self, "Start Backfill",
            "This will migrate all links from your Telegram bot queue to Cloudflare.\n\n"
            "After cutover, the desktop will poll Cloudflare instead of Telegram.\n\n"
            "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        # This needs to be triggered by main.py, which has access to the bot queue
        # Emit a signal or call a callback
        if hasattr(self.parent(), 'start_backfill'):
            self.parent().start_backfill()
        else:
            self._log("⚠️ Backfill requires main.py integration")

    # ========================================
    # Signal handlers (from CloudflareManager)
    # ========================================

    def _on_sync_completed(self, stats: dict):
        entries = stats.get('entries', 0)
        total = stats.get('total', 0)
        self._log(f"✅ Synced {entries} entries (total: {total})")

    def _on_poll_completed(self, count: int):
        if count > 0:
            self._log(f"📥 Polled: {count} pending link(s)")

    def _on_pending_received(self, links: list):
        self._log(f"📋 Received {len(links)} pending link(s) from bot")

    def _on_error(self, severity: str, message: str):
        self._log(f"⚠️ [{severity}] {message}")

    def _on_auth_required(self, service: str):
        if service == 'gdrive':
            self._log("🔴 Google Drive auth expired — re-authorize in GDrive tab")
        elif service == 'cloudflare':
            self._log("🔴 Cloudflare auth required — re-pair")

    # ========================================
    # Status refresh
    # ========================================

    def _refresh_status(self):
        """Refresh the status display."""
        if self.cf_manager:
            status = self.cf_manager.get_status()
        else:
            status = {
                'cloudflare_enabled': self.config.get('cloudflare_enabled', False),
                'cloudflare_paired': bool(self.config.get('cloudflare_install_id')),
                'gdrive_enabled': False,
                'gdrive_authorized': False,
                'last_poll': 0,
                'error_stats': {},
                'poll_interval': 300
            }

        if status['cloudflare_enabled'] and status['cloudflare_paired']:
            self.status_label.setText("● Connected")
            self.status_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #10B981;")
        elif status['cloudflare_paired']:
            self.status_label.setText("● Paired (disabled)")
            self.status_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #F59E0B;")
        else:
            self.status_label.setText("● Not paired")
            self.status_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #EF4444;")

        details_parts = []
        if status['cloudflare_paired']:
            if status['last_poll']:
                import time as _time
                ago = int(_time.time() - status['last_poll'])
                if ago < 60:
                    details_parts.append(f"Last poll: {ago}s ago")
                else:
                    details_parts.append(f"Last poll: {ago // 60}m ago")

            err_stats = status.get('error_stats', {})
            unack = sum(err_stats.values())
            if unack > 0:
                details_parts.append(f"Errors: {unack} unpushed")

        self.details_label.setText("  •  ".join(details_parts) if details_parts else "—")

    # ========================================
    # Logging
    # ========================================

    def _log(self, message: str):
        """Append to the activity log."""
        from datetime import datetime
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.append(f"[{timestamp}] {message}")

    # ========================================
    # Save on close
    # ========================================

    def save_config(self):
        """Call this before closing to save UI state."""
        self._save_config()
