#!/usr/bin/env python3
"""
web_fetch.py — polite website fetching for the Websites pipeline (Phase 2).

SPEC §4.3 step 3: "Fetch politely: timeout, size cap, per-domain rate
limit, a clear User-Agent. Record fetch_status: full, partial
(JavaScript-only or paywalled), or failed."

This module is the fetch half. It speaks plain ``urllib`` (standard library
first, per SPEC) and returns a ``FetchResult`` — it never raises for a
bad page: a fetch that cannot complete is DATA (status='failed'), not an
exception, so one failing link never stops the batch (non-negotiable #7).

Statuses produced here:
    full    — 200-range HTML/text body, not truncated
    partial — body retrieved but unusable as-is (size-capped or a PDF)
    failed  — DNS/connect/HTTP error, timeout, too many redirects

("JavaScript-only shell" and "paywall" partials are detected later by
``web_extract`` from the fetched body — the fetcher cannot know.)

Politeness:
    - per-domain rate limiting (``DomainRateLimiter``; shared instance so
      the whole batch pauses between hits on the same host)
    - connect+read timeout on every request
    - a hard byte cap (reads stream in chunks and stop at the cap)

v0.46.0 — THE LADDER. The owner's fresh failure pile ("HTTP 307",
"proxy: [SSL: SSLV3_ALERT_HANDSHAKE_FAILURE] | direct: getaddrinfo
failed", "HTTP 404", "429 | 429", "HTTP 523", "HTTP 402" …) maps
every remaining wall to its honest door:

    - redirects are now followed by OUR loop, not urllib's: hop
      cookies ride along (the "lost a cookie during it" login-loop
      class), 308 is followed, loops are named, and a redirect with
      no Location header is an honest broken-redirect failure — never
      a bare "HTTP 307" again.
    - a TLS-handshake-class wall (SSLV3_ALERT_HANDSHAKE_FAILURE,
      handshake timed out, UNEXPECTED_EOF) opens the third door too:
      v0.45 fired curl_cffi only on refusal-family HTTP answers, but a
      handshake refusal is just as much a fingerprint verdict.
    - a name-resolution failure on the direct line gets a DNS verdict
      via DNS-over-HTTPS: the local resolver is lying (poisoned), the
domain is really dead (NXDOMAIN), or the verdict is unknown.
    - a dead page (404/410) climbs its rescue ladder before the
      verdict: URL variants (trailing slash, www), then the Wayback
      Machine's archived copy — a rescue is a real success with the
      story in the reason; nothing rescued → category 'dead'.
    - 429/503: the site's Retry-After is read, named, and paid into
      the domain limiter (capped at five minutes — one slow domain
      never stalls a batch).
    - 521-524 (Cloudflare's "the origin is down") and 402/401/405
      get their names in the reason, and every failure carries a
      CATEGORY (``FetchResult.category``: paywalled, dead, refused,
      redirect_broken, blocked_bot, proxy_error, retry_later,
      archived) so the pipeline retries only what time can heal.

v0.43.0 — BOTH DOORS. The owner still met walls after v0.41.0:
a 403 aimed at the PROXY EXIT's datacenter IP (the residential line
was never asked), or both doors dead at once ("proxy: timeout |
direct: connection: timed out"). A 403/405/429/451 is an answer aimed
at the ROUTE'S IP — a bot wall, a per-IP rate limit, a method-block,
a geo-block — NOT an answer about the page. The fetcher that already
alternates to DIRECT on connection-class proxy failures (v0.21.0)
now alternates on these route-aimed refusals too: one attempt per
route, politeness untouched, a success via the second route is a
real success (the wall is named in its reason), and a wall on BOTH
routes reports both reasons in one honest line. Telegram keeps its
own law — MTProto always rides the proxy when one is enabled; only
HTTP website fetches get the second door.

v0.41.0 — The UA wall comes down. The honest "GitCurator/…" User-Agent
was answered with HTTP 403 by every CDN bot defense on the golden list
(pixabay, reddit, coolors, iconscout — working sites that simply refuse
non-browser clients), exactly as the v0.30 golden report suspected: "a
fetch-layer matter". The fetcher now presents as a current Chrome on
Windows — the most common browser on the owner's planet — with Chrome's
own header set (Accept, Accept-Language, Sec-Fetch-*, sec-ch-ua client
hints) and REAL gzip/deflate decoding to back the Accept-Encoding it
sends. Politeness is untouched (rate limit, timeout, byte cap, proxy
rules): presenting as a browser is not permission to hammer. A 403/429
that carries a visible bot-defense marker (cf-mitigated, Server:
cloudflare/akamai/…) now names the wall in the failure reason, so a
_review note says WHY a working site refused us. An owner-set
``web_user_agent`` in config.json overrides the presentation (the old
honest string can be restored there); custom non-Chrome UAs drop the
Chrome-only client-hint headers so the request stays self-consistent.

v0.19.0 — Web fetches through the owner's proxy. Behind x.com / t.co /
youtu.be the local DNS is poisoned (connection REFUSED on a fake IP) while
the rest of the web fetches fine — the Websites pipeline must ride the
same local proxy Telegram already uses (Settings -> Proxy, e.g. a v2rayN
SOCKS5 listener). Rules:

    - ``proxy_from_config`` reads the app config (``proxy`` block); the
      toggle ``use_for_web`` DEFAULTS TO ON when the proxy itself is
      enabled — the fix works on the owner's machine without touching
      Settings, and the checkbox can turn it off.
    - SOCKS proxies resolve DNS AT THE PROXY (rdns=True) — that is the
      whole point: the poisoned local resolver must never be consulted
      for the target host. PySocks (a hard Telethon dependency, so it is
      always present on a working install) provides the socket.
    - loopback targets are NEVER proxied (the v0.15.1 rule — a proxy can
      only break them).
    - an HTTP-type proxy rides urllib's native ``ProxyHandler``.

Pure stdlib + optional PySocks, no PyQt.
"""

import gzip
import http.client
import http.cookiejar
import io
import json
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
import zlib
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Dict, Optional, Tuple

# ===========================================================================
# CONFIGURATION (safe to edit)
# ===========================================================================
# v0.41.0 — browser-grade UA. The pre-v0.41 honest string
# ("GitCurator/0.19 (+personal website library builder; polite fetcher;
# …)") was 403-walled by CDN bot defenses on sites that work fine in a
# browser — the UA is the FIRST thing a WAF checks. A current stable
# Chrome on Windows 10 (the most common browser/OS combo worldwide) is
# the presentation least likely to be challenged. Overridable per
# install via config.json "web_user_agent".
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/131.0.0.0 Safari/537.36")

DEFAULT_PROXY_TYPE = 'socks5'    # v2rayN's default listener
DEFAULT_PROXY_HOST = '127.0.0.1'
DEFAULT_PROXY_PORT = 10808
PROXY_PREFLIGHT_TIMEOUT_S = 2.5  # proxy TCP reachability probe


class WebProxyError(RuntimeError):
    """A proxy-specific failure with a user-facing remedy (missing
    PySocks, unreachable proxy). Never raised out of ``fetch_url`` — it
    becomes a FetchResult reason so one bad proxy never kills a batch."""

DEFAULT_TIMEOUT_S = 20          # connect + per-read timeout
DEFAULT_MAX_BYTES = 2_000_000   # 2 MB body cap
DEFAULT_DOMAIN_DELAY_S = 2.0    # min seconds between hits on one domain
MAX_REDIRECTS = 5

# v0.46.0 — THE LADDER: every failure class gets its door.
_REDIRECT_STATUSES = (301, 302, 303, 307, 308)

# Cloudflare's "the site's own server is broken" answers (521-524):
# site-side truths — never the fetcher's fault, worth a later retry,
# and worth their name in the reason.
_CLOUDFLARE_ORIGIN_STATUSES = {
    521: 'web server is down',
    522: 'connection to the origin timed out',
    523: 'origin is unreachable',
    524: 'timed out waiting for the origin',
}

# Connection-class reasons that mean "the TLS handshake itself was
# refused" — a fingerprint verdict (the third door's trigger), not a
# network truth. Certificate failures are deliberately NOT here: a
# bad cert is the site's problem, never something to talk down.
_TLS_HANDSHAKE_MARKERS = (
    'SSLV3_ALERT_HANDSHAKE_FAILURE', 'alert handshake failure',
    'handshake operation timed out', 'handshake timed out',
    'UNEXPECTED_EOF_WHILE_READING', 'EOF occurred in violation of protocol',
    'WRONG_VERSION_NUMBER', 'TLSV1_ALERT', 'sslv3 alert',
)

# Connection-class reasons that mean "the NAME would not resolve" —
# the DNS verdict's trigger (the owner's [Errno 11001] line).
_DNS_FAIL_MARKERS = (
    'getaddrinfo', 'name or service not known', 'no such host',
    'temporary failure in name resolution', 'nodename nor servname',
    'WSAHOST_NOT_FOUND', 'Name or service not known',
)

# Failure categories (FetchResult.category): what TIME can heal.
# The pipeline reads this to decide retry-vs-retire; '' on success.
_CATEGORY_PRECEDENCE = ('paywalled', 'dead', 'refused', 'redirect_broken',
                        'blocked_bot', 'proxy_error', 'retry_later')
