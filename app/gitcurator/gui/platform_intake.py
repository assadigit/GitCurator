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
PLATFORM_INFO = {
    'x_twitter':       ('🐦 X / Twitter',       'x_twitter_links.md'),
    'reddit':          ('👽 Reddit',             'reddit_links.md'),
    'youtube':         ('📺 YouTube',            'youtube_links.md'),
    'linkedin':        ('💼 LinkedIn',           'linkedin_links.md'),
    'medium':          ('✍️ Medium',             'medium_links.md'),
    'huggingface':     ('🤗 HuggingFace',        'huggingface_links.md'),
    'arxiv':           ('📄 arXiv',              'arxiv_links.md'),
    'package_registry':('📦 Package Registry',   'package_registry_links.md'),
    'other':           ('🔗 Other',              'other_links.md'),
}

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

def classify_platform(url: str) -> str:
    """Return the platform key for a non-GitHub URL.

    Recognises: X/Twitter, Reddit, YouTube, LinkedIn, Medium, HuggingFace,
    arXiv, npm/PyPI. Anything else (including unparseable URLs) returns
    'other'. GitHub URLs return 'github' (callers should never send GitHub
    URLs here, but we handle it defensively)."""
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url)
        domain = (parsed.netloc or '').lower()
    except Exception:
        return 'other'
    if not domain:
        return 'other'
    if 'x.com' in domain or 'twitter.com' in domain:
        return 'x_twitter'
    if 'reddit.com' in domain:
        return 'reddit'
    if 'youtube.com' in domain or 'youtu.be' in domain:
        return 'youtube'
    if 'linkedin.com' in domain:
        return 'linkedin'
    if 'medium.com' in domain:
        return 'medium'
    if 'github.com' in domain:
        return 'github'
    if 'huggingface.co' in domain:
        return 'huggingface'
    if 'arxiv.org' in domain:
        return 'arxiv'
    if 'npmjs.com' in domain or 'pypi.org' in domain:
        return 'package_registry'
    return 'other'

def write_inbox_links_by_platform(vault_path, non_github_urls, source="Saved", log_callback=None):
    """Write non-GitHub links to per-platform files in `<vault>/_inbox/`.

    Each platform gets its own .md file with a markdown table. The function
    is idempotent — URLs already present in the target file (matched by
    normalized URL) are skipped. The file is written atomically
    (tempfile + os.replace) so a crash mid-write cannot corrupt the table.

    Returns: total number of NEW rows added across all platforms.
    Logs per-platform counts via ``log_callback(msg, level)`` if supplied."""
    try:
        if not vault_path or not non_github_urls:
            return 0
        inbox_folder = os.path.join(vault_path, "_inbox")
        # v0.09.5 — Phase 0 (dry-run): recorded, not performed.
        _dryrun.makedirs(inbox_folder, exist_ok=True)

        # Group URLs by platform
        platform_urls = {}  # platform -> [urls]
        for url in non_github_urls:
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
        return total_new
    except Exception as e:
        if log_callback:
            try:
                log_callback(f"Failed to update inbox: {e}", "warning")
            except Exception:
                pass
        return 0
