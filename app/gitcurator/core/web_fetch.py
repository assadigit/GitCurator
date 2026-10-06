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
import io
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
import zlib
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

# Content types that are HTML-ish enough to parse.
_HTML_TYPES = ('text/html', 'application/xhtml', 'text/plain')


class FetchResult:
    """One fetch attempt. ``body`` is raw bytes (capped); ``text`` is the
    best-effort decode (charset from headers; web_extract re-decodes with
    the page's own <meta charset> when the header lies)."""

    __slots__ = ('url', 'final_url', 'status', 'reason', 'http_status',
                 'content_type', 'charset', 'body', 'text', 'elapsed_s')

    def __init__(self, url, final_url='', status='failed', reason='',
                 http_status=None, content_type='', charset='',
                 body=b'', text='', elapsed_s=0.0):
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

    def wait(self, domain: str) -> None:
        """Block (sleep) until hitting ``domain`` is polite again."""
        if not domain or self.delay_s <= 0:
            return
        while True:
            with self._lock:
                now = time.monotonic()
                last = self._last_hit.get(domain, 0.0)
                due = last + self.delay_s
                if now >= due:
                    self._last_hit[domain] = now
                    return
                sleep_for = due - now
            time.sleep(min(sleep_for, self.delay_s))


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
    """
    handlers = [_RedirectCap, urllib.request.HTTPSHandler(context=context)]
    if force_direct:
        handlers.insert(0, urllib.request.ProxyHandler({}))
    elif proxy:
        handlers.insert(0, _proxy_handler_for(proxy))
    return urllib.request.build_opener(*handlers)


# ---------------------------------------------------------------------------
# The fetcher
# ---------------------------------------------------------------------------

class _RedirectCap(urllib.request.HTTPRedirectHandler):
    """Counts redirects and refuses beyond MAX_REDIRECTS (default urllib
    behavior is 10; a link that wanders 6+ hops is a redirect loop or a
    tracker chute — failed, not followed)."""

    max_redirections = MAX_REDIRECTS


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


def fetch_url(url: str,
              timeout_s: float = DEFAULT_TIMEOUT_S,
              max_bytes: int = DEFAULT_MAX_BYTES,
              rate_limiter: Optional[DomainRateLimiter] = None,
              user_agent: str = USER_AGENT,
              proxy: Optional[Dict] = None,
              direct_fallback: bool = True) -> FetchResult:
    """Fetch one URL politely. Never raises — every failure is a
    FetchResult(status='failed', reason=...).

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
    blocked on both lines fail with BOTH reasons in the result. HTTP
    errors (4xx/5xx) mean the site ANSWERED — no fallback, the response
    is the truth. Loopback is never proxied, so it never falls back
    either."""
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
                           elapsed_s=time.monotonic() - started)

    first = _fetch_once(url, timeout_s, max_bytes, user_agent, proxy,
                        started)
    if first.ok or first.http_status is not None:
        # Success, or the site itself answered (HTTP error) — done.
        return first
    if not proxy or not direct_fallback or _is_loopback_url(url):
        return first

    # Connection-class failure through the proxy: one DIRECT attempt.
    second = _fetch_once(url, timeout_s, max_bytes, user_agent, None,
                         started)
    if second.ok:
        second.reason = (f"via direct fallback (proxy path failed: "
                         f"{first.reason})")
        return second
    second.reason = (f"proxy: {first.reason} | direct: {second.reason}")
    return second


def _fetch_once(url: str, timeout_s: float, max_bytes: int,
                user_agent: str, proxy: Optional[Dict],
                started: float) -> FetchResult:
    """One fetch attempt (the pre-v0.21.0 fetch_url body). Never raises;
    ``started`` is the outer monotonic clock so elapsed covers fallbacks.
    v0.41.0 — the request is browser-grade (Chrome's header set, gzip
    accepted and decoded); see ``_request_headers`` and
    ``_decompress_capped``."""
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
                           elapsed_s=time.monotonic() - started)

    req = urllib.request.Request(url, headers=headers)
    try:
        with opener.open(req, timeout=float(timeout_s)) as resp:
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
            final_url = resp.geturl() or url
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
    except urllib.error.HTTPError as e:
        reason = f'HTTP {e.code}'
        if e.code in (403, 429):
            # v0.41.0 — name the bot wall when one is visible: a
            # "working site" that 403s a polite fetcher is almost always
            # a challenge, and the _review note should say so.
            hint = _bot_defense_hint(e.headers)
            if hint:
                reason = f'HTTP {e.code} — {hint}'
        return FetchResult(url, status='failed', reason=reason,
                           http_status=e.code,
                           elapsed_s=time.monotonic() - started)
    except urllib.error.URLError as e:
        reason = getattr(e, 'reason', None) or str(e)
        if isinstance(reason, WebProxyError):
            reason = str(reason)
        return FetchResult(url, status='failed', reason=f'connection: {reason}',
                           elapsed_s=time.monotonic() - started)
    except WebProxyError as e:
        return FetchResult(url, status='failed',
                           reason=f'proxy: {e}',
                           elapsed_s=time.monotonic() - started)
    except socket.timeout:
        return FetchResult(url, status='failed', reason='timeout',
                           elapsed_s=time.monotonic() - started)
    except Exception as e:  # never let one link break the batch
        return FetchResult(url, status='failed',
                           reason=f'{type(e).__name__}: {e}',
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