_NO_HEAL_CATEGORIES = ('dead', 'paywalled', 'refused')

# The rate-limit penalty law: a 429/503 answer pays the site's
# Retry-After (or a 60s default) into the domain limiter, capped at
# five minutes — the long game is the retry queue's day-scale backoff.
_RATE_PENALTY_DEFAULT_S = 60.0
_RATE_PENALTY_CAP_S = 300.0

# The DNS-over-HTTPS verdict (the owner's "getaddrinfo failed" class).
_DOH_ENDPOINT = 'https://cloudflare-dns.com/dns-query'
_DOH_TIMEOUT_S = 5.0
_DOH_CACHE: Dict[str, str] = {}
_DOH_CACHE_LOCK = threading.Lock()

# The archive door (the 404/410 rescue's last rung).
_ARCHIVE_API = 'https://archive.org/wayback/available'
_ARCHIVE_MAX_BYTES = 65_536

# v0.43.0 — HTTP answers that are aimed at the ROUTE, not the page:
# the WAF/IP-reputation wall (403), the method-block some CDNs answer
# bots with (405 — in the owner's own _review pile), the per-IP rate
# limit (429 — the OTHER route has its own quota), and the geo-block
# (451 — the other route is in another country). When the primary
# route gets one of these, the OTHER route still gets its one attempt.
# Every other HTTP error (404 gone, 401 auth, 410, 5xx broken) is the
# site answering about the RESOURCE — the truth on any route, no
# fallback.
ROUTE_REFUSAL_STATUSES = (403, 405, 429, 451)

# v0.45.0 — THE THIRD DOOR: the handshake itself. When BOTH routes are
# walled with a refusal-family answer (the owner's exact line:
# "Fetch failed: proxy: HTTP 403 | direct: HTTP 403 — bot defense
# (Cloudflare: challenge)"), the wall is aimed at the REQUESTER — and
# the one thing urllib can never present is a browser's own TLS/HTTP2
# fingerprint (Cloudflare's bot management scores Python's JA3 as
# automated before a single header is read). curl_cffi (optional, the
# escalation lever the v0.43.0 record named) wraps curl-impersonate:
# the Chrome handshake itself, one attempt per route, same politeness.
# Missing library → the honest reason gains the install hint; nothing
# breaks. Opt out with config "web_impersonate_fallback": false.
IMPERSONATE_TARGET = "chrome"
_IMP_INSTALL_HINT = (" — a third door exists: pip install curl_cffi "
                     "(the browser-TLS handshake)")
_CHALLENGE_WON_HINT = (" — the challenge needs a live browser; the "
                       "fetcher cannot solve it")

# The curl_cffi import probe — once per process, never raises. Tests
# flip _CURL_CFFI_STATE directly (the house patch-target pattern).
_CURL_CFFI_STATE = {'tried': False, 'ok': False}


def curl_cffi_available() -> bool:
    """v0.45.0 — True when curl_cffi (the optional browser-TLS library)
    is importable in this process. Probed once; never raises — a missing
    optional dependency is a degraded third door, never a dead fetcher."""
    if not _CURL_CFFI_STATE['tried']:
        try:
            from curl_cffi import requests as _creq  # noqa: F401
            _CURL_CFFI_STATE['ok'] = True
        except Exception:
            _CURL_CFFI_STATE['ok'] = False
        _CURL_CFFI_STATE['tried'] = True
    return _CURL_CFFI_STATE['ok']


# One cached impersonation session (a curl handle + cookie jar — a
# clearance cookie earned on one domain keeps working on the next visit
# to that domain, exactly like a browser). The lock serializes the
# rare third-door attempts; the fetcher never shares it across threads
# unguarded.
_IMP_SESSION = None
_IMP_SESSION_LOCK = threading.Lock()


def _new_impersonation_session():
    """Create one curl_cffi session impersonating the target browser.
    Module-level so tests can swap the factory (the fake-session
    pattern — same law as the injected fetch_fn)."""
    from curl_cffi import requests as creq
    return creq.Session(impersonate=IMPERSONATE_TARGET)

# Content types that are HTML-ish enough to parse.
_HTML_TYPES = ('text/html', 'application/xhtml', 'text/plain')


class FetchResult:
    """One fetch attempt. ``body`` is raw bytes (capped); ``text`` is the
    best-effort decode (charset from headers; web_extract re-decodes with
    the page's own <meta charset> when the header lies).

    v0.46.0 — two new fields: ``category`` (the failure class: '' on
    success, else one of paywalled / dead / refused / redirect_broken /
    blocked_bot / proxy_error / retry_later / archived — what the
    PIPELINE reads to retry only what time can heal) and
    ``retry_after_s`` (the site's own Retry-After on a 429/503, paid
    into the domain limiter by ``fetch_url``)."""

    __slots__ = ('url', 'final_url', 'status', 'reason', 'http_status',
                 'content_type', 'charset', 'body', 'text', 'elapsed_s',
                 'category', 'retry_after_s')

    def __init__(self, url, final_url='', status='failed', reason='',
                 http_status=None, content_type='', charset='',
                 body=b'', text='', elapsed_s=0.0,
                 category='', retry_after_s=None):
        self.url = url
        self.final_url = final_url or url
        self.status = status              # full | partial | failed
        self.reason = reason              # why partial/failed
        self.http_status = http_status
        self.content_type = content_type
        self.charset = charset
        self.body = body
        self.text = text
        self.elapsed_s = elapsed_s
        self.category = category          # v0.46.0 — the failure class
        self.retry_after_s = retry_after_s  # v0.46.0 — 429/503 Retry-After

    @property
    def ok(self) -> bool:
        """True when SOMETHING was fetched (full or partial)."""
        return self.status != 'failed'

    def __repr__(self):
        return (f"<FetchResult {self.status} {self.url!r} "
                f"http={self.http_status} bytes={len(self.body)} "
                f"reason={self.reason!r}>")


class DomainRateLimiter:
    """Minimum delay between requests to the same domain.

    One instance is shared by a whole batch. Thread-safe under a lock —
    the pipeline runs on the worker thread, but golden runs and future
    callers may not be so tidy.
    """

    def __init__(self, delay_s: float = DEFAULT_DOMAIN_DELAY_S):
        self.delay_s = max(0.0, float(delay_s))
        self._lock = threading.Lock()
        self._last_hit: Dict[str, float] = {}
        self._penalty_until: Dict[str, float] = {}

    def wait(self, domain: str) -> None:
        """Block (sleep) until hitting ``domain`` is polite again.
        v0.46.0 — a domain carrying a PAID penalty (a 429/503 answer)
        waits until its penalty expires too: the site asked for a
        pause, the limiter pays it. Sleeps in ≤1s slices so a big
        penalty never locks the batch's thread unresponsively."""
        if not domain or (self.delay_s <= 0
                          and not self._penalty_until.get(domain)):
            return
        while True:
            with self._lock:
                now = time.monotonic()
                last = self._last_hit.get(domain, 0.0)
                due = max(last + self.delay_s,
                          self._penalty_until.get(domain, 0.0))
                if now >= due:
                    self._last_hit[domain] = now
                    return
                sleep_for = due - now
            time.sleep(min(sleep_for, 1.0))

    def penalize(self, domain: str, seconds: float) -> None:
        """v0.46.0 — the site answered 429/503 (maybe with Retry-After):
        the domain's NEXT hit waits this much longer. The polite
        circuit breaker — a hard wall would stall a batch; a delayed
        next hit spreads the load honestly. Never raises."""
        try:
            if not domain or seconds <= 0:
                return
            until = time.monotonic() + float(seconds)
            with self._lock:
                if until > self._penalty_until.get(domain, 0.0):
                    self._penalty_until[domain] = until
        except Exception:
            pass  # politeness bookkeeping never kills a fetch


# ---------------------------------------------------------------------------
# v0.19.0 — The web proxy (Settings → Proxy rides the Websites pipeline)
# ---------------------------------------------------------------------------

_PROXY_TYPES = ('socks5', 'socks4', 'http')


def proxy_from_config(config: Optional[dict]) -> Optional[Dict]:
    """Extract the web-fetch proxy from the app config.

    Rules (never raises — an unusable config means NO proxy, exactly like
    today's direct behavior):

    - ``config['proxy']`` missing/not a dict, or ``enabled`` falsy → None
    - ``use_for_web`` explicitly False → None. **Missing → ON** (v0.19.0:
      the owner's blocked-web fix must work on the existing config.json
      without touching Settings; the checkbox opts out).
    - type must be socks5/socks4/http; host non-empty; port an int in
      1..65535 (defaults 127.0.0.1:10808 — the v2rayN listener).

    Returns ``{'type','host','port','username','password'}`` or None.
    """
    try:
        block = (config or {}).get('proxy')
        if not isinstance(block, dict) or not block.get('enabled'):
            return None
        if block.get('use_for_web') is False:
            return None
        ptype = str(block.get('type') or DEFAULT_PROXY_TYPE).strip().lower()
        if ptype not in _PROXY_TYPES:
            return None
        host = str(block.get('host') or DEFAULT_PROXY_HOST).strip()
        if not host:
            return None
        raw_port = block.get('port')
        if raw_port is None or raw_port == '':
            port = DEFAULT_PROXY_PORT
        else:
            try:
                port = int(raw_port)
            except (TypeError, ValueError):
                return None
        if not (1 <= port <= 65535):
            return None
        return {'type': ptype, 'host': host, 'port': port,
                'username': str(block.get('username') or ''),
                'password': str(block.get('password') or '')}
    except Exception:
        return None


