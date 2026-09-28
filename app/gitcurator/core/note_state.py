#!/usr/bin/env python3
"""
note_state.py — persistent per-note state in cache.db (Phase 1, v0.10.0).

SPEC §4.4 ("Moves are corrections"): the app keeps a persistent record per
machine-vault note so it can tell the owner's *folder moves* (corrections,
to be honored) apart from *content edits* (to be flagged and left alone)
and *deletions* (to be dismissed, never re-added). The record lives in the
SAME cache.db the app already uses — same resolved path as ``CacheDB``
(the working-directory bug fixed in v0.09.4 must not come back).

What Phase 1 ships: the ``note_state`` table, the helpers below, and a
silent ONE-TIME baseline recording hooked at the start of every real
(non-dry-run) batch. Nothing ACTS on the state yet — Phase 3 turns on the
start-of-run comparison (moves accepted, edits flagged, deletes
dismissed). ``classify_changes()`` is the pure detector Phase 3 will use;
it is fully built and tested NOW so Phase 3 is mostly wiring.

Schema (one row per note the app has seen):

    vault         'github' | 'websites'      (label, not path — paths move)
    source_url    the note's normalized ``source:`` URL (its identity)
    path          the note path as last seen (absolute)
    fingerprint   sha256 of the content at last sight, EXCLUDING the
                  app-managed delimited block below (so a Phase 6 recall
                  block added by the app never reads as a human edit)
    category      category key derived from the note's folder
    subcategory   websites only (Phase 2); always NULL for GitHub notes
    locked        1 after the owner moved the note (Phase 3 sets it)

Fingerprint contract: the app may later append a delimited block between
``RECALL_START`` / ``RECALL_END`` markers (Phase 6). ``compute_fingerprint``
strips that block before hashing, so app-added recall blocks are invisible
to the changed-by-hand detector. The markers are defined HERE so Phase 6
does not get to invent incompatible ones.

Pure stdlib, no PyQt — importable from the CLI, tests and CI.
"""

import hashlib
import os
import re
import sqlite3
import threading
from datetime import datetime
from typing import Dict, List, Optional

from gitcurator.constants import APP_DIR, CATEGORY_FOLDERS
from gitcurator.core.links import normalize_url

# ---------------------------------------------------------------------------
# Configuration (safe to edit)
# ---------------------------------------------------------------------------

# Delimited app-managed block (Phase 6 writes it; the fingerprint ignores it).
RECALL_START = "<!-- gitcurator:recall:start -->"
RECALL_END = "<!-- gitcurator:recall:end -->"

# The same special folders VaultIndex skips (SPEC §4.4: _review is NOT skipped).
SKIPPED_FOLDER_MARKS = ('_moc', '_inbox', 'attachments', '.obsidian')

# Reverse lookup: vault folder -> category key (SPEC §4.4 "rules of the road").
FOLDER_TO_CATEGORY = {folder: key for key, folder in CATEGORY_FOLDERS.items()}

_RECALL_BLOCK = re.compile(
    re.escape(RECALL_START) + r".*?" + re.escape(RECALL_END), re.DOTALL)

_SOURCE_RE = re.compile(r'^source:\s*(.+)$', re.MULTILINE)

VAULT_GITHUB = 'github'
VAULT_WEBSITES = 'websites'


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------

def compute_fingerprint(content: str) -> str:
    """Stable content fingerprint (sha256 hex).

    The app-managed delimited block (if present) is stripped first, so the
    app appending a recall block later never registers as a human edit.
    Trailing whitespace is normalized away too — Phase 6 will append its
    block with surrounding newlines, and whitespace-only tails are not
    meaningful content.
    """
    stripped = _RECALL_BLOCK.sub('', content or '').rstrip()
    return hashlib.sha256(stripped.encode('utf-8', 'replace')).hexdigest()


# ---------------------------------------------------------------------------
# Vault walking (same rules as VaultIndex)
# ---------------------------------------------------------------------------

def _skip_dir(root: str) -> bool:
    return any(mark in root for mark in SKIPPED_FOLDER_MARKS)


def scan_vault(vault_path: str) -> List[Dict]:
    """Read-only walk of a vault. Returns one dict per .md note WITH a
    ``source:`` line, plus every unmanaged file (no source) flagged as such:

        {path, source_url, folder, category, subcategory, unmanaged}

    Category comes from the FOLDER (reverse CATEGORY_FOLDERS lookup), not
    the frontmatter line — a moved note's frontmatter may still carry the
    old category, and §4.4 says the folder is the correction signal.
    """
    notes: List[Dict] = []
    if not vault_path or not os.path.isdir(vault_path):
        return notes
    for root, _dirs, files in os.walk(vault_path):
        if _skip_dir(root):
            continue
        for fname in files:
            if not fname.endswith('.md'):
                continue
            fpath = os.path.join(root, fname)
            rel = os.path.relpath(fpath, vault_path).replace('\\', '/')
            folder = os.path.dirname(rel)
            category, subcategory = folder_category(folder)
            try:
                with open(fpath, 'r', encoding='utf-8', errors='replace') as f:
                    head = f.read(800)
            except OSError:
                continue
            match = _SOURCE_RE.search(head)
            source_url = ''
            if match:
                source_url = normalize_url(match.group(1).strip().strip('"\''))
            notes.append({
                'path': fpath,
                'source_url': source_url,
                'folder': folder,
                'category': category,
                'subcategory': subcategory,
                'unmanaged': not bool(source_url),
            })
    return notes


