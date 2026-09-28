#!/usr/bin/env python3
"""
dryrun.py — the Phase 0 dry-run mechanism (v0.09.5).

GitCurator writes into the owner's Obsidian vaults. Before any risky run,
the owner must be able to ask: "what WOULD this run change?" — and get an
exact answer without a single byte being touched (SPEC.md non-negotiable
#1: a real-vault run happens only after a dry-run, a snapshot, and the
owner's explicit OK).

This module is that switch. It has two halves:

1. A global on/off flag (``enable()`` / ``disable()`` / ``is_enabled()``)
   plus a LOG of everything that was withheld. While the flag is ON:

     - ``core/storage.py`` refuses to perform ``atomic_write_text`` /
       ``atomic_write_bytes`` (notes, banners, config.json) and records
       them here instead — so every canonical vault write is covered
       automatically, today and in every future phase.
     - The small helpers below (``makedirs``, ``write_text``,
       ``append_text``, ``remove``, ``move``) behave identically to the
       raw filesystem calls they replace, EXCEPT that they record instead
       of touching the disk. They exist so the handful of legacy raw
       writes in ``gui/app.py`` (master index, MOCs, processing reports,
       inbox tables, undo list, banner markers) can be dry-run-aware with
       a one-line change each.
     - ``shadow_cache_path()`` hands the batch a throwaway COPY of
       cache.db: the dry-run still SEES what was already processed (so
       its "would process X of Y" numbers are real) but cannot record
       anything (no processed-repo marks, no 404 strikes, no retry-queue
       changes) that would make the next REAL run skip links.

2. While the flag is OFF (the default, and the only state the GUI ever
   sees), every helper performs exactly the plain filesystem operation
   it stands for. Default behavior is byte-for-byte unchanged.

Pure stdlib — no PyQt, no third-party deps, importable from the GUI app,
the CLI, the tools, and the telegram worker subprocess alike.

Thread model: one batch runs at a time (the GUI's Telegram lock and the
CLI's single worker guarantee it), so a module-level flag is safe. The
log itself is guarded by a lock because the banner thread writes markers
concurrently with the main worker loop.
"""

import os
import shutil
import tempfile
import threading
from datetime import datetime

# ===========================================================================
# CONFIGURATION (safe to edit)
# ===========================================================================
# How many characters of withheld content to keep in the log preview.
# Long enough to recognize a note, short enough to keep the report readable.
PREVIEW_CHARS = 160

# ===========================================================================
# State
# ===========================================================================

_lock = threading.Lock()
_enabled = False
_entries = []          # list of dicts: op / path / size / preview / time
_shadow_dir = None     # temp dir holding the throwaway cache copy
_shadow_cache = None   # resolved path of the shadow cache (copied once)


# ===========================================================================
# Switch
# ===========================================================================

def enable():
    """Turn dry-run ON. Starts a fresh log (previous entries are dropped)."""
    global _enabled, _shadow_dir, _shadow_cache
    with _lock:
        _enabled = True
        _entries.clear()
        _shadow_cache = None


def disable():
    """Turn dry-run OFF. Keeps the log so the caller can still print it."""
    global _enabled, _shadow_dir, _shadow_cache
    with _lock:
        _enabled = False
        _shadow_dir = None
        _shadow_cache = None
    _cleanup_shadow_dir()


def is_enabled() -> bool:
    """True while a dry-run batch is active."""
    return _enabled


# ===========================================================================
# Log
# ===========================================================================

def record(op: str, path, content=None, note: str = ""):
    """Record one withheld operation. Called by every helper while enabled.

    ``content`` may be str or bytes; only a short preview is kept.
    Never raises — a logging failure must not break a batch.
    """
    try:
        size = None
        preview = ""
        if isinstance(content, bytes):
            size = len(content)
            preview = content[:PREVIEW_CHARS].decode('utf-8', errors='replace')
        elif isinstance(content, str):
            size = len(content.encode('utf-8'))
            preview = content[:PREVIEW_CHARS]
        preview = preview.replace('\r', '').replace('\n', '⏎')
        if len(preview) >= PREVIEW_CHARS:
            preview = preview[:PREVIEW_CHARS] + '…'
        entry = {
            'time': datetime.now().strftime('%H:%M:%S'),
            'op': str(op),
            'path': str(path),
            'size': size,
            'preview': preview,
            'note': note or '',
        }
        with _lock:
            _entries.append(entry)
    except Exception:
        pass


def entries():
    """A copy of the withheld-operation log (list of dicts)."""
    with _lock:
        return list(_entries)


def entry_count() -> int:
    with _lock:
        return len(_entries)


def clear():
    """Drop the log (used by tests)."""
    with _lock:
        _entries.clear()


def summary() -> dict:
    """Counts per operation type, e.g. {'write': 12, 'makedirs': 5}."""
    with _lock:
        out = {}
        for e in _entries:
            out[e['op']] = out.get(e['op'], 0) + 1
        return out