def proxy_label(proxy: Optional[Dict]) -> str:
    """"SOCKS5 127.0.0.1:10808" — for log lines."""
    if not proxy:
        return 'none'
    t = str(proxy.get('type') or '').upper()
    return f"{t} {proxy.get('host')}:{proxy.get('port')}"


def _socks_module():
    """Import PySocks lazily with a clear remedy (it ships with Telethon,
    so a working install always has it — but the zip must not crash on a
    stripped-down Python)."""
    try:
        import socks  # PySocks
    except ImportError as e:
        raise WebProxyError(
            "PySocks is not installed (pip install PySocks) — the SOCKS "
            "proxy cannot be used for web fetches") from e
    return socks


def _socks_type(pysocks, ptype: str) -> int:
    return {'socks5': pysocks.SOCKS5,
            'socks4': pysocks.SOCKS4,
            'http': pysocks.HTTP}[ptype]


def _socks_connect(proxy_args: tuple, address, timeout=None):
    """Open a socket to ``address`` THROUGH the SOCKS proxy, resolving
    the target hostname AT THE PROXY (rdns=True — the local resolver is
    the thing being routed around). Mirrors ``socket.create_connection``
    for ``http.client``'s ``_create_connection`` hook."""
    ptype, phost, pport, username, password = proxy_args
    socks = _socks_module()
    sock = socks.socksocket()
    try:
        # rdns=True: send the HOSTNAME to the proxy, never resolve here.
        sock.set_proxy(ptype, phost, pport, rdns=True,
                       username=username or None, password=password or None)
        if timeout is not None:
            sock.settimeout(timeout)
        sock.connect(address)
        return sock
    except WebProxyError:
        sock.close()
        raise
    except Exception as e:
        sock.close()
        raise WebProxyError(
            f"proxy {phost}:{pport} connection failed: "
            f"{getattr(e, 'reason', None) or e}") from e


class _SocksConnectionMixin:
    """Routes ``http.client``'s socket factory through the SOCKS proxy.
    HTTP(S)Connection.__init__ binds ``socket.create_connection`` as an
    INSTANCE attribute, so the override must be re-bound after super().__init__
    (a plain mixin method would be shadowed). HTTPS keeps its full
    context/cert/SNI handling — only the raw socket changes."""

    def _socks_create_connection(self, address, timeout=None,
                                 source_address=None):
        return _socks_connect(self._socks_proxy_args, address, timeout)


class _SocksHTTPConnection(_SocksConnectionMixin, http.client.HTTPConnection):
    def __init__(self, proxy_args, *args, **kwargs):
        self._socks_proxy_args = proxy_args
        super().__init__(*args, **kwargs)
        self._create_connection = self._socks_create_connection


class _SocksHTTPSConnection(_SocksConnectionMixin, http.client.HTTPSConnection):
    def __init__(self, proxy_args, *args, **kwargs):
        self._socks_proxy_args = proxy_args
        super().__init__(*args, **kwargs)
        self._create_connection = self._socks_create_connection


class _SocksHandler(urllib.request.HTTPHandler, urllib.request.HTTPSHandler):
    """A urllib handler whose HTTP(S) connections all ride the proxy."""

    def __init__(self, proxy_args):
        self._socks_proxy_args = proxy_args
        urllib.request.HTTPHandler.__init__(self)

    def http_open(self, req):
        return self.do_open(
            lambda *a, **kw: _SocksHTTPConnection(self._socks_proxy_args, *a, **kw),
            req)

    def https_open(self, req):
        return self.do_open(
            lambda *a, **kw: _SocksHTTPSConnection(self._socks_proxy_args, *a, **kw),
            req)


def _proxy_handler_for(proxy: Dict):
    """The urllib handler that forces traffic through ``proxy`` — SOCKS
    via the connection classes above, HTTP via the native ProxyHandler
    (absolute-URI requests: the proxy resolves the hostname, same effect
    as rdns=True). Raises WebProxyError when PySocks is missing."""
    if proxy['type'] == 'http':
        auth = ''
        if proxy.get('username'):
            auth = (f"{proxy['username']}:{proxy.get('password') or ''}@")
        url = f"http://{auth}{proxy['host']}:{proxy['port']}"
        return urllib.request.ProxyHandler({'http': url, 'https': url})
    socks = _socks_module()
    return _SocksHandler((_socks_type(socks, proxy['type']),
                          proxy['host'], proxy['port'],
                          proxy.get('username') or '',
                          proxy.get('password') or ''))


def web_proxy_preflight(proxy: Optional[Dict],
                        timeout_s: float = PROXY_PREFLIGHT_TIMEOUT_S
                        ) -> Tuple[bool, str]:
    """Is the proxy usable RIGHT NOW? (TCP reachable; PySocks importable
    for SOCKS types.) Pure stdlib, never raises, fast — called once per
    Websites batch so the whole run knows before link #1."""
    if not proxy:
        return (False, 'no proxy configured')
    if proxy['type'] != 'http':
        try:
            _socks_module()
        except WebProxyError as e:
            return (False, str(e))
    try:
        with socket.create_connection(
                (proxy['host'], proxy['port']), timeout=timeout_s):
            pass
        return (True, '')
    except OSError as e:
        return (False, f"{proxy['host']}:{proxy['port']} unreachable "
                       f"({getattr(e, 'strerror', None) or e}) — is the "
                       f"proxy client (v2rayN) running?")


def _is_loopback_url(url: str) -> bool:
    """True when the URL points at THIS machine (127.x / ::1 / localhost).
    Same rule as llm_client's — loopback traffic must never ride a proxy."""
    from urllib.parse import urlparse
    raw = str(url or '').strip()
    if not raw:
        return False
    if '://' not in raw:
        raw = 'http://' + raw
    try:
        host = (urlparse(raw).hostname or '').lower()
    except ValueError:
        return False
    if host == 'localhost':
        return True
    try:
        import ipaddress
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _build_opener(proxy: Optional[Dict], context: ssl.SSLContext,
                  force_direct: bool = False):
    """The fetch opener.

    - ``proxy`` set → ALL traffic is forced through it (DNS at the proxy).
    - ``force_direct`` (loopback target, the v0.15.1 rule) →
      ``ProxyHandler({})``: no proxy of ANY kind, not even the system one.
    - neither → today's behavior (system proxy settings, if any, still
      apply — urllib's default opener handlers).

    v0.46.0 — the chain deliberately carries NO auto-following redirect
    handler (``_NoRedirectHandler`` replaces urllib's): redirects are
    followed by ``_fetch_once``'s OWN loop, which carries hop cookies,
    follows 308s, and names broken redirects. urllib's follower cannot
    do any of that — and its silent give-up was the bare "HTTP 307"
    in the owner's failure pile.
    """
    handlers = [_NoRedirectHandler,
                urllib.request.HTTPSHandler(context=context)]
    if force_direct:
        handlers.insert(0, urllib.request.ProxyHandler({}))
    elif proxy:
        handlers.insert(0, _proxy_handler_for(proxy))
    return urllib.request.build_opener(*handlers)


# ---------------------------------------------------------------------------
# The fetcher
# ---------------------------------------------------------------------------

class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """v0.46.0 — the redirect law moved out of urllib. This handler
    replaces the default ``HTTPRedirectHandler`` in the chain and
    refuses every redirect (``redirect_request`` → None → urllib raises
    HTTPError with the redirect's own headers) so ``_fetch_once``'s
    loop can follow hops itself: hop cookies ride along, 308 is
    followed (urllib <3.11 refuses it), loops are detected, and a
    redirect with no Location header becomes an honest broken-redirect
    failure instead of urllib's silent bare "HTTP 307"."""

    max_redirections = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# v0.46.0 — the persistent cookie jar (the browser-session law: a
# clearance cookie earned on one domain keeps working on the next visit
# to that domain, and cookies SET ON A REDIRECT HOP — the "lost a cookie
# during it" login-loop class — reach the next hop). Process-lifetime,
# domain-scoped by http.cookiejar itself, guarded by a lock.
_COOKIE_JAR = http.cookiejar.CookieJar()
_COOKIE_LOCK = threading.Lock()


class _CookieResponseShim:
    """http.cookiejar wants ``response.info()``; an HTTPMessage already
    IS one — this shim just hands it over."""

    __slots__ = ('_headers',)

    def __init__(self, headers):
        self._headers = headers

    def info(self):
        return self._headers


def _absorb_cookies(headers, request) -> None:
    """Extract Set-Cookie from one response into the shared jar.
    Bookkeeping never kills a fetch."""
    if headers is None or request is None:
        return
    try:
        with _COOKIE_LOCK:
            _COOKIE_JAR.extract_cookies(
                _CookieResponseShim(headers), request)
    except Exception:
        pass


