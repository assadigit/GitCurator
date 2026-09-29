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
    - a clear User-Agent identifying the app (not a browser impersonation)
    - per-domain rate limiting (``DomainRateLimiter``; shared instance so
      the whole batch pauses between hits on the same host)
    - connect+read timeout on every request
    - a hard byte cap (reads stream in chunks and stop at the cap)

Pure stdlib, no PyQt.
"""

import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
from typing import Dict, Optional

# ===========================================================================
# CONFIGURATION (safe to edit)
# ===========================================================================
USER_AGENT = ("GitCurator/0.11 (+personal website library builder; "
              "polite fetcher; https://github.com/assadigit/GitCurator)")

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
# The fetcher
# ---------------------------------------------------------------------------

class _RedirectCap(urllib.request.HTTPRedirectHandler):
    """Counts redirects and refuses beyond MAX_REDIRECTS (default urllib
    behavior is 10; a link that wanders 6+ hops is a redirect loop or a
    tracker chute — failed, not followed)."""

    max_redirections = MAX_REDIRECTS


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
              user_agent: str = USER_AGENT) -> FetchResult:
    """Fetch one URL politely. Never raises — every failure is a
    FetchResult(status='failed', reason=...)."""
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

    headers = {'User-Agent': user_agent,
               'Accept': 'text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5',
               'Accept-Language': 'en'}

    # Public websites: certificates are verified. A bad cert is a failed
    # fetch (recorded, retried, reported) — not silently accepted.
    ctx = ssl.create_default_context()
    opener = urllib.request.build_opener(
        _RedirectCap, urllib.request.HTTPSHandler(context=ctx))

    req = urllib.request.Request(url, headers=headers)
    try:
        with opener.open(req, timeout=float(timeout_s)) as resp:
            content_type = (resp.headers.get('Content-Type') or '').lower()
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
        return FetchResult(url, status='failed', reason=f'HTTP {e.code}',
                           http_status=e.code,
                           elapsed_s=time.monotonic() - started)
    except urllib.error.URLError as e:
        reason = getattr(e, 'reason', None) or str(e)
        return FetchResult(url, status='failed', reason=f'connection: {reason}',
                           elapsed_s=time.monotonic() - started)
    except socket.timeout:
        return FetchResult(url, status='failed', reason='timeout',
                           elapsed_s=time.monotonic() - started)
    except Exception as e:  # never let one link break the batch
        return FetchResult(url, status='failed',
                           reason=f'{type(e).__name__}: {e}',
                           elapsed_s=time.monotonic() - started)

    elapsed = time.monotonic() - started

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
