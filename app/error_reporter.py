"""
error_reporter.py — Desktop error outbox for bot DMs
=====================================================
Queues desktop errors locally and pushes them to the Cloudflare Worker
for DM routing (CRITICAL → immediate, WARNING → deduped 1h, INFO → daily summary).

Severity levels:
  CRITICAL  — Telegram session lost, GDrive auth expired, DB corruption
  WARNING   — LLM timeout, proxy flaky, single-repo fetch failed
  INFO      — Batch complete, sync done, backup uploaded
  DEBUG     — Individual repo skip reasons (never DM'd)

Usage:
  from error_reporter import ErrorReporter

  reporter = ErrorReporter(db_path="config.json.db")
  reporter.log("CRITICAL", "GDRIVE_AUTH_EXPIRED", "OAuth token expired — backup halted")
  reporter.log("WARNING", "LLM_TIMEOUT", "Ollama response >30s", details={"url": "..."})

  # On batch end or every 60s:
  reporter.push_to_worker(cloudflare_sync)
"""

import json
import sqlite3
import time
import threading
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
from pathlib import Path


# ========================================
# Severity levels
# ========================================

SEVERITY_CRITICAL = 'CRITICAL'
SEVERITY_WARNING = 'WARNING'
SEVERITY_INFO = 'INFO'
SEVERITY_DEBUG = 'DEBUG'

VALID_SEVERITIES = [SEVERITY_CRITICAL, SEVERITY_WARNING, SEVERITY_INFO, SEVERITY_DEBUG]


# ========================================
# ErrorReporter
# # ========================================