def _attach_cookies(request) -> None:
    """Add the jar's Cookie header for this request's domain."""
    if request is None:
        return
    try:
        with _COOKIE_LOCK:
            _COOKIE_JAR.add_cookie_header(request)
    except Exception:
        pass


class _RedirectBrokenError(Exception):
    """v0.46.0 — a redirect answer that cannot be followed (no Location
    header, a non-http target, a loop, or a hop chain past the budget).
    Carried to ``_fetch_once``'s verdict as category 'redirect_broken'
    with the precise story in the message."""

    def __init__(self, code: int, detail: str):
        super().__init__(f'HTTP {code} — broken redirect: {detail}')
        self.code = int(code)
        self.detail = str(detail)


# ---------------------------------------------------------------------------
# v0.41.0 — the browser-grade request: headers, compression, bot walls
# ---------------------------------------------------------------------------

def _request_headers(user_agent: str) -> Dict[str, str]:
    """Chrome 131's own navigation header set, in Chrome's own order —
    v0.41.0's answer to the 403 wall (a WAF-classed "bot" UA plus a
    3-header request was trivially filterable; a real browser's request
    is not). Chrome-only client hints (sec-ch-ua*) are dropped for a
    custom non-Chrome User-Agent so the request never contradicts
    itself — a Firefox UA carrying Chrome hints is itself a bot signal."""
    ua = user_agent or USER_AGENT
    headers = {
        'sec-ch-ua': '"Google Chrome";v="131", "Chromium";v="131", '
                    '"Not_A Brand";v="24"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'Upgrade-Insecure-Requests': '1',
        'User-Agent': ua,
        'Accept': ('text/html,application/xhtml+xml,application/xml;q=0.9,'
                   'image/avif,image/webp,image/apng,*/*;q=0.8,'
                   'application/signed-exchange;v=b3;q=0.7'),
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-User': '?1',
        'Sec-Fetch-Dest': 'document',
        'Accept-Encoding': 'gzip, deflate',
        'Accept-Language': 'en-US,en;q=0.9',
    }
    if 'Chrome/' not in ua:
        for key in ('sec-ch-ua', 'sec-ch-ua-mobile', 'sec-ch-ua-platform'):
            headers.pop(key, None)
    return headers


# Server values that, on a 403/429, mean "a bot-defense answered" — a
# CDN fronting half the web is invisible on a 200, but is the likely
# challenger on a refusal.
_BOT_DEFENSE_SERVERS = ('cloudflare', 'akamai', 'cloudfront', 'fastly',
                        'imperva', 'incapsula', 'sucuri', 'edgecast')


def _bot_defense_hint(headers) -> str:
    """A VISIBLE bot-defense marker on a 403/429 — '' when nothing
    identifiable is on the wire. Names the wall in the failure reason so
    the _review note for a "working site" says why it refused us
    (v0.41.0): e.g. 'HTTP 403 — bot defense (server: cloudflare)'."""
    if headers is None:
        return ''
    try:
        mitigated = str(headers.get('cf-mitigated') or '').strip()
        if mitigated:
            return f'bot defense (Cloudflare: {mitigated})'
        server = str(headers.get('Server') or '').strip().lower()
        if server in _BOT_DEFENSE_SERVERS:
            return f'bot defense (server: {server})'
    except Exception:
        return ''
    return ''


def _redirect_target(headers) -> str:
    """The Location (or URI) header of a redirect answer, '' when the
    server sent none — urllib's silent give-up case (the bare
    "HTTP 301/307" in the owner's pile: a redirect answer with no
    Location is the server's answer being BROKEN, not ours)."""
    if headers is None:
        return ''
    try:
        loc = str(headers.get('Location') or '').strip()
        if loc:
            return loc.replace(' ', '%20')
        loc = str(headers.get('URI') or '').strip()
        if loc:
            return loc.replace(' ', '%20')
    except Exception:
        return ''
    return ''


def _hop_headers(base: Dict[str, str], prev_url: str,
                 next_url: str) -> Dict[str, str]:
    """v0.46.0 — the header set for a redirect HOP. Chrome's navigation
    headers stay (a redirect preserves the initiating request's
    Sec-Fetch-* values — the address-bar navigation's 'none' rides
    along); Referer joins the hop exactly the way a real browser sends
    it: the full URL for a same-host hop, the ORIGIN only for a
    cross-host hop (Chrome's default policy — leaking the full URL
    cross-site is itself a bot tell)."""
    from urllib.parse import urlparse
    out = dict(base)
    try:
        prev_host = (urlparse(prev_url).netloc or '').lower()
        next_host = (urlparse(next_url).netloc or '').lower()
        parsed = urlparse(prev_url)
        if prev_host and prev_host == next_host:
            referer = prev_url
        elif parsed.scheme and parsed.netloc:
            referer = f'{parsed.scheme}://{parsed.netloc}/'
        else:
            referer = ''
    except Exception:
        referer = ''
    if referer:
        ordered = {}
        for key, value in out.items():
            if key == 'Accept-Encoding':
                ordered['Referer'] = referer
            ordered[key] = value
        out = ordered
    return out


def _open_following(opener, url: str, headers: Dict[str, str],
                    timeout_s: float):
    """v0.46.0 — open ``url`` following redirect hops OURSELVES.

    urllib's HTTPRedirectHandler silently gives up on a redirect with
    no Location header (the bare "HTTP 307" in the owner's pile) and
    never lets hop-set cookies reach the next hop — the "lost a cookie
    during it" login-loop class (urllib re-opens from inside its error
    handler, so the jar never sees the intermediate response). This
    loop instead: carries the cookie jar across hops, follows
    301/302/303/307/308 (308 needs it on urllib < 3.11), caps hops at
    MAX_REDIRECTS, detects loops, and raises a precise
    ``_RedirectBrokenError`` when the redirect answer is broken.

    Returns ``(resp, final_url)`` — the FINAL non-redirect response
    (the caller owns closing it). Non-redirect HTTP errors raise
    HTTPError as before; everything else raises exactly what
    ``opener.open`` raised."""
    from urllib.parse import urljoin, urlparse
    current = url
    seen = {url}
    hops = 0
    while True:
        req = urllib.request.Request(current, headers=dict(headers))
        _attach_cookies(req)
        try:
            resp = opener.open(req, timeout=float(timeout_s))
        except urllib.error.HTTPError as e:
            _absorb_cookies(e.headers, req)
            if e.code not in _REDIRECT_STATUSES:
                raise
            target = _redirect_target(e.headers)
            try:
                e.close()
            except Exception:
                pass
            if not target:
                raise _RedirectBrokenError(
                    e.code, 'no Location header in the answer — the '
                            "server's redirect is broken (site-side)") from e
            try:
                next_url = urljoin(current, target)
                scheme = (urlparse(next_url).scheme or '').lower()
            except Exception as ex:
                raise _RedirectBrokenError(
                    e.code, f'unusable Location ({ex})') from e
            if scheme not in ('http', 'https'):
                raise _RedirectBrokenError(
                    e.code, f'redirect to a non-http target '
                            f'({scheme or "no scheme"}: {target[:80]})') from e
            if next_url in seen:
                raise _RedirectBrokenError(
                    e.code, f'redirect loop (the chain revisits '
                            f'{next_url[:120]} after {hops} hop(s) — '
                            'cookies are carried, so this is the '
                            "server's own loop)") from e
            if hops >= MAX_REDIRECTS:
                raise _RedirectBrokenError(
                    e.code, f'more than {MAX_REDIRECTS} redirects '
                            '(a tracker chute or a wandering chain)') from e
            hops += 1
            seen.add(next_url)
            headers = _hop_headers(headers, current, next_url)
            current = next_url
            continue
        _absorb_cookies(resp.headers, req)
        return resp, current


def _parse_retry_after(headers) -> Optional[float]:
    """v0.46.0 — the site's Retry-After (seconds or an HTTP-date) as a
    float of seconds from NOW; None when absent or unparsable. The
    politeness law: a site that says 'come back in N' gets believed."""
    if headers is None:
        return None
    try:
        raw = str(headers.get('Retry-After') or '').strip()
    except Exception:
        return None
    if not raw:
        return None
    try:
        return max(0.0, float(int(raw)))
    except (TypeError, ValueError):
        pass
    try:
        when = parsedate_to_datetime(raw)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
    except Exception:
        return None


