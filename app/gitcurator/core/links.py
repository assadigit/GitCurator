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
    """Lowercased netloc of a URL ('' when unparseable).

    v0.29.0 — scheme-tolerant: a bare ``x.com/foo`` (an import-file
    line with no scheme) parses as host ``x.com``, not ``''``. THE
    LAW's blocked-domain check must never be bypassable by dropping
    the scheme off a link.
    """
    try:
        u = str(url or '').strip()
        if not u:
            return ''
        if '://' not in u:
            u = 'https://' + u
        return (urlparse(u).netloc or '').lower()
    except Exception:
        return ''


# ---------------------------------------------------------------------------
# v0.20.0 — Blocked domains (the X fix) → superseded by the v0.28.0 LAW
# ---------------------------------------------------------------------------
#
# v0.28.0 — THE LAW (the owner's words, 2026-10-01): "EVERY X And GITHUB
# domain (all of its group) must be banned from showing on websites
# directory. … This 3 are forbiddan: Hugginface, Github, Twitter (X),
# Instagram, Facebook, Linkedin." The law is the FLOOR: the config's
# ``web_blocked_domains`` can only ADD domains, never remove these.
#
# v0.35.0 — THE LAW, second reading (the owner's words, 2026-10-05):
# "omit all youtube and every social media links. ONLY ONLY ONLY websites
# that aren't social media domains, and github" + "app must not process
# [share.google] google drive /sheets/docs link(s)" + "must not collect
# youtube links for note or review, same for X, and hugging face links".
# The extension: the whole YouTube group, the Google share/drive/docs
# family, and the social-media majors (TikTok, Threads, Snapchat,
# Pinterest, Twitch, Discord, Telegram/WhatsApp share links, VK, Bluesky,
# Weibo). Banned links are now omitted ENTIRELY — never fetched, never
# noted, never collected in the _inbox review-queue tables (the manifest's
# blocked bucket is the count). Deliberately NOT banned: Reddit and Medium
# (content platforms the owner curates — the golden set's reddit wiki is
# an approved Knowledge/Research link), arXiv + the package registries
# (knowledge), and GitHub (explicitly allowed by the owner's rule).

#: THE LAW — domains that can NEVER appear in the Websites vault, whatever
#: the config says. The Websites pipeline refuses them (never fetched,
#: never noted, never retried, never collected — v0.35.0: the _inbox
#: tables stopped being the record for them), and a run-start sweep
#: quarantines any legacy note that carries one in its ``source:``
#: frontmatter (v0.28.0) plus the banned platforms' _inbox tables
#: themselves (v0.35.0).
LAW_BLOCKED_DOMAINS = (
    # X / Twitter (the original v0.20.0 fix)
    'x.com', 'twitter.com', 't.co',
    # GitHub — the whole group. Repo links belong to the GitHub vault's
    # own pipeline; gists, Pages sites and the raw file hosts are never
    # "websites" either (the owner's law settles the old SPEC §4.2
    # gist/Pages judgment call for good).
    'github.com', 'gist.github.com', 'github.io', 'githubusercontent.com',
    # HuggingFace
    'huggingface.co', 'hf.co',
    # Instagram
    'instagram.com', 'instagr.am',
    # Facebook
    'facebook.com', 'fb.com', 'fb.me', 'fb.watch',
    # LinkedIn
    'linkedin.com', 'lnkd.in',
    # v0.35.0 — YouTube (the whole group; m./music./www. hosts match the
    # youtube.com entry by suffix, so they need no row of their own)
    'youtube.com', 'youtu.be', 'youtube-nocookie.com',
    # v0.35.0 — Google share/drive/docs family: shared files are never
    # "websites" (share.google is the Google-app share shortener;
    # docs.google.com covers docs + sheets + slides + forms-by-docs)
    'share.google', 'drive.google.com', 'docs.google.com',
    'forms.google.com',
    # v0.35.0 — the social-media majors ("ONLY websites that aren't
    # social media domains, and github")
    'tiktok.com',                                          # TikTok (+vm.*)
    'threads.net', 'threads.com',                          # Threads
    'snapchat.com',                                        # Snapchat
    'pinterest.com', 'pin.it',                             # Pinterest
    'twitch.tv',                                           # Twitch
    'discord.gg', 'discord.com', 'discordapp.com',         # Discord
    't.me', 'telegram.me',                                 # Telegram shares
    'wa.me', 'whatsapp.com',                               # WhatsApp
    'vk.com',                                              # VK
    'bsky.app',                                            # Bluesky
    'weibo.com',                                           # Weibo
)