class ErrorReporter:
    """Local error outbox that pushes to the Cloudflare Worker."""

    def __init__(self, db_path: str = "error_outbox.db"):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        """Initialize the error_outbox SQLite table."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS error_outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                severity TEXT NOT NULL,
                error_code TEXT NOT NULL,
                message TEXT NOT NULL,
                details TEXT,
                occurred_at TEXT NOT NULL,
                pushed INTEGER DEFAULT 0,
                pushed_at TEXT,
                push_count INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_outbox_pushed
            ON error_outbox(pushed) WHERE pushed = 0
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_outbox_severity
            ON error_outbox(severity)
        """)
        conn.commit()
        conn.close()

    # ========================================
    # Log error
    # ========================================

    def log(
        self,
        severity: str,
        error_code: str,
        message: str,
        details: Dict[str, Any] = None
    ) -> int:
        """
        Log an error to the local outbox.

        Args:
            severity: One of CRITICAL, WARNING, INFO, DEBUG
            error_code: Short code like "GDRIVE_AUTH_EXPIRED", "LLM_TIMEOUT"
            message: Human-readable message
            details: Optional dict with extra context (stack trace, URL, etc.)

        Returns the error ID.
        """
        if severity not in VALID_SEVERITIES:
            severity = SEVERITY_WARNING

        occurred_at = datetime.now(timezone.utc).isoformat()
        details_str = json.dumps(details) if details else None

        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.execute("""
                INSERT INTO error_outbox (severity, error_code, message, details, occurred_at)
                VALUES (?, ?, ?, ?, ?)
            """, (severity, error_code, message, details_str, occurred_at))
            error_id = cursor.lastrowid
            conn.commit()
            conn.close()

        # Print to console for immediate visibility
        emoji = {
            SEVERITY_CRITICAL: '🔴',
            SEVERITY_WARNING: '🟠',
            SEVERITY_INFO: '🔵',
            SEVERITY_DEBUG: '⚪'
        }.get(severity, '❓')
        print(f"{emoji} [{severity}] {error_code}: {message}")

        return error_id

    # ========================================
    # Get unpushed errors
    # ========================================

    def get_unpushed(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get unpushed errors, oldest first."""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT * FROM error_outbox
                WHERE pushed = 0
                ORDER BY occurred_at ASC
                LIMIT ?
            """, (limit,)).fetchall()
            conn.close()

        return [dict(row) for row in rows]

    # ========================================
    # Push to Worker
    # ========================================

    def push_to_worker(self, cloudflare_sync) -> int:
        """
        Push all unpushed errors to the Cloudflare Worker.

        Args:
            cloudflare_sync: CloudflareSync instance

        Returns number of errors pushed.
        """
        unpushed = self.get_unpushed(limit=100)
        if not unpushed:
            return 0

        # Format for Worker API
        errors_payload = []
        for err in unpushed:
            errors_payload.append({
                'severity': err['severity'],
                'error_code': err['error_code'],
                'message': err['message'],
                'details': err['details'],
                'occurred_at': err['occurred_at']
            })

        success = cloudflare_sync.push_errors(errors_payload)

        if success:
            # Mark as pushed
            now_iso = datetime.now(timezone.utc).isoformat()
            with self._lock:
                conn = sqlite3.connect(self.db_path)
                for err in unpushed:
                    conn.execute("""
                        UPDATE error_outbox
                        SET pushed = 1, pushed_at = ?, push_count = push_count + 1
                        WHERE id = ?
                    """, (now_iso, err['id']))
                conn.commit()
                conn.close()

            print(f"[ErrorReporter] Pushed {len(unpushed)} errors to Worker")
            return len(unpushed)
        else:
            print(f"[ErrorReporter] Failed to push errors to Worker")
            return 0

    # ========================================
    # Cleanup old errors
    # ========================================

    def cleanup(self, days: int = 30):
        """Delete pushed errors older than N days."""
        cutoff = datetime.now(timezone.utc).timestamp() - (days * 86400)
        cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()

        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                DELETE FROM error_outbox
                WHERE pushed = 1 AND occurred_at < ?
            """, (cutoff_iso,))
            conn.commit()
            conn.close()

    # ========================================
    # Prune if too many (prevent unbounded growth)
    # ========================================

    def prune_excess(self, max_count: int = 1000):
        """If local queue exceeds max_count, remove oldest DEBUG entries."""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            count = conn.execute("SELECT COUNT(*) FROM error_outbox").fetchone()[0]

            if count > max_count:
                # Delete oldest DEBUG entries first
                conn.execute("""
                    DELETE FROM error_outbox
                    WHERE id IN (
                        SELECT id FROM error_outbox
                        WHERE severity = 'DEBUG'
                        ORDER BY occurred_at ASC
                        LIMIT ?
                    )
                """, (count - max_count,))
                conn.commit()

            conn.close()

    # ========================================
    # Get stats
    # ========================================

    def get_stats(self) -> Dict[str, int]:
        """Get error counts by severity (unpushed only)."""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            rows = conn.execute("""
                SELECT severity, COUNT(*) as count
                FROM error_outbox
                WHERE pushed = 0
                GROUP BY severity
            """).fetchall()
            conn.close()

        stats = {s: 0 for s in VALID_SEVERITIES}
        for sev, count in rows:
            stats[sev] = count
        return stats


# ========================================
# Convenience functions for common errors
# # ========================================

def log_critical(reporter: ErrorReporter, code: str, message: str, details: Dict = None):
    """Log a CRITICAL error (immediate DM)."""
    return reporter.log(SEVERITY_CRITICAL, code, message, details)


def log_warning(reporter: ErrorReporter, code: str, message: str, details: Dict = None):
    """Log a WARNING error (deduped 1h)."""
    return reporter.log(SEVERITY_WARNING, code, message, details)


def log_info(reporter: ErrorReporter, code: str, message: str, details: Dict = None):
    """Log an INFO event (daily summary)."""
    return reporter.log(SEVERITY_INFO, code, message, details)


def log_debug(reporter: ErrorReporter, code: str, message: str, details: Dict = None):
    """Log a DEBUG event (never DM'd, local only)."""
    return reporter.log(SEVERITY_DEBUG, code, message, details)