def _http_error_verdict(code, headers) -> Tuple[str, str, Optional[float]]:
    """v0.46.0 — one place that names every HTTP error the owner's pile
    showed: (reason, category, retry_after_s). Codes outside the named
    families keep the honest plain 'HTTP {code}' line. The 403/429
    bot-defense hint keeps its v0.41.0 formatting byte-for-byte."""
    code = int(code)
    if code == 402:
        return ('HTTP 402 — paywalled (payment required; the content '
                'is behind a paywall, not gone)', 'paywalled', None)
    if code == 401:
        return ('HTTP 401 — authorization required (the page is '
                'private; credentials the fetcher does not have)',
                'refused', None)
    if code == 405:
        return ('HTTP 405 — method refused (the server does not allow '
                'GET here — often an API-only endpoint)', 'refused', None)
    if code in _CLOUDFLARE_ORIGIN_STATUSES:
        return (f'HTTP {code} — Cloudflare: the site\'s own server '
                f'{_CLOUDFLARE_ORIGIN_STATUSES[code]} (site-side, '
                'retry later — not the fetcher\'s fault)',
                'retry_later', None)
    if code in (404, 410):
        gone = 'the page is gone' if code == 404 else 'the page was removed'
        return (f'HTTP {code} — {gone}', 'dead', None)
    if code == 429:
        retry_after = _parse_retry_after(headers)
        hint = _bot_defense_hint(headers)
        if hint:
            return (f'HTTP 429 — {hint}', 'blocked_bot', retry_after)
        if retry_after is not None:
            return (f'HTTP 429 — rate limited (Retry-After: '
                    f'{retry_after:.0f}s — the site asked for a pause)',
                    'retry_later', retry_after)
        return ('HTTP 429 — rate limited (no Retry-After given; the '
                'domain limiter backs off anyway)', 'retry_later', None)
    if code == 503:
        retry_after = _parse_retry_after(headers)
        if retry_after is not None:
            return (f'HTTP 503 — service unavailable (Retry-After: '
                    f'{retry_after:.0f}s)', 'retry_later', retry_after)
        return ('HTTP 503 — service unavailable (the site is '
                'temporarily down or maintaining)', 'retry_later', None)
    if code == 403:
        hint = _bot_defense_hint(headers)
        if hint:
            return (f'HTTP 403 — {hint}', 'blocked_bot', None)
        return ('HTTP 403 — forbidden (the server refused this client '
                'without naming a wall)', 'blocked_bot', None)
    category = 'retry_later' if (code >= 500 or code == 408) else 'refused'
    return f'HTTP {code}', category, None


def _decompress_capped(body: bytes, encoding: str,
                       max_bytes: int) -> Tuple[bytes, bool]:
    """Decode a gzip/deflate response body, capped at ``max_bytes``
    DECOMPRESSED bytes (v0.41.0: the fetcher asks for gzip like a
    browser, so it must also decode it — and a "gzip bomb" must stop at
    the same byte budget as everything else). Returns
    ``(data, truncated)``; raises on an undecodable stream (the caller
    turns that into a failed/partial FetchResult with an honest
    reason — never an exception out of the fetcher)."""
    out = io.BytesIO()
    state = {'truncated': False}

    def _absorb(piece: bytes) -> None:
        if not piece:
            return
        if out.tell() + len(piece) > max_bytes:
            out.write(piece[:max_bytes - out.tell()])
            state['truncated'] = True
            return
        out.write(piece)

    enc = (encoding or '').strip().lower()
    if enc in ('gzip', 'x-gzip'):
        with gzip.GzipFile(fileobj=io.BytesIO(body)) as gz:
            while not state['truncated']:
                chunk = gz.read(64 * 1024)
                if not chunk:
                    break
                _absorb(chunk)
    elif enc == 'deflate':
        last_err: Exception = ValueError('deflate decode failed')
        for wbits in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
            out = io.BytesIO()
            state['truncated'] = False
            dec = zlib.decompressobj(wbits)
            try:
                data = body
                while True:
                    piece = dec.decompress(data, 64 * 1024)
                    _absorb(piece)
                    if state['truncated']:
                        break
                    tail = dec.unconsumed_tail
                    if not tail:
                        break
                    data = tail
                return out.getvalue(), state['truncated']
            except zlib.error as e:
                last_err = e
                continue  # try the raw-deflate fallback
        raise last_err
    else:
        raise ValueError(f'unsupported Content-Encoding: {encoding!r}')
    return out.getvalue(), state['truncated']


def _looks_like_pdf(content_type: str, url: str, body: bytes) -> bool:
    if 'application/pdf' in (content_type or ''):
        return True
    if 'octet-stream' in (content_type or '') and \
            url.rstrip('/').lower().endswith('.pdf'):
        return True
    return body[:5] == b'%PDF-'


def _proxy_url_for_curl(proxy: Optional[Dict]) -> Optional[str]:
    """The config proxy dict as a libcurl proxy URL. SOCKS becomes
    ``socks5h://`` — the SAME DNS-at-the-proxy law PySocks' rdns=True
    enforced since v0.19.0 (the local resolver is poisoned for the
    blocked-web domains; asking it would defeat the proxy)."""
    if not proxy:
        return None
    scheme = {'socks5': 'socks5h', 'socks4': 'socks4', 'http': 'http'}.get(
        str(proxy.get('type') or '').strip().lower())
    if not scheme:
        return None
    auth = ''
    if str(proxy.get('username') or '').strip():
        from urllib.parse import quote
        auth = (quote(str(proxy.get('username')), safe='') + ':'
                + quote(str(proxy.get('password') or ''), safe='') + '@')
    return (f"{scheme}://{auth}{proxy.get('host')}:{proxy.get('port')}")


def _impersonated_fetch_once(url: str, timeout_s: float, max_bytes: int,
                             proxy: Optional[Dict],
                             started: float) -> FetchResult:
    """v0.45.0 — one fetch attempt through curl_cffi impersonating
    Chrome: the browser's own TLS/HTTP2 fingerprint AND header order,
    the one presentation urllib cannot make. Never raises; the same
    FetchResult contract as _fetch_once (streamed, size-capped,
    charset-aware, pdf-aware). The body arrives already decompressed
    (libcurl decodes gzip/deflate/brotli itself), so the cap bounds the
    DEcompressed bytes — the same budget law as the stdlib door."""
    global _IMP_SESSION
    proxy_url = _proxy_url_for_curl(proxy)
    try:
        with _IMP_SESSION_LOCK:
            if _IMP_SESSION is None:
                _IMP_SESSION = _new_impersonation_session()
            session = _IMP_SESSION
            resp = session.get(
                url, timeout=float(timeout_s), proxies=None
                if not proxy_url else
                {'http': proxy_url, 'https': proxy_url},
                allow_redirects=True, max_redirects=MAX_REDIRECTS,
                stream=True)
    except Exception as e:
        # curl_cffi raises its own Errors (timeouts, connect failures,
        # bad TLS) — one honest reason, never an exception out.
        reason = 'timeout' if 'timeout' in str(e).lower() \
            else f'connection: {e}'
        return FetchResult(url, status='failed', reason=reason,
                           elapsed_s=time.monotonic() - started)
    try:
        http_status = int(getattr(resp, 'status_code', 0) or 0) or None
        headers = getattr(resp, 'headers', None) or {}
        content_type = str(headers.get('Content-Type') or '').lower()
        if http_status is not None and http_status >= 400:
            reason = f'HTTP {http_status}'
            if http_status in (403, 429):
                hint = _bot_defense_hint(headers)
                if hint:
                    reason = f'HTTP {http_status} — {hint}'
            return FetchResult(url, status='failed', reason=reason,
                               http_status=http_status,
                               elapsed_s=time.monotonic() - started)
        charset = str(getattr(resp, 'charset_encoding', None) or '') \
            .strip() or ''
        if not charset:
            for part in content_type.split(';'):
                part = part.strip()
                if part.startswith('charset='):
                    charset = part.split('=', 1)[1].strip().strip('"\'')
        final_url = str(getattr(resp, 'url', None) or url)
        # Stream with the size cap — same 64 KB chunk law as _fetch_once.
        chunks = []
        total = 0
        truncated = False
        try:
            for piece in resp.iter_content():
                if not piece:
                    continue
                total += len(piece)
                if total > max_bytes:
                    chunks.append(piece[:max_bytes - (total - len(piece))])
                    truncated = True
                    break
                chunks.append(piece)
        finally:
            try:
                resp.close()
            except Exception:
                pass
        body = b''.join(chunks)
    except Exception as e:
        return FetchResult(url, status='failed',
                           reason=f'{type(e).__name__}: {e}',
                           elapsed_s=time.monotonic() - started)

    elapsed = time.monotonic() - started
    if truncated:
        return FetchResult(url, final_url, status='partial',
                           reason=f'size cap ({max_bytes:,} bytes) reached',
                           http_status=http_status,
                           content_type=content_type, charset=charset,
                           body=body, text=_decode_best_effort(body, charset),
                           elapsed_s=elapsed)
    if _looks_like_pdf(content_type, url, body):
        return FetchResult(url, final_url, status='partial', reason='pdf',
                           http_status=http_status, content_type=content_type,
                           charset=charset, body=b'', text='',
                           elapsed_s=elapsed)
    return FetchResult(url, final_url, status='full', reason='',
                       http_status=http_status, content_type=content_type,
                       charset=charset, body=body,
                       text=_decode_best_effort(body, charset),
                       elapsed_s=elapsed)


def _is_tls_handshake_failure(reason: str) -> bool:
    """v0.46.0 — True when a connection-class reason names a TLS
    HANDSHAKE refusal (the fingerprint verdict). Certificate failures
    are deliberately excluded: a bad cert is the site's problem, not a
    wall to talk down."""
    low = str(reason or '').lower()
    return any(marker.lower() in low for marker in _TLS_HANDSHAKE_MARKERS)