#: The subset of the law used where GITHUB links must keep flowing (the
#: bot-queue classifier's repo filter): the social platforms only. The
#: GitHub group is NOT here — github.com repo links must never be
#: blanket-banned from the queue; gists and bare Pages sites are refused
#: later at the Websites pipeline gate instead.
LAW_PLATFORM_DOMAINS = tuple(
    d for d in LAW_BLOCKED_DOMAINS
    if not (d == 'github.com' or d.endswith('.github.com')
            or d == 'github.io' or d.endswith('.github.io')
            or d == 'githubusercontent.com'
            or d.endswith('.githubusercontent.com')))

#: Back-compat alias (the v0.20.0 name): the default ban list IS the law
#: now. The user-editable part is whatever the config ADDS on top.
DEFAULT_BLOCKED_DOMAINS = LAW_BLOCKED_DOMAINS


def _extra_domains_from_config(config, key: str) -> list:
    """The config's EXTRA domain list for ``key`` (never raises; any
    problem → []). Accepts a list/tuple or a comma-separated string;
    entries are lowercased, stripped, deduped (order preserved)."""
    try:
        raw = (config or {}).get(key)
        if raw is None:
            return []
        if isinstance(raw, str):
            items = raw.split(',')
        elif isinstance(raw, (list, tuple)):
            items = list(raw)
        else:
            return []
        out, seen = [], set()
        for item in items:
            d = str(item or '').strip().lower().lstrip('.')
            if d and d not in seen:
                seen.add(d)
                out.append(d)
        return out
    except Exception:
        return []


def _merge_domain_lists(base, extras) -> list:
    """Ordered dedupe union: ``base`` first (the law), then the extras
    the config adds. A base entry repeated in extras is kept once."""
    out, seen = [], set()
    for d in list(base or []) + list(extras or []):
        d = str(d or '').strip().lower().lstrip('.')
        if d and d not in seen:
            seen.add(d)
            out.append(d)
    return out


def blocked_domains_from_config(config) -> list:
    """The Websites-pipeline ban list from the app config (never raises).

    v0.28.0 LAW: ``LAW_BLOCKED_DOMAINS`` is the floor — the config's
    ``web_blocked_domains`` (list or comma string) can only ADD entries.
    A missing key, a hostile config, or an EMPTY value all still yield
    the law (the ban can no longer be opted out of; empty now means
    "nothing beyond the law", not "allow all").
    """
    return _merge_domain_lists(
        LAW_BLOCKED_DOMAINS,
        _extra_domains_from_config(config, 'web_blocked_domains'))


def platform_domains_from_config(config) -> list:
    """The bot-queue ban list: the LAW's social-platform subset (the
    GitHub group EXCLUDED — repo links must keep flowing to the GitHub
    pipeline) plus the config's extra ``web_blocked_domains`` entries.
    Same never-raises contract as ``blocked_domains_from_config``."""
    return _merge_domain_lists(
        LAW_PLATFORM_DOMAINS,
        _extra_domains_from_config(config, 'web_blocked_domains'))


def domain_is_blocked(url: str, blocked_domains) -> bool:
    """True when ``url``'s host is ``blocked_domains`` or a subdomain of
    one. ``x.com`` matches ``x.com``, ``www.x.com``, ``mobile.x.com`` —
    but never ``x.com.example.org`` (the suffix test is anchored on a
    literal dot). Empty list → False for everything."""
    if not blocked_domains:
        return False
    host = domain_of(url)
    if not host:
        return False
    host = host.split(':')[0]  # strip a port if present
    for entry in blocked_domains:
        entry = str(entry or '').strip().lower().lstrip('.')
        if not entry:
            continue
        if host == entry or host.endswith('.' + entry):
            return True
    return False


