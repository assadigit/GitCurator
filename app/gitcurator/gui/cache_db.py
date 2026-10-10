"""CacheDB — moved verbatim from gitcurator/gui/app.py (branch refactor/gui-app-split; see docs/history/REFACTOR_PLAN.md)."""

import sys
import os
import re
import json
import time
import urllib.error
import sqlite3
import subprocess
import html as _html_module
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Any
import threading
import logging
from logging.handlers import RotatingFileHandler

from gitcurator.constants import (
    APP_DIR, COLORS, CONFIG_FILE, CONFIG_EXAMPLE, CATEGORY_FOLDERS,
    CATEGORY_KEYS, DEFAULT_SYSTEM_PROMPT,
)
from gitcurator.core import links as _links
from gitcurator.core import storage as _storage
from gitcurator.core import note_builder as _note_builder
from gitcurator.core import llm_client as _llm_client
from gitcurator.core import dryrun as _dryrun
from gitcurator.core import note_state as _note_state
from gitcurator.core import website_pipeline as _website_pipeline
from gitcurator.core import connection_check as _connection_check
from gitcurator.integrations import vaultseal as _vaultseal
from gitcurator.integrations import goodrepos as _goodrepos
from gitcurator.gui.telegram_lock import TelegramLockManager
from gitcurator.gui import icons as _icons
from gitcurator.integrations.subprocess_runner import (
    run_telegram_worker as _run_worker_subprocess,
    kill_all_workers as _kill_all_telegram_workers,
    live_worker_count as _live_telegram_worker_count,
)

_APP_DIR = APP_DIR

from gitcurator.gui.dead_links import DEAD_LINK_THRESHOLD

from gitcurator.gui.link_helpers import normalize_url

# v0.63.3 — THE SETTLED REPOS LEDGER's one-time guard (the twin of the
# websites' websites_settled_at, v0.63.2): present once settle_existing_repos()
# has run on this machine. Pre-settlement cries about the old piles stay
# silent; the first queue check settles them with one honest 🤝 line.
REPOS_SETTLED_META_KEY = 'repos_settled_at'


