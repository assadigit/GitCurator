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
from typing import Dict, Iterable, List, Optional

from gitcurator.constants import APP_DIR
from gitcurator.core.links import normalize_website_url as _normalize_url

MAX_FETCH_RETRIES = 3            # SPEC: retried automatically up to 3 times
RETRY_BACKOFF_DAYS = 2           # "over several days"

#: v0.64.0 — the column lists the healing pass writes back (the two
#: simplest tables share the first-wins preference; the URL column is
# always rewritten to the canonical key).
_TABLE_COLS = {
    'dismissed_urls': ('url', 'reason', 'dismissed_at'),
    'websites_settled': ('url', 'settled_at'),
}


def _row_note_alive(row) -> bool:
    """v0.64.0 — does a processed row's note still exist on disk? (the
    healing pass's preference: a live note beats a remembered one)."""
    try:
        path = str(row[1] or '')
        return bool(path) and os.path.isfile(path)
    except Exception:
        return False

def _key(url) -> str:
    """v0.64.0 — THE ONE SPELLING at the state DB's own boundary: every
    URL that crosses into or out of a ledger method is normalized
    FIRST, so a row written as ``https://site/`` answers a probe for
    ``https://site`` (and every other spelling) — the tables hold one
    canonical key per link, whatever spelling the caller carried.
    Broken input returns the raw string unchanged (the old behavior:
    the row simply never matches)."""
    try:
        u = str(url or '').strip()
        return _normalize_url(u) if u else ''
    except Exception:
        return str(url or '')


#: v0.63.2 — the meta key that stamps THE SETTLEMENT (one-time): every
#: website the system already knew when the owner's law arrived is
#: "addressed and processed" — never machine-fetched again.
SETTLED_META_KEY = 'websites_settled_at'

#: v0.64.1 — the meta key that stamps THE QUEUE-HISTORY SETTLEMENT (the
#: second one-time pass, the websites twin of v0.63.3's repos extras):
#: every link present in the bot's history at the queue door is settled
#: — the never-batched history stops counting as pending work.
QUEUE_HISTORY_SETTLED_META_KEY = 'websites_queue_history_settled_at'