def _is_dns_failure(reason: str) -> bool:
    """v0.46.0 — True when a connection-class reason says the NAME would
    not resolve (the owner's [Errno 11001] getaddrinfo failed class)."""
    low = str(reason or '')
    return any(marker in low for marker in _DNS_FAIL_MARKERS)


def _doh_probe_once(host: str, proxy: Optional[Dict]) -> str:
    """v0.46.0 — ONE DNS-over-HTTPS question to Cloudflare's resolver
    (JSON API): 'exists' / 'nxdomain' / 'unknown'. Rides the primary
    route (the proxy when one is configured — the local resolver is
    the suspect when direct DNS failed; the exit's resolver answers
    the existence question). Pure stdlib, tiny, never raises. The
    module-level seam tests swap (``wf._doh_probe_once``)."""
    from urllib.parse import quote
    try:
        ctx = ssl.create_default_context()
        opener = _build_opener(proxy, ctx)
    except Exception:
        return 'unknown'
    req = urllib.request.Request(
        f"{_DOH_ENDPOINT}?name={quote(host)}&type=A",
        headers={'Accept': 'application/dns-json',
                 'User-Agent': USER_AGENT})
    try:
        with opener.open(req, timeout=_DOH_TIMEOUT_S) as resp:
            raw = resp.read(_ARCHIVE_MAX_BYTES)
    except Exception:
        return 'unknown'
    try:
        data = json.loads(raw.decode('utf-8', errors='replace'))
        status = int(data.get('Status', -1))
    except Exception:
        return 'unknown'
    if status == 0:
        return 'exists'
    if status == 3:
        return 'nxdomain'
    return 'unknown'


def _doh_verdict(host: str, proxy: Optional[Dict]) -> str:
    """The cached DoH verdict for a host (a domain's existence rarely
    flips mid-process; one question per host, forever cached)."""
    key = str(host or '').lower()
    if not key:
        return 'unknown'
    with _DOH_CACHE_LOCK:
        if key in _DOH_CACHE:
            return _DOH_CACHE[key]
    verdict = _doh_probe_once(key, proxy)
    with _DOH_CACHE_LOCK:
        _DOH_CACHE[key] = verdict
    return verdict


def _dns_verdict_suffix(direct_reason: str, url: str,
                        proxy: Optional[Dict], doh_probe: bool
                        ) -> Tuple[str, bool]:
    """v0.46.0 — when the DIRECT line could not resolve the name, ask
    DNS-over-HTTPS whose fault it is: (reason suffix, is_dead).

    * nxdomain — the domain itself does not exist: the local resolver
      was RIGHT, the link is dead, and no retry will heal it.
    * exists — the name resolves out there: the LOCAL resolver is
      lying (the v0.19.0 poisoned-DNS class) — the proxy route is the
      door that can still reach the site.
    * unknown — the DoH resolver itself could not be asked; no
      verdict, no false accusation."""
    if not doh_probe or not _is_dns_failure(direct_reason or ''):
        return '', False
    from urllib.parse import urlparse
    try:
        host = (urlparse(url or '').hostname or '').strip()
    except Exception:
        return '', False
    if not host:
        return '', False
    verdict = _doh_verdict(host, proxy)
    if verdict == 'nxdomain':
        return (' | dns: NXDOMAIN even via DNS-over-HTTPS — the domain '
                'itself is dead (the local resolver was right)'), True
    if verdict == 'exists':
        return (' | dns: the name resolves via DNS-over-HTTPS — the '
                'local resolver is lying (poisoned DNS; the proxy route '
                'is the door that can still reach it)'), False
    return (' | dns: DNS-over-HTTPS itself unreachable — verdict '
            'unknown'), False


def _is_bare_hostname(netloc: str) -> bool:
    """True for a plain DNS host ('example.org') — no userinfo, no
    port, not an IP, not localhost: only these have a meaningful www
    variant."""
    nl = str(netloc or '').strip()
    if not nl or '@' in nl or ':' in nl:
        return False
    if nl.lower() == 'localhost' or '.' not in nl:
        return False
    try:
        import ipaddress
        ipaddress.ip_address(nl)
        return False  # an IP has no www form
    except ValueError:
        return True


def _url_variants(url: str) -> list:
    """v0.46.0 — up to two honest variants of a dead URL: the
    trailing-slash toggle and the www toggle — the two moves that
    rescue the common 'moved without a redirect' class. https stays
    https (never a scheme downgrade); an IP or localhost host has no
    www form."""
    from urllib.parse import urlsplit, urlunsplit
    try:
        parts = urlsplit(str(url or ''))
    except ValueError:
        return []
    if parts.scheme not in ('http', 'https'):
        return []
    out = []
    path = parts.path or ''
    if path.endswith('/') and len(path) > 1:
        variant_path = path[:-1]
        label = 'no-trailing-slash'
    else:
        variant_path = path + '/'
        label = 'trailing-slash'
    out.append((label, urlunsplit(
        (parts.scheme, parts.netloc, variant_path,
         parts.query, parts.fragment))))
    nl = parts.netloc
    if nl.lower().startswith('www.'):
        out.append(('non-www', urlunsplit(
            (parts.scheme, nl[4:], path, parts.query, parts.fragment))))
    elif _is_bare_hostname(nl):
        out.append(('www', urlunsplit(
            (parts.scheme, 'www.' + nl, path,
             parts.query, parts.fragment))))
    return [(label, u) for label, u in out if u and u != url]


def _archive_lookup(url: str, proxy: Optional[Dict], timeout_s: float,
                    started: float) -> Tuple[str, str]:
    """v0.46.0 — the Wayback Machine's availability answer for a dead
    page: ``('no-snapshot', '')`` when nothing is archived,
    ``('unreachable', '')`` when the archive itself could not be asked,
    or ``(snapshot_url, timestamp)`` when a capture exists. One polite
    probe, primary route, 64 KB cap. The module-level seam tests swap
    (``wf._archive_lookup``)."""
    from urllib.parse import quote
    probe_url = f"{_ARCHIVE_API}?url={quote(url, safe='')}"
    try:
        res = _fetch_once(probe_url, min(float(timeout_s), 15.0),
                          _ARCHIVE_MAX_BYTES, USER_AGENT, proxy, started)
    except Exception:
        return 'unreachable', ''
    if not res.ok:
        return 'unreachable', ''
    try:
        data = json.loads((res.text or '').strip() or '{}')
        closest = ((data.get('archived_snapshots') or {})
                   .get('closest') or {})
        if closest.get('available') and str(closest.get('url') or ''):
            return (str(closest['url']),
                    str(closest.get('timestamp') or ''))
    except Exception:
        pass
    return 'no-snapshot', ''


def _domain_of(url: str) -> str:
    from urllib.parse import urlparse
    try:
        return (urlparse(str(url or '')).netloc or '').lower()
    except Exception:
        return ''


def _wait_polite(rate_limiter, url_or_domain: str) -> None:
    """The ladder's every rung is polite: waiting on the domain before
    a variant ask, the archive probe, or the snapshot fetch."""
    if rate_limiter is None:
        return
    try:
        rate_limiter.wait(_domain_of(url_or_domain) or str(url_or_domain))
    except Exception:
        pass  # politeness must never break the fetch


def _dead_page_rescue(url: str, dead: FetchResult, timeout_s: float,
                      max_bytes: int, user_agent: str,
                      proxy: Optional[Dict], rate_limiter,
                      archive_fallback: bool,
                      started: float) -> Tuple[Optional[FetchResult], list, str]:
    """v0.46.0 — a 404/410 verdict climbs its rescue ladder before it
    is final: URL variants (trailing slash, www — the 'moved without a
    redirect' class), then the Wayback Machine's archived copy. A
    rescue is a REAL result (full from a variant, partial 'archived'
    from the Wayback capture) with the story in the reason. Returns
    (rescue|None, tried_labels, archive_verdict); loopback NEVER gets
    the ladder (the v0.15.1 rule: no door escalates off-machine)."""
    if _is_loopback_url(url):
        return None, [], 'skipped'
    tried = []
    for label, variant in _url_variants(url):
        _wait_polite(rate_limiter, variant)
        res = _fetch_once(variant, timeout_s, max_bytes, user_agent,
                          proxy, started)
        tried.append(label)
        if res.ok:
            res.reason = (f"the original URL answered HTTP "
                          f"{dead.http_status} — the {label} variant "
                          "answered; rescued")
            return res, tried, 'skipped'
    archive_verdict = 'skipped'
    if archive_fallback:
        _wait_polite(rate_limiter, 'archive.org')
        archive_verdict, timestamp = _archive_lookup(
            url, proxy, timeout_s, started)
        if archive_verdict not in ('no-snapshot', 'unreachable'):
            snapshot_url, ts = archive_verdict, timestamp
            _wait_polite(rate_limiter, 'web.archive.org')
            page = _fetch_once(snapshot_url, timeout_s, max_bytes,
                               user_agent, proxy, started)
            if page.ok:
                # An archived copy is real content, but the LIVE page
                # is gone: partial + category 'archived' — the pipeline
                # files it in _review with the story (the owner decides
                # where a dead link's snapshot belongs).
                page.status = 'partial'
                page.category = 'archived'
                page.reason = (
                    f"archived copy — the live page answered HTTP "
                    f"{dead.http_status}; the Wayback Machine's capture"
                    + (f" ({ts})" if ts else '')
                    + " served this")
                return page, tried, 'served'
    return None, tried, archive_verdict


