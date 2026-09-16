"""
integration_snippet.py — Example integration into main.py
==========================================================
This is NOT a standalone module. It shows how to wire the Cloudflare
modules into the existing main.py (MainWindow class).

Copy the relevant parts into main.py. Do NOT import this file.

What this adds to main.py:
  1. Settings tabs (Cloudflare + Google Drive)
  2. CloudflareManager background thread
  3. Batch-complete hook → push vault_mirror + GDrive backup
  4. 404 hook → push decommission
  5. Error hook → push to error outbox
  6. Pending-links callback → process new repos
  7. Backfill/cutover flow
"""

# ========================================
# 1. IMPORTS (add to top of main.py)
# ========================================

from cloudflare_manager import CloudflareManager
from cloudflare_gui import CloudflareSettingsPanel
from gdrive_gui import GDriveSettingsPanel
from backfill_manager import BackfillManager
from error_reporter import (
    ErrorReporter,
    SEVERITY_CRITICAL, SEVERITY_WARNING, SEVERITY_INFO, SEVERITY_DEBUG
)


# ========================================
# 2. IN MainWindow.__init__ (add after existing init)
# ========================================

def init_cloudflare(self):
    """Initialize Cloudflare + GDrive integration."""
    # Create the manager (runs in background QThread)
    self.cf_manager = CloudflareManager(
        config=self.config,
        vault_index=self.vault_index,    # existing VaultIndex instance
        cache_db=self.cache_db,          # existing CacheDB instance
        parent=self
    )

    # Connect signals
    self.cf_manager.pending_links_received.connect(self.on_cf_pending_received)
    self.cf_manager.error_occurred.connect(self.on_cf_error)
    self.cf_manager.backup_completed.connect(self.on_cf_backup_done)
    self.cf_manager.backup_failed.connect(self.on_cf_backup_failed)
    self.cf_manager.auth_required.connect(self.on_cf_auth_required)

    # Set the pending-links callback (processes new repos from bot)
    self.cf_manager.on_pending_callback = self.process_pending_from_bot

    # Start the manager (if enabled)
    if self.cf_manager.is_cloudflare_enabled():
        self.cf_manager.start()


# ========================================
# 3. SETTINGS TABS (add to settings dialog)
# ========================================

def add_settings_tabs(self, settings_dialog):
    """Add Cloudflare + GDrive tabs to the settings dialog."""
    # Existing tabs: General, Telegram, LLM, etc.

    # Add Cloudflare tab
    cf_panel = CloudflareSettingsPanel(
        config=self.config,
        cf_manager=self.cf_manager,
        parent=settings_dialog
    )
    settings_dialog.tabs.addTab(cf_panel, "Cloudflare")

    # Add Google Drive tab
    gdrive_panel = GDriveSettingsPanel(
        config=self.config,
        cf_manager=self.cf_manager,
        parent=settings_dialog
    )
    settings_dialog.tabs.addTab(gdrive_panel, "Google Drive")

    # Save panels for config access on close
    settings_dialog.cf_panel = cf_panel
    settings_dialog.gdrive_panel = gdrive_panel


# ========================================
# 4. BATCH COMPLETE HOOK (call after processing_finished)
# ========================================

def on_batch_complete(self, processed_count: int, skipped_count: int = 0):
    """Call this after a processing batch finishes."""
    if hasattr(self, 'cf_manager') and self.cf_manager:
        self.cf_manager.on_batch_complete(processed_count, skipped_count)


# ========================================
# 5. DECOMMISSION HOOK (call when a repo returns 404)
# ========================================

def on_repo_404(self, url: str, details: str = ''):
    """Call this when a GitHub repo returns 404."""
    if hasattr(self, 'cf_manager') and self.cf_manager:
        self.cf_manager.on_decommission(url, reason='404', details=details)


# ========================================
# 6. ERROR HOOK (call on any error)
# ========================================

def on_error(self, severity: str, error_code: str, message: str, details: dict = None):
    """Call this on any error worth reporting to the bot."""
    if hasattr(self, 'cf_manager') and self.cf_manager:
        self.cf_manager.on_error(severity, error_code, message, details)


# ========================================
# 7. PENDING LINKS CALLBACK (process repos from bot)
# ========================================

def process_pending_from_bot(self, pending_links: list):
    """
    Called when the CloudflareManager receives pending links from the Worker.
    Add these to the processing queue.
    """
    for link in pending_links:
        url = link.get('url_normalized', '')
        if not url:
            continue

        # Check if already in vault (dedup)
        if self.vault_index and self.vault_index.has_url(url):
            continue  # Already processed

        # Check if decommissioned locally
        if self.cache_db and self.cache_db.is_decommissioned(url):
            continue  # Skip dead repos

        # Add to processing queue
        # (Adapt this to your existing queue mechanism)
        github_metadata = link.get('github_metadata', {})
        self.processing_queue.append({
            'url': url,
            'original': link.get('url_original', url),
            'owner': link.get('github_owner', ''),
            'repo': link.get('github_repo', ''),
            'stars': github_metadata.get('stars', 0),
            'description': github_metadata.get('description', ''),
            'language': github_metadata.get('language', ''),
            'source': 'cloudflare_bot'
        })

    # Update UI
    self.update_queue_display()


# ========================================
# 8. SIGNAL HANDLERS (add to MainWindow)
# ========================================