# ---------------------------------------------------------------------------
# v0.35.0 — Platform recognition (moved from gui/platform_intake.py so the
# core consumers — the law sweep quarantining banned platforms' _inbox
# tables — and the GUI intake share ONE table. Matching is now
# suffix-anchored like ``domain_is_blocked`` (the old gui code's substring
# test also matched look-alikes such as notyoutube.com).
# ---------------------------------------------------------------------------

#: platform key -> the domains that belong to it (host or any subdomain).
#: Ordered: the FIRST matching platform wins, so keep the specific
#: shorteners before general hosts where they overlap (they don't today).
PLATFORM_DOMAINS = {
    'x_twitter': ('x.com', 'twitter.com', 't.co'),
    'youtube': ('youtube.com', 'youtu.be', 'youtube-nocookie.com'),
    'reddit': ('reddit.com', 'redd.it'),
    'linkedin': ('linkedin.com', 'lnkd.in'),
    'medium': ('medium.com',),
    'huggingface': ('huggingface.co', 'hf.co'),
    'arxiv': ('arxiv.org',),
    'package_registry': ('npmjs.com', 'pypi.org'),
}

#: platform key -> the _inbox table file its links are collected in.
#: 'other' is the catch-all (every unrecognized domain). GitHub is not a
#: table: repo links belong to the GitHub pipeline, never to _inbox.
PLATFORM_TABLE_FILES = {
    'x_twitter': 'x_twitter_links.md',
    'youtube': 'youtube_links.md',
    'reddit': 'reddit_links.md',
    'linkedin': 'linkedin_links.md',
    'medium': 'medium_links.md',
    'huggingface': 'huggingface_links.md',
    'arxiv': 'arxiv_links.md',
    'package_registry': 'package_registry_links.md',
    'other': 'other_links.md',
}


def classify_platform(url: str) -> str:
    """Return the platform key for a non-GitHub URL.

    Recognises: X/Twitter, YouTube, Reddit, LinkedIn, Medium, HuggingFace,
    arXiv, npm/PyPI — by HOST (exact or subdomain, never a substring).
    GitHub URLs return 'github' (callers should never send GitHub URLs
    here, but we handle it defensively); anything else — including
    unparseable URLs — returns 'other'.

    v0.35.0: lives in core (the law sweep needs the platform tables);
    the gui re-exports it for every existing import site.
    """
    host = domain_of(url)
    if not host:
        return 'other'
    host = host.split(':')[0]  # strip a port if present
    if host == 'github.com' or host.endswith('.github.com'):
        return 'github'
    for platform, domains in PLATFORM_DOMAINS.items():
        for d in domains:
            if host == d or host.endswith('.' + d):
                return platform
    return 'other'


# ---------------------------------------------------------------------------
# v0.21.0 — Self domains (the app's own bot) + token scrubbing
# ---------------------------------------------------------------------------

#: Hosts that belong to THIS deployment — the Telegram bot's own worker.
#: The bot sends its own auth links (``/auth/?token=<hex>``) into the very
#: chat the curator reads, so without this rule the pipeline would try to
#: FETCH the bot's OAuth handoff URLs (and store them, token and all, in
#: _review notes). Never fetched, never noted — the _inbox row (with the
#: token scrubbed) is the record. Editable in Settings → 📁 Vault and in
#: config.json (``web_self_domains``).
DEFAULT_SELF_DOMAINS = ('github-to-obsidian-bot.aliassadi-plus.workers.dev',)


