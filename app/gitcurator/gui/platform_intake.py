"""PLATFORM_INFO, _inbox_table_vault, classify_platform, write_inbox_links_by_platform — moved verbatim from gitcurator/gui/app.py (branch refactor/gui-app-split; see docs/history/REFACTOR_PLAN.md)."""

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

# v25 pre-flight: non-GitHub link platform classification.
# Used by _create_inbox_notes (worker) and MainWindow.check_bot_queue to
# route each link to the right per-platform file in _inbox/.
#
# v0.35.0 — the recognition logic (classify_platform + the domain and
# table-file maps) moved to core/links.py so the law sweep shares ONE
# source of truth; this module keeps the DISPLAY names and re-exports the
# classifier for every existing import site.
_PLATFORM_DISPLAY = {
    'x_twitter':       '🐦 X / Twitter',
    'reddit':          '👽 Reddit',
    'youtube':         '📺 YouTube',
    'linkedin':        '💼 LinkedIn',
    'medium':          '✍️ Medium',
    'huggingface':     '🤗 HuggingFace',
    'arxiv':           '📄 arXiv',
    'package_registry':'📦 Package Registry',
    'other':           '🔗 Other',
}

#: platform key -> (display name, _inbox table filename). The filenames
#: come from core/links.PLATFORM_TABLE_FILES (single source of truth) —
#: the display names live only here (pure presentation).
PLATFORM_INFO = {
    key: (display, _links.PLATFORM_TABLE_FILES.get(key, 'other_links.md'))
    for key, display in _PLATFORM_DISPLAY.items()
}

# v0.35.0 — re-export the core classifier (suffix-anchored host match;
# the old local copy also matched look-alike hosts like notyoutube.com).
classify_platform = _links.classify_platform

def _inbox_table_vault(config) -> str:
    """v0.20.0 — WHICH vault receives the per-platform _inbox tables.

    The owner's rule: "the github vault only manages its domains" — every
    non-GitHub link's table row belongs to the WEBSITES vault when one is
    configured (created on demand by the writer); without a Websites
    vault the GitHub vault keeps the legacy fallback so links are never
    silently lost."""
    cfg = config or {}
    web_vault = (cfg.get('website_vault_path') or '').strip()
    if web_vault:
        return web_vault
    return cfg.get('vault_path', '') or ''

def _stored_urls_probe(vault_path):
    """v0.35.0 — a ``url -> bool`` probe for "this link is ALREADY a note
    in the vault". The Websites VaultIndex (the same ground-truth dedupe
    layer the pipeline uses) over the TABLE vault, keyed with the website
    normalizer. Returns None when the vault can't be scanned (the caller
    then skips the stored-check rather than false-positive rows)."""
    try:
        from gitcurator.gui.vault_index import VaultIndex
        vi = VaultIndex(vault_path,
                        normalizer=_links.normalize_website_url)
        vi.rebuild(log_signal=None)
        return vi.has_url
    except Exception:
        return None


def _inbox_row_url(line):
    """The URL column of one _inbox table row (column 4, same parse the
    writer's dedupe reader uses). '' when the line is not a data row."""
    if not (line.startswith('| ') and 'http' in line):
        return ''
    parts = line.split('|')
    if len(parts) < 4:
        return ''
    u = parts[3].strip()
    return u if u.startswith('http') else ''


