"""VaultIndex, find_obsidian_vaults, _safe_moc_name — moved verbatim from gitcurator/gui/app.py (branch refactor/gui-app-split; see docs/history/REFACTOR_PLAN.md)."""

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

from gitcurator.gui.link_helpers import normalize_url

class VaultIndex:
    """In-memory index of all notes in the vault, keyed by normalized source URL.
    Rebuilt at the start of each processing run. Updated incrementally as new
    notes are written.

    This is the GROUND TRUTH for dedup — if a URL is in the vault, it's been
    processed (regardless of what the SQLite cache says).

    v0.11.0 — Phase 2: an optional ``normalizer`` overrides the URL keying;
    the Websites vault passes ``links.normalize_website_url`` (query params
    are part of a website's identity, unlike a GitHub repo's). The default
    keeps ``normalize_url`` — the GitHub vault's behavior is unchanged.

    v0.64.0 — ONE NOTE PER SITE: the index also parses each note's
    ``site_links: [..]`` frontmatter list (the links consolidated into
    their site's note), so a consolidated URL reads "in the vault" exactly
    like a note of its own would; and it keeps a SITE map (host → the
    note that owns the site — app-owned, real, outside ``_review``), the
    probe the pipeline's consolidation gate asks through
    (:meth:`site_note_for`).
    """

    def __init__(self, vault_path: str, normalizer=None):
        self.vault_path = vault_path
        self._normalize = normalizer or normalize_url
        self._url_to_path = {}  # normalized_url -> note_path
        self._site_to_path = {}  # site key (host minus www) -> note_path
        self._built = False

    def rebuild(self, log_signal=None):
        """Scan the vault and build the index. Reads the head of each
        .md file (frontmatter) for speed. v0.64.0 — the read grew to
        4000 chars: the frontmatter's ``site_links:`` line (and the
        ownership/status keys the site map reads) live past the old
        800-char window on note-schema notes."""
        self._url_to_path = {}
        self._site_to_path = {}
        if not self.vault_path or not os.path.isdir(self.vault_path):
            self._built = True
            return

        count = 0
        for root, dirs, files in os.walk(self.vault_path):
            # Skip non-note folders — but INCLUDE _review (those are real notes!)
            # v0.28.0 — .trash too: the law sweep moves banned-domain notes
            # there (Obsidian's hidden trash); they must stop counting as
            # "in vault" or the dedupe layer would keep them alive.
            if any(skip in root for skip in ['_moc', '_inbox', 'attachments', '.obsidian', '.trash']):
                continue
            for fname in files:
                if not fname.endswith('.md'):
                    continue
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, 'r', encoding='utf-8') as f:
                        content = f.read(4000)  # frontmatter + the site_links line
                    # Extract source: from frontmatter (first few lines)
                    match = re.search(r'^source:\s*(.+)$', content, re.MULTILINE)
                    if match:
                        source_url = match.group(1).strip()
                        # Strip quotes if present
                        source_url = source_url.strip('"\'')
                        normalized = self._normalize(source_url)
                        if normalized:
                            self._url_to_path[normalized] = fpath
                            count += 1
                            self._register_site_note(fpath, content,
                                                     source_url, normalized)
                    # v0.64.0 — ONE NOTE PER SITE: the consolidated links
                    # ride the site note's frontmatter list; each indexes
                    # to the note that now carries it.
                    for m in re.finditer(r'^site_links:\s*(.+)$',
                                         content, re.MULTILINE):
                        inner = m.group(1).strip()
                        if inner.startswith('[') and inner.endswith(']'):
                            inner = inner[1:-1]
                        for item in inner.split(','):
                            u = item.strip().strip('"').strip("'")
                            if not u:
                                continue
                            nu = self._normalize(u)
                            if nu:
                                self._url_to_path.setdefault(nu, fpath)
                except Exception:
                    pass

        self._built = True
        if log_signal:
            log_signal.emit(f"📚 Vault index: {count} notes indexed", "info")

    def _register_site_note(self, fpath: str, content: str,
                            source_url: str, normalized: str):
        """v0.64.0 — ONE NOTE PER SITE's map half: register ``fpath`` as
        the site's note when it is a REAL one (the app's own — the
        ``managed_by`` line says so; outside ``_review``; not a failed
        fetch). First-come-wins in the walk's deterministic order; the
        map is a probe, never a gate — an empty map simply leaves the
        law off."""
        try:
            if '/_review/' in fpath.replace('\\', '/'):
                return
            m = re.search(r'^managed_by:\s*(.+)$', content, re.MULTILINE)
            if not m or 'gitcurator' not in (m.group(1) or '').lower():
                return        # a hand-written note is the owner's, never the anchor
            s = re.search(r'^fetch_status:\s*(.+)$', content, re.MULTILINE)
            if s and (s.group(1) or '').strip().strip('"\'') \
                    .lower() == 'failed':
                return        # a failed placeholder is not the site's note
            from gitcurator.core.links import site_key_of
            key = site_key_of(normalized or source_url)
            if key and key not in self._site_to_path:
                self._site_to_path[key] = fpath
        except Exception:
            pass

    def has_url(self, url: str) -> bool:
        """Check if a URL (normalized) is already in the vault."""
        return self._normalize(url) in self._url_to_path

    def get_path(self, url: str) -> Optional[str]:
        """Get the note path for a URL, or None if not found."""
        return self._url_to_path.get(self._normalize(url))

    def site_note_for(self, url: str) -> Optional[str]:
        """v0.64.0 — ONE NOTE PER SITE's probe: the note that owns this
        link's SITE (its host), or None when the vault holds no real
        note for the site. The pipeline's consolidation gate asks
        through this — a link whose site is already noted never gets a
        second note."""
        try:
            from gitcurator.core.links import site_key_of
            key = site_key_of(self._normalize(url) or url)
            return self._site_to_path.get(key)
        except Exception:
            return None

    def add_url(self, url: str, path: str):
        """Add a new URL→path mapping (called after writing a new note).
        v0.64.0 — the site map learns the new note too (a later link of
        the same site in the SAME batch must find it), by reading the
        note's own frontmatter."""
        self._url_to_path[self._normalize(url)] = path
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read(4000)
            self._register_site_note(path, content,
                                     self._normalize(url), self._normalize(url))
        except Exception:
            pass

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