def folder_category(folder: str):
    """Map a vault-relative folder to (category_key, subcategory) using the
    SAME rules for both vaults: exact CATEGORY_FOLDERS reverse hit wins;
    ``_review`` and the vault root are (None, None); anything else keeps the
    folder name raw with subcategory None — classify_changes reports those
    as 'unmapped'."""
    folder = (folder or '').strip('/').replace('\\', '/')
    if not folder or folder == '_review':
        return None, None
    if folder in FOLDER_TO_CATEGORY:
        return FOLDER_TO_CATEGORY[folder], None
    # A category folder with something nested under it (websites, Phase 2):
    # match the longest registered prefix and treat the rest as subcategory.
    parts = folder.split('/')
    for depth in range(len(parts) - 1, 0, -1):
        prefix = '/'.join(parts[:depth])
        if prefix in FOLDER_TO_CATEGORY:
            return FOLDER_TO_CATEGORY[prefix], '/'.join(parts[depth:])
    return folder, None


# ---------------------------------------------------------------------------
# The database
# ---------------------------------------------------------------------------

class NoteStateDB:
    """SQLite-backed note state. Same file as CacheDB (cache.db), own table,
    own connection + lock — CacheDB is not touched (it lives in the GUI
    module and must stay PyQt-importable-only)."""

    def __init__(self, db_path: str = "cache.db"):
        # Same resolution rule as CacheDB.__init__ (v0.09.4 fix): the plain
        # default is anchored to APP_DIR, never the current working dir.
        if db_path == "cache.db":
            db_path = os.path.join(APP_DIR, "cache.db")
        self.db_path = db_path
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
        self.conn.execute("PRAGMA busy_timeout = 30000")
        with self._lock:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS note_state (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    vault TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    path TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    category TEXT,
                    subcategory TEXT,
                    locked INTEGER NOT NULL DEFAULT 0,
                    first_seen TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(vault, source_url)
                )
            """)
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_note_state_vault "
                "ON note_state (vault)")
            self.conn.commit()

    # -- writes -----------------------------------------------------------

    def upsert(self, vault: str, source_url: str, path: str, fingerprint: str,
               category: Optional[str] = None, subcategory: Optional[str] = None,
               locked: bool = False) -> None:
        """Insert or refresh one note's row (identity = vault + source_url)."""
        now = datetime.now().isoformat(timespec='seconds')
        with self._lock:
            self.conn.execute(
                "UPDATE note_state SET path=?, fingerprint=?, category=?, "
                "subcategory=?, locked=?, updated_at=? "
                "WHERE vault=? AND source_url=?",
                (path, fingerprint, category, subcategory, int(bool(locked)),
                 now, vault, source_url))
            self.conn.execute(
                "INSERT OR IGNORE INTO note_state "
                "(vault, source_url, path, fingerprint, category, subcategory,"
                " locked, first_seen, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (vault, source_url, path, fingerprint, category, subcategory,
                 int(bool(locked)), now, now))
            self.conn.commit()

    def record_note(self, vault: str, source_url: str, path: str,
                    content: Optional[str] = None,
                    category: Optional[str] = None,
                    subcategory: Optional[str] = None,
                    normalizer=None) -> None:
        """Record one note the app just wrote (identity = vault + source).
        ``content`` is hashed when given; otherwise the file is read.

        v0.11.0 — Phase 2: ``normalizer`` overrides the URL keying (the
        websites pipeline passes ``links.normalize_website_url`` so a URL
        with meaningful query parameters keeps its identity; GitHub keeps
        the default ``normalize_url``)."""
        if content is None:
            try:
                with open(path, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()
            except OSError:
                content = ''
        key_fn = normalizer or normalize_url
        self.upsert(vault, key_fn(source_url) or source_url, path,
                    compute_fingerprint(content), category, subcategory)

    def set_locked(self, vault: str, source_url: str, locked: bool = True) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE note_state SET locked=?, updated_at=? "
                "WHERE vault=? AND source_url=?",
                (int(bool(locked)),
                 datetime.now().isoformat(timespec='seconds'), vault, source_url))
            self.conn.commit()

    # -- reads ------------------------------------------------------------

    def count(self, vault: str) -> int:
        with self._lock:
            row = self.conn.execute(
                "SELECT COUNT(*) FROM note_state WHERE vault=?", (vault,)).fetchone()
        return int(row[0]) if row else 0

    def all_rows(self, vault: str) -> Dict[str, Dict]:
        """source_url -> row dict for one vault."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT source_url, path, fingerprint, category, subcategory,"
                " locked, first_seen, updated_at FROM note_state WHERE vault=?",
                (vault,)).fetchall()
        return {
            r[0]: {'source_url': r[0], 'path': r[1], 'fingerprint': r[2],
                   'category': r[3], 'subcategory': r[4], 'locked': bool(r[5]),
                   'first_seen': r[6], 'updated_at': r[7]}
            for r in rows
        }

    def close(self) -> None:
        with self._lock:
            try:
                self.conn.close()
            except sqlite3.Error:
                pass


# ---------------------------------------------------------------------------
# Baseline + change detection (pure functions over NoteStateDB)
# ---------------------------------------------------------------------------

def record_baseline_if_empty(vault: str, vault_path: str,
                             db_path: str = "cache.db",
                             log=None) -> Optional[int]:
    """ONE-TIME silent baseline for a machine vault (SPEC §4.4: "the first
    run after this feature ships records a baseline silently").

    Records every note found by scan_vault() ONLY when the vault has no
    rows yet. Returns the number recorded, or None when a baseline already
    existed (so callers can distinguish "recorded N" from "kept as-is").
    Notes WITHOUT a source: line are not recorded (they are 'unmanaged'
    per §4.4 — ignored and listed, never adopted).
    """
    db = NoteStateDB(db_path)
    try:
        if db.count(vault) > 0:
            return None
        notes = [n for n in scan_vault(vault_path) if not n['unmanaged']]
        for n in notes:
            try:
                with open(n['path'], 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()
            except OSError:
                content = ''
            db.upsert(vault, n['source_url'], n['path'],
                      compute_fingerprint(content), n['category'],
                      n['subcategory'])
        if log and notes:
            log(f"🗂️ Note-state baseline recorded: {len(notes)} notes "
                f"({vault} vault)", "info")
        return len(notes)
    finally:
        db.close()


def classify_changes(vault: str, vault_path: str,
                     db_path: str = "cache.db") -> Dict[str, List[Dict]]:
    """Pure DETECTION (Phase 3 acts on the result; Phase 1 only tests it).

    Compares a vault on disk with the stored state and sorts every
    difference into the §4.4 table:

        moved       same source, different path (folder correction)
        edited      same path, content fingerprint changed (human edit)
        deleted     in state, file gone (-> dismissed list, Phase 3)
        duplicates  two files on disk share one source
        unmanaged   file without a ``source:`` line
        unmapped    note sits in a folder no category maps to
        unknown     file with a source the app never recorded (hand-pasted)
    """
    db = NoteStateDB(db_path)
    try:
        state = db.all_rows(vault)
    finally:
        db.close()

    disk = scan_vault(vault_path)

    by_source: Dict[str, List[Dict]] = {}
    for n in disk:
        if not n['unmanaged']:
            by_source.setdefault(n['source_url'], []).append(n)

    moved, edited, duplicates, unmapped, unknown = [], [], [], [], []
    seen_sources = set()

    for source, files in by_source.items():
        seen_sources.add(source)
        if len(files) > 1:
            duplicates.append({'source_url': source,
                               'paths': [f['path'] for f in files]})
            continue                      # §4.4: flag both, touch neither
        note = files[0]
        row = state.get(source)
        if row is None:
            unknown.append({'source_url': source, 'path': note['path']})
        elif os.path.normcase(note['path']) != os.path.normcase(row['path']):
            moved.append({
                'source_url': source,
                'from_path': row['path'], 'to_path': note['path'],
                'from_category': row['category'],
                'to_category': note['category'],
                'to_folder': note['folder'],
            })
        else:
            try:
                with open(note['path'], 'r', encoding='utf-8',
                          errors='replace') as f:
                    current = compute_fingerprint(f.read())
            except OSError:
                current = ''
            if current != row['fingerprint']:
                edited.append({'source_url': source, 'path': note['path']})

    deleted = [{'source_url': s, 'last_path': row['path']}
               for s, row in state.items() if s not in seen_sources]

    unmanaged = [{'path': n['path']} for n in disk if n['unmanaged']]

    # unmapped: a KNOWN note whose current folder matches no category and is
    # not a special one (root/_review). Duplicates/unmanaged already listed
    # are not double-reported.
    special = {'', '_review'}
    for source, files in by_source.items():
        if len(files) > 1:
            continue
        note = files[0]
        if (note['folder'] not in special
                and note['folder'] not in FOLDER_TO_CATEGORY
                and note['category'] == note['folder']):
            unmapped.append({'source_url': source, 'path': note['path'],
                             'folder': note['folder']})

    return {
        'moved': moved, 'edited': edited, 'deleted': deleted,
        'duplicates': duplicates, 'unmanaged': unmanaged,
        'unmapped': unmapped, 'unknown': unknown,
    }
