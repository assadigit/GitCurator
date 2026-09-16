"""
cloudflare_manager.py — Background orchestrator for Cloudflare sync
===================================================================
Runs in a QThread, handles:
  - Polling /api/pending every 5 min (configurable)
  - Pushing vault_mirror after each batch (debounced 5s)
  - Pushing errors every 60s
  - Triggering GDrive backup after batch (debounced 5s)
  - Pulling bot-side decommissions on each poll
  - Health check + auto-recovery

This module is the glue between the desktop app (main.py) and the three
sync modules:
  - cloudflare_sync.CloudflareSync  (Worker API)
  - error_reporter.ErrorReporter    (local error outbox)
  - gdrive_backup.GDriveBackup      (Google Drive backup)

main.py only needs to:
  1. Create the manager: self.cf_manager = CloudflareManager(config, vault_index, cache_db)
  2. Start it: self.cf_manager.start()
  3. On batch complete: self.cf_manager.on_batch_complete()
  4. On error: self.cf_manager.log_error(severity, code, message)
  5. On 404: self.cf_manager.on_decommission(url)
  6. On shutdown: self.cf_manager.stop()

The manager handles everything else asynchronously.
"""

import time
import threading
import logging
from typing import Optional, List, Dict, Any, Callable, Tuple
from datetime import datetime, timezone

try:
    from PyQt6.QtCore import QThread, pyqtSignal, QTimer
    HAS_QT = True
except ImportError:
    HAS_QT = False
    # Fallback for non-Qt environments (testing)
    class QThread:
        class __mock:
            def __init__(self): pass
            def emit(self, *a): pass
        started = __mock(); finished = __mock()
        def __init__(self): pass
        def start(self): pass
        def wait(self, ms=0): pass
        def isRunning(self): return False
        def run(self): pass
    def pyqtSignal(*a, **k):
        class __mock:
            def emit(self, *a): pass
        return __mock()

from cloudflare_sync import CloudflareSync, build_vault_mirror_entries, build_decommission_events
from error_reporter import ErrorReporter, SEVERITY_CRITICAL, SEVERITY_WARNING, SEVERITY_INFO, SEVERITY_DEBUG
from gdrive_backup import GDriveBackup

logger = logging.getLogger(__name__)


# ========================================
# Constants
# ========================================

POLL_INTERVAL_DEFAULT = 300        # 5 minutes
ERROR_PUSH_INTERVAL = 60            # 60 seconds
DEBOUNCE_BATCH_PUSH = 5             # 5 seconds
DEBOUNCE_BACKUP = 5                 # 5 seconds
MAX_PENDING_PER_POLL = 100


# ========================================
# CloudflareManager (QThread)
# ========================================