def _rate_limit_penalty(*results) -> Optional[float]:
    """v0.46.0 — the polite circuit breaker's price: when any leg of
    the final failure answered 429/503, the domain's next hit waits
    the site's Retry-After (or a 60s default), capped at five minutes.
    None when no rate-limit answer was seen."""
    for res in results:
        if res is not None and getattr(res, 'http_status', None) in (429, 503):
            ra = getattr(res, 'retry_after_s', None)
            return min(float(ra) if ra is not None else
                       _RATE_PENALTY_DEFAULT_S, _RATE_PENALTY_CAP_S)
    return None


def _combined_category(*results) -> str:
    """v0.46.0 — the most informative leg's failure class wins (a 404
    truth beats the 403 wall that hid it; a paywall beats a timeout)."""
    cats = set()
    for res in results:
        if res is not None and getattr(res, 'category', ''):
            cats.add(res.category)
    for cat in _CATEGORY_PRECEDENCE:
        if cat in cats:
            return cat
    return ''


def _maybe_impersonate(url: str, timeout_s: float, max_bytes: int,
                       routes: list, enabled: bool,
                       walled: FetchResult,
                       started: float) -> FetchResult:
    """v0.45.0 — the third door's own law.

    ``routes`` is the door sequence the CALLER allows (fetch_url
    computes it: ``[proxy, None]`` when both stdlib doors ran,
    ``[proxy]`` when the direct opt-out held, ``[None]`` on a no-proxy
    install) — the third door never widens a route the owner opted out
    of. Fires when every stdlib door is walled with a refusal-family
    answer (403/405/429/451 — the wall aimed at the REQUESTER; the
    owner's exact "proxy: HTTP 403 | direct: HTTP 403 — bot defense
    (Cloudflare: challenge)" class). v0.46.0 — a TLS-handshake refusal
    (SSLV3_ALERT_HANDSHAKE_FAILURE, handshake timed out,
    UNEXPECTED_EOF — the owner's "proxy: connection: [SSL: …]" lines)
    opens the door too: it is just as much a fingerprint verdict. A
    plain timeout or DNS failure stays a network truth (no verdict to
    answer); resource truths never reach here; loopback never gets ANY
    door's escalation. One impersonated attempt per allowed route.
    Success via the third door is a REAL success with the wall named; a
    challenge that survives the Chrome handshake gets the three-leg
    honest line (plus the "needs a live browser" verdict — the owner's
    graveyard decision is then informed). Missing curl_cffi → the
    install hint, never an error."""
    if not enabled or _is_loopback_url(url):
        return walled
    if walled.http_status is not None:
        if walled.http_status not in ROUTE_REFUSAL_STATUSES:
            return walled  # the site answered about the RESOURCE
    elif not _is_tls_handshake_failure(walled.reason):
        return walled  # connection-class truth — not a fingerprint verdict
    if not curl_cffi_available():
        walled.reason = (walled.reason or '') + _IMP_INSTALL_HINT
        return walled
    tried = []
    for route in routes:
        res = _impersonated_fetch_once(url, timeout_s, max_bytes, route,
                                       started)
        if res.ok:
            res.reason = (f"via impersonated Chrome (curl_cffi) — the "
                          f"stdlib doors were walled: {walled.reason}")
            return res
        tried.append(res.reason)
    combined = ' / '.join(dict.fromkeys([t for t in tried if t]))
    walled.reason = (f"{walled.reason} | chrome-impersonated: {combined}")
    if 'challenge' in combined.lower():
        walled.reason += _CHALLENGE_WON_HINT
    return walled


def _final_verdict(result: FetchResult, direct_reason: str, url: str,
                   proxy: Optional[Dict], rate_limiter, domain: str,
                   doh_probe: bool, legs: tuple) -> FetchResult:
    """v0.46.0 — the rungs every final failure answer shares: the DNS
    verdict (when the DIRECT line could not resolve the name,
    DNS-over-HTTPS names whose fault it is — a poisoned local resolver
    vs. a dead domain) and the polite circuit breaker's price (a
    429/503 leg pays its Retry-After into the domain limiter, capped).
    Mutates and returns ``result``."""
    suffix, dead = _dns_verdict_suffix(direct_reason, url, proxy, doh_probe)
    if suffix:
        result.reason = (result.reason or '') + suffix
        if dead:
            result.category = 'dead'
    try:
        penalty = _rate_limit_penalty(*legs)
        if penalty is not None and rate_limiter is not None:
            rate_limiter.penalize(domain, penalty)
    except Exception:
        pass  # politeness bookkeeping never breaks the verdict
    return result


def fetch_url(url: str,
              timeout_s: float = DEFAULT_TIMEOUT_S,
              max_bytes: int = DEFAULT_MAX_BYTES,
              rate_limiter: Optional[DomainRateLimiter] = None,
              user_agent: str = USER_AGENT,
              proxy: Optional[Dict] = None,
              direct_fallback: bool = True,
              impersonate_fallback: bool = True,
              archive_fallback: bool = True,
              doh_probe: bool = True) -> FetchResult:
    """Fetch one URL politely. Never raises — every failure is a
    FetchResult(status='failed', reason=..., category=...).

    v0.19.0 ``proxy``: force the fetch through the configured proxy
    (``proxy_from_config`` shape). Loopback URLs NEVER ride a proxy (the
    v0.15.1 rule — enforced here, ahead of the opener). A proxy-specific
    failure (PySocks missing, proxy down) is a normal failed FetchResult
    whose reason names the proxy, so the batch keeps going and the
    _review note tells the owner exactly what to fix.

    v0.21.0 ``direct_fallback``: when a PROXIED fetch fails without ever
    getting an HTTP answer (TLS reset at the exit — the owner's
    gist.github.com / huggingface.co class of failures, where the proxy
    exit IP is blocked by the site's CDN — or a timeout, or the proxy
    dying mid-batch), the SAME URL is retried once DIRECT. Sites the exit
    cannot reach but the local line can (gist.github.com) succeed; sites
    blocked on both lines fail with BOTH reasons in the result. Loopback
    is never proxied, so it never falls back either.

    v0.43.0 — BOTH DOORS: a route-aimed refusal (403/405/429/451 —
    ``ROUTE_REFUSAL_STATUSES``; the wall the owner kept meeting after
    v0.41.0 was aimed at the proxy exit's datacenter IP, and the direct
    residential line was never asked) alternates to the other route
    exactly like a connection-class failure. HTTP errors OUTSIDE the
    refusal family (404/401/410/5xx) still mean the site ANSWERED about
    the resource — no fallback, the response is the truth. A success via
    the second route is a real success (reason names the wall it went
    around); a refusal on BOTH routes reports both reasons in the
    ``proxy: … | direct: …`` house line.

    v0.45.0 — THE THIRD DOOR: when every stdlib door is walled with a
    refusal-family answer (the "proxy: HTTP 403 | direct: HTTP 403 —
    bot defense (Cloudflare: challenge)" class), the wall is aimed at
    the requester's TLS fingerprint — something urllib can never
    present. With curl_cffi importable, the URL is re-asked with
    Chrome's own handshake (one attempt per route, ``socks5h`` DNS at
    the proxy), and a success there is a REAL success with the wall
    named. ``impersonate_fallback=False`` (config
    ``web_impersonate_fallback``) opts out. Missing library → the
    honest reason gains the install hint.

    v0.46.0 — THE LADDER: a TLS-handshake-class wall opens the third
    door just like a refusal-family answer; a dead page (404/410)
    climbs its rescue ladder — URL variants (trailing slash, www),
    then the Wayback Machine's archived copy (``archive_fallback=False``
    opts out; a rescue is a partial 'archived' result with the story);
    a direct-line DNS failure gets a DNS-over-HTTPS verdict
    (``doh_probe=False`` opts out; NXDOMAIN → category 'dead'); a
    429/503 leg pays its Retry-After into the domain limiter; and every
    failure carries its ``category`` so the pipeline retries only what
    time can heal."""
    started = time.monotonic()
    try:
        from urllib.parse import urlparse
        domain = (urlparse(url or '').netloc or '').lower()
    except Exception:
        domain = ''
    if rate_limiter:
        try:
            rate_limiter.wait(domain)
        except Exception:
            pass  # politeness must never break the fetch

    if not url or not url.lower().startswith(('http://', 'https://')):
        return FetchResult(url, status='failed',
                           reason='not an http(s) URL',
                           category='bad_url',
                           elapsed_s=time.monotonic() - started)

    first = _fetch_once(url, timeout_s, max_bytes, user_agent, proxy,
                        started)
    if first.ok:
        return first
    if (first.http_status is not None
            and first.http_status not in ROUTE_REFUSAL_STATUSES):
        # The site answered about the RESOURCE (404 gone, 401 auth,
        # 5xx broken) — the answer is the truth on any route.
        if first.http_status in (404, 410) and not _is_loopback_url(url):
            # v0.46.0 — the dead-page rescue ladder: variants, then the
            # Wayback Machine; the verdict only lands when nothing
            # rescued the link.
            rescue, tried, archive = _dead_page_rescue(
                url, first, timeout_s, max_bytes, user_agent, proxy,
                rate_limiter, archive_fallback, started)
            if rescue is not None:
                return rescue
            extra = []
            if tried:
                extra.append('variants tried: ' + ', '.join(tried))
            if archive == 'no-snapshot':
                extra.append('no archived copy exists on the Wayback '
                              'Machine')
            elif archive == 'unreachable':
                extra.append('the Wayback Machine itself could not be '
                              'asked')
            if extra:
                first.reason = (f"{first.reason} ({'; '.join(extra)})")
            # the ladder found nothing — the dead verdict is final
            first.category = 'dead'
        return _final_verdict(first, '', url, proxy, rate_limiter,
                              domain, doh_probe, (first,))
    if not proxy or not direct_fallback or _is_loopback_url(url):
        # v0.45.0 — the single-door walled case still earns the third
        # door (the owner's challenge line names both routes, but a
        # no-proxy install walls on its only route just the same). The
        # route list honors the direct opt-out: proxy-only when
        # direct_fallback is off, direct-only when no proxy exists.
        routes = [proxy] if (proxy and not direct_fallback) else [None]
        direct_reason = '' if proxy else (first.reason or '')
        walled = _maybe_impersonate(url, timeout_s, max_bytes, routes,
                                    impersonate_fallback, first, started)
        return _final_verdict(walled, direct_reason, url, proxy,
                              rate_limiter, domain, doh_probe, (first,))

    # v0.43.0 — the both-doors rule. The primary (proxied) route either
    # never got an answer (connection class, v0.21.0) or was REFUSED
    # with a route-aimed status (403/405/429/451 — the wall aimed at
    # THIS route's IP, not an answer about the page). Either way the
    # OTHER door gets its one attempt: DIRECT.
    second = _fetch_once(url, timeout_s, max_bytes, user_agent, None,
                         started)
    if second.ok:
        second.reason = (f"via direct fallback (proxy path failed: "
                         f"{first.reason})")
        return second
    direct_reason = second.reason or ''
    second.reason = (f"proxy: {first.reason} | direct: {direct_reason}")
    second.category = _combined_category(first, second)
    # v0.45.0 — both stdlib doors walled: ask the Chrome handshake on
    # the same two routes (one polite attempt each).
    walled = _maybe_impersonate(url, timeout_s, max_bytes, [proxy, None],
                                impersonate_fallback, second, started)
    return _final_verdict(walled, direct_reason, url, proxy,
                          rate_limiter, domain, doh_probe, (first, second))