# ===========================================================================
# Filesystem helpers — identical to the raw calls they replace when OFF,
# recorded (not performed) when ON.
# ===========================================================================

def makedirs(path, exist_ok=False):
    """``os.makedirs`` stand-in. Dry-run: records, does not create."""
    if is_enabled():
        record('makedirs', path, note='create folder' if not os.path.exists(path) else 'folder exists')
        return
    os.makedirs(path, exist_ok=exist_ok)


def write_text(path, content, encoding='utf-8'):
    """Plain ``open(path, 'w')`` stand-in. Dry-run: records, does not write."""
    if is_enabled():
        record('write', path, content=content)
        return
    with open(path, 'w', encoding=encoding) as f:
        f.write(content)


def append_text(path, text, encoding='utf-8'):
    """Plain ``open(path, 'a')`` stand-in. Dry-run: records, does not append."""
    if is_enabled():
        record('append', path, content=text)
        return
    with open(path, 'a', encoding=encoding) as f:
        f.write(text)


def remove(path):
    """``os.remove`` stand-in. Dry-run: records, does not delete.

    May raise OSError when disabled — exactly like os.remove — so the
    caller's existing try/except keeps working unchanged.
    """
    if is_enabled():
        record('remove', path, note='delete file')
        return
    os.remove(path)


def move(src, dst):
    """``shutil.move`` stand-in. Dry-run: records, does not move."""
    if is_enabled():
        record('move', dst, note=f'from {src}')
        return
    shutil.move(src, dst)


# ===========================================================================
# Shadow cache — read the real cache.db, write to a throwaway copy
# ===========================================================================

def shadow_cache_path(source_db: str) -> str:
    """Path of a throwaway COPY of ``source_db`` for a dry-run batch.

    The copy is made once per enable() session. The batch reads real
    "already processed" state from the copy but every write (processed
    marks, 404 strikes, retry queue) lands in the copy and is discarded
    when disable() cleans the temp dir — so a dry run can never make the
    next REAL run skip links.

    A missing ``source_db`` (fresh install — no cache yet) is normal: the
    shadow is simply a fresh empty database. If a real cache EXISTS but
    cannot be copied, a warning is recorded and the dry-run proceeds with
    an empty shadow (its "would process" numbers are then pessimistic —
    misleading but safe; the real cache is never written and never lost).
    """
    global _shadow_dir, _shadow_cache
    with _lock:
        if _shadow_cache is not None:
            return _shadow_cache
        if _shadow_dir is None:
            _shadow_dir = tempfile.mkdtemp(prefix='gitcurator-dryrun-')
        shadow = os.path.join(_shadow_dir, 'cache.db')
        _shadow_cache = shadow
    # The copy happens OUTSIDE the lock — record() takes the same lock,
    # and copying while holding it would deadlock on failure paths
    # (caught live during the Phase 0 end-to-end demo, fresh-install case).
    if os.path.exists(source_db):
        try:
            shutil.copy2(source_db, shadow)
            # A live SQLite database may keep recent commits in its WAL
            # sidecar files — copy them too so the copy is consistent.
            for suffix in ('-wal', '-shm'):
                side = source_db + suffix
                if os.path.exists(side):
                    shutil.copy2(side, shadow + suffix)
        except Exception as exc:
            record('warning', shadow,
                   note=f'could not copy the real cache ({exc}); '
                        'the dry-run sees an empty cache')
    else:
        record('info', shadow,
               note='no real cache.db yet — the dry-run starts from an '
                    'empty shadow cache')
    return shadow


def _cleanup_shadow_dir():
    """Best-effort removal of the shadow temp dir (called by disable())."""
    global _shadow_dir
    d = _shadow_dir
    if d and os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)


# ===========================================================================
# Reporting
# ===========================================================================

def render_report_markdown(title: str = "Dry-run report") -> str:
    """Render the withheld-operation log as a readable Markdown document."""
    es = entries()
    counts = summary()
    lines = []
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")
    lines.append("")
    lines.append("**Nothing below was written.** Every operation was recorded "
                 "instead of performed.")
    lines.append("")
    if not es:
        lines.append("No vault writes were attempted during this dry-run.")
        lines.append("")
        return '\n'.join(lines)
    lines.append(f"**{len(es)} operation(s) would have been performed:**")
    lines.append("")
    for op, n in sorted(counts.items()):
        lines.append(f"- {op}: {n}")
    lines.append("")
    lines.append("| # | Time | Op | Target | Size | Preview / note |")
    lines.append("|---|------|----|--------|------|----------------|")
    for i, e in enumerate(es, 1):
        size = f"{e['size']:,} B" if e['size'] is not None else ""
        extra = e['preview'] or e['note']
        if e['preview'] and e['note']:
            extra = f"{e['preview']} ({e['note']})"
        extra = extra.replace('|', '\\|')
        lines.append(f"| {i} | {e['time']} | {e['op']} | `{e['path']}` | {size} | {extra} |")
    lines.append("")
    return '\n'.join(lines)
