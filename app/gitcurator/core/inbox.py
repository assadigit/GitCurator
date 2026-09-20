#!/usr/bin/env python3
"""gitcurator.core.inbox — non-GitHub link routing (v25 pre-flight).

Moved verbatim from ``gitcurator/gui/app.py`` (v32.3 modularization).

* :data:`PLATFORM_INFO` — platform key -> (display name, file name) for
  the per-platform review tables in ``<vault>/_inbox/``.
* :func:`classify_platform` — X/Twitter, Reddit, YouTube, LinkedIn,
  Medium, HuggingFace, arXiv, npm/PyPI detection; anything else (and
  unparseable URLs) fall back to ``'other'``.
* :func:`write_inbox_links_by_platform` — idempotent per-platform
  markdown tables, atomic writes, returns the number of NEW rows.

Pure stdlib; no PyQt — importable by the CLI, the worker and the GUI.
"""

import os
from datetime import datetime

from gitcurator.core.links import normalize_url

__all__ = ["PLATFORM_INFO", "classify_platform", "write_inbox_links_by_platform"]

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
        os.makedirs(inbox_folder, exist_ok=True)

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
                new_rows.append(f"| - | {date_str} | {url} | {domain} | {source} | unreviewed | |")

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
            import tempfile
            tmp_fd, tmp_path = tempfile.mkstemp(dir=inbox_folder, suffix='.tmp')
            try:
                with os.fdopen(tmp_fd, 'w', encoding='utf-8') as f:
                    f.write(content)
                os.replace(tmp_path, table_path)
            except Exception:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
                raise

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