def _fetch_once(url: str, timeout_s: float, max_bytes: int,
                user_agent: str, proxy: Optional[Dict],
                started: float) -> FetchResult:
    """One fetch attempt (the pre-v0.21.0 fetch_url body). Never raises;
    ``started`` is the outer monotonic clock so elapsed covers fallbacks.
    v0.41.0 — the request is browser-grade (Chrome's header set, gzip
    accepted and decoded); see ``_request_headers`` and
    ``_decompress_capped``.
    v0.46.0 — redirects are followed by ``_open_following`` (hop cookies,
    308, loop detection, honest broken-redirect verdicts) and every
    failure carries its CATEGORY via ``_http_error_verdict``."""
    headers = _request_headers(user_agent)

    # Public websites: certificates are verified. A bad cert is a failed
    # fetch (recorded, retried, reported) — not silently accepted.
    ctx = ssl.create_default_context()
    try:
        # Loopback → never ANY proxy (v0.15.1 rule); else the configured
        # proxy when one is set. PySocks missing → a clear failed reason.
        opener = _build_opener(
            proxy, ctx, force_direct=_is_loopback_url(url))
    except WebProxyError as e:
        return FetchResult(url, status='failed', reason=str(e),
                           category='proxy_error',
                           elapsed_s=time.monotonic() - started)

    try:
        resp, final_url = _open_following(opener, url, headers, timeout_s)
    except _RedirectBrokenError as e:
        return FetchResult(url, status='failed', reason=str(e),
                           http_status=e.code,
                           category='redirect_broken',
                           elapsed_s=time.monotonic() - started)
    except urllib.error.HTTPError as e:
        # v0.46.0 — every named family gets its reason and category from
        # one place; the 403/429 bot-defense line keeps v0.41.0's format.
        reason, category, retry_after = _http_error_verdict(e.code, e.headers)
        try:
            e.close()
        except Exception:
            pass
        return FetchResult(url, status='failed', reason=reason,
                           http_status=e.code, category=category,
                           retry_after_s=retry_after,
                           elapsed_s=time.monotonic() - started)
    except urllib.error.URLError as e:
        reason = getattr(e, 'reason', None) or str(e)
        if isinstance(reason, WebProxyError):
            reason = str(reason)
        return FetchResult(url, status='failed', reason=f'connection: {reason}',
                           category='proxy_error' if isinstance(
                               getattr(e, 'reason', None), WebProxyError)
                           else 'retry_later',
                           elapsed_s=time.monotonic() - started)
    except WebProxyError as e:
        return FetchResult(url, status='failed',
                           reason=f'proxy: {e}',
                           category='proxy_error',
                           elapsed_s=time.monotonic() - started)
    except socket.timeout:
        return FetchResult(url, status='failed', reason='timeout',
                           category='retry_later',
                           elapsed_s=time.monotonic() - started)
    except Exception as e:  # never let one link break the batch
        return FetchResult(url, status='failed',
                           reason=f'{type(e).__name__}: {e}',
                           category='retry_later',
                           elapsed_s=time.monotonic() - started)

    try:
        with resp:
            content_type = (resp.headers.get('Content-Type') or '').lower()
            # v0.41.0 — we ask for gzip/deflate; record what actually
            # came back so the body can be decoded below.
            content_encoding = (resp.headers.get('Content-Encoding')
                                or '').strip().lower()
            charset = ''
            for part in content_type.split(';'):
                part = part.strip()
                if part.startswith('charset='):
                    charset = part.split('=', 1)[1].strip().strip('"\'')
            http_status = getattr(resp, 'status', None) or resp.getcode()

            # Stream with the size cap — never buffer a "huge page" whole.
            chunks = []
            total = 0
            truncated = False
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    chunks.append(chunk[:max_bytes - (total - len(chunk))])
                    truncated = True
                    break
                chunks.append(chunk)
            body = b''.join(chunks)
    except Exception as e:  # never let one link break the batch
        return FetchResult(url, status='failed',
                           reason=f'{type(e).__name__}: {e}',
                           category='retry_later',
                           elapsed_s=time.monotonic() - started)

    elapsed = time.monotonic() - started

    # v0.41.0 — decode the compressed body (we ask for gzip/deflate like
    # a browser, so a compressed answer is the NORM on real sites). The
    # cap bounds the DECOMPRESSED size too: a "gzip bomb" stops at
    # max_bytes. A stream cut at the WIRE cap is undecodable by
    # construction — the size cap IS the honest reason. An undecodable
    # whole body is a failed fetch, never an exception.
    if content_encoding and content_encoding != 'identity':
        if truncated:
            return FetchResult(url, final_url, status='partial',
                               reason=f'size cap ({max_bytes:,} bytes) '
                                       'reached',
                               http_status=http_status,
                               content_type=content_type,
                               charset=charset, body=b'', text='',
                               elapsed_s=elapsed)
        try:
            body, dec_truncated = _decompress_capped(
                body, content_encoding, max_bytes)
            truncated = truncated or dec_truncated
        except Exception as e:
            return FetchResult(url, status='failed',
                               reason=f'{content_encoding} decode failed: '
                                      f'{type(e).__name__}',
                               http_status=http_status,
                               content_type=content_type,
                               elapsed_s=elapsed)

    if _looks_like_pdf(content_type, url, body):
        return FetchResult(url, final_url, status='partial', reason='pdf',
                           http_status=http_status, content_type=content_type,
                           charset=charset, body=b'', text='',
                           elapsed_s=elapsed)

    text = _decode_best_effort(body, charset)

    if truncated:
        return FetchResult(url, final_url, status='partial',
                           reason=f'size cap ({max_bytes:,} bytes) reached',
                           http_status=http_status, content_type=content_type,
                           charset=charset, body=body, text=text,
                           elapsed_s=elapsed)

    return FetchResult(url, final_url, status='full', reason='',
                       http_status=http_status, content_type=content_type,
                       charset=charset, body=body, text=text,
                       elapsed_s=elapsed)


def _decode_best_effort(body: bytes, charset: str = '') -> str:
    """Decode with the header charset, else UTF-8 with replacement (a
    windows-1252 page decoded as UTF-8 keeps its text with a few
    replacement chars — web_extract re-decodes from ``body`` when the
    page's own <meta charset> disagrees with the header)."""
    for cs in (charset or '', 'utf-8'):
        if not cs:
            continue
        try:
            return body.decode(cs)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode('utf-8', errors='replace')
