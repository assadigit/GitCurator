#!/usr/bin/env python3
"""gitcurator.core.vault — the Obsidian vault model.

Extracted verbatim from ``gitcurator/gui/app.py`` (v32.3 modularization)
so the vault-side logic is importable — and unit-testable — without PyQt6:

* :class:`VaultIndex`            — in-memory normalized-URL -> note-path
                                    index, rebuilt per run; the GROUND
                                    TRUTH for dedup (beats the cache.db).
* :func:`find_obsidian_vaults`   — vault discovery via Obsidian's own
                                    registry (obsidian.json) plus a
                                    common-path fallback scan.
* :func:`_safe_moc_name`         — the one canonical Windows-safe MOC
                                    filename sanitizer (v32.1 crash fix),
                                    kept in sync with ``[[_moc/...]]`` links.

Pure stdlib. Bodies are behavioral twins of the former gui/app.py code.
"""

import json
import os
import re
from typing import List, Optional

from gitcurator.core.links import normalize_url

__all__ = ["VaultIndex", "find_obsidian_vaults", "_safe_moc_name"]


class VaultIndex:
    """In-memory index of all notes in the vault, keyed by normalized source URL.
    Rebuilt at the start of each processing run. Updated incrementally as new
    notes are written.

    This is the GROUND TRUTH for dedup — if a URL is in the vault, it's been
    processed (regardless of what the SQLite cache says).
    """

    def __init__(self, vault_path: str):
        self.vault_path = vault_path
        self._url_to_path = {}  # normalized_url -> note_path
        self._built = False

    def rebuild(self, log_signal=None):
        """Scan the vault and build the index. Reads only the first 500 chars
        of each .md file (frontmatter) for speed."""
        self._url_to_path = {}
        if not self.vault_path or not os.path.isdir(self.vault_path):
            self._built = True
            return

        count = 0
        for root, dirs, files in os.walk(self.vault_path):
            # Skip non-note folders — but INCLUDE _review (those are real notes!)
            if any(skip in root for skip in ['_moc', '_inbox', 'attachments', '.obsidian']):
                continue
            for fname in files:
                if not fname.endswith('.md'):
                    continue
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, 'r', encoding='utf-8') as f:
                        content = f.read(800)  # read enough for frontmatter
                    # Extract source: from frontmatter (first few lines)
                    match = re.search(r'^source:\s*(.+)$', content, re.MULTILINE)
                    if match:
                        source_url = match.group(1).strip()
                        # Strip quotes if present
                        source_url = source_url.strip('"\'')
                        normalized = normalize_url(source_url)
                        if normalized:
                            self._url_to_path[normalized] = fpath
                            count += 1
                except Exception:
                    pass

        self._built = True
        if log_signal:
            log_signal.emit(f"📚 Vault index: {count} notes indexed", "info")

    def has_url(self, url: str) -> bool:
        """Check if a URL (normalized) is already in the vault."""
        return normalize_url(url) in self._url_to_path

    def get_path(self, url: str) -> Optional[str]:
        """Get the note path for a URL, or None if not found."""
        return self._url_to_path.get(normalize_url(url))

    def add_url(self, url: str, path: str):
        """Add a new URL→path mapping (called after writing a new note)."""
        self._url_to_path[normalize_url(url)] = path

    @property
    def count(self) -> int:
        return len(self._url_to_path)

def find_obsidian_vaults() -> List[str]:
    """Discover Obsidian vaults from:
       1. Obsidian's own vault registry (obsidian.json) - the authoritative list
       2. Common filesystem paths (recursive one-level fallback)

    The registry is written by Obsidian itself and lists every vault ever
    opened on this machine, so this finds vaults in non-standard locations
    that the old fixed-path scan missed.
    """
    vaults: List[str] = []

    # --- 1. Obsidian's own registry ---
    # Windows: %APPDATA%/obsidian/obsidian.json
    # macOS:   ~/Library/Application Support/obsidian/obsidian.json
    # Linux:   ~/.config/obsidian/obsidian.json
    appdata = os.environ.get('APPDATA')
    candidates = []
    if appdata:
        candidates.append(os.path.join(appdata, 'obsidian', 'obsidian.json'))
    candidates.append(os.path.expanduser(
        '~/Library/Application Support/obsidian/obsidian.json'))
    candidates.append(os.path.expanduser('~/.config/obsidian/obsidian.json'))

    for cfg_path in candidates:
        try:
            if os.path.isfile(cfg_path):
                with open(cfg_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                for vault in (data.get('vaults') or {}).values():
                    path = vault.get('path')
                    if path and os.path.isdir(path):
                        vaults.append(path)
        except (json.JSONDecodeError, OSError, KeyError):
            continue

    # --- 2. Common path fallback (one level deep) ---
    home = os.path.expanduser("~")
    common_roots = [
        os.path.join(home, "Documents"),
        os.path.join(home, "OneDrive", "Documents"),
        os.path.join(home, "Desktop"),
        os.path.join(home, "Obsidian"),
        os.path.join(home, "Vaults"),
        os.path.join(home, "Documents", "Obsidian Vaults"),
        os.path.join(home, "Documents", "Obsidian"),
        os.path.join(home, "OneDrive", "Documents", "Obsidian Vaults"),
        os.path.join(home, "OneDrive", "Documents", "Obsidian"),
        os.path.join(home, "Desktop", "Obsidian Vaults"),
        os.path.join(home, "Desktop", "Obsidian"),
    ]
    for root in common_roots:
        if not os.path.isdir(root):
            continue
        # root itself may be a vault
        if os.path.isdir(os.path.join(root, ".obsidian")):
            vaults.append(root)
        # one level deep
        try:
            for entry in os.listdir(root):
                sub = os.path.join(root, entry)
                if os.path.isdir(sub) and os.path.isdir(os.path.join(sub, ".obsidian")):
                    vaults.append(sub)
        except OSError:
            continue

    # de-duplicate, preserve order
    seen = set()
    unique = []
    for v in vaults:
        v_norm = os.path.normpath(v)
        if v_norm not in seen:
            seen.add(v_norm)
            unique.append(v)
    return unique
# ============================================================================
# v32.1 — MOC filename sanitizer (Windows crash fix)
# ============================================================================

def _safe_moc_name(cat: str) -> str:
    """Sanitize a category name for use as a MOC filename / wiki-link.

    Observed in the wild (user log 2026-09-16): the LLM returned a category
    like ``"Agents_Skills"`` WITH literal quotes, and the master-index
    generator built ``_moc/\"Agents_Skills\".md`` — quotes are ILLEGAL in
    Windows filenames, so ``open()`` died with ``[Errno 22] Invalid
    argument`` and the whole master index was skipped. ``>`` (category
    separators like ``AI > Skills``), ``:``, ``|`` etc. are equally fatal.

    One canonical sanitizer keeps the file on disk and the
    ``[[_moc/...|View MOC]]`` wiki-links pointing at it in sync:
      - ``/`` and ``\\`` become ``_`` (they would split the path)
      - every Windows-illegal char is removed (``<>:"|?*`` + control chars)
      - whitespace collapses to single spaces, no trailing dots/spaces
      - empty result falls back to ``Uncategorized``
    """
    name = (cat or "").replace('/', '_').replace('\\', '_')
    name = re.sub(r'[<>:"|?*\x00-\x1f]', '', name)
    name = re.sub(r'\s+', ' ', name).strip(' .')
    return name or "Uncategorized"
