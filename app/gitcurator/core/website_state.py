#!/usr/bin/env python3
"""
website_state.py — the Websites pipeline's persistent state ledger.

``WebsiteStateDB`` owns the ``websites_processed`` / ``websites_dismissed`` /
``websites_retry_queue`` tables in cache.db (the same file as CacheDB and
NoteStateDB, same APP_DIR anchoring rule) plus the fetch-retry policy
constants. Extracted verbatim from website_pipeline.py at v0.25.0 —
the pipeline module re-exports these names so existing import paths
(``from gitcurator.core.website_pipeline import WebsiteStateDB``) and
test patch targets keep working unchanged.
"""

import os
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from gitcurator.constants import APP_DIR

MAX_FETCH_RETRIES = 3            # SPEC: retried automatically up to 3 times
RETRY_BACKOFF_DAYS = 2           # "over several days"


class WebsiteStateDB:
    """cache.db tables for the websites pipeline (same file as CacheDB and
    NoteStateDB — same APP_DIR anchoring rule, own connection + lock).

    Tables:
      websites_processed — one row per processed URL (dedupe layer 2)
      website_retry_queue — fetch failures awaiting automatic retry
      dismissed_urls — URLs whose note the owner deleted (never re-add;
        populated by Phase 3's delete detection, read here from day one)
    """

    def __init__(self, db_path: str = "cache.db"):
        if db_path == "cache.db":
            db_path = os.path.join(APP_DIR, "cache.db")
        self.db_path = db_path
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
        self.conn.execute("PRAGMA busy_timeout = 30000")
        with self._lock:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS websites_processed (
                    url TEXT PRIMARY KEY,
                    note_path TEXT,
                    category TEXT,
                    subcategory TEXT,
                    fetch_status TEXT,
                    processed_at TEXT
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS website_retry_queue (
                    url TEXT PRIMARY KEY,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    next_attempt_at TEXT,
                    first_failed_at TEXT,
                    last_failed_at TEXT
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS dismissed_urls (
                    url TEXT PRIMARY KEY,
                    reason TEXT,
                    dismissed_at TEXT
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS website_state_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            self.conn.commit()

    # -- writes -----------------------------------------------------------

    def mark_processed(self, url: str, note_path: str, category: str,
                       subcategory: str, fetch_status: str) -> None:
        """Record the dedupe row for a processed URL. Does NOT touch the
        retry queue — a fetch-failed link keeps its _review note AND its
        pending retries; resolve_retry() clears the queue only on full
        success."""
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO websites_processed "
                "(url, note_path, category, subcategory, fetch_status,"
                " processed_at) VALUES (?,?,?,?,?,?)",
                (url, note_path, category, subcategory, fetch_status,
                 datetime.now().isoformat(timespec='seconds')))
            self.conn.commit()

    def enqueue_retry(self, url: str, error: str) -> None:
        """Record/refresh a fetch failure. ``next_attempt_at`` backs off
        RETRY_BACKOFF_DAYS per attempt so retries spread over several days.
        The ORIGINAL first_failed_at survives refreshes."""
        now = datetime.now()
        now_iso = now.isoformat(timespec='seconds')
        with self._lock:
            row = self.conn.execute(
                "SELECT attempts, first_failed_at FROM website_retry_queue"
                " WHERE url=?", (url,)).fetchone()
            attempts = (row[0] + 1) if row else 1
            first_failed = (row[1] if row and row[1] else now_iso)
            next_at = (now + timedelta(days=RETRY_BACKOFF_DAYS * attempts)) \
                .isoformat(timespec='seconds')
            self.conn.execute(
                "INSERT OR REPLACE INTO website_retry_queue "
                "(url, attempts, last_error, next_attempt_at, first_failed_at,"
                " last_failed_at) VALUES (?,?,?,?,?,?)",
                (url, attempts, error[:500], next_at, first_failed, now_iso))
            self.conn.commit()

    def resolve_retry(self, url: str) -> None:
        """Drop a link from the retry queue — called when processing fully
        succeeded (note written with real content). Kept SEPARATE from
        mark_processed on purpose: a fetch-failed link writes a _review note
        AND stays queued for its automatic retries."""
        with self._lock:
            self.conn.execute("DELETE FROM website_retry_queue WHERE url=?",
                              (url,))
            self.conn.commit()

    def dismiss(self, url: str, reason: str = "note deleted by owner") -> None:
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO dismissed_urls (url, reason,"
                " dismissed_at) VALUES (?,?,?)",
                (url, reason[:200], datetime.now().isoformat(timespec='seconds')))
            self.conn.commit()

    def undismiss(self, url: str) -> bool:
        """v0.44.0 — the graveyard's other door: remove a dismissal so a
        ♻️-revived link is fetched like new again (the table's revive
        marker is the owner's hand, this is the enforcement). Returns
        True when a dismissal was actually removed."""
        with self._lock:
            cur = self.conn.execute(
                "DELETE FROM dismissed_urls WHERE url=?", (url,))
            self.conn.commit()
            return bool(cur.rowcount)

    def forget_failed_row(self, url: str) -> bool:
        """v0.44.0 — delete a link's processed row ONLY when it is a
        failed ``_review`` placeholder record (fetch_status 'failed' +
        note_path inside _review) — the decommission consume calls this
        once the placeholder FILE is gone, so the ledger carries no row
        pointing at nothing. A real note's row is NEVER touched (the
        same guard purge_blocked_domains uses). Returns True when a row
        was removed."""
        with self._lock:
            row = self.conn.execute(
                "SELECT note_path, fetch_status FROM websites_processed"
                " WHERE url=?", (url,)).fetchone()
            if not row:
                return False
            path, status = row
            if status != 'failed' \
                    or '_review' not in str(path or '').replace('\\', '/'):
                return False
            self.conn.execute(
                "DELETE FROM websites_processed WHERE url=?", (url,))
            self.conn.commit()
            return True

    # -- v0.19.0 proxy-epoch meta + retry re-arm --------------------------

    def get_meta(self, key: str) -> Optional[str]:
        """One row from website_state_meta (None when absent)."""
        with self._lock:
            row = self.conn.execute(
                "SELECT value FROM website_state_meta WHERE key=?",
                (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO website_state_meta (key, value)"
                " VALUES (?,?)", (key, str(value)))
            self.conn.commit()

    def rearm_retries(self) -> int:
        """v0.19.0 — reset the ENTIRE retry queue (attempts=0, due NOW).
        Called when the web proxy turns ACTIVE: the queued failures were
        almost certainly the blocked-web pattern (x.com/t.co/youtu.be
        connection-refused on a poisoned resolver), and with DNS now
        resolving at the proxy they deserve an immediate fresh set.
        Returns how many rows were re-armed."""
        now_iso = datetime.now().isoformat(timespec='seconds')
        with self._lock:
            cur = self.conn.execute(
                "UPDATE website_retry_queue"
                " SET attempts=0, next_attempt_at=?", (now_iso,))
            self.conn.commit()
            return cur.rowcount or 0

    def purge_blocked_domains(self, is_blocked) -> Dict:
        """v0.20.0 — remove blocked-domain links from the retry queue and
        the failed ``_review`` placeholder records, marking every purged
        URL dismissed so it can never re-enter (the robust prevention the
        owner asked for). ``is_blocked(url) -> bool`` is injected (links.
        domain_is_blocked partial) so this stays stdlib-testable.

        Only app-owned ``_review`` placeholders are deleted (a real note
        for a blocked domain is the owner's — kept). File deletion is the
        CALLER's job (dry-run aware); this method only touches the DB.
        Returns ``{'retries': n, 'placeholders': [(url, path)],
        'dismissed': n}`` (idempotent — a second run finds nothing)."""
        with self._lock:
            retry_rows = self.conn.execute(
                "SELECT url FROM website_retry_queue").fetchall()
            retry_gone = [r[0] for r in retry_rows if is_blocked(r[0])]
            processed_rows = self.conn.execute(
                "SELECT url, note_path, fetch_status FROM"
                " websites_processed").fetchall()
            placeholders = []
            for url, path, status in processed_rows:
                if status != 'failed' or not path:
                    continue
                if not is_blocked(url):
                    continue
                # ONLY app-owned _review placeholders — never a real note.
                if '_review' not in str(path).replace('\\', '/'):
                    continue
                placeholders.append((url, path))
            now_iso = datetime.now().isoformat(timespec='seconds')
            purged = set(retry_gone) | {u for u, _ in placeholders}
            for url in retry_gone:
                self.conn.execute(
                    "DELETE FROM website_retry_queue WHERE url=?", (url,))
            for url, _path in placeholders:
                self.conn.execute(
                    "DELETE FROM websites_processed WHERE url=?", (url,))
            for url in purged:
                self.conn.execute(
                    "INSERT OR REPLACE INTO dismissed_urls (url, reason,"
                    " dismissed_at) VALUES (?,?,?)",
                    (url, "blocked domain (v0.20.0 purge)", now_iso))
            self.conn.commit()
        return {'retries': len(retry_gone), 'placeholders': placeholders,
                'dismissed': len(purged)}

    # -- reads ------------------------------------------------------------

    def is_processed(self, url: str) -> bool:
        with self._lock:
            row = self.conn.execute(
                "SELECT 1 FROM websites_processed WHERE url=?", (url,)).fetchone()
        return row is not None

    def processed_row(self, url: str) -> Optional[Dict]:
        with self._lock:
            row = self.conn.execute(
                "SELECT url, note_path, category, subcategory, fetch_status,"
                " processed_at FROM websites_processed WHERE url=?", (url,)).fetchone()
        if not row:
            return None
        return {'url': row[0], 'note_path': row[1], 'category': row[2],
                'subcategory': row[3], 'fetch_status': row[4],
                'processed_at': row[5]}

    def is_dismissed(self, url: str) -> bool:
        with self._lock:
            row = self.conn.execute(
                "SELECT 1 FROM dismissed_urls WHERE url=?", (url,)).fetchone()
        return row is not None

    def dismissed_row(self, url: str) -> Optional[Dict]:
        """v0.46.0 — the dismissal's stored reason. The auto-verdicts
        the fetcher writes (category 'dead'/'paywalled'/'refused') name
        themselves in the reason so the skip line can tell a retired
        link from a deleted note."""
        with self._lock:
            row = self.conn.execute(
                "SELECT url, reason, dismissed_at FROM dismissed_urls"
                " WHERE url=?", (url,)).fetchone()
        if not row:
            return None
        return {'url': row[0], 'reason': row[1], 'dismissed_at': row[2]}

    def retry_row(self, url: str) -> Optional[Dict]:
        with self._lock:
            row = self.conn.execute(
                "SELECT url, attempts, last_error, next_attempt_at,"
                " first_failed_at, last_failed_at FROM website_retry_queue"
                " WHERE url=?", (url,)).fetchone()
        if not row:
            return None
        return {'url': row[0], 'attempts': row[1], 'last_error': row[2],
                'next_attempt_at': row[3], 'first_failed_at': row[4],
                'last_failed_at': row[5]}

    def due_retries(self, now: Optional[datetime] = None) -> List[str]:
        """Retry-queue URLs whose backoff has elapsed (attempts < cap)."""
        now = now or datetime.now()
        with self._lock:
            rows = self.conn.execute(
                "SELECT url, attempts, next_attempt_at FROM website_retry_queue"
            ).fetchall()
        out = []
        for url, attempts, next_at in rows:
            if attempts >= MAX_FETCH_RETRIES:
                continue
            try:
                if datetime.fromisoformat(next_at) <= now:
                    out.append(url)
            except (TypeError, ValueError):
                out.append(url)
        return out

    def close(self) -> None:
        with self._lock:
            try:
                self.conn.close()
            except sqlite3.Error:
                pass