def write_inbox_links_by_platform(vault_path, non_github_urls, source="Saved",
                                   log_callback=None, blocked_domains=None,
                                   vault_index_has=None):
    """Write non-GitHub links to per-platform files in `<vault>/_inbox/`.

    Each platform gets its own .md file with a markdown table. The function
    is idempotent — URLs already present in the target file (matched by
    normalized URL) are skipped. The file is written atomically
    (tempfile + os.replace) so a crash mid-write cannot corrupt the table.

    v0.35.0 — the owner's omission rule, enforced at the intake: links on
    ``blocked_domains`` (THE LAW + the config's extras — YouTube, X/t.co,
    HuggingFace, Google share/drive/docs, the social-media majors, …) are
    NEVER collected — no note, no _review, no _inbox row (the manifest's
    blocked bucket is the count). Links already stored as notes in the
    vault are not re-collected either ("already addressed and stored in
    correct notes and formats inside the vault"). After writing, every
    table is PRUNED so legacy rows of both kinds disappear (see
    ``prune_inbox_tables``).

    ``blocked_domains`` — the ban list (None = no ban filter; production
    callers pass ``_links.blocked_domains_from_config(config)``).
    ``vault_index_has`` — an existing ``url -> bool`` "is a note in the
    vault" probe to reuse; when None a fresh VaultIndex over the table
    vault is built (only when there is something left to check).

    Returns: total number of NEW rows added across all platforms.
    Logs per-platform counts via ``log_callback(msg, level)`` if supplied."""
    try:
        if not vault_path or not non_github_urls:
            return 0
        inbox_folder = os.path.join(vault_path, "_inbox")

        # v0.35.0 — filter 1: banned domains are never collected.
        _kept_urls = list(non_github_urls)
        _omitted = 0
        if blocked_domains:
            _kept_urls = []
            for url in non_github_urls:
                if _links.domain_is_blocked(url, blocked_domains):
                    _omitted += 1
                else:
                    _kept_urls.append(url)
            if _omitted and log_callback:
                try:
                    log_callback(
                        f"🚫 {_omitted} banned-domain link(s) omitted — "
                        f"never collected (no note, no _review, no _inbox "
                        f"row)", "info")
                except Exception:
                    pass
        if not _kept_urls:
            # Nothing to write — but still prune the legacy tables so old
            # banned/stored rows leave on this pass too.
            prune_inbox_tables(vault_path,
                               blocked_domains=blocked_domains,
                               vault_index_has=vault_index_has,
                               log_callback=log_callback)
            return 0
        # v0.35.0 — filter 2: links already stored as notes in the vault
        # are not re-collected. The probe is built lazily (a vault scan)
        # and only when at least one link survived the ban filter.
        _already_stored = 0
        _stored_probe = vault_index_has
        if _stored_probe is None:
            _stored_probe = _stored_urls_probe(vault_path)
        if _stored_probe is not None:
            _survivors = []
            for url in _kept_urls:
                try:
                    if _stored_probe(url):
                        _already_stored += 1
                    else:
                        _survivors.append(url)
                except Exception:
                    _survivors.append(url)   # probe trouble → keep the row
            _kept_urls = _survivors
            if _already_stored and log_callback:
                try:
                    log_callback(
                        f"🔗 {_already_stored} link(s) already stored as "
                        f"note(s) in the vault — not re-added to _inbox",
                        "info")
                except Exception:
                    pass

        # Group URLs by platform
        platform_urls = {}  # platform -> [urls]
        for url in _kept_urls:
            platform = classify_platform(url)
            # Defensive: GitHub URLs should never reach here, but if they
            # do, route them to 'other' rather than dropping them.
            if platform == 'github':
                platform = 'other'
            platform_urls.setdefault(platform, []).append(url)

        total_new = 0
        for platform, urls in platform_urls.items():
            display_name, filename = PLATFORM_INFO.get(platform, ('🔗 Other', 'other_links.md'))
            table_path = os.path.join(inbox_folder, filename)
            # v0.09.5 — Phase 0 (dry-run): recorded, not performed. Only
            # created when there is at least one row to write (v0.35.0 —
            # an all-banned batch must not leave an empty _inbox/ dir).
            _dryrun.makedirs(inbox_folder, exist_ok=True)

            # Read existing URLs for dedup
            existing_urls = set()
            existing_content = ""
            if os.path.exists(table_path):
                try:
                    with open(table_path, 'r', encoding='utf-8') as f:
                        existing_content = f.read()
                    # v0.21.0 — scrub live tokens out of rows written
                    # before this release (the bot's auth links carried
                    # ?token=<hex> into the tables). Rewritten in place,
                    # atomically, only when something actually changes.
                    _clean = _links.scrub_urls_in_text(existing_content)
                    if _clean != existing_content:
                        tmp_path = table_path + ".tmp"
                        with open(tmp_path, 'w', encoding='utf-8') as f:
                            f.write(_clean)
                        os.replace(tmp_path, table_path)
                        existing_content = _clean
                        if log_callback:
                            try:
                                log_callback(
                                    f"🔒 {filename}: scrubbed secret token(s) "
                                    f"out of stored URL(s)", "info")
                            except Exception:
                                pass
                    for line in existing_content.split('\n'):
                        if line.startswith('| ') and 'http' in line:
                            parts = line.split('|')
                            if len(parts) >= 4:
                                u = parts[3].strip()
                                if u.startswith('http'):
                                    existing_urls.add(normalize_url(u))
                except Exception:
                    pass

            # Find new URLs
            from urllib.parse import urlparse
            new_rows = []
            for url in urls:
                norm = normalize_url(url)
                if norm in existing_urls:
                    continue
                existing_urls.add(norm)
                try:
                    parsed = urlparse(url)
                    domain = parsed.netloc or "unknown"
                except Exception:
                    domain = "unknown"
                date_str = datetime.now().strftime("%Y-%m-%d")
                # v0.21.0 — never store secret query values (the bot's
                # auth links: ?token=<64 hex>). The row keeps the URL with
                # the parameter name and a … placeholder.
                new_rows.append(f"| - | {date_str} | {_links.scrub_url_token(url)} | {domain} | {source} | unreviewed | |")

            if not new_rows:
                continue

            # Build or append table
            if not existing_content:
                content = f"""# {display_name} — Review Queue

> Auto-updated. DO NOT delete rows — only update the Status column.
> Edit Status to: ✅ reviewed / ❌ ignored
> Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}

| # | Date | URL | Domain | Source | Status | Notes |
|---|------|-----|--------|--------|--------|-------|
"""
                content += '\n'.join(new_rows) + '\n'
            else:
                lines = existing_content.split('\n')
                last_data_idx = 0
                for i, line in enumerate(lines):
                    if line.startswith('| ') and 'http' in line:
                        last_data_idx = i
                lines = lines[:last_data_idx + 1] + new_rows + lines[last_data_idx + 1:]
                for i, line in enumerate(lines):
                    if 'Last updated:' in line:
                        lines[i] = f"> Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
                content = '\n'.join(lines)

            # Atomic write
            # v0.09.5 — Phase 0 (dry-run): routed through the shared atomic
            # writer in core/storage.py so a --dry-run batch logs this write
            # instead of performing it. Same bytes on disk when dry-run is
            # off (plus fsync durability — the previous inline copy had none).
            _storage.atomic_write_text(table_path, content)

            total_new += len(new_rows)
            if log_callback:
                try:
                    log_callback(
                        f"📥 {display_name}: {len(new_rows)} new links → {filename}",
                        "info"
                    )
                except Exception:
                    pass

        if total_new > 0 and log_callback:
            try:
                log_callback(
                    f"📥 Total: {total_new} non-GitHub links added to _inbox/ (by platform)",
                    "info"
                )
            except Exception:
                pass
        # v0.35.0 — after every write, prune the tables: legacy banned
        # rows (x, t.co, youtube, …) and rows for links that are already
        # stored as notes leave the review queue for good. The probe built
        # for filter 2 is reused — no second vault scan.
        prune_inbox_tables(vault_path,
                           blocked_domains=blocked_domains,
                           vault_index_has=_stored_probe,
                           log_callback=log_callback)
        return total_new
    except Exception as e:
        if log_callback:
            try:
                log_callback(f"Failed to update inbox: {e}", "warning")
            except Exception:
                pass
        return 0


