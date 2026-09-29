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

# GitHub Pages sites: https://<owner>.github.io/<repo>/… (v0.11.0 Phase 2 —
# SPEC §4.2: mapped to github.com/<owner>/<repo> and sent to the GitHub
# pipeline; a bare <owner>.github.io with no repo path is a real website
# and stays on the Website pipeline).
GITHUB_IO_PATTERN = re.compile(
    r'https?://([a-zA-Z0-9\-_.]+)\.github\.io/([a-zA-Z0-9\-_.]+)'
)

# Gists (SPEC §4.2): gist.github.com links go to the Website pipeline and
# get the #snippet tag — they are snippets, not full projects.
GIST_URL_PATTERN = re.compile(
    r'https?://(?:www\.)?gist\.github\.com/', re.IGNORECASE)

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


def map_github_io_url(url: str):
    """Map a GitHub Pages URL to its canonical GitHub repo URL.

    https://owner.github.io/repo/anything -> https://github.com/owner/repo
    Returns '' when ``url`` is not a <owner>.github.io/<repo> page (a bare
    ``owner.github.io`` site has no repo path and is treated as a website).
    """
    if not url:
        return ''
    m = GITHUB_IO_PATTERN.match(url.strip())
    if not m:
        return ''
    owner, repo = m.group(1), m.group(2)
    if not (_VALID_REPO_CHARS.match(owner) and _VALID_REPO_CHARS.match(repo)):
        return ''
    return f"https://github.com/{owner}/{repo}"


def is_gist_url(url: str) -> bool:
    """True for gist.github.com links (Website pipeline + #snippet tag)."""
    return bool(url and GIST_URL_PATTERN.match(url.strip()))


def split_links(text: str) -> Tuple[List[str], List[str], int]:
    """Split all links in ``text`` into (github_urls, non_github_urls, raw_count).

    Convenience used by the Telegram fetch paths: returns deduped GitHub
    URLs, deduped non-GitHub links (order-preserved), and the raw link count
    before dedup (for duplicate statistics).

    v0.11.0 — Phase 2 (SPEC §4.2): ``owner.github.io/repo`` links are mapped
    to ``github.com/owner/repo`` and routed to the GitHub pipeline. Gists
    and every other link stay in non_github (the Website pipeline's intake).
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
            continue
        # v0.11.0 — Phase 2: GitHub Pages sites belong to the repo they
        # publish (SPEC §4.2). A mapping that collides with an already
        # captured github.com URL is silently deduped like any other.
        gh_io = map_github_io_url(link)
        if gh_io:
            if gh_io not in github_urls:
                github_urls.append(gh_io)
            continue
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


# Tracking parameters that carry no identity for a website (SPEC §4.3.1:
# "utm_*/fbclid/gclid/ref parameters … all ignored"). Everything else in
# a query string is kept — a YouTube video id or a route path in ?p= is
# part of the page's identity, unlike GitHub URLs where the repo is the
# identity. Bare "utm" is included: it is only ever a tracker.
_TRACKING_PARAM_RE = re.compile(
    r'^(utm(?:_[a-z0-9_]+)?|fbclid|gclid|ref|ref_src|ref_url|igshid'
    r'|mc_cid|mc_eid)$',
    re.IGNORECASE)


def normalize_website_url(url: str) -> str:
    """Canonicalize a WEBSITE URL for dedupe + note identity (SPEC §4.3.1).

    - scheme -> https (http redirects are near-universal today; one form)
    - domain lowercased, leading ``www.`` dropped
    - tracking parameters (utm_*, fbclid, gclid, ref, …) dropped;
      every other query parameter KEPT (unlike GitHub's normalize_url)
    - #fragment dropped, trailing ``/`` dropped

    Deliberately does NOT touch ``normalize_url`` (the GitHub/VaultIndex
    normalizer) — its behavior for GitHub links is frozen by SPEC §4.3.1.
    """
    if not url:
        return ""
    url = url.strip()
    if '#' in url:
        url = url.split('#')[0]
    try:
        p = urlparse(url)
    except Exception:
        return url
    scheme = 'https'
    host = (p.netloc or '').lower()
    if host.startswith('www.'):
        host = host[4:]
    # Keep only non-tracking query parameters, order-preserved.
    kept = []
    if p.query:
        for pair in p.query.split('&'):
            if not pair:
                continue
            key = pair.split('=', 1)[0]
            if key and not _TRACKING_PARAM_RE.match(key):
                kept.append(pair)
    path = p.path or ''
    if path.endswith('/') and len(path) > 1:
        path = path.rstrip('/')
    out = f"{scheme}://{host}{path}"
    if kept:
        out += '?' + '&'.join(kept)
    return out


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