class CloudflareManager(QThread if HAS_QT else object):
    """
    Background thread that orchestrates all Cloudflare sync operations.

    Signals (emitted to main GUI thread):
      - pending_links_received(list) — new pending links from Worker
      - status_updated(str)          — status string for GUI display
      - error_occurred(str, str)     — (severity, message) for GUI alert
      - backup_completed(str)        — backup file name
      - backup_failed(str)           — error message
      - auth_required(str)           — "gdrive" or "cloudflare" — user needs to re-auth
    """

    # Signals
    pending_links_received = pyqtSignal(list)
    status_updated = pyqtSignal(str)
    error_occurred = pyqtSignal(str, str)
    backup_completed = pyqtSignal(str)
    backup_failed = pyqtSignal(str)
    auth_required = pyqtSignal(str)  # "gdrive" | "cloudflare"
    sync_completed = pyqtSignal(dict)  # sync stats
    poll_completed = pyqtSignal(int)   # pending count

    def __init__(
        self,
        config: dict,
        vault_index=None,
        cache_db=None,
        parent=None
    ):
        if HAS_QT:
            super().__init__(parent)

        self.config = config
        self.vault_index = vault_index      # VaultIndex instance (or dict)
        self.cache_db = cache_db            # CacheDB instance (for decommission lookup)

        # Initialize sub-modules
        self.sync = CloudflareSync(config)
        self.error_reporter = ErrorReporter(
            db_path=self._get_error_db_path()
        )
        self.gdrive = GDriveBackup(config)

        # State
        self._running = False
        self._last_poll = 0
        self._last_error_push = 0
        self._last_decomm_pull = '1970-01-01T00:00:00Z'

        # Debounce queues
        self._batch_push_pending = False
        self._batch_push_timer = 0
        self._backup_pending = False
        self._backup_timer = 0
        self._lock = threading.Lock()

        # Callbacks (set by main.py)
        self.on_pending_callback: Optional[Callable[[List[Dict]], None]] = None

    def _get_error_db_path(self) -> str:
        """Get path for error outbox DB (next to config.json)."""
        import os
        config_dir = os.path.dirname(self.config.get('_config_path', 'config.json'))
        return os.path.join(config_dir or '.', 'error_outbox.db')

    # ========================================
    # Thread lifecycle
    # ========================================

    def run(self):
        """Main loop — runs in background thread."""
        self._running = True
        logger.info("CloudflareManager started")

        # Initial reconciliation push on startup
        self._reconcile_on_startup()

        while self._running:
            try:
                now = time.time()

                # 1. Poll pending links (every poll_interval)
                if self.sync.is_enabled() and (now - self._last_poll) >= self._get_poll_interval():
                    self._poll_pending()

                # 2. Push errors (every 60s)
                if self.sync.is_enabled() and (now - self._last_error_push) >= ERROR_PUSH_INTERVAL:
                    self._push_errors()

                # 3. Debounced batch push
                if self._batch_push_pending and (now - self._batch_push_timer) >= DEBOUNCE_BATCH_PUSH:
                    self._do_batch_push()

                # 4. Debounced backup
                if self._backup_pending and (now - self._backup_timer) >= DEBOUNCE_BACKUP:
                    self._do_backup()

                # 5. Check for pending backup/sync requests from bot
                self._check_bot_requests()

                # Sleep 1 second between cycles
                self.msleep(1000) if HAS_QT else time.sleep(1)

            except Exception as e:
                logger.error(f"CloudflareManager loop error: {e}", exc_info=True)
                self.error_reporter.log(
                    SEVERITY_WARNING,
                    'CF_MANAGER_LOOP',
                    f"Manager loop error: {e}"
                )
                time.sleep(5)  # Back off on error

        logger.info("CloudflareManager stopped")

    def stop(self):
        """Stop the manager gracefully."""
        self._running = False
        if HAS_QT:
            self.wait(5000)  # Wait up to 5s

    # ========================================
    # Public API (called from main.py)
    # ========================================

    def on_batch_complete(self, processed_count: int = 0, skipped_count: int = 0):
        """
        Called by main.py after a processing batch completes.
        Schedules debounced vault_mirror push + GDrive backup.
        """
        with self._lock:
            self._batch_push_pending = True
            self._batch_push_timer = time.time()
            self._backup_pending = True
            self._backup_timer = time.time()

        # Log INFO event for daily summary
        self.error_reporter.log(
            SEVERITY_INFO,
            'BATCH_COMPLETE',
            f"Batch complete: {processed_count} processed, {skipped_count} skipped",
            {'processed': processed_count, 'skipped': skipped_count}
        )

    def on_decommission(self, url: str, reason: str = '404', details: str = ''):
        """
        Called by main.py when a repo returns 404.
        Pushes decommission to Worker in real-time (not debounced).
        """
        if not self.sync.is_enabled():
            return

        url_norm = self.sync.normalize_url(url)
        threading.Thread(
            target=self._push_decommission_async,
            args=(url_norm, reason, details),
            daemon=True
        ).start()

    def on_error(
        self,
        severity: str,
        error_code: str,
        message: str,
        details: Dict[str, Any] = None
    ):
        """
        Called by main.py to log an error.
        Error is queued locally and pushed to Worker on next cycle.
        """
        self.error_reporter.log(severity, error_code, message, details)

    def force_sync_now(self):
        """Force an immediate vault_mirror push (e.g., user clicked 'Sync Now')."""
        with self._lock:
            self._batch_push_pending = True
            self._batch_push_timer = 0  # Immediate

    def force_poll_now(self):
        """Force an immediate poll for pending links."""
        self._last_poll = 0

    def force_backup_now(self):
        """Force an immediate GDrive backup."""
        with self._lock:
            self._backup_pending = True
            self._backup_timer = 0  # Immediate

    def is_cloudflare_enabled(self) -> bool:
        return self.sync.is_enabled()

    def is_paired(self) -> bool:
        return self.sync.is_paired()

    def is_gdrive_enabled(self) -> bool:
        return self.gdrive.is_enabled()

    def is_gdrive_authorized(self) -> bool:
        return self.gdrive.is_authorized()

    def get_status(self) -> Dict[str, Any]:
        """Get current status for GUI display."""
        return {
            'cloudflare_enabled': self.sync.is_enabled(),
            'cloudflare_paired': self.sync.is_paired(),
            'gdrive_enabled': self.gdrive.is_enabled(),
            'gdrive_authorized': self.gdrive.is_authorized(),
            'gdrive_token_expired': self.gdrive.is_token_expired(),
            'last_poll': self._last_poll,
            'error_stats': self.error_reporter.get_stats(),
            'poll_interval': self._get_poll_interval()
        }

    # ========================================
    # Internal: polling
    # ========================================

    def _poll_pending(self):
        """Poll Worker for pending links."""
        self._last_poll = time.time()

        try:
            pending = self.sync.get_pending()

            if pending:
                logger.info(f"Received {len(pending)} pending links from Worker")
                self.pending_links_received.emit(pending)

                # Callback for main.py
                if self.on_pending_callback:
                    self.on_pending_callback(pending)

                # Pull bot-side decommissions
                self._pull_decommissions()

            self.poll_completed.emit(len(pending))

        except Exception as e:
            logger.error(f"Poll error: {e}")
            self.error_reporter.log(
                SEVERITY_WARNING,
                'CF_POLL_FAILED',
                f"Failed to poll Worker: {e}"
            )

    def _pull_decommissions(self):
        """Pull bot-side decommission events and merge into local cache_db."""
        if not self.cache_db or not hasattr(self.cache_db, 'decommission'):
            return

        try:
            events = self.sync.get_decommissions(self._last_decomm_pull)
            for evt in events:
                url = evt.get('url_normalized', '')
                reason = evt.get('reason', '404')
                if url:
                    # Merge into local decommission table
                    if hasattr(self.cache_db, 'decommission'):
                        self.cache_db.decommission(url, reason, source='bot')
                    self._last_decomm_pull = evt.get('decommissioned_at', self._last_decomm_pull)

            if events:
                logger.info(f"Pulled {len(events)} bot-side decommissions")

        except Exception as e:
            logger.error(f"Pull decommissions error: {e}")

    # ========================================
    # Internal: batch push (debounced)
    # ========================================

    def _do_batch_push(self):
        """Push vault_mirror to Worker."""
        with self._lock:
            self._batch_push_pending = False

        if not self.sync.is_enabled():
            return

        try:
            # Build entries from vault_index
            if self.vault_index:
                if hasattr(self.vault_index, 'get_index'):
                    index_data = self.vault_index.get_index()
                elif hasattr(self.vault_index, 'index'):
                    index_data = self.vault_index.index
                else:
                    index_data = dict(self.vault_index)
            else:
                index_data = {}

            entries = build_vault_mirror_entries(index_data)
            decommissions = build_decommission_events(self.cache_db)

            # Compute vault index hash
            import hashlib
            urls_sorted = sorted(index_data.keys()) if index_data else []
            vault_hash = hashlib.sha256('|'.join(urls_sorted).encode()).hexdigest()

            success, response = self.sync.push_vault_mirror(
                entries=entries,
                decommission_events=decommissions,
                vault_index_hash=vault_hash
            )

            if success:
                logger.info(f"Pushed {len(entries)} entries, {len(decommissions)} decommissions to Worker")
                self.sync_completed.emit({
                    'entries': len(entries),
                    'decommissions': len(decommissions),
                    'total': response.get('vault_mirror_total', 0)
                })
            else:
                logger.error(f"Push failed: {response}")
                self.error_reporter.log(
                    SEVERITY_WARNING,
                    'CF_PUSH_FAILED',
                    f"Failed to push vault_mirror: {response.get('error', 'unknown')}"
                )

        except Exception as e:
            logger.error(f"Batch push error: {e}", exc_info=True)
            self.error_reporter.log(
                SEVERITY_WARNING,
                'CF_PUSH_ERROR',
                f"Batch push exception: {e}"
            )

    def _push_decommission_async(self, url_norm: str, reason: str, details: str):
        """Push a single decommission event (runs in background thread)."""
        success = self.sync.push_decommission(url_norm, reason, details)
        if not success:
            logger.warning(f"Failed to push decommission for {url_norm}")

    # ========================================
    # Internal: error push
    # ========================================

    def _push_errors(self):
        """Push queued errors to Worker."""
        self._last_error_push = time.time()

        if not self.sync.is_enabled():
            return

        try:
            count = self.error_reporter.push_to_worker(self.sync)
            if count > 0:
                logger.info(f"Pushed {count} errors to Worker")

            # Prune excess periodically
            self.error_reporter.prune_excess(max_count=1000)

        except Exception as e:
            logger.error(f"Error push failed: {e}")

    # ========================================
    # Internal: GDrive backup (debounced)
    # ========================================

    def _do_backup(self):
        """Create and upload a GDrive backup."""
        with self._lock:
            self._backup_pending = False

        if not self.gdrive.is_enabled():
            return

        vault_path = self.config.get('vault_path', '')
        if not vault_path:
            return

        # Check token validity
        if self.gdrive.is_token_expired():
            if not self.gdrive.refresh_token():
                # Auth expired — alert user
                self.error_reporter.log(
                    SEVERITY_CRITICAL,
                    'GDRIVE_AUTH_EXPIRED',
                    "Google Drive OAuth token expired — backup halted. Re-authorize in Settings."
                )
                self.auth_required.emit("gdrive")
                self.backup_failed.emit("Google Drive auth expired — re-authorize in Settings")
                return

        try:
            # Count repos in vault
            repo_count = 0
            if self.vault_index:
                if hasattr(self.vault_index, 'get_count'):
                    repo_count = self.vault_index.get_count()
                elif hasattr(self.vault_index, '__len__'):
                    repo_count = len(self.vault_index)

            backup_id = self.gdrive.backup_vault(
                vault_path=vault_path,
                repo_count=repo_count,
                trigger='batch_complete'
            )

            if backup_id:
                # Push backup status to Worker (for /backup, /restore commands)
                import os
                from datetime import datetime, timezone
                timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%M%SZ')
                file_name = f'vault-{timestamp}.zip'

                self.sync.push_backup_status({
                    'backup_id': backup_id,
                    'file_name': file_name,
                    'file_size_bytes': 0,  # Unknown at this point
                    'repo_count': repo_count,
                    'trigger': 'batch_complete',
                    'created_at': datetime.now(timezone.utc).isoformat(),
                    'status': 'uploaded'
                })

                self.backup_completed.emit(file_name)
                self.error_reporter.log(
                    SEVERITY_INFO,
                    'BACKUP_UPLOADED',
                    f"Backup uploaded: {file_name} ({repo_count} repos)"
                )
                logger.info(f"Backup uploaded: {file_name}")
            else:
                self.backup_failed.emit("Backup failed — check logs")
                self.error_reporter.log(
                    SEVERITY_WARNING,
                    'BACKUP_FAILED',
                    "GDrive backup failed — will retry on next batch"
                )

        except Exception as e:
            logger.error(f"Backup error: {e}", exc_info=True)
            self.backup_failed.emit(str(e))
            self.error_reporter.log(
                SEVERITY_WARNING,
                'BACKUP_ERROR',
                f"Backup exception: {e}"
            )

    # ========================================
    # Internal: startup reconciliation
    # ========================================

    def _reconcile_on_startup(self):
        """Push vault_mirror on startup to reconcile state."""
        if not self.sync.is_enabled():
            return

        logger.info("Startup reconciliation — pushing vault_mirror")
        with self._lock:
            self._batch_push_pending = True
            self._batch_push_timer = 0  # Immediate

    # ========================================
    # Internal: check bot requests
    # ========================================

    def _check_bot_requests(self):
        """Check if user requested backup/sync via bot commands."""
        # The Worker sets sync_state keys when user runs /backup or /sync
        # We check these via the verify endpoint (lightweight)
        # This is polled infrequently — every poll cycle
        pass  # Implemented via pending_links_received callback in main.py

    # ========================================
    # Internal: config helpers
    # ========================================

    def _get_poll_interval(self) -> int:
        return self.config.get('cloudflare_poll_interval', POLL_INTERVAL_DEFAULT)

    # ========================================
    # Cutover / backfill support
    # ========================================

    def start_backfill(
        self,
        all_bot_links: List[Dict[str, Any]],
        chunk_size: int = 50,
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> Tuple[bool, Dict]:
        """
        Start the one-time backfill from Telegram bot queue to Worker.

        Args:
            all_bot_links: List of {url_normalized, url_original, url_type, github_owner, github_repo, first_seen_at}
            chunk_size: Number of entries per chunk
            progress_callback: Called with (current, total) for GUI progress bar

        Returns (success, result_dict).
        """
        from backfill_manager import BackfillManager
        manager = BackfillManager(self.sync, self.error_reporter)
        return manager.run(all_bot_links, chunk_size, progress_callback)

    def complete_cutover(self) -> bool:
        """Mark cutover as complete (desktop switches from Telegram to Worker polling)."""
        if not self.sync.is_enabled():
            return False

        # The Worker tracks this in sync_state.cutover_complete
        # We push it via vault_mirror push with a special flag
        success, _ = self.sync.push_vault_mirror(
            entries=[],
            decommission_events=[],
            vault_index_hash='cutover_complete'
        )
        return success