def prune_inbox_tables(vault_path, blocked_domains=None, vault_index_has=None,
                       log_callback=None, stored_urls=None):
    """v0.35.0 — drop the rows the app must not collect from every
    ``<vault>/_inbox/*.md`` platform table:

    * rows whose URL is on a ``blocked_domains`` domain (x.com, t.co,
      youtube, share.google, … — THE LAW's platforms are never collected),
    * rows whose URL is ALREADY a note in the vault (``vault_index_has``
      probe, or a fresh Websites VaultIndex over the table vault), or in
      ``stored_urls`` (a set of canonical website URLs — the batch's
      just-processed results, passed by the websites phase).

    Everything else is kept VERBATIM (the owner's ✅ reviewed / ❌ ignored
    Status marks included). The rewritten file stays byte-identical apart
    from the dropped rows and the "Last updated:" stamp. Never raises;
    unreadable tables are skipped; dry-run aware (the rewrite is recorded,
    not performed). Returns the number of rows dropped."""
    dropped_total = 0
    try:
        if not vault_path:
            return 0
        inbox_folder = os.path.join(vault_path, "_inbox")
        if not os.path.isdir(inbox_folder):
            return 0
        _stored_probe = vault_index_has
        _stored_set = {str(u) for u in (stored_urls or ())}
        for fname in sorted(os.listdir(inbox_folder)):
            if not fname.endswith('.md'):
                continue
            table_path = os.path.join(inbox_folder, fname)
            if not os.path.isfile(table_path):
                continue
            try:
                with open(table_path, 'r', encoding='utf-8') as f:
                    lines = f.read().split('\n')
            except Exception:
                continue
            # Lazy shared probe: only build the vault scan when the first
            # candidate row actually needs the "is it a note?" answer.
            _dropped = 0
            _kept_lines = []
            _dirty = False
            for line in lines:
                row_url = _inbox_row_url(line)
                if not row_url:
                    _kept_lines.append(line)
                    continue
                _drop = False
                if blocked_domains and _links.domain_is_blocked(
                        row_url, blocked_domains):
                    _drop = True
                if not _drop:
                    _canon = _links.normalize_website_url(row_url)
                    if _canon in _stored_set:
                        _drop = True
                if not _drop and (vault_index_has is not None
                                  or stored_urls is None):
                    if _stored_probe is None:
                        _stored_probe = _stored_urls_probe(vault_path)
                    if _stored_probe is not None:
                        try:
                            _drop = bool(_stored_probe(row_url))
                        except Exception:
                            _drop = False
                if _drop:
                    _dropped += 1
                    _dirty = True
                else:
                    _kept_lines.append(line)
            if not _dirty:
                continue
            content = '\n'.join(_kept_lines)
            # Refresh the "Last updated:" stamp like the writer does.
            _now = datetime.now().strftime('%Y-%m-%d %H:%M')
            content = '\n'.join(
                (f"> Last updated: {_now}" if 'Last updated:' in ln else ln)
                for ln in content.split('\n'))
            _storage.atomic_write_text(table_path, content)
            dropped_total += _dropped
            if log_callback:
                try:
                    log_callback(
                        f"🧹 {fname}: pruned {_dropped} row(s) — banned "
                        f"domain(s) and link(s) already stored as note(s) "
                        f"in the vault", "info")
                except Exception:
                    pass
        return dropped_total
    except Exception as e:
        if log_callback:
            try:
                log_callback(f"⚠️ _inbox prune skipped: {e}", "warning")
            except Exception:
                pass
        return dropped_total
