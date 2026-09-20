#!/usr/bin/env python3
"""gitcurator.core.cache_db — the SQLite side-cache (``cache.db``).

Extracted verbatim from ``gitcurator/gui/app.py`` (v32.3 modularization).
Tracks processed repos, failed URLs and decommissioned repos so retry /
verify / undo features have persistent state next to the vault.

Concurrency contract (unchanged from v30):
* ``sqlite3.connect(check_same_thread=False, timeout=30)`` + PRAGMA
  ``busy_timeout`` — a second thread (bot-queue check, verify, retry)
  gets a clean wait instead of "database is locked".
* every statement runs under an ``RLock`` — sqlite connections with
  ``check_same_thread=False`` are NOT safe for concurrent cursor use.
* ``close()`` is idempotent; call sites use try/finally.

Pure stdlib (sqlite3 + threading) — eligible for the CI test gate.
"""

import os
import sqlite3
import threading
from datetime import datetime, timedelta

from gitcurator.core.links import normalize_url

__all__ = ["CacheDB"]


# ============================================================================
# Database (SQLite Cache)
# ============================================================================

class CacheDB:
    """v30 — Fix (Close CacheDB + busy_timeout + lock, W8):
    - sqlite3 connection now uses timeout=30 AND PRAGMA busy_timeout so a
      second thread (bot-queue check, verify, retry) touching the same
      cache.db gets a clean wait instead of 'database is locked' errors.
    - Every statement runs under an RLock — sqlite connections with
      check_same_thread=False are NOT safe for concurrent cursor use.
    - close() is idempotent; call sites use try/finally (see run()).
    """

    def __init__(self, db_path="cache.db"):
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
        self.conn.execute("PRAGMA busy_timeout = 30000")
        with self._lock:
            self.cursor = self.conn.cursor()
            self._create_tables()

    def _create_tables(self):
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS processed_repos (
                repo_id INTEGER PRIMARY KEY,
                url TEXT,
                owner TEXT,
                repo_name TEXT,
                processed_at TIMESTAMP,
                last_checked TIMESTAMP,
                note_path TEXT,
                category TEXT
            )
        """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS checkpoints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT,
                processed_count INTEGER,
                total_count INTEGER,
                status TEXT,
                timestamp TIMESTAMP
            )
        """)
        # v22 Feature 4: Retry queue — failed repos are tracked here so the
        # user can reprocess them with the "🔄 Retry Failed" button. Uses
        # CREATE TABLE IF NOT EXISTS so existing DBs are upgraded in place
        # (backward-compatible).
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS failed_repos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT,
                error TEXT,
                failed_at TIMESTAMP,
                retry_count INTEGER DEFAULT 0,
                resolved BOOLEAN DEFAULT 0
            )
        """)
        # Decommissioned repos — 404s that don't exist on GitHub.
        # Silently skipped in verification + processing. User doesn't need
        # to be reminded of them every batch.
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS decommissioned_repos (
                url TEXT PRIMARY KEY,
                reason TEXT,
                decommissioned_at TIMESTAMP
            )
        """)
        self.conn.commit()

    def is_duplicate(self, repo_id: int) -> bool:
        with self._lock:
            self.cursor.execute("SELECT repo_id FROM processed_repos WHERE repo_id = ?", (repo_id,))
            return self.cursor.fetchone() is not None

    def get_note_path(self, repo_id: int):
        """Return the stored note_path for a repo, or None if not cached."""
        with self._lock:
            self.cursor.execute("SELECT note_path FROM processed_repos WHERE repo_id = ?", (repo_id,))
            row = self.cursor.fetchone()
            return row[0] if row else None

    def remove_entry(self, repo_id: int):
        """Remove a repo from the cache (used when its note file was deleted)."""
        with self._lock:
            self.cursor.execute("DELETE FROM processed_repos WHERE repo_id = ?", (repo_id,))
            self.conn.commit()

    def is_note_valid(self, repo_id: int) -> bool:
        """Check if the note file for a cached repo still exists on disk.
        Returns False if the repo isn't cached OR if the note file is missing."""
        note_path = self.get_note_path(repo_id)
        if not note_path:
            return False
        return os.path.isfile(note_path)

    def add_processed(self, repo_id: int, url: str, owner: str, repo_name: str, note_path: str, category: str):
        now = datetime.now().isoformat()
        with self._lock:
            self.cursor.execute("""
                INSERT OR REPLACE INTO processed_repos
                (repo_id, url, owner, repo_name, processed_at, last_checked, note_path, category)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (repo_id, url, owner, repo_name, now, now, note_path, category))
            self.conn.commit()

    def update_last_checked(self, repo_id: int):
        now = datetime.now().isoformat()
        with self._lock:
            self.cursor.execute("UPDATE processed_repos SET last_checked = ? WHERE repo_id = ?", (now, repo_id))
            self.conn.commit()

    def purge_old(self, months=6):
        cutoff = (datetime.now() - timedelta(days=30*months)).isoformat()
        with self._lock:
            self.cursor.execute("DELETE FROM processed_repos WHERE last_checked < ?", (cutoff,))
            self.conn.commit()

    def clear_cache(self):
        with self._lock:
            self.cursor.execute("DELETE FROM processed_repos")
            self.conn.commit()

    # ------------------------------------------------------------------
    # v22 Feature 4: Retry Queue Database
    # ------------------------------------------------------------------
    def add_failed(self, url: str, error: str):
        """Record a failed repo so it can be retried later via 'Retry Failed'."""
        now = datetime.now().isoformat()
        try:
            with self._lock:
                self.cursor.execute(
                    "INSERT INTO failed_repos (url, error, failed_at, retry_count) VALUES (?, ?, ?, 0)",
                    (url, error, now)
                )
                self.conn.commit()
        except Exception:
            pass  # best-effort — never crash the batch on a DB error

    def get_failed_urls(self) -> list:
        """Return all unresolved (url, error) tuples, most-recent first."""
        try:
            with self._lock:
                self.cursor.execute(
                    "SELECT url, error FROM failed_repos WHERE resolved = 0 ORDER BY failed_at DESC"
                )
                return self.cursor.fetchall()
        except Exception:
            return []

    def mark_failed_resolved(self, url: str):
        """Mark a previously-failed URL as resolved (after successful reprocessing)."""
        try:
            with self._lock:
                self.cursor.execute(
                    "UPDATE failed_repos SET resolved = 1 WHERE url = ?", (url,)
                )
                self.conn.commit()
        except Exception:
            pass  # best-effort

    def get_failed_count(self) -> int:
        """Count of unresolved failed URLs."""
        try:
            with self._lock:
                self.cursor.execute("SELECT COUNT(*) FROM failed_repos WHERE resolved = 0")
                return self.cursor.fetchone()[0]
        except Exception:
            return 0

    def decommission(self, url: str, reason: str = "404 Not Found"):
        """Mark a URL as decommissioned (permanently skipped)."""
        now = datetime.now().isoformat()
        try:
            with self._lock:
                self.cursor.execute(
                    "INSERT OR REPLACE INTO decommissioned_repos (url, reason, decommissioned_at) VALUES (?, ?, ?)",
                    (normalize_url(url), reason, now)
                )
                self.conn.commit()
        except Exception:
            pass

    def is_decommissioned(self, url: str) -> bool:
        """Check if a URL has been decommissioned."""
        try:
            with self._lock:
                self.cursor.execute("SELECT url FROM decommissioned_repos WHERE url = ?", (normalize_url(url),))
                return self.cursor.fetchone() is not None
        except Exception:
            return False

    def get_all_decommissioned(self) -> list:
        """Get all decommissioned URLs."""
        try:
            with self._lock:
                self.cursor.execute("SELECT url, reason FROM decommissioned_repos")
                return self.cursor.fetchall()
        except Exception:
            return []

    def get_all_processed_urls(self) -> list:
        """Get all processed URLs (for verify fallback)."""
        try:
            with self._lock:
                self.cursor.execute("SELECT url FROM processed_repos")
                return [row[0] for row in self.cursor.fetchall()]
        except Exception:
            return []

    def close(self):
        """v30 — idempotent close (safe under races with worker threads)."""
        with self._lock:
            try:
                self.conn.close()
            except Exception:
                pass