def on_cf_pending_received(self, links: list):
    """Slot: pending links received from Worker."""
    self.log_message(f"📋 Received {len(links)} pending link(s) from bot")


def on_cf_error(self, severity: str, message: str):
    """Slot: error from CloudflareManager."""
    if severity == 'CRITICAL':
        self.log_message(f"🔴 [CRITICAL] {message}")
        # Show in status bar or notification
        self.statusBar().showMessage(f"🔴 {message}", 5000)
    elif severity == 'WARNING':
        self.log_message(f"🟠 [WARNING] {message}")


def on_cf_backup_done(self, file_name: str):
    """Slot: GDrive backup completed."""
    self.log_message(f"💾 Backup uploaded: {file_name}")


def on_cf_backup_failed(self, error: str):
    """Slot: GDrive backup failed."""
    self.log_message(f"❌ Backup failed: {error}")


def on_cf_auth_required(self, service: str):
    """Slot: re-authorization needed."""
    if service == 'gdrive':
        QMessageBox.warning(
            self, "Google Drive Auth Expired",
            "Your Google Drive authorization has expired.\n\n"
            "Backups are halted until you re-authorize.\n"
            "Go to Settings → Google Drive → Authorize"
        )


# ========================================
# 9. BACKFILL FLOW (called from CloudflareSettingsPanel)
# ========================================

def start_backfill(self):
    """Start the one-time backfill from Telegram to Cloudflare."""
    if not self.cf_manager or not self.cf_manager.sync.is_paired():
        QMessageBox.warning(self, "Not Paired", "Pair with Cloudflare first.")
        return

    # Check if already done
    backfill_mgr = BackfillManager(self.cf_manager.sync, self.cf_manager.error_reporter)
    if backfill_mgr.is_cutover_complete():
        QMessageBox.information(self, "Already Done", "Cutover was already completed.")
        return

    # Fetch all links from Telegram bot queue (one last time)
    self.log_message("📥 Fetching all links from Telegram bot queue...")
    all_messages = self.fetch_all_bot_messages()  # Your existing method

    # Extract links
    all_links = BackfillManager.extract_links_from_bot_messages(all_messages)
    self.log_message(f"📋 Found {len(all_links)} unique links in bot queue")

    if not all_links:
        QMessageBox.information(self, "No Links", "No links found in bot queue.")
        return

    # Show progress dialog
    from cloudflare_gui import BackfillProgressDialog
    progress_dialog = BackfillProgressDialog(self)
    progress_dialog.show()

    # Run backfill in background thread
    class BackfillWorker(QThread):
        result = pyqtSignal(bool, dict)

        def __init__(self, manager, links, progress_cb):
            super().__init__()
            self.manager = manager
            self.links = links
            self.progress_cb = progress_cb

        def run(self):
            success, result = self.manager.run(
                self.links,
                chunk_size=50,
                progress_callback=self.progress_cb
            )
            self.result.emit(success, result)

    def on_progress(current, total, message):
        progress_dialog.update_progress(current, total, message)
        if progress_dialog.is_cancelled():
            backfill_mgr.cancel()

    worker = BackfillWorker(backfill_mgr, all_links, on_progress)
    worker.result.connect(lambda ok, result: self._on_backfill_complete(ok, result, progress_dialog))
    worker.start()
    self._backfill_worker = worker  # Keep reference


def _on_backfill_complete(self, success: bool, result: dict, progress_dialog):
    """Called when backfill completes."""
    progress_dialog.close()

    if success:
        # Mark cutover complete
        self.cf_manager.complete_cutover()
        self.log_message(
            f"✅ Backfill complete: {result.get('inserted', 0)} links migrated "
            f"in {result.get('duration_seconds', 0)}s"
        )
        QMessageBox.information(
            self, "Cutover Complete",
            f"✅ Successfully migrated {result.get('inserted', 0)} links to Cloudflare.\n\n"
            "The desktop app will now poll Cloudflare instead of Telegram.\n"
            "You can still forward links to the bot — they'll be picked up automatically."
        )
    else:
        error = result.get('error', 'Unknown error')
        self.log_message(f"❌ Backfill failed: {error}")
        QMessageBox.critical(self, "Backfill Failed", error)


# ========================================
# 10. SHUTDOWN (add to MainWindow.closeEvent)
# ========================================

def shutdown_cloudflare(self):
    """Stop the CloudflareManager on app shutdown."""
    if hasattr(self, 'cf_manager') and self.cf_manager:
        self.cf_manager.stop()
        self.cf_manager.wait(5000)


# ========================================
# SUMMARY OF CHANGES TO main.py
# ========================================
#
# 1. Add imports (section 1)
# 2. Call self.init_cloudflare() in __init__ (section 2)
# 3. Add settings tabs (section 3)
# 4. Call self.on_batch_complete() at end of processing_finished() (section 4)
# 5. Call self.on_repo_404(url) when a 404 is detected (section 5)
# 6. Call self.on_error() on any error (section 6)
# 7. Add process_pending_from_bot() method (section 7)
# 8. Add signal handler methods (section 8)
# 9. Add start_backfill() method (section 9)
# 10. Call self.shutdown_cloudflare() in closeEvent (section 10)
#
# That's it — ~10 integration points, no rewriting existing code.