def self_domains_from_config(config) -> list:
    """The self-domain list from the app config (never raises). Same
    contract as ``blocked_domains_from_config``: list/tuple or comma
    string; missing key → DEFAULT; empty → opt-out (nothing is self)."""
    try:
        raw = (config or {}).get('web_self_domains')
        if raw is None:
            return list(DEFAULT_SELF_DOMAINS)
        if isinstance(raw, str):
            items = raw.split(',')
        elif isinstance(raw, (list, tuple)):
            items = list(raw)
        else:
            return list(DEFAULT_SELF_DOMAINS)
        out, seen = [], set()
        for item in items:
            d = str(item or '').strip().lower().lstrip('.')
            if d and d not in seen:
                seen.add(d)
                out.append(d)
        return out
    except Exception:
        return list(DEFAULT_SELF_DOMAINS)


def domain_is_self(url: str, self_domains) -> bool:
    """True when ``url`` points at one of OUR OWN hosts (the bot's
    worker). Same suffix-anchored matching as ``domain_is_blocked``."""
    return domain_is_blocked(url, self_domains)


#: Secret-named query parameters, as a TEXT-level scrub (values ≥16 chars
#: of token-ish characters, so ordinary short values survive). Used both
#: per-URL (new rows) and per-file (rewriting _inbox tables written before
#: v0.21.0 that still carry live tokens).
_SECRET_URL_TEXT_RE = re.compile(
    r'([?&](?:token|secret|access_token|api_key|apikey|password|passcode'
    r'|signature|sig|auth)='
    r')([A-Za-z0-9_\-.%]{16,})',
    re.IGNORECASE)


def scrub_url_token(url: str) -> str:
    """``…/auth/?token=e8d16400…`` → ``…/auth/?token=…`` — the value of
    any secret-named query parameter is replaced with a literal ``…``.
    Non-matching parameters and the rest of the URL are untouched; an
    unparseable input is returned as-is (never raises)."""
    raw = str(url or '')
    if '=' not in raw or '?' not in raw:
        return raw
    try:
        return _SECRET_URL_TEXT_RE.sub(r'\1…', raw)
    except Exception:
        return raw


