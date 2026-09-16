#!/usr/bin/env python3
"""
links.py — THE single source of truth for link parsing & normalization.

v30 — Fix (Standardize link parsing): before this module, four divergent
copies of the GitHub URL regex lived in main.py, telegram_fetch_worker.py
(x2), backfill_manager.py and telethon_fetcher.py:

    main.py:290              no-www, no-dots in owner/repo  (misses real repos)
    telegram_fetch_worker:372 www + dots                     (most permissive)
    backfill_manager:281      www + dots
    telethon_fetcher:314      no-www, no-dots

Consequences of the drift: links like https://www.github.com/john.doe/my.project
were captured by some code paths and silently dropped by others, so the
same message could yield different link sets depending on which module
extracted it (dedup misses, "phantom" re-processing, inconsistent manifests).

This module implements the most permissive correct grammar ONCE:
    https?://(www.)github.com/<owner>/<repo>   owner/repo may contain
    letters, digits, '-', '_', '.'

Every consumer imports from here. No consumer defines its own regex.

Pure stdlib — importable from the GUI app, headless mode, and the
telegram_fetch_worker.py subprocess without any third-party deps.
"""

import re
from typing import List, Tuple
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# Canonical GitHub repo pattern — the union of every dialect that existed:
# optional www, http or https, owner/repo may contain dots.
GITHUB_URL_PATTERN = re.compile(
    r'https?://(?:www\.)?github\.com/([a-zA-Z0-9\-_.]+)/([a-zA-Z0-9\-_.]+)'
)

# Generic link pattern for non-GitHub link capture (inbox feature).
ALL_LINKS_PATTERN = re.compile(r'https?://[^\s<>"\)\]]+')

# Trailing punctuation that commonly clings to URLs pasted in chat messages.
_TRAILING_PUNCT = '.,);:!\'\"'

# Characters that are safe inside a GitHub owner/repo name (for validation).
# GitHub names must start and end with an alphanumeric — this also rejects
# traversal payloads like '..' or '../etc'.
_VALID_REPO_CHARS = re.compile(r'^[a-zA-Z0-9](?:[a-zA-Z0-9\-_.]*[a-zA-Z0-9])?$')


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def extract_github_urls(text: str, dedupe: bool = True) -> List[str]:
    """Extract GitHub repo URLs from arbitrary text.

    Handles www., dots in owner/repo names, and extra path components
    (/issues, /pulls/12, ...). Returns canonical
    https://github.com/<owner>/<repo> URLs, order-preserved.
    """
    if not text:
        return []
    urls = []
    seen = set()
    for owner, repo in GITHUB_URL_PATTERN.findall(text):
        url = f"https://github.com/{owner}/{repo}"
        if dedupe:
            if url in seen:
                continue
            seen.add(url)
        urls.append(url)
    return urls


def extract_all_links(text: str) -> List[str]:
    """Extract ALL http(s) links from text (GitHub and non-GitHub).

    Strips trailing punctuation that regexes tend to swallow
    (e.g. "see https://x.com/a." -> "https://x.com/a").
    Order-preserved, duplicates KEPT (callers classify first, dedupe later
    — the raw count is used for the "N duplicates removed" report).
    """
    if not text:
        return []
    return [lnk.rstrip(_TRAILING_PUNCT) for lnk in ALL_LINKS_PATTERN.findall(text)]


def split_links(text: str) -> Tuple[List[str], List[str], int]:
    """Split all links in ``text`` into (github_urls, non_github_urls, raw_count).

    Convenience used by the Telegram fetch paths: returns deduped GitHub
    URLs, deduped non-GitHub links (order-preserved), and the raw link count
    before dedup (for duplicate statistics).
    """
    raw = extract_all_links(text)
    github_urls = []
    non_github = []
    seen = set()
    for link in raw:
        if link in seen:
            continue
        seen.add(link)
        m = GITHUB_URL_PATTERN.match(link)
        if m:
            canonical = f"https://github.com/{m.group(1)}/{m.group(2)}"
            if canonical not in github_urls:
                github_urls.append(canonical)
        else:
            non_github.append(link)
    return github_urls, non_github, len(raw)


def is_github_url(url: str) -> bool:
    """True if ``url`` points at a GitHub repo (owner/repo sanity-checked)."""
    if not url:
        return False
    m = GITHUB_URL_PATTERN.match(url.strip())
    return bool(m and _VALID_REPO_CHARS.match(m.group(1))
                and _VALID_REPO_CHARS.match(m.group(2)))


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def clean_url(url: str) -> str:
    """Minimal cleanup: strip whitespace, ensure a scheme."""
    url = (url or '').strip()
    if url and not url.startswith("http"):
        url = "https://" + url
    return url


def normalize_url(url: str) -> str:
    """Normalize a URL for dedup comparison.

    - Lowercase the domain (scheme preserved)
    - Replace twitter.com with x.com
    - Strip query params (?s=20, ?ref=...)
    - Strip #fragment
    - Strip trailing /
    - Strip trailing whitespace

    NOTE: deliberately does NOT lowercase the path — GitHub paths are
    case-sensitive (github.com/User/Repo != github.com/user/repo) and the
    vault dedup must treat them as different repos.
    """
    if not url:
        return ""
    url = url.strip()
    # Replace twitter.com with x.com
    url = url.replace('twitter.com', 'x.com')
    # Strip fragment
    if '#' in url:
        url = url.split('#')[0]
    # Strip query params
    if '?' in url:
        url = url.split('?')[0]
    # Strip trailing slash (but keep root /)
    if url.endswith('/') and not url.endswith('://'):
        url = url.rstrip('/')
    # Lowercase the domain part only
    if '://' in url:
        scheme, rest = url.split('://', 1)
        if '/' in rest:
            domain, path = rest.split('/', 1)
            url = f"{scheme}://{domain.lower()}/{path}"
        else:
            url = f"{scheme}://{rest.lower()}"
    return url


def domain_of(url: str) -> str:
    """Lowercased netloc of a URL ('' when unparseable)."""
    try:
        return (urlparse(url or '').netloc or '').lower()
    except Exception:
        return ''


def dedupe_urls(urls: List[str]) -> Tuple[List[str], int]:
    """Order-preserving dedup by normalized form.

    Returns (unique_urls_original_form, duplicate_count).
    """
    seen = set()
    unique = []
    for u in urls:
        key = normalize_url(u)
        if key in seen:
            continue
        seen.add(key)
        unique.append(u)
    return unique, len(urls) - len(unique)