def failed_rows_truth(rows, github_has=None):
    """v0.63.1 — THE TRUTH PASS over the retry queue's rows (pure, no DB,
    no files — the caller supplies the probes).

    The owner's report (v0.63.0 session): "a weird message … asks for
    retrying repos and websites … every repo and website is processed and
    tidy, and nothing needs retry." The queue's old rows outlived their
    truth: a WEBSITE url enqueued by Verify Vault's Phase-3 fallback (it
    added every failed manifest row, non-github too — and the GitHub-only
    resolver could never clear it), a repo whose retry succeeded under a
    drifted URL spelling, a repo that later took the dedupe-skip path —
    all of them stayed ``resolved = 0`` forever, crying at every launch.

    This classifier re-asks the only question that matters — is the
    note actually missing? ``rows`` are ``(url, error)`` tuples (the
    shape :meth:`CacheDB.get_failed_urls` returns); ``github_has`` is an
    optional ``url -> bool`` probe over the GitHub vault's index (the
    same VaultIndex ground truth reconciliation uses; None = no probe
    available). Returns ``(stale, missing, websites)``:

      * ``stale`` — github urls whose notes ARE in the vault: old cries,
        not missing work; the caller resolves them out of the queue.
      * ``missing`` — github urls whose notes are genuinely absent (or
        unprovable — a missing probe keeps the old, loud behavior):
        the honest cry, worth announcing.
      * ``websites`` — non-github urls: NEVER the repos retry queue's
        business (the retry batch feeds them to the GitHub loop, which
        just says "Skipping non-GitHub URL"). The caller resolves them
        out too; their own doors (the Websites pipeline's retry queue,
        the master table) tell their story.
    """
    stale, missing, websites = [], [], []
    for row in rows or []:
        if isinstance(row, dict):
            url = row.get('url')
        elif isinstance(row, (tuple, list)) and row:
            url = row[0]
        else:
            url = None            # a None or empty row is nobody's retry
        url = str(url or '').strip()
        if not url:
            continue
        if _links.extract_github_urls(url):
            if github_has is not None and github_has(url):
                stale.append(url)
            else:
                missing.append(url)
        else:
            websites.append(url)
    return stale, missing, websites


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
        # v0.09.4 — Fix (split-state root cause): the default path used to be
        # CWD-relative "cache.db". Every documented launcher (both .bat files,
        # `python main.py` from app/) runs with cwd == APP_DIR, so the GUI
        # always used <app>/cache.db — but a CLI run started from ANY other
        # directory (scheduled task, `python C:\...\app\main.py --cli --auto`
        # from a project folder) silently created a parallel EMPTY cache in
        # that cwd: processed_repos, the 404 quarantine and the retry queue
        # all read 0 → the app "thinks none of the links is processed" and
        # re-processes the whole bot queue (duplication). The default is now
        # anchored to APP_DIR so the GUI and the CLI ALWAYS share one cache;
        # explicit db_path arguments (tests) are passed through unchanged.
        if db_path == "cache.db":
            db_path = os.path.join(APP_DIR, "cache.db")
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
        self.conn.execute("PRAGMA busy_timeout = 30000")
        # v0.06 — Perf (SQLite): WAL mode lets readers and the writer work
        # concurrently (the old rollback journal serialized EVERYTHING and
        # produced 'database is locked' under load); synchronous=NORMAL is
        # the recommended pairing with WAL — durable enough for a local
        # cache, far fewer fsyncs than FULL. Two indexes back the hot
        # lookup columns (failed url resolution + note-path joins), which
        # the schema grew without.
        try:
            self.conn.execute("PRAGMA journal_mode = WAL")
            self.conn.execute("PRAGMA synchronous = NORMAL")
        except sqlite3.Error:
            pass  # e.g. read-only filesystem — keep the old journal mode
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
        # v0.06 — Perf: index the columns the hot queries filter on.
        self.cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_processed_repos_url
            ON processed_repos(url)
        """)
        self.cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_processed_repos_note_path
            ON processed_repos(note_path)
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
        # v0.06 — Perf: the retry queue resolves by URL; index it.
        self.cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_failed_repos_url
            ON failed_repos(url)
        """)
        # Decommissioned repos — 404s that don't exist on GitHub.
        # v0.08 — 404 QUARANTINE (owner report: "6-8 deleted repos that
        # became 404 — the app repeats to find and 404 them again and add
        # them to the logs. After 2-3 tries across different sessions,
        # ignore those links"): the table now counts consecutive 404
        # attempts per URL. A link is CONFIRMED DEAD once fail_count
        # reaches DEAD_LINK_THRESHOLD (3) and is then skipped silently in
        # every input path — bot queue, Telethon channel fetch, import
        # files — so it can never re-enter a batch. Attempts 1 and 2 are
        # still logged (a 404 can be a transient API hiccup, and the
        # count must accumulate ACROSS SESSIONS, which the SQLite cache
        # provides for free).
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS decommissioned_repos (
                url TEXT PRIMARY KEY,
                reason TEXT,
                decommissioned_at TIMESTAMP,
                fail_count INTEGER NOT NULL DEFAULT 3
            )
        """)
        # v0.08 migration: pre-v0.08 databases have no fail_count column.
        # Rows that already existed were 404'd at least once in a PREVIOUS
        # session — per the owner's spec ("after 2-3 tries across different
        # sessions, ignore") they are treated as fully confirmed dead
        # (fail_count = threshold) so the fix takes effect immediately on
        # the 6-8 repos the owner already keeps re-hitting.
        try:
            cols = [row[1] for row in self.cursor.execute(
                "PRAGMA table_info(decommissioned_repos)").fetchall()]
            if 'fail_count' not in cols:
                self.cursor.execute(
                    "ALTER TABLE decommissioned_repos "
                    "ADD COLUMN fail_count INTEGER NOT NULL DEFAULT 3")
                self.conn.commit()
        except Exception:
            pass  # best-effort migration — a missing column just means
                  # unconfirmed counting until the table is recreated
        # v0.09 (lineage merge) — one-time migration from the v0.07 strike
        # table. Our v0.07.x lineage persisted 404 strikes in a dedicated
        # notfound_strikes table; the unified design counts attempts in
        # decommissioned_repos.fail_count. Move any strike counts over
        # (keeping the HIGHER count when a URL exists in both), then drop
        # the old table so --status and the viewers see one system.
        try:
            has_strikes = self.cursor.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='notfound_strikes'").fetchone()
            if has_strikes:
                for s_url, s_count in self.cursor.execute(
                        "SELECT url, strikes FROM notfound_strikes").fetchall():
                    if not s_url:
                        continue
                    self.cursor.execute(
                        """
                        INSERT INTO decommissioned_repos
                            (url, reason, decommissioned_at, fail_count)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(url) DO UPDATE SET
                            fail_count = MAX(fail_count, excluded.fail_count)
                        """,
                        (normalize_url(s_url),
                         "404 Not Found (migrated v0.07 strike counter)",
                         datetime.now().isoformat(),
                         int(s_count or 1)))
                self.cursor.execute("DROP TABLE notfound_strikes")
        except Exception:
            pass  # best-effort — on error the old table simply stays unused
        # v0.63.3 — THE SETTLED REPOS LEDGER (the twin of the websites'
        # websites_settled, v0.63.2): repos already sent to the bot and
        # addressed are settled — never machine-fetched, never counted
        # as pending work again. Seeded ONCE by settle_existing_repos()
        # (the meta key below is the guard); grows only through the
        # owner's own verdict doors (the manual-resolve dialog); shrinks
        # through reset_dead_links (the ♻️ door back). cache_meta is the
        # ledger's own little key-value store (the twin of
        # website_state_meta).
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS repos_settled (
                url TEXT PRIMARY KEY,
                settled_at TIMESTAMP
            )
        """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS cache_meta (
                key TEXT PRIMARY KEY,
                value TEXT
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
    # (v0.63.1 — the queue learned the truth: rows are keyed by the
    #  normalized URL, never stack, and resolve under either spelling)
    # ------------------------------------------------------------------
    def add_failed(self, url: str, error: str):
        """Record a failed repo so it can be retried later via 'Retry Failed'.

        v0.63.1 — THE QUEUE'S TWO OLD LIES fixed here:
          * STACKING: a plain INSERT meant the same URL re-failed (or
            re-enqueued by every Verify Vault click) stacked row after
            row, so the startup cry counted one repo many times over.
            Now an unresolved row for the URL is UPDATED in place (the
            fresher error wins); resolved history is never touched.
          * SPELLING: the row is keyed by ``normalize_url`` (the same
            grammar every intake uses), so a later resolve under a
            trailing-slash / case-drifted spelling actually matches.
        Best-effort — never crashes the batch on a DB error."""
        now = datetime.now().isoformat()
        norm = normalize_url(url or '')
        try:
            with self._lock:
                cur = self.cursor.execute(
                    "UPDATE failed_repos SET error = ?, failed_at = ? "
                    "WHERE url = ? AND resolved = 0",
                    (error, now, norm))
                if not cur.rowcount:
                    self.cursor.execute(
                        "INSERT INTO failed_repos "
                        "(url, error, failed_at, retry_count) "
                        "VALUES (?, ?, ?, 0)",
                        (norm, error, now))
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
        """Mark a previously-failed URL as resolved (after successful
        reprocessing).

        v0.63.1 — the resolve now matches BOTH spellings: the normalized
        key (what add_failed writes since this version) AND the raw URL
        (what legacy rows carry), so a repo that failed as
        ``…/repo`` and came back as ``…/Repo/`` still clears its row —
        the exact drift that used to leave the queue crying forever."""
        try:
            with self._lock:
                self.cursor.execute(
                    "UPDATE failed_repos SET resolved = 1 "
                    "WHERE url = ? OR url = ?",
                    (url, normalize_url(url or '')))
                self.conn.commit()
        except Exception:
            pass  # best-effort

    def resolve_failed_urls(self, urls):
        """v0.63.1 — bulk mark_failed_resolved: the startup truth pass
        (and any sweep that re-classifies rows against the vaults)
        clears a whole list in one go, each URL under both spellings.
        Returns how many rows actually flipped (0 when nothing matched
        — the caller may stay quiet)."""
        flipped = 0
        try:
            with self._lock:
                for url in urls or []:
                    try:
                        cur = self.cursor.execute(
                            "UPDATE failed_repos SET resolved = 1 "
                            "WHERE (url = ? OR url = ?) AND resolved = 0",
                            (url, normalize_url(url or '')))
                        flipped += cur.rowcount or 0
                    except Exception:
                        pass
                self.conn.commit()
        except Exception:
            pass
        return flipped

    def get_failed_count(self) -> int:
        """Count of unresolved failed URLs."""
        try:
            with self._lock:
                self.cursor.execute("SELECT COUNT(*) FROM failed_repos WHERE resolved = 0")
                return self.cursor.fetchone()[0]
        except Exception:
            return 0

    def decommission(self, url: str, reason: str = "404 Not Found"):
        """Mark a URL as decommissioned (permanently skipped).

        v0.08: kept for compatibility (cloud sync / older call sites);
        the processing pipeline now uses :meth:`record_404`, which counts
        attempts and only confirms death at DEAD_LINK_THRESHOLD."""
        now = datetime.now().isoformat()
        try:
            with self._lock:
                self.cursor.execute(
                    "INSERT OR REPLACE INTO decommissioned_repos (url, reason, decommissioned_at, fail_count) VALUES (?, ?, ?, ?)",
                    (normalize_url(url), reason, now, DEAD_LINK_THRESHOLD)
                )
                self.conn.commit()
        except Exception:
            pass

    def record_404(self, url: str, reason: str = "404 Not Found") -> int:
        """v0.08 — Count one 404 attempt for a URL (across sessions).

        Returns the NEW consecutive-failure count (1 on first sighting).
        When the count reaches DEAD_LINK_THRESHOLD the link is CONFIRMED
        DEAD and every input path skips it silently from then on."""
        now = datetime.now().isoformat()
        norm = normalize_url(url)
        try:
            with self._lock:
                row = self.cursor.execute(
                    "SELECT fail_count FROM decommissioned_repos WHERE url = ?",
                    (norm,)).fetchone()
                count = (row[0] if row and row[0] else 0) + 1
                self.cursor.execute(
                    "INSERT OR REPLACE INTO decommissioned_repos (url, reason, decommissioned_at, fail_count) VALUES (?, ?, ?, ?)",
                    (norm, reason, now, count)
                )
                self.conn.commit()
                return count
        except Exception:
            return DEAD_LINK_THRESHOLD  # fail closed: on DB error treat as
                                        # confirmed so the batch still moves on

    def confirm_dead(self, url: str, reason: str = "404 Not Found",
                     threshold: int = None) -> None:
        """v0.20.0 — immediately mark a URL CONFIRMED dead (fail_count set
        to at least the configured threshold). Used when a missing-repo
        note is written: a GitHub 404 on /repos/{owner}/{repo} is
        definitive (deleted or private), and the note in the vault keeps
        the link out of every pending count from now on."""
        _thr = int(threshold or DEAD_LINK_THRESHOLD)
        now = datetime.now().isoformat()
        norm = normalize_url(url)
        try:
            with self._lock:
                row = self.cursor.execute(
                    "SELECT fail_count FROM decommissioned_repos WHERE url = ?",
                    (norm,)).fetchone()
                count = max(row[0] if row and row[0] else 0, _thr)
                self.cursor.execute(
                    "INSERT OR REPLACE INTO decommissioned_repos (url, reason, decommissioned_at, fail_count) VALUES (?, ?, ?, ?)",
                    (norm, reason, now, count)
                )
                self.conn.commit()
        except Exception:
            pass

    def get_unconfirmed_404s(self, threshold: int = None) -> list:
        """v0.20.0 — [(url, fail_count)] for rows that 404'd at least once
        but are not yet confirmed (1 <= fail_count < threshold) — the
        missing-repo-note backfill set. With the note flow, new 404s are
        confirmed immediately; this list is the LEGACY tail (repos that
        struck out in earlier versions and never got a note)."""
        _thr = int(threshold or DEAD_LINK_THRESHOLD)
        try:
            with self._lock:
                rows = self.cursor.execute(
                    "SELECT url, fail_count FROM decommissioned_repos "
                    "WHERE fail_count >= 1 AND fail_count < ? "
                    "ORDER BY fail_count DESC, url", (_thr,)).fetchall()
            return [(r[0], r[1]) for r in rows]
        except Exception:
            return []

    def is_dead_link(self, url: str, threshold: int = None) -> bool:
        """v0.08 — True when the URL is CONFIRMED dead (fail_count >=
        threshold). Unconfirmed entries (1-2 attempts) return False so they
        get their remaining attempts. v0.09: threshold is a parameter
        (default DEAD_LINK_THRESHOLD; callers pass the configured value)."""
        _thr = int(threshold or DEAD_LINK_THRESHOLD)
        try:
            with self._lock:
                row = self.cursor.execute(
                    "SELECT fail_count FROM decommissioned_repos WHERE url = ?",
                    (normalize_url(url),)).fetchone()
                return bool(row and row[0] and row[0] >= _thr)
        except Exception:
            return False

    def get_dead_url_set(self, threshold: int = None) -> set:
        """v0.08 — Set of CONFIRMED-dead normalized URLs (fail_count >=
        threshold). Loaded ONCE per batch/queue-check and tested with
        `normalize_url(url) in dead` — no per-URL queries. v0.09: threshold
        is a parameter (callers pass the configured value)."""
        _thr = int(threshold or DEAD_LINK_THRESHOLD)
        try:
            with self._lock:
                rows = self.cursor.execute(
                    "SELECT url FROM decommissioned_repos WHERE fail_count >= ?",
                    (_thr,)).fetchall()
                return {r[0] for r in rows}
        except Exception:
            return set()

    def get_dead_urls(self, threshold: int = None) -> list:
        """v0.08 — Confirmed-dead rows as (url, reason, fail_count,
        decommissioned_at) tuples, oldest first — for the quarantine
        viewer / CLI listing. v0.09: threshold is a parameter (callers
        pass the configured value)."""
        _thr = int(threshold or DEAD_LINK_THRESHOLD)
        try:
            with self._lock:
                return self.cursor.execute(
                    "SELECT url, reason, fail_count, decommissioned_at "
                    "FROM decommissioned_repos WHERE fail_count >= ? "
                    "ORDER BY decommissioned_at ASC",
                    (_thr,)).fetchall()
        except Exception:
            return []

    def get_quarantine_stats(self) -> list:
        """v0.09 (lineage merge) — EVERY quarantine row (confirmed AND
        in-progress attempts) as (url, reason, fail_count,
        decommissioned_at) tuples, most-strikes first — for the Settings →
        Dashboard manager and CLI --status (the v0.07 strike viewer showed
        under-threshold rows too; get_dead_urls filters them out)."""
        try:
            with self._lock:
                return self.cursor.execute(
                    "SELECT url, reason, fail_count, decommissioned_at "
                    "FROM decommissioned_repos "
                    "ORDER BY fail_count DESC, decommissioned_at DESC"
                ).fetchall()
        except Exception:
            return []

    def reset_dead_links(self, url: str = None) -> int:
        """v0.08 — Clear the 404 quarantine (whole table, or one URL).

        For false positives (a repo that went PRIVATE reads as 404 to an
        unauthorized token; restoring it later should work again).
        Returns how many entries were removed.

        v0.63.3 — the ♻️ door back through THE SETTLED REPOS LEDGER: the
        repos twin of the master table's ♻️ revive. A quarantined URL
        was settled the moment it was addressed (seeded by the one-time
        settlement, or by the manual-resolve verdict), so removing its
        quarantine row WITHOUT un-settling it would be a silent no-op
        under the owner's law ("only repos added from now on are
        fetched") — the reset now removes BOTH marks (and ONLY for the
        URLs whose quarantine row is actually going; every OTHER
        settled repo keeps its settlement), and the repo is fetched
        like new again."""
        try:
            with self._lock:
                if url:
                    norm = normalize_url(url)
                    cur = self.cursor.execute(
                        "DELETE FROM decommissioned_repos WHERE url = ?",
                        (norm,))
                    _removed = cur.rowcount or 0
                    if _removed:
                        self.cursor.execute(
                            "DELETE FROM repos_settled WHERE url = ?",
                            (norm,))
                else:
                    _revived = [r[0] for r in self.cursor.execute(
                        "SELECT url FROM decommissioned_repos").fetchall()]
                    cur = self.cursor.execute(
                        "DELETE FROM decommissioned_repos")
                    _removed = cur.rowcount or 0
                    if _revived:
                        self.cursor.executemany(
                            "DELETE FROM repos_settled WHERE url = ?",
                            [(u,) for u in _revived])
                self.conn.commit()
                return _removed
        except Exception:
            return 0

    def is_decommissioned(self, url: str) -> bool:
        """Check if a URL has been decommissioned (any entry, confirmed or
        not — legacy callers). Prefer is_dead_link()/get_dead_url_set() for
        batch filtering."""
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

    # -- v0.63.3 THE SETTLED REPOS LEDGER -----------------------------------
    # (the twin of core/website_state.py's settled ledger, v0.63.2 — the
    #  owner's report, session, verbatim: "Do the same for github repos,
    #  I want you to only count from here and now on. since older ones
    #  are processed. and not needed you to read from the beginning.")

    def get_meta(self, key: str) -> Optional[str]:
        """v0.63.3 — cache_meta read (the ledger's own little kv store;
        the twin of WebsiteStateDB.get_meta)."""
        try:
            with self._lock:
                row = self.cursor.execute(
                    "SELECT value FROM cache_meta WHERE key=?", (key,)).fetchone()
                return row[0] if row else None
        except Exception:
            return None

    def set_meta(self, key: str, value: str) -> None:
        """v0.63.3 — cache_meta write (INSERT OR REPLACE)."""
        try:
            with self._lock:
                self.cursor.execute(
                    "INSERT OR REPLACE INTO cache_meta (key, value)"
                    " VALUES (?,?)", (key, value))
                self.conn.commit()
        except Exception:
            pass  # best-effort — a meta write never breaks a caller

    def is_repo_settled(self, url: str) -> bool:
        """v0.63.3 — is this repo settled (already sent to the bot and
        addressed — the owner's law: never machine-fetched, never counted
        as pending work again)? Normalized through the same link grammar
        the queue classification and the dead-set use."""
        try:
            with self._lock:
                row = self.cursor.execute(
                    "SELECT 1 FROM repos_settled WHERE url=?",
                    (normalize_url(url),)).fetchone()
                return row is not None
        except Exception:
            return False

    def settle_repos(self, urls) -> int:
        """v0.63.3 — add repos to the settled ledger (idempotent; the
        owner's verdict doors use this — the manual-resolve dialog's
        "Mark as Processed" / "Decommission"). Returns how many rows were
        actually inserted."""
        now_iso = datetime.now().isoformat(timespec='seconds')
        added = 0
        with self._lock:
            for u in (urls or []):
                if not u:
                    continue
                cur = self.cursor.execute(
                    "INSERT OR IGNORE INTO repos_settled (url, settled_at)"
                    " VALUES (?,?)", (normalize_url(u), now_iso))
                added += cur.rowcount or 0
            if added:
                self.conn.commit()
        return added

    def unsettle_repo(self, url: str) -> bool:
        """v0.63.3 — the ♻️ door back: remove one repo from the settled
        ledger so a revived link is fetched like new again (the twin of
        WebsiteStateDB.unsettle). Returns True when a settled row was
        actually removed."""
        try:
            with self._lock:
                cur = self.cursor.execute(
                    "DELETE FROM repos_settled WHERE url=?",
                    (normalize_url(url),))
                self.conn.commit()
                return bool(cur.rowcount)
        except Exception:
            return False

    def settled_repos_count(self) -> int:
        """v0.63.3 — the ledger's size (the honest settlement line)."""
        try:
            with self._lock:
                row = self.cursor.execute(
                    "SELECT COUNT(*) FROM repos_settled").fetchone()
                return int(row[0]) if row else 0
        except Exception:
            return 0

    def get_settled_repo_set(self) -> set:
        """v0.63.3 — the settled set, loaded ONCE per queue-check/batch and
        tested with ``normalize_url(url) in settled`` — no per-URL queries
        (the same shape as get_dead_url_set)."""
        try:
            with self._lock:
                rows = self.cursor.execute(
                    "SELECT url FROM repos_settled").fetchall()
                return {r[0] for r in rows}
        except Exception:
            return set()

    def repos_ledger_settled(self) -> bool:
        """v0.63.3 — has THE SETTLEMENT run on this machine yet? (The
        ``repos_settled_at`` meta guard — pre-settlement cries about the
        old piles stay silent: the first queue check settles them.)"""
        return self.get_meta(REPOS_SETTLED_META_KEY) is not None

    def settle_existing_repos(self, extra_urls=None) -> Dict:
        """v0.63.3 — THE SETTLEMENT, one time, on the owner's word.

        The owner's report (session, verbatim): "Do the same for github
        repos, I want you to only count from here and now on. since older
        ones are processed. and not needed you to read from the
        beginning." Every repo the system ALREADY knows is settled —
        addressed and processed, never machine-fetched, never counted as
        pending work again — and the repos retry queue (whose every row
        predates the law) gets its settlement date too: cleared, so no
        startup cry ever re-serves an old link. Only repos added from
        now on are counted (and their own failures earn their own
        retries).

        Seeds the ledger from every URL the cache knows —
        ``processed_repos`` (stored), ``failed_repos`` (the unresolved
        pile), ``decommissioned_repos`` (the quarantine, confirmed or
        not) — plus the ``extra_urls`` the queue-door caller adds (the
        FULL bot history at the settlement door: every repo already sent
        to the bot is an "older one", whatever became of it). Guarded by
        the ``repos_settled_at`` meta key: a second call is a no-op
        (``{'settled': 0, 'retries_cleared': 0, 'already': True}``), so
        every caller may call it freely. Returns
        ``{'settled': n, 'retries_cleared': m, 'already': bool}``."""
        with self._lock:
            row = self.cursor.execute(
                "SELECT value FROM cache_meta WHERE key=?",
                (REPOS_SETTLED_META_KEY,)).fetchone()
        if row:
            return {'settled': 0, 'retries_cleared': 0, 'already': True}
        now_iso = datetime.now().isoformat(timespec='seconds')
        with self._lock:
            urls: List[str] = [r[0] for r in self.cursor.execute(
                "SELECT url FROM processed_repos").fetchall()]
            urls += [r[0] for r in self.cursor.execute(
                "SELECT url FROM failed_repos").fetchall()]
            urls += [r[0] for r in self.cursor.execute(
                "SELECT url FROM decommissioned_repos").fetchall()]
            for u in (extra_urls or []):
                if u:
                    urls.append(u)
            seen: set = set()
            unique = []
            for u in urls:
                norm = normalize_url(u) if u else ''
                if norm and norm not in seen:
                    seen.add(norm)
                    unique.append(norm)
            self.cursor.executemany(
                "INSERT OR IGNORE INTO repos_settled (url, settled_at)"
                " VALUES (?,?)", [(u, now_iso) for u in unique])
            cur = self.cursor.execute("DELETE FROM failed_repos")
            retries_cleared = cur.rowcount or 0
            self.cursor.execute(
                "INSERT OR REPLACE INTO cache_meta (key, value)"
                " VALUES (?,?)", (REPOS_SETTLED_META_KEY, now_iso))
            self.conn.commit()
        return {'settled': len(unique), 'retries_cleared': retries_cleared,
                'already': False}

    def close(self):
        """v30 — idempotent close (safe under races with worker threads)."""
        with self._lock:
            try:
                self.conn.close()
            except Exception:
                pass