def scrub_urls_in_text(text: str) -> str:
    """Scrub secret query values in EVERY URL-shaped run of ``text``
    (used to clean _inbox tables that were written before v0.21.0)."""
    if not text:
        return text
    try:
        return _SECRET_URL_TEXT_RE.sub(r'\1…', text)
    except Exception:
        return text


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

    v0.64.0 — THE ONE SPELLING (the owner's report, verbatim: "it
    processed a same website two times, only one has slash, other
    doesnt — https://cleanup.pictures/ / https://cleanup.pictures"):
    the ROOT path ``/`` is a trailing slash too. ``https://site/`` and
    ``https://site`` are ONE canonical form (the bare domain) — the
    old normalizer kept the root slash (it only stripped when the
    path was longer than one character), so the two spellings made
    two canonical keys, two fetches, and two notes for one site.

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
    # v0.64.0 — THE ONE SPELLING: the root '/' is a trailing slash
    # too. https://cleanup.pictures/ and https://cleanup.pictures are
    # the same site, the same key, the same note — one spelling. With a
    # query riding the root the slash is the PRETTY form's own
    # ('site.com/?p=1'), so it stays — and the bare 'site.com?p=1'
    # spelling gains it, folding the two into one.
    if path == '/' and not kept:
        path = ''
    elif path == '' and kept:
        path = '/'
    elif path.endswith('/') and len(path) > 1:
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


# ---------------------------------------------------------------------------
# v0.29.0 — Faithful batch imports (the owner's fidelity test: "give the
# app a .txt or .md file with one website address each … it must be
# robust and accurate and do not miss anything")
# ---------------------------------------------------------------------------

# A line that is ONE bare address with no scheme: a domain (or IPv4),
# optional port, optional path/query/fragment. No spaces, no markdown —
# those go through extract_all_links instead. The TLD must be ≥2 letters
# so 'not a url at all' / 'just-text' never match.
_BARE_ADDRESS_RE = re.compile(
    r'^(?:'
    r'[a-zA-Z0-9][a-zA-Z0-9\-.]*\.[a-zA-Z]{2,}'   # domain.tld(…)
    r'|\d{1,3}(?:\.\d{1,3}){3}'                   # IPv4 literal
    r')'
    r'(?::\d+)?'                                   # optional port
    r'(?:[/?#].*)?$'                               # optional path/query/#
)

# Markdown list decoration that may cling to the START of an address
# line ('- ', '* ', '+ ', '1. ' …). extract_all_links already finds the
# URL inside such lines; this is used only to keep the line-level
# accounting (unparsed detection) honest.


def read_import_file(path: str) -> str:
    """Read an import file to text, never raising for encoding issues.

    UTF-8 with BOM first (the Windows Notepad case — a leading ``\\ufeff``
    used to glue itself onto the first URL and corrupt its scheme), then
    a latin-1 fallback so an old .txt never crashes the batch.
    """
    with open(path, 'rb') as f:
        raw = f.read()
    try:
        return raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        return raw.decode('latin-1', errors='replace')


def parse_import_text(text: str) -> dict:
    """Parse a one-address-per-line import file (.txt or .md).

    Every non-comment line yields its address(es), whatever decoration
    they carry:

    * ``https://example.com`` — plain (always worked)
    * ``http://…`` / ``www.….com`` / bare ``example.com/path`` — any
      scheme form
    * ``- https://example.com`` — bullet lists
    * ``[Name](https://example.com)`` — Markdown links
    * ``https://example.com — a description`` — trailing text
    * two addresses on one line — both are captured

    Routing uses the SAME grammar as every other intake (split_links):
    github.com repos (http/www tolerated) → canonical GitHub URLs;
    ``owner.github.io/repo`` → the repo; everything else → websites.
    Blocked-domain (THE LAW) filtering is NOT done here — the Websites
    pipeline's gate stays the single enforcement point.

    Returns a dict (all lists order-preserving, deduped):

    ``github_urls``   canonical https://github.com/owner/repo list
    ``website_urls``  scheme-ful non-GitHub list
    ``unparsed``      the raw text of lines that yielded no address
    ``duplicates``    how many addresses were dropped as repeats
    ``raw_count``     total addresses seen before dedup
    ``skipped_comment_lines``  blank/# lines (for the report)
    """
    github_urls: List[str] = []
    website_urls: List[str] = []
    unparsed: List[str] = []
    duplicates = 0
    raw_count = 0
    skipped_comment_lines = 0
    seen_github = set()
    seen_website = set()

    def _route(link: str) -> None:
        nonlocal raw_count, duplicates
        raw_count += 1
        m = GITHUB_URL_PATTERN.match(link)
        if m:
            canonical = f"https://github.com/{m.group(1)}/{m.group(2)}"
            if canonical in seen_github:
                duplicates += 1
                return
            seen_github.add(canonical)
            github_urls.append(canonical)
            return
        gh_io = map_github_io_url(link)
        if gh_io:
            if gh_io in seen_github:
                duplicates += 1
                return
            seen_github.add(gh_io)
            github_urls.append(gh_io)
            return
        key = normalize_website_url(link)
        if key in seen_website:
            duplicates += 1
            return
        seen_website.add(key)
        website_urls.append(link)

    for line in (text or '').splitlines():
        line = line.strip().lstrip('\ufeff')
        if not line or line.startswith('#'):
            skipped_comment_lines += 1
            continue
        links = extract_all_links(line)
        if not links:
            # No http(s) link on the line. One bare address without a
            # scheme (coolors.co, www.paletton.com, github.com/o/r,
            # 127.0.0.1:8901/site) is still a valid import line.
            if _BARE_ADDRESS_RE.match(line):
                _route(clean_url(line))
            else:
                unparsed.append(line)
            continue
        for link in links:
            _route(link)

    return {
        'github_urls': github_urls,
        'website_urls': website_urls,
        'unparsed': unparsed,
        'duplicates': duplicates,
        'raw_count': raw_count,
        'skipped_comment_lines': skipped_comment_lines,
    }