class WebsiteStateDB:
    """cache.db tables for the websites pipeline (same file as CacheDB and
    NoteStateDB — same APP_DIR anchoring rule, own connection + lock).

    Tables:
      websites_processed — one row per processed URL (dedupe layer 2)
      website_retry_queue — fetch failures awaiting automatic retry
      dismissed_urls — URLs whose note the owner deleted (never re-add;
        populated by Phase 3's delete detection, read here from day one)
      websites_settled — v0.63.2 THE SETTLED LEDGER: URLs the owner has
        already sent to the bot and considers addressed — the machine
        never fetches them again (only ♻️ revived in the master table
        un-settles one, or 🖐 hand-delivery finishes it by hand)
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
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS websites_settled (
                    url TEXT PRIMARY KEY,
                    settled_at TEXT
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
                (_key(url), note_path, category, subcategory, fetch_status,
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
                " WHERE url=?", (_key(url),)).fetchone()
            attempts = (row[0] + 1) if row else 1
            first_failed = (row[1] if row and row[1] else now_iso)
            next_at = (now + timedelta(days=RETRY_BACKOFF_DAYS * attempts)) \
                .isoformat(timespec='seconds')
            self.conn.execute(
                "INSERT OR REPLACE INTO website_retry_queue "
                "(url, attempts, last_error, next_attempt_at, first_failed_at,"
                " last_failed_at) VALUES (?,?,?,?,?,?)",
                (_key(url), attempts, error[:500], next_at, first_failed,
                 now_iso))
            self.conn.commit()

    def resolve_retry(self, url: str) -> None:
        """Drop a link from the retry queue — called when processing fully
        succeeded (note written with real content). Kept SEPARATE from
        mark_processed on purpose: a fetch-failed link writes a _review note
        AND stays queued for its automatic retries."""
        with self._lock:
            self.conn.execute("DELETE FROM website_retry_queue WHERE url=?",
                              (_key(url),))
            self.conn.commit()

    def dismiss(self, url: str, reason: str = "note deleted by owner") -> None:
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO dismissed_urls (url, reason,"
                " dismissed_at) VALUES (?,?,?)",
                (_key(url), reason[:200],
                 datetime.now().isoformat(timespec='seconds')))
            self.conn.commit()

    def undismiss(self, url: str) -> bool:
        """v0.44.0 — the graveyard's other door: remove a dismissal so a
        ♻️-revived link is fetched like new again (the table's revive
        marker is the owner's hand, this is the enforcement). Returns
        True when a dismissal was actually removed."""
        with self._lock:
            cur = self.conn.execute(
                "DELETE FROM dismissed_urls WHERE url=?", (_key(url),))
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
                " WHERE url=?", (_key(url),)).fetchone()
            if not row:
                return False
            path, status = row
            if status != 'failed' \
                    or '_review' not in str(path or '').replace('\\', '/'):
                return False
            self.conn.execute(
                "DELETE FROM websites_processed WHERE url=?", (_key(url),))
            self.conn.commit()
            return True

    def forget_row(self, url: str) -> bool:
        """v0.58.0 — THE BANISHMENT's ledger half: remove a link's
        processed row UNCONDITIONALLY.

        The caller just removed the note FILE on purpose (🗑️ — the note
        carried the owner's delete mark and left for
        ``.trash/banished``, or the owner deleted it by hand and marked
        the master-table row), so the row must not outlive the note it
        pointed at — the exact false-success shape v0.57.0 closed for
        the hand rows ("the state ledger remembered a note the vault no
        longer carried"). :meth:`forget_failed_row` keeps its guard (it
        serves verdicts that only sweep FAILED placeholders); THIS door
        serves the deliberate removal of a REAL, proper note. Returns
        True when a row was removed."""
        with self._lock:
            cur = self.conn.execute(
                "DELETE FROM websites_processed WHERE url=?", (_key(url),))
            self.conn.commit()
            return bool(cur.rowcount)

    # -- v0.63.2 THE SETTLED LEDGER ----------------------------------------

    def is_settled(self, url: str) -> bool:
        """v0.63.2 — is this URL settled (already sent to the bot and
        addressed — the owner's law: never machine-fetched again)?"""
        with self._lock:
            row = self.conn.execute(
                "SELECT 1 FROM websites_settled WHERE url=?",
                (_key(url),)).fetchone()
        return row is not None

    def settle_urls(self, urls: Iterable[str]) -> int:
        """v0.63.2 — add URLs to the settled ledger (idempotent). Returns
        how many rows were actually inserted. v0.64.0 — THE ONE
        SPELLING: every URL is normalized before it lands (a link the
        owner re-sends as ``https://site/`` settles under the same key
        as ``https://site`` — one spelling, one row, one verdict)."""
        now_iso = datetime.now().isoformat(timespec='seconds')
        added = 0
        with self._lock:
            for u in (urls or []):
                u = _normalize_url(u) if u else ''
                if not u:
                    continue
                cur = self.conn.execute(
                    "INSERT OR IGNORE INTO websites_settled (url, settled_at)"
                    " VALUES (?,?)", (u, now_iso))
                added += cur.rowcount or 0
            if added:
                self.conn.commit()
        return added

    def unsettle(self, url: str) -> bool:
        """v0.63.2 — the ♻️ door back: remove one URL from the settled
        ledger so a revived link is fetched like new again (the table's
        revive marker is the owner's hand; this is the enforcement —
        the twin of :meth:`undismiss`). Returns True when a settled row
        was actually removed."""
        with self._lock:
            cur = self.conn.execute(
                "DELETE FROM websites_settled WHERE url=?", (_key(url),))
            self.conn.commit()
            return bool(cur.rowcount)

    def settled_count(self) -> int:
        """v0.63.2 — the ledger's size (the honest settlement line)."""
        with self._lock:
            row = self.conn.execute(
                "SELECT COUNT(*) FROM websites_settled").fetchone()
        return int(row[0]) if row else 0

    def settle_existing(self, extra_urls: Optional[Iterable[str]] = None
                        ) -> Dict:
        """v0.63.2 — THE SETTLEMENT, one time, on the owner's word.

        The owner's report (session, verbatim): "Do not fetch current
        websites which are sent to bot, because they're already
        addressed and processed. Fetch only websites, that are added to
        bot, from now on." Every website the system ALREADY knows is
        settled — addressed and processed, never machine-fetched again
        — and the fetch-retry queue (whose every row predates the law)
        gets its settlement date too: cleared, so no backoff timer ever
        re-serves an old link. Only websites added from now on are
        fetched (and their own failures earn their own 3 retries).

        Seeds the settled ledger from every URL the state knows —
        ``websites_processed`` (stored or walled), ``website_retry_queue``
        (the wall pile), ``dismissed_urls`` (the graveyard) — plus the
        ``extra_urls`` the vault-level caller adds (the master table's
        data rows and the ``_review`` note URLs — hand-added rows and
        lost-state placeholders included). Guarded by the
        ``websites_settled_at`` meta key: a second call is a no-op
        (``{'settled': 0, 'retries_cleared': 0, 'already': True}``), so
        every caller may call it freely. Returns
        ``{'settled': n, 'retries_cleared': m, 'already': bool}``."""
        with self._lock:
            row = self.conn.execute(
                "SELECT value FROM website_state_meta WHERE key=?",
                (SETTLED_META_KEY,)).fetchone()
        if row:
            return {'settled': 0, 'retries_cleared': 0, 'already': True}
        now_iso = datetime.now().isoformat(timespec='seconds')
        with self._lock:
            urls: List[str] = [r[0] for r in self.conn.execute(
                "SELECT url FROM websites_processed").fetchall()]
            urls += [r[0] for r in self.conn.execute(
                "SELECT url FROM website_retry_queue").fetchall()]
            urls += [r[0] for r in self.conn.execute(
                "SELECT url FROM dismissed_urls").fetchall()]
            for u in (extra_urls or []):
                if u:
                    urls.append(u)
            # v0.64.0 — THE ONE SPELLING: whatever spelling a row (or a
            # caller's extra) carries, the ledger settles the CANONICAL
            # key — both spellings of one link are one settled verdict.
            urls = [_normalize_url(u) for u in urls]
            urls = [u for u in urls if u]
            seen: set = set()
            unique = []
            for u in urls:
                if u and u not in seen:
                    seen.add(u)
                    unique.append(u)
            self.conn.executemany(
                "INSERT OR IGNORE INTO websites_settled (url, settled_at)"
                " VALUES (?,?)", [(u, now_iso) for u in unique])
            retries_cleared = 0
            cur = self.conn.execute("DELETE FROM website_retry_queue")
            retries_cleared = cur.rowcount or 0
            self.conn.execute(
                "INSERT OR REPLACE INTO website_state_meta (key, value)"
                " VALUES (?,?)", (SETTLED_META_KEY, now_iso))
            self.conn.commit()
        return {'settled': len(unique), 'retries_cleared': retries_cleared,
                'already': False}

    # -- v0.64.1 THE QUEUE-HISTORY SETTLEMENT --------------------------------

    def settle_queue_history(self, extra_urls: Optional[Iterable[str]] = None
                             ) -> Dict:
        """v0.64.1 — THE QUEUE-HISTORY SETTLEMENT, one time, on the owner's word.

        The owner's report (session, verbatim): "despite everything is
        fetched and processed, somehow the app says 85 sites need
        processing. it's probably false positive, because in the
        procedure they'll get skipped nonetheless." The v0.63.2
        settlement seeded only what the STATE knew (processed /
        retry-queue / dismissed rows) plus the vault's own truth — so a
        link the owner sent to the bot that never became a state row
        (never batched, a batch the owner stopped midway, a note deleted
        by hand outside the app) was left UN-settled and every queue
        check counted it as PENDING forever. This second pass settles
        every URL the queue door hands it — the FULL bot history at that
        moment — exactly the extras the repos twin (v0.63.3's
        settle_existing_repos) always took. Retry rows of exactly those
        URLs resolve (their retries stop firing; the banner stops crying
        for them), but the retry queue is NOT bulk-cleared: that clear
        belonged to the first settlement, and post-law failures keep
        their own honest 3-retry lifecycle. Guarded by the
        ``websites_queue_history_settled_at`` meta key: a second call is
        a no-op (``{'settled': 0, 'already': True}``), so every caller
        may call it freely. Returns ``{'settled': n, 'already': bool}``
        where ``settled`` counts the rows this pass actually inserted."""
        with self._lock:
            row = self.conn.execute(
                "SELECT value FROM website_state_meta WHERE key=?",
                (QUEUE_HISTORY_SETTLED_META_KEY,)).fetchone()
        if row:
            return {'settled': 0, 'already': True}
        now_iso = datetime.now().isoformat(timespec='seconds')
        urls: List[str] = [u for u in (extra_urls or []) if u]
        # v0.64.0 — THE ONE SPELLING: whatever spelling the history
        # carries, the ledger settles the CANONICAL key.
        urls = [_normalize_url(u) for u in urls]
        urls = [u for u in urls if u]
        seen: set = set()
        unique: List[str] = []
        for u in urls:
            if u and u not in seen:
                seen.add(u)
                unique.append(u)
        with self._lock:
            before = self.conn.execute(
                "SELECT COUNT(*) FROM websites_settled").fetchone()
            before_n = int(before[0]) if before else 0
            self.conn.executemany(
                "INSERT OR IGNORE INTO websites_settled (url, settled_at)"
                " VALUES (?,?)", [(u, now_iso) for u in unique])
            after = self.conn.execute(
                "SELECT COUNT(*) FROM websites_settled").fetchone()
            after_n = int(after[0]) if after else 0
            # the settled URLs' retry rows resolve — a settled link's
            # retries never fire again, and the banner never cries for
            # one (the queue itself is NOT bulk-cleared: post-law
            # failures keep their lifecycle)
            for u in unique:
                self.conn.execute(
                    "DELETE FROM website_retry_queue WHERE url=?", (u,))
            self.conn.execute(
                "INSERT OR REPLACE INTO website_state_meta (key, value)"
                " VALUES (?,?)", (QUEUE_HISTORY_SETTLED_META_KEY, now_iso))
            self.conn.commit()
        return {'settled': after_n - before_n, 'already': False}

    # -- v0.64.0 THE ONE SPELLING — the ledgers' healing pass ------------

    def normalize_ledger_keys(self) -> Dict:
        """v0.64.0 — THE ONE SPELLING's healing pass: re-key every
        ledger row under the fixed normalizer.

        The owner's report (session, verbatim): "it processed a same
        website two times, only one has slash, other doesnt". The old
        normalizer kept the ROOT slash, so ``https://cleanup.pictures/``
        and ``https://cleanup.pictures`` lived as two keys in every
        table — two settled rows, two processed rows, two retries, two
        notes. With the root slash now stripped, every historical
        spelling is re-keyed to the one canonical form; when both
        spellings of one link already exist, the rows MERGE under a
        deterministic preference (a processed row whose note exists on
        disk beats one whose does not; a retry row with more attempts
        beats one with fewer — the closer to retirement wins; the
        dismissed and settled tables keep the first row by key order).
        Idempotent and cheap (a SELECT over each table; rows whose key
        already normalizes to itself are untouched), so every door may
        call it freely — the first door after the upgrade heals the
        machine's whole history in one pass. Returns
        ``{'rekeyed': n, 'merged': m}``; never raises on the caller's
        head (a broken table is skipped, the old keys keep working)."""
        rekeyed = 0
        merged = 0
        try:
            with self._lock:
                # ---- websites_processed: prefer a live note on disk ----
                rows = self.conn.execute(
                    "SELECT url, note_path, category, subcategory,"
                    " fetch_status, processed_at FROM websites_processed"
                    " ORDER BY url").fetchall()
                by_key: Dict[str, tuple] = {}
                for r in rows:
                    key = _normalize_url(r[0]) if r[0] else ''
                    if not key:
                        continue
                    if key == r[0]:
                        by_key[key] = r           # already canonical
                        continue
                    prev = by_key.get(key)
                    if prev is None or _row_note_alive(r) \
                            and not _row_note_alive(prev):
                        by_key[key] = r           # the better row wins
                    merged += 1 if prev is not None else 0
                    rekeyed += 1
                if any(_normalize_url(r[0]) != r[0] for r in rows if r[0]):
                    self.conn.execute("DELETE FROM websites_processed")
                    self.conn.executemany(
                        "INSERT OR REPLACE INTO websites_processed"
                        " (url, note_path, category, subcategory,"
                        " fetch_status, processed_at) VALUES (?,?,?,?,?,?)",
                        [(k,) + tuple(v)[1:] for k, v in by_key.items()])
                # ---- website_retry_queue: more attempts wins ----------
                rows = self.conn.execute(
                    "SELECT url, attempts, last_error, next_attempt_at,"
                    " first_failed_at, last_failed_at FROM"
                    " website_retry_queue ORDER BY url").fetchall()
                by_key = {}
                for r in rows:
                    key = _normalize_url(r[0]) if r[0] else ''
                    if not key:
                        continue
                    if key == r[0]:
                        by_key[key] = r
                        continue
                    prev = by_key.get(key)
                    if prev is None or (r[1] or 0) > (prev[1] or 0):
                        by_key[key] = r
                    merged += 1 if prev is not None else 0
                    rekeyed += 1
                if any(_normalize_url(r[0]) != r[0] for r in rows if r[0]):
                    self.conn.execute("DELETE FROM website_retry_queue")
                    self.conn.executemany(
                        "INSERT OR REPLACE INTO website_retry_queue"
                        " (url, attempts, last_error, next_attempt_at,"
                        " first_failed_at, last_failed_at)"
                        " VALUES (?,?,?,?,?,?)",
                        [(k,) + tuple(v)[1:] for k, v in by_key.items()])
                # ---- dismissed_urls + websites_settled: first wins -----
                for table in ('dismissed_urls', 'websites_settled'):
                    rows = self.conn.execute(
                        f"SELECT * FROM {table} ORDER BY url").fetchall()
                    changed = False
                    kept: Dict[str, tuple] = {}
                    for r in rows:
                        key = _normalize_url(r[0]) if r[0] else ''
                        if not key:
                            kept.setdefault(r[0], r)
                            continue
                        if key == r[0]:
                            kept.setdefault(key, r)
                            continue
                        changed = True
                        rekeyed += 1
                        if key in kept:
                            merged += 1
                        else:
                            kept[key] = (key,) + tuple(r[1:])
                    if changed:
                        self.conn.execute(f"DELETE FROM {table}")
                        self.conn.executemany(
                            f"INSERT OR REPLACE INTO {table}"
                            f" ({', '.join(_TABLE_COLS[table])})"
                            " VALUES ("
                            + ','.join('?' * len(_TABLE_COLS[table])) + ")",
                            [tuple(v) for v in kept.values()])
                self.conn.commit()
        except Exception:
            try:
                self.conn.rollback()
            except Exception:
                pass
        return {'rekeyed': rekeyed, 'merged': merged}

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

    def reset_retry_attempts(self, url: str) -> bool:
        """v0.51.0 — ONE link's retry counter reborn: attempts=0, due
        NOW. The master-table waiting pass calls this for a " - " row
        whose 3 automatic retries burned out — the owner's table row
        says the link is valid and the fetch failed, so the link gets a
        fresh set through the front door instead of being silently
        skipped as "no more retries" forever. Unlike ``rearm_retries``
        (v0.19.0, the WHOLE queue) this touches a single row — the
        other waiting links keep their backoff schedules. Returns True
        when a row was actually reset."""
        now_iso = datetime.now().isoformat(timespec='seconds')
        with self._lock:
            cur = self.conn.execute(
                "UPDATE website_retry_queue"
                " SET attempts=0, next_attempt_at=? WHERE url=?",
                (now_iso, _key(url)))
            self.conn.commit()
            return bool(cur.rowcount)

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
                "SELECT 1 FROM websites_processed WHERE url=?",
                (_key(url),)).fetchone()
        return row is not None

    def processed_row(self, url: str) -> Optional[Dict]:
        with self._lock:
            row = self.conn.execute(
                "SELECT url, note_path, category, subcategory, fetch_status,"
                " processed_at FROM websites_processed WHERE url=?",
                (_key(url),)).fetchone()
        if not row:
            return None
        return {'url': row[0], 'note_path': row[1], 'category': row[2],
                'subcategory': row[3], 'fetch_status': row[4],
                'processed_at': row[5]}

    def is_dismissed(self, url: str) -> bool:
        with self._lock:
            row = self.conn.execute(
                "SELECT 1 FROM dismissed_urls WHERE url=?",
                (_key(url),)).fetchone()
        return row is not None

    def dismissed_row(self, url: str) -> Optional[Dict]:
        """v0.46.0 — the dismissal's stored reason. The auto-verdicts
        the fetcher writes (category 'dead'/'paywalled'/'refused') name
        themselves in the reason so the skip line can tell a retired
        link from a deleted note."""
        with self._lock:
            row = self.conn.execute(
                "SELECT url, reason, dismissed_at FROM dismissed_urls"
                " WHERE url=?", (_key(url),)).fetchone()
        if not row:
            return None
        return {'url': row[0], 'reason': row[1], 'dismissed_at': row[2]}

    def retry_row(self, url: str) -> Optional[Dict]:
        with self._lock:
            row = self.conn.execute(
                "SELECT url, attempts, last_error, next_attempt_at,"
                " first_failed_at, last_failed_at FROM website_retry_queue"
                " WHERE url=?", (_key(url),)).fetchone()
        if not row:
            return None
        return {'url': row[0], 'attempts': row[1], 'last_error': row[2],
                'next_attempt_at': row[3], 'first_failed_at': row[4],
                'last_failed_at': row[5]}

    def all_retry_rows(self) -> List[Dict]:
        """v0.47.0 — EVERY waiting retry-queue row (not only the due
        ones): the master table's waiting section is the whole queue —
        attempts spent, last error, and the whole backoff story — so the
        owner sees the full picture, not just what a batch would touch
        today. Oldest first (the longest-suffering link leads)."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT url, attempts, last_error, next_attempt_at,"
                " first_failed_at, last_failed_at FROM"
                " website_retry_queue ORDER BY first_failed_at").fetchall()
        return [{'url': r[0], 'attempts': r[1], 'last_error': r[2],
                 'next_attempt_at': r[3], 'first_failed_at': r[4],
                 'last_failed_at': r[5]} for r in rows]

    def dismissed_rows(self, reason_prefix: str = '') -> List[Dict]:
        """v0.47.0 — the dismissals whose reason starts with
        ``reason_prefix`` (the pipeline asks for the 'auto-verdict:'
        class — links the fetcher's own ladder retired: dead /
        paywalled / refused). The master table lists them so the owner
        can see every retirement in one ledger and ♻️-revive any he
        disagrees with. Oldest first."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT url, reason, dismissed_at FROM dismissed_urls"
                " ORDER BY dismissed_at").fetchall()
        out: List[Dict] = []
        for url, reason, at in rows:
            if reason_prefix \
                    and not str(reason or '').startswith(reason_prefix):
                continue
            out.append({'url': url, 'reason': reason or '',
                        'dismissed_at': at})
        return out

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
