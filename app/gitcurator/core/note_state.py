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
from collections import Counter
from datetime import datetime
from typing import Callable, Dict, List, Optional

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


def scan_vault(vault_path: str, resolver: Optional[Callable] = None) -> List[Dict]:
    """Read-only walk of a vault. Returns one dict per .md note WITH a
    ``source:`` line, plus every unmanaged file (no source) flagged as such:

        {path, source_url, folder, category, subcategory, unmanaged}

    Category comes from the FOLDER, not the frontmatter line — a moved
    note's frontmatter may still carry the old category, and §4.4 says the
    folder is the correction signal.

    v0.12.0 — Phase 3: ``resolver`` maps a vault-relative folder to
    (category, subcategory). The default (None) keeps the GitHub-vault
    behavior (reverse CATEGORY_FOLDERS lookup). The websites vault passes
    a taxonomy-aware resolver — its folders are taxonomy NAMES
    (``Category/Subcategory``), not CATEGORY_FOLDERS keys, so the GitHub
    map would misread every nested website note.
    """
    resolver = resolver or folder_category
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
            category, subcategory = resolver(folder)
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
            # v0.12.0 — Phase 3 (SPEC §6): the corrections log and the
            # dismissed list live in the SAME cache.db.
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS corrections_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    vault TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    from_category TEXT,
                    to_category TEXT,
                    from_path TEXT,
                    to_path TEXT,
                    note TEXT
                )
            """)
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_corrections_vault "
                "ON corrections_log (vault)")
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS note_state_dismissed (
                    vault TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    last_path TEXT,
                    dismissed_at TEXT NOT NULL,
                    PRIMARY KEY (vault, source_url)
                )
            """)
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

    # -- corrections log (Phase 3) -----------------------------------------

    def log_correction(self, vault: str, source_url: str,
                       from_category: Optional[str], to_category: Optional[str],
                       from_path: str = '', to_path: str = '',
                       note: str = '') -> None:
        """Append one row to the corrections log (append-only history)."""
        with self._lock:
            self.conn.execute(
                "INSERT INTO corrections_log "
                "(ts, vault, source_url, from_category, to_category, "
                " from_path, to_path, note) VALUES (?,?,?,?,?,?,?,?)",
                (datetime.now().isoformat(timespec='seconds'), vault,
                 source_url, from_category, to_category, from_path, to_path,
                 note))
            self.conn.commit()

    def correction_count(self, vault: str) -> int:
        with self._lock:
            row = self.conn.execute(
                "SELECT COUNT(*) FROM corrections_log WHERE vault=?",
                (vault,)).fetchone()
        return int(row[0]) if row else 0

    def recent_corrections(self, vault: str, limit: int = 20) -> List[Dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT ts, source_url, from_category, to_category, "
                "from_path, to_path, note FROM corrections_log "
                "WHERE vault=? ORDER BY id DESC LIMIT ?",
                (vault, int(limit))).fetchall()
        return [{'ts': r[0], 'source_url': r[1], 'from_category': r[2],
                 'to_category': r[3], 'from_path': r[4], 'to_path': r[5],
                 'note': r[6]} for r in rows]

    def corrections_for(self, vault: str, source_url: str) -> List[Dict]:
        """Full correction history for ONE note, oldest first (the optional
        Phase 3 few-shot hook: the most similar past corrections become
        classifier prompt examples)."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT ts, from_category, to_category, note "
                "FROM corrections_log WHERE vault=? AND source_url=? "
                "ORDER BY id ASC", (vault, source_url)).fetchall()
        return [{'ts': r[0], 'from_category': r[1], 'to_category': r[2],
                 'note': r[3]} for r in rows]

    # -- dismissed list (Phase 3) ------------------------------------------

    def dismiss(self, vault: str, source_url: str, last_path: str = '') -> None:
        """Record a deleted note's URL — §4.4: never re-add it afterwards."""
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO note_state_dismissed "
                "(vault, source_url, last_path, dismissed_at) "
                "VALUES (?,?,?,?)",
                (vault, source_url, last_path,
                 datetime.now().isoformat(timespec='seconds')))
            self.conn.commit()

    def is_dismissed(self, vault: str, source_url: str) -> bool:
        with self._lock:
            row = self.conn.execute(
                "SELECT 1 FROM note_state_dismissed WHERE vault=? AND source_url=?",
                (vault, source_url)).fetchone()
        return row is not None

    def dismissed_count(self, vault: str) -> int:
        with self._lock:
            row = self.conn.execute(
                "SELECT COUNT(*) FROM note_state_dismissed WHERE vault=?",
                (vault,)).fetchone()
        return int(row[0]) if row else 0

    def dismissed_set(self, vault: str) -> set:
        """All dismissed source URLs for one vault (the processing loops
        check this BEFORE doing any work — a deleted note never returns)."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT source_url FROM note_state_dismissed WHERE vault=?",
                (vault,)).fetchall()
        return {r[0] for r in rows}

    def clear_dismissed(self, vault: str, source_url: str) -> bool:
        """Un-dismiss one URL (a note deleted by accident can come back).
        Returns True when a row was removed."""
        with self._lock:
            cur = self.conn.execute(
                "DELETE FROM note_state_dismissed WHERE vault=? AND source_url=?",
                (vault, source_url))
            self.conn.commit()
        return cur.rowcount > 0

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

    def row_for(self, vault: str, source_url: str) -> Optional[Dict]:
        """The row for ONE note (identity = vault + source URL), or None.
        v0.11.0 — Phase 2: the websites pipeline uses this to prove an old
        _review placeholder is still app-owned (fingerprint unchanged)
        before replacing or removing it."""
        with self._lock:
            row = self.conn.execute(
                "SELECT source_url, path, fingerprint, category, subcategory,"
                " locked, first_seen, updated_at FROM note_state"
                " WHERE vault=? AND source_url=?", (vault, source_url)
            ).fetchone()
        if not row:
            return None
        return {'source_url': row[0], 'path': row[1], 'fingerprint': row[2],
                'category': row[3], 'subcategory': row[4],
                'locked': bool(row[5]), 'first_seen': row[6],
                'updated_at': row[7]}

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
                     db_path: str = "cache.db",
                     taxonomy=None) -> Dict[str, List[Dict]]:
    """Pure DETECTION (Phase 3 acts on the result via apply_corrections).

    Compares a vault on disk with the stored state and sorts every
    difference into the §4.4 table:

        moved       same source, different path (folder correction)
        edited      same path, content fingerprint changed (human edit)
        deleted     in state, file gone (-> dismissed list, Phase 3)
        duplicates  two files on disk share one source
        unmanaged   file without a ``source:`` line
        unmapped    note sits in a folder no category maps to
        unknown     file with a source the app never recorded (hand-pasted)

    v0.12.0 — Phase 3: ``taxonomy`` (the websites vault) switches the
    folder -> (category, subcategory) resolver from the GitHub
    CATEGORY_FOLDERS map to the taxonomy's own names, and 'unmapped'
    then means "folder the taxonomy does not define".
    """
    if taxonomy is not None:
        resolver = _taxonomy_resolver(taxonomy)
    else:
        resolver = folder_category

    db = NoteStateDB(db_path)
    try:
        state = db.all_rows(vault)
    finally:
        db.close()

    disk = scan_vault(vault_path, resolver=resolver)

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
        if note['folder'] in special:
            continue
        if taxonomy is not None:
            cat, sub = resolver(note['folder'])
            valid = (taxonomy.is_category(cat or '')
                     and (not sub or taxonomy.is_subcategory_of(cat, sub)))
            if not valid:
                unmapped.append({'source_url': source, 'path': note['path'],
                                 'folder': note['folder']})
        elif (note['folder'] not in FOLDER_TO_CATEGORY
                and note['category'] == note['folder']):
            unmapped.append({'source_url': source, 'path': note['path'],
                             'folder': note['folder']})

    return {
        'moved': moved, 'edited': edited, 'deleted': deleted,
        'duplicates': duplicates, 'unmanaged': unmanaged,
        'unmapped': unmapped, 'unknown': unknown,
    }


# ---------------------------------------------------------------------------
# Phase 3 — acting on the §4.4 table (v0.12.0)
# ---------------------------------------------------------------------------

def _taxonomy_resolver(taxonomy):
    """Websites-vault folder resolver: folders are taxonomy NAMES
    (``Category`` or ``Category/Subcategory``). Special folders behave
    exactly like the GitHub resolver (root / _review -> (None, None))."""
    def resolver(folder):
        folder = (folder or '').strip('/').replace('\\', '/')
        if not folder or folder == '_review':
            return None, None
        parts = [p for p in folder.split('/') if p]
        if not parts:
            return None, None
        category = parts[0]
        subcategory = '/'.join(parts[1:]) if len(parts) > 1 else None
        return category, subcategory
    return resolver


# -- front-matter surgery (targeted line edits, SPEC §4.4) ------------------

def _fm_bounds(lines):
    """(start, end) of the leading front-matter block, or None."""
    if not lines or lines[0].strip() != '---':
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == '---':
            return 1, i
    return None      # unterminated front-matter — never touch such a file


def fm_replace_field(content: str, field: str, new_line: str):
    """Replace a ``field:`` line inside the front-matter (or insert the line
    just before the closing ``---``). Returns (content, changed). A file
    without a well-formed front-matter block is returned UNCHANGED."""
    lines = content.split('\n')
    bounds = _fm_bounds(lines)
    if bounds is None:
        return content, False
    start, end = bounds
    pat = re.compile(r'^' + re.escape(field) + r'(\s*:)')
    for i in range(start, end):
        if pat.match(lines[i]):
            if lines[i] == new_line:
                return content, False
            lines[i] = new_line
            return '\n'.join(lines), True
    lines.insert(end, new_line)
    return '\n'.join(lines), True


def fm_swap_tag(content: str, remove=None, add=None):
    """Swap one token inside the ``tags: [ ... ]`` front-matter line.
    Returns (content, changed). Keeps every other tag exactly as-is."""
    remove = (remove or '').strip()
    add = (add or '').strip()
    if not remove and not add:
        return content, False
    lines = content.split('\n')
    bounds = _fm_bounds(lines)
    if bounds is None:
        return content, False
    start, end = bounds
    pat = re.compile(r'^tags:\s*\[(.*)\]\s*$')
    for i in range(start, end):
        m = pat.match(lines[i])
        if m:
            items = [t.strip().strip('"\'') for t in m.group(1).split(',')
                     if t.strip()]
            if remove:
                items = [t for t in items if t != remove]
            if add and add not in items:
                items.insert(0, add)
            new_line = ('tags: [' + ', '.join(items) + ']') if items \
                else 'tags: []'
            if new_line == lines[i]:
                return content, False
            lines[i] = new_line
            return '\n'.join(lines), True
    return content, False


def move_summary_lines(corrections: List[Dict]) -> List[str]:
    """The optional Phase 3 report line: repeated moves grouped, e.g.
    ``"9 notes moved from X to Y"`` (SPEC §6 Phase 3, optional item)."""
    counts = Counter(
        ((c.get('from_category') or '?'), (c.get('to_category') or '?'))
        for c in (corrections or []))
    lines = []
    for (frm, to), n in counts.most_common():
        plural = 'notes' if n != 1 else 'note'
        lines.append(f"{n} {plural} moved from {frm} to {to}")
    return lines


def apply_corrections(vault: str, vault_path: str, changes: Dict[str, List],
                      db: "NoteStateDB", log=None, taxonomy=None) -> Dict:
    """ACT on classify_changes() results per the §4.4 table.

    Writes (all atomic / gated by core.dryrun):
      - moved  : the note's ``category:`` (+ ``subcategory:`` on websites)
                 and ``tags:`` lines updated to match the new folder, a
                 ``category_locked: true`` line added, the note_state row
                 refreshed (new path + locked), one corrections_log row.
                 Moving OUT of ``_review`` counts as a correction the same
                 way — same code path, nothing special-cased.
      - deleted: the URL goes on the dismissed list (never re-added).
      Everything else (edited / duplicates / unmanaged / unmapped / unknown)
      is REPORT-ONLY — the app never touches those files.

    Returns a summary dict for the run report.
    """
    from gitcurator.core.note_builder import yaml_scalar
    from gitcurator.core import storage as _storage
    from gitcurator.core import dryrun as _dryrun

    summary = {'moved_applied': 0, 'moved_failed': 0, 'dismissed': 0,
               'corrections': [], 'errors': []}
    log = log or (lambda *a, **k: None)

    # A dry-run never records bookkeeping (the Phase 1 contract: a rehearsal
    # records nothing) — detection results are logged by the CALLER instead.
    if _dryrun.is_enabled():
        summary['dry_run'] = True
        return summary

    # ---- moved -> correction --------------------------------------------
    # A move into a folder no taxonomy/category maps to is NOT a correction
    # (SPEC §4.4 "unmapped": keep it there, report it, never invent a
    # category) — those source URLs are skipped here.
    unmapped_sources = {u.get('source_url')
                        for u in (changes.get('unmapped') or [])}
    summary['moved_unmapped'] = 0

    for mv in (changes.get('moved') or []):
        source = mv.get('source_url', '')
        if source in unmapped_sources:
            summary['moved_unmapped'] += 1
            log(f"📍 Note moved into an unmapped folder — kept as-is, "
                f"reported ({source})", "warning")
            continue
        to_path = mv.get('to_path', '')
        from_cat = mv.get('from_category')
        folder = mv.get('to_folder') or ''
        if taxonomy is not None:
            parts = [p for p in folder.strip('/').replace('\\', '/')
                     .split('/') if p]
            new_cat = parts[0] if parts else ''
            new_sub = '/'.join(parts[1:]) if len(parts) > 1 else ''
        else:
            new_cat, new_sub = folder_category(folder)
            new_cat = new_cat or ''
            new_sub = new_sub or ''
        try:
            with open(to_path, 'r', encoding='utf-8',
                      errors='replace') as f:
                content = f.read()
        except OSError as exc:
            summary['moved_failed'] += 1
            summary['errors'].append(f"{to_path}: {exc}")
            continue

        changed = False
        if new_cat:
            content, c = fm_replace_field(
                content, 'category', f"category: {yaml_scalar(new_cat)}")
            changed = changed or c
            content, c = fm_swap_tag(content, remove=from_cat, add=new_cat)
            changed = changed or c
        if vault == VAULT_WEBSITES or new_sub:
            # Website notes always carry the line (build_website_note);
            # GitHub notes only when a subcategory exists (nested folder).
            content, c = fm_replace_field(
                content, 'subcategory',
                f"subcategory: {yaml_scalar(new_sub) if new_sub else '\"\"'}")
            changed = changed or c
        content, c = fm_replace_field(content, 'category_locked',
                                      'category_locked: true')
        changed = changed or c

        try:
            if changed:
                # atomic_write_text is gated by core.dryrun (Phase 0): in a
                # dry-run the edit is recorded, not written.
                _storage.atomic_write_text(to_path, content)
            db.upsert(vault, source, to_path,
                      compute_fingerprint(content),
                      new_cat or None, new_sub or None, locked=True)
            db.log_correction(vault, source, from_cat, new_cat or '',
                              mv.get('from_path', ''), to_path)
        except OSError as exc:
            summary['moved_failed'] += 1
            summary['errors'].append(f"{to_path}: {exc}")
            continue

        summary['moved_applied'] += 1
        summary['corrections'].append({
            'source_url': source, 'from_category': from_cat,
            'to_category': new_cat, 'to_path': to_path})
        log(f"📌 Correction: note moved to {new_cat}"
            + (f" / {new_sub}" if new_sub else "")
            + f" — front-matter updated, category locked ({source})",
            "success")

    # ---- deleted -> dismissed -------------------------------------------
    for dl in (changes.get('deleted') or []):
        source = dl.get('source_url', '')
        if source and not db.is_dismissed(vault, source):
            db.dismiss(vault, source, dl.get('last_path', ''))
            summary['dismissed'] += 1
            log(f"🗑️ Note deleted by you — URL dismissed, will not be "
                f"re-added ({source})", "info")

    return summary


def run_start_check(vault: str, vault_path: str,
                    db: Optional["NoteStateDB"] = None, log=None,
                    taxonomy=None, db_path: str = "cache.db") -> Optional[Dict]:
    """The start-of-run §4.4 pass for ONE vault (Phase 3): silent baseline
    when the vault has no rows yet, then detect + apply.

    Returns {'baseline': N|None, 'changes': {...}, 'applied': {...}}, or
    None when the vault path is missing. ``taxonomy`` only for the
    websites vault (None = GitHub CATEGORY_FOLDERS semantics).

    ``db`` is the caller's open connection (real runs); ``db_path`` is the
    fallback used when ``db`` is None (dry-runs open no connection, per
    the Phase 1 contract — a rehearsal records nothing, it only detects
    and logs)."""
    from gitcurator.core import dryrun as _dryrun

    if not vault_path or not os.path.isdir(vault_path):
        return None
    log = log or (lambda *a, **k: None)
    path = db.db_path if db is not None else db_path

    if _dryrun.is_enabled():
        changes = classify_changes(vault, vault_path, db_path=path,
                                   taxonomy=taxonomy)
        counts = {k: len(v) for k, v in changes.items()
                  if isinstance(v, list)}
        flagged = {k: n for k, n in counts.items() if n}
        if flagged:
            log(f"🔍 [dry-run] note state ({vault} vault): "
                + ", ".join(f"{k}={n}" for k, n in flagged.items())
                + " — nothing applied, nothing recorded", "info")
        return {'baseline': None, 'changes': changes,
                'applied': {'dry_run': True}}

    baseline = record_baseline_if_empty(vault, vault_path, db_path=path,
                                        log=log)
    changes = classify_changes(vault, vault_path, db_path=path,
                               taxonomy=taxonomy)
    if db is None:
        return {'baseline': baseline, 'changes': changes,
                'applied': {'skipped': 'no note-state connection'}}
    applied = apply_corrections(vault, vault_path, changes, db,
                                log=log, taxonomy=taxonomy)
    return {'baseline': baseline, 'changes': changes, 'applied': applied}
