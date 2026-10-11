"""tests/test_ladder.py — v0.46.0, the ladder: every failure class gets
its honest door.

The owner's fresh failure pile (session): "Fetch failed: HTTP 307",
"proxy: connection: [SSL: SSLV3_ALERT_HANDSHAKE_FAILURE] … | direct:
connection: [Errno 11001] getaddrinfo failed", "HTTP 404", "proxy:
HTTP 429 | direct: HTTP 429", "HTTP 523", "proxy: HTTP 405 | direct:
HTTP 405", "HTTP 402", "HTTP 301" — each maps to a root cause and to
the door this release opens for it.

Covered here (unit law: hand-built FetchResults, patched seams, zero
sockets — plus REAL local http.server cases for the redirect loop,
which is the one law that needs a live wire to prove):

* the redirect truth — hops carry their cookies (the "lost a cookie
  during it" login-loop class), 308 is followed, a redirect with no
  Location header is an honest broken-redirect failure (never a bare
  "HTTP 307"), loops are named, the hop budget holds; Referer joins
  hops the way a real browser sends it (full URL same-host, origin
  only cross-host).
* the HTTP verdicts — 402/401/405/521-524/429/503 get their names,
  their categories, and Retry-After (seconds AND HTTP-date) is read.
* the third door's new trigger — a TLS-handshake-class wall opens it
  (the owner's exact SSL line), a plain timeout still never does.
* the DNS verdict — getaddrinfo failures get the DNS-over-HTTPS answer
  (nxdomain → dead; exists → the local resolver is lying; unknown → no
  false accusation), cached per host, opt-out honored.
* the dead-page rescue ladder — 404/410 climb the variants (trailing
  slash, www) then the Wayback Machine; a rescue is a real result with
  the story, nothing rescued is a category-'dead' verdict; loopback
  never gets the ladder; the archive opt-out is honored.
* the polite circuit breaker — a 429/503 leg pays its Retry-After (or
  the 60s default, capped at five minutes) into the domain limiter.
* the pipeline's category gate — dead/paywalled/refused never requeue
  (the note is still written, the link is auto-dismissed with the
  verdict as its reason — the graveyard's gate, earned by the
  fetcher), heal-able failures keep their retries, and an archived
  rescue files under _review without a re-fetch loop.

No PyQt import at module level (the libEGL-less sandbox rule).
"""

import json
import os
import shutil
import tempfile
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

from gitcurator.core import dryrun
from gitcurator.core import web_fetch as wf
from gitcurator.core import website_pipeline as wp


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------

_PROXY = {'type': 'socks5', 'host': 'p.example.net', 'port': 10808,
          'username': '', 'password': ''}
_WALL_URL = 'http://walled.example.net/page'
_OWNER_TLS_LINE = ("proxy: connection: [SSL: SSLV3_ALERT_HANDSHAKE_FAILURE] "
                   "sslv3 alert handshake failure (_ssl.c:1010) | "
                   "direct: connection: [Errno 11001] getaddrinfo failed")


def _res(reason, http_status=None, category='', retry_after_s=None,
         url=_WALL_URL):
    return wf.FetchResult(url, status='failed', reason=reason,
                          http_status=http_status, category=category,
                          retry_after_s=retry_after_s)


def _ok(url, body=b'<html><body>the page</body></html>'):
    return wf.FetchResult(url, final_url=url, status='full',
                          http_status=200, content_type='text/html',
                          charset='utf-8', body=body,
                          text=body.decode('utf-8'))


class _FakeLimiter:
    """Records politeness asks and penalties; never sleeps."""

    def __init__(self):
        self.waits = []
        self.penalties = []

    def wait(self, domain):
        self.waits.append(domain)

    def penalize(self, domain, seconds):
        self.penalties.append((domain, seconds))


# ---------------------------------------------------------------------------
# 1. the redirect truth (REAL local server — the one law that needs a
#    live wire; loopback, so no door ever escalates off-machine)
# ---------------------------------------------------------------------------

class _LadderHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path == '/article':
            body = (b'<html><head><title>Practical Typography Guide'
                    b'</title></head><body>the article</body></html>')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == '/hop-cookie':
            # 307 that SETS the cookie the target demands — urllib's own
            # follower loses exactly this cookie (it re-opens from inside
            # its error handler, so the jar never sees the hop).
            self.send_response(307)
            self.send_header('Location', '/hop-cookie-target')
            self.send_header('Set-Cookie', 'gate=open; Path=/')
            self.end_headers()
        elif self.path == '/hop-cookie-target':
            if 'gate=open' in (self.headers.get('Cookie') or ''):
                body = b'<html><head><title>Behind the gate</title></head></html>'
                self.send_response(200)
                self.send_header('Content-Type',
                                  'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(403)
                self.send_header('Content-Length', '0')
                self.end_headers()
        elif self.path == '/no-location':
            # The owner's bare "HTTP 307": a redirect answer with no
            # Location header at all.
            self.send_response(307)
            self.end_headers()
        elif self.path == '/loop':
            self.send_response(302)
            self.send_header('Location', '/loop')
            self.end_headers()
        elif self.path.startswith('/chain/'):
            n = int(self.path.rsplit('/', 1)[1])
            self.send_response(302)
            self.send_header('Location', f'/chain/{n + 1}')
            self.end_headers()
        elif self.path == '/moved-308':
            self.send_response(308)
            self.send_header('Location', '/article')
            self.end_headers()
        else:
            self.send_response(404)
            self.send_header('Content-Length', '0')
            self.end_headers()

    def log_message(self, *args):
        pass


class TestRedirectTruth(unittest.TestCase):
    """The redirect law moved out of urllib into _fetch_once's own loop."""

    @classmethod
    def setUpClass(cls):
        cls._srv = HTTPServer(('127.0.0.1', 0), _LadderHandler)
        import threading
        cls._thread = threading.Thread(
            target=cls._srv.serve_forever, daemon=True)
        cls._thread.start()

    @classmethod
    def tearDownClass(cls):
        cls._srv.shutdown()
        cls._srv.server_close()

    def setUp(self):
        wf._COOKIE_JAR.clear()

    def _url(self, path):
        return f'http://127.0.0.1:{self._srv.server_port}{path}'

    def test_hop_cookies_ride_the_redirect(self):
        """The 'lost a cookie during it' class: the hop sets the cookie,
        the target serves ONLY with it — the loop must carry it across."""
        res = wf.fetch_url(self._url('/hop-cookie'), timeout_s=10)
        self.assertTrue(res.ok, res.reason)
        self.assertEqual(res.status, 'full')
        self.assertIn('Behind the gate', res.text)
        self.assertEqual(res.final_url, self._url('/hop-cookie-target'))

    def test_redirect_without_location_is_an_honest_broken_answer(self):
        """The owner's bare 'HTTP 307': named, categorized, never silent."""
        res = wf.fetch_url(self._url('/no-location'), timeout_s=10)
        self.assertFalse(res.ok)
        self.assertEqual(res.http_status, 307)
        self.assertEqual(res.category, 'redirect_broken')
        self.assertIn('no Location header', res.reason)
        self.assertIn('broken redirect', res.reason)

    def test_redirect_loop_is_named(self):
        res = wf.fetch_url(self._url('/loop'), timeout_s=10)
        self.assertFalse(res.ok)
        self.assertEqual(res.category, 'redirect_broken')
        self.assertIn('redirect loop', res.reason)

    def test_hop_budget_holds(self):
        """A wandering chain (a tracker chute) stops at MAX_REDIRECTS."""
        res = wf.fetch_url(self._url('/chain/1'), timeout_s=10)
        self.assertFalse(res.ok)
        self.assertEqual(res.category, 'redirect_broken')
        self.assertIn(f'more than {wf.MAX_REDIRECTS} redirects', res.reason)

    def test_308_is_followed(self):
        """urllib < 3.11 refuses 308; the house loop follows it."""
        res = wf.fetch_url(self._url('/moved-308'), timeout_s=10)
        self.assertTrue(res.ok, res.reason)
        self.assertEqual(res.status, 'full')
        self.assertIn('/article', res.final_url)

    def test_plain_fetch_still_full(self):
        res = wf.fetch_url(self._url('/article'), timeout_s=10)
        self.assertTrue(res.ok)
        self.assertEqual(res.status, 'full')


class TestHopHeaders(unittest.TestCase):
    """The Referer law: full URL on a same-host hop, origin only on a
    cross-host hop (Chrome's default — leaking the full URL cross-site
    is itself a bot tell)."""

    BASE = {'Accept': 'text/html', 'Accept-Encoding': 'gzip, deflate'}

    def test_same_host_hop_gets_the_full_url(self):
        h = wf._hop_headers(dict(self.BASE),
                            'https://example.org/a?q=1',
                            'https://example.org/b')
        self.assertEqual(h['Referer'], 'https://example.org/a?q=1')
        # Chrome's order: Referer right before Accept-Encoding
        keys = list(h.keys())
        self.assertLess(keys.index('Referer'), keys.index('Accept-Encoding'))

    def test_cross_host_hop_gets_origin_only(self):
        h = wf._hop_headers(dict(self.BASE),
                            'https://example.org/a?q=1',
                            'https://other.example.net/b')
        self.assertEqual(h['Referer'], 'https://example.org/')

    def test_bad_urls_get_no_referer(self):
        h = wf._hop_headers(dict(self.BASE), 'not a url', '')
        self.assertNotIn('Referer', h)


# ---------------------------------------------------------------------------
# 2. the HTTP verdicts (pure unit)
# ---------------------------------------------------------------------------

class _Headers(dict):
    """A case-insensitive header stand-in (HTTPMessage.get semantics)."""

    def __init__(self, **kw):
        super().__init__({k.replace('_', '-').lower(): v
                          for k, v in kw.items()})

    def get(self, key, default=None):
        return super().get(str(key).lower(), default)


class TestHttpVerdicts(unittest.TestCase):

    def test_402_is_paywalled(self):
        reason, cat, ra = wf._http_error_verdict(402, _Headers())
        self.assertEqual(cat, 'paywalled')
        self.assertIn('paywalled', reason)
        self.assertIn('payment required', reason)
        self.assertIsNone(ra)

    def test_401_is_private(self):
        reason, cat, _ = wf._http_error_verdict(401, _Headers())
        self.assertEqual(cat, 'refused')
        self.assertIn('private', reason)

    def test_405_is_method_refused(self):
        reason, cat, _ = wf._http_error_verdict(405, _Headers())
        self.assertEqual(cat, 'refused')
        self.assertIn('GET', reason)

    def test_523_names_cloudflare_origin_truth(self):
        for code in (521, 522, 523, 524):
            reason, cat, _ = wf._http_error_verdict(code, _Headers())
            self.assertEqual(cat, 'retry_later')
            self.assertIn('Cloudflare', reason)
            self.assertIn('site-side', reason)

    def test_429_with_retry_after_reads_it(self):
        reason, cat, ra = wf._http_error_verdict(
            429, _Headers(retry_after='120'))
        self.assertEqual(cat, 'retry_later')
        self.assertEqual(ra, 120.0)
        self.assertIn('Retry-After: 120s', reason)

    def test_429_without_retry_after_names_the_default(self):
        reason, cat, ra = wf._http_error_verdict(429, _Headers())
        self.assertEqual(cat, 'retry_later')
        self.assertIsNone(ra)
        self.assertIn('rate limited', reason)

    def test_429_with_bot_defense_hint_keeps_v041_format(self):
        reason, cat, ra = wf._http_error_verdict(
            429, _Headers(server='cloudflare'))
        self.assertEqual(cat, 'blocked_bot')
        self.assertEqual(reason, 'HTTP 429 — bot defense (server: cloudflare)')

    def test_403_with_bot_defense_hint_keeps_v041_format(self):
        reason, cat, _ = wf._http_error_verdict(
            403, _Headers(**{'cf_mitigated': 'challenge'}))
        self.assertEqual(cat, 'blocked_bot')
        self.assertEqual(
            reason, 'HTTP 403 — bot defense (Cloudflare: challenge)')

    def test_403_without_a_marker_names_the_refusal(self):
        reason, cat, _ = wf._http_error_verdict(403, _Headers(server='nginx'))
        self.assertEqual(cat, 'blocked_bot')
        self.assertIn('forbidden', reason)

    def test_503_service_unavailable_with_retry_after(self):
        reason, cat, ra = wf._http_error_verdict(
            503, _Headers(retry_after='30'))
        self.assertEqual(cat, 'retry_later')
        self.assertEqual(ra, 30.0)
        self.assertIn('service unavailable', reason)

    def test_404_is_dead(self):
        reason, cat, _ = wf._http_error_verdict(404, _Headers())
        self.assertEqual(cat, 'dead')
        self.assertIn('gone', reason)

    def test_500_is_retry_later_and_406_is_refused(self):
        self.assertEqual(wf._http_error_verdict(500, _Headers())[1],
                         'retry_later')
        self.assertEqual(wf._http_error_verdict(406, _Headers())[1],
                         'refused')

    def test_parse_retry_after_seconds_and_http_date(self):
        from datetime import datetime, timedelta, timezone
        self.assertEqual(wf._parse_retry_after(_Headers(retry_after='7')),
                         7.0)
        when = (datetime.now(timezone.utc)
                + timedelta(seconds=90)).strftime('%a, %d %b %Y %H:%M:%S GMT')
        ra = wf._parse_retry_after(_Headers(retry_after=when))
        self.assertIsNotNone(ra)
        self.assertGreater(ra, 60.0)
        self.assertLess(ra, 120.0)

    def test_parse_retry_after_garbage_is_none(self):
        self.assertIsNone(wf._parse_retry_after(_Headers(retry_after='soon')))
        self.assertIsNone(wf._parse_retry_after(None))
        self.assertIsNone(
            wf._parse_retry_after(_Headers(retry_after='')))


# ---------------------------------------------------------------------------
# 3. the third door's new trigger (the owner's TLS-wall line)
# ---------------------------------------------------------------------------

class _FakeImpResponse:
    def __init__(self, status_code=200,
                 body=b'<html><body>fix live</body></html>',
                 headers=None, url='', charset='utf-8'):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {'Content-Type':
                                   'text/html; charset=utf-8'}
        self.url = url
        self.charset_encoding = charset

    def iter_content(self):
        yield self._body

    def close(self):
        pass


class _FakeImpSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append({'url': url, **kwargs})
        r = self.responses.pop(0) if self.responses \
            else _FakeImpResponse(status_code=403, body=b'')
        return r


class TestThirdDoorTlsTrigger(unittest.TestCase):
    """v0.45 fired curl_cffi only on refusal-family HTTP answers; the
    owner's 'proxy: [SSL: SSLV3_ALERT_HANDSHAKE_FAILURE] …' line is a
    fingerprint verdict too — the door now opens for it."""

    def setUp(self):
        self._probe = dict(wf._CURL_CFFI_STATE)
        wf._CURL_CFFI_STATE['tried'] = True
        wf._CURL_CFFI_STATE['ok'] = True
        self._factory = wf._new_impersonation_session
        self._session_global = wf._IMP_SESSION
        wf._IMP_SESSION = None
        self._doh = wf._doh_probe_once
        wf._doh_probe_once = lambda host, proxy: 'unknown'
        wf._DOH_CACHE.clear()
        self.sessions = []

    def tearDown(self):
        wf._CURL_CFFI_STATE.clear()
        wf._CURL_CFFI_STATE.update(self._probe)
        wf._new_impersonation_session = self._factory
        wf._IMP_SESSION = self._session_global
        wf._doh_probe_once = self._doh
        wf._DOH_CACHE.clear()

    def arm_fake_session(self, *responses):
        def factory():
            s = _FakeImpSession(responses)
            self.sessions.append(s)
            return s

        wf._IMP_SESSION = None
        wf._new_impersonation_session = factory

    def test_the_owners_tls_line_opens_the_third_door(self):
        """proxy: TLS handshake refused | direct: DNS dead — both stdlib
        doors walled, the Chrome handshake answers."""
        self.arm_fake_session(_FakeImpResponse(status_code=200))
        calls = []

        def _fake(url, timeout_s, max_bytes, user_agent, proxy, started):
            calls.append(proxy)
            if proxy is not None:
                return _res('connection: [SSL: SSLV3_ALERT_HANDSHAKE_FAILURE'
                            '] sslv3 alert handshake failure (_ssl.c:1010)')
            return _res('connection: [Errno 11001] getaddrinfo failed')

        with mock.patch.object(wf, '_fetch_once', side_effect=_fake):
            res = wf.fetch_url(_WALL_URL, timeout_s=2,
                               proxy=dict(_PROXY))
        self.assertTrue(res.ok, res.reason)
        self.assertEqual(res.status, 'full')
        self.assertIn('via impersonated Chrome', res.reason)
        self.assertIn('SSLV3_ALERT_HANDSHAKE_FAILURE', res.reason)
        # the DNS verdict landed too (the direct leg's getaddrinfo)
        self.assertIn('verdict unknown', res.reason)
        # both stdlib routes ran before the third door asked
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(self.sessions), 1)

    def test_plain_timeout_still_never_asks_chrome(self):
        self.arm_fake_session(_FakeImpResponse(status_code=200))

        def _fake(url, timeout_s, max_bytes, user_agent, proxy, started):
            return _res('timeout', None, 'retry_later')

        with mock.patch.object(wf, '_fetch_once', side_effect=_fake):
            res = wf.fetch_url(_WALL_URL, timeout_s=2, proxy=dict(_PROXY))
        self.assertFalse(res.ok)
        self.assertEqual(res.category, 'retry_later')
        self.assertEqual(len(self.sessions), 0)

    def test_single_door_tls_wall_opens_the_door_direct(self):
        """A no-proxy install walls on its only route just the same."""
        self.arm_fake_session(_FakeImpResponse(status_code=200))

        def _fake(url, timeout_s, max_bytes, user_agent, proxy, started):
            return _res('connection: [SSL: UNEXPECTED_EOF_WHILE_READING] '
                        'EOF occurred in violation of protocol '
                        '(_ssl.c:1010)')

        with mock.patch.object(wf, '_fetch_once', side_effect=_fake):
            res = wf.fetch_url(_WALL_URL, timeout_s=2)
        self.assertTrue(res.ok, res.reason)
        call = self.sessions[0].calls[0]
        self.assertEqual(call['url'], _WALL_URL)
        self.assertFalse(call.get('proxies'))

    def test_certificate_failures_do_not_open_the_door(self):
        """A bad cert is the site's problem, not a wall to talk down."""
        self.arm_fake_session(_FakeImpResponse(status_code=200))

        def _fake(url, timeout_s, max_bytes, user_agent, proxy, started):
            return _res('connection: [SSL: CERTIFICATE_VERIFY_FAILED] '
                        'certificate verify failed')

        with mock.patch.object(wf, '_fetch_once', side_effect=_fake):
            res = wf.fetch_url(_WALL_URL, timeout_s=2)
        self.assertFalse(res.ok)
        self.assertEqual(len(self.sessions), 0)


# ---------------------------------------------------------------------------
# 4. the DNS verdict (patched DoH probe — zero sockets)
# ---------------------------------------------------------------------------

class TestDnsVerdict(unittest.TestCase):

    def setUp(self):
        self._probe = wf._doh_probe_once
        self.calls = []
        wf._DOH_CACHE.clear()

    def tearDown(self):
        wf._doh_probe_once = self._probe
        wf._DOH_CACHE.clear()

    def arm(self, verdict):
        def _fake(host, proxy):
            self.calls.append((host, proxy))
            return verdict
        wf._doh_probe_once = _fake

    def _dns_fail(self, category='retry_later', started_calls=None):
        def _fake(url, timeout_s, max_bytes, user_agent, proxy, started):
            if started_calls is not None:
                started_calls.append(url)
            return _res('connection: [Errno 11001] getaddrinfo failed',
                        None, category)
        return _fake

    def test_nxdomain_verdict_marks_the_domain_dead(self):
        self.arm('nxdomain')
        with mock.patch.object(wf, '_fetch_once',
                               side_effect=self._dns_fail()):
            res = wf.fetch_url('http://gone.example.net/x', timeout_s=2)
        self.assertFalse(res.ok)
        self.assertEqual(res.category, 'dead')
        self.assertIn('NXDOMAIN even via DNS-over-HTTPS', res.reason)
        self.assertEqual(self.calls, [('gone.example.net', None)])

    def test_exists_verdict_names_the_poisoned_resolver(self):
        self.arm('exists')
        with mock.patch.object(wf, '_fetch_once',
                               side_effect=self._dns_fail()):
            res = wf.fetch_url('http://x.example.net/x', timeout_s=2)
        self.assertFalse(res.ok)
        self.assertEqual(res.category, 'retry_later')
        self.assertIn('local resolver is lying', res.reason)

    def test_unknown_verdict_never_accuses(self):
        self.arm('unknown')
        with mock.patch.object(wf, '_fetch_once',
                               side_effect=self._dns_fail()):
            res = wf.fetch_url('http://y.example.net/x', timeout_s=2)
        self.assertFalse(res.ok)
        self.assertIn('verdict unknown', res.reason)
        self.assertEqual(res.category, 'retry_later')

    def test_verdict_is_cached_per_host(self):
        self.arm('nxdomain')
        fake = self._dns_fail()
        with mock.patch.object(wf, '_fetch_once', side_effect=fake):
            wf.fetch_url('http://z.example.net/x', timeout_s=2)
            wf.fetch_url('http://z.example.net/x', timeout_s=2)
        self.assertEqual(len(self.calls), 1)

    def test_opt_out_skips_the_probe(self):
        self.arm('nxdomain')
        with mock.patch.object(wf, '_fetch_once',
                               side_effect=self._dns_fail()):
            res = wf.fetch_url('http://w.example.net/x', timeout_s=2,
                               doh_probe=False)
        self.assertFalse(res.ok)
        self.assertEqual(self.calls, [])
        self.assertNotIn('dns:', res.reason)

    def test_non_dns_failure_never_probes(self):
        self.arm('nxdomain')

        def _fake(url, timeout_s, max_bytes, user_agent, proxy, started):
            return _res('timeout')

        with mock.patch.object(wf, '_fetch_once', side_effect=_fake):
            res = wf.fetch_url('http://v.example.net/x', timeout_s=2)
        self.assertEqual(self.calls, [])


# ---------------------------------------------------------------------------
# 5. the dead-page rescue ladder (patched _fetch_once + _archive_lookup)
# ---------------------------------------------------------------------------

class TestDeadPageRescue(unittest.TestCase):

    def setUp(self):
        self._archive = wf._archive_lookup
        wf._DOH_CACHE.clear()

    def tearDown(self):
        wf._archive_lookup = self._archive
        wf._DOH_CACHE.clear()

    def arm_archive(self, verdict, timestamp=''):
        calls = []

        def _fake(url, proxy, timeout_s, started):
            calls.append(url)
            return verdict, timestamp
        wf._archive_lookup = _fake
        return calls

    def test_www_variant_rescues_the_dead_page(self):
        calls = []
        pages = {'https://moved.example.org/page': _res(
            'HTTP 404 — the page is gone', 404, 'dead'),
            'https://moved.example.org/page/': _res(
                'HTTP 404 — the page is gone', 404, 'dead'),
            'https://www.moved.example.org/page': _ok(
                'https://www.moved.example.org/page')}

        def _fake(url, timeout_s, max_bytes, user_agent, proxy, started):
            calls.append(url)
            return pages[url]

        archive = self.arm_archive(('no-snapshot', ''))
        with mock.patch.object(wf, '_fetch_once', side_effect=_fake):
            res = wf.fetch_url('https://moved.example.org/page', timeout_s=2)
        self.assertTrue(res.ok, res.reason)
        self.assertEqual(res.status, 'full')
        self.assertIn('www variant answered; rescued', res.reason)
        self.assertEqual(res.final_url, 'https://www.moved.example.org/page')
        # the archive door was never asked (a variant rescued first)
        self.assertEqual(archive, [])

    def test_nothing_rescued_is_a_dead_verdict_with_the_story(self):
        calls = []

        def _fake(url, timeout_s, max_bytes, user_agent, proxy, started):
            calls.append(url)
            if url.startswith('https://archive.org/wayback/available'):
                return _ok(url, body=b'{"archived_snapshots": {}}')
            return _res('HTTP 404 — the page is gone', 404, 'dead')

        with mock.patch.object(wf, '_fetch_once', side_effect=_fake):
            res = wf.fetch_url('https://gone.example.org/dead',
                               timeout_s=2)
        self.assertFalse(res.ok)
        self.assertEqual(res.category, 'dead')
        self.assertIn('variants tried: trailing-slash, www', res.reason)
        self.assertIn('no archived copy exists', res.reason)

    def test_archived_snapshot_is_a_partial_with_the_story(self):
        self.arm_archive('skipped')  # replaced below via direct fetches
        pages = {}

        def _fake(url, timeout_s, max_bytes, user_agent, proxy, started):
            pages.setdefault(url, _res('HTTP 404 — the page is gone',
                                       404, 'dead'))
            return pages[url]

        def _archive(url, proxy, timeout_s, started):
            return ('https://web.archive.org/web/20150601/https://'
                    'past.example.org/a'), '20150601'

        wf._archive_lookup = _archive

        def _route_by_url(url, timeout_s, max_bytes, user_agent, proxy,
                          started):
            if 'web.archive.org' in url:
                return _ok(url, body=b'<html><head><title>The Past'
                                     b'</title></head></html>')
            return _res('HTTP 410 — the page was removed', 410, 'dead')

        with mock.patch.object(wf, '_fetch_once',
                               side_effect=_route_by_url):
            res = wf.fetch_url('https://past.example.org/a', timeout_s=2)
        self.assertTrue(res.ok, res.reason)
        self.assertEqual(res.status, 'partial')
        self.assertEqual(res.category, 'archived')
        self.assertIn('archived copy', res.reason)
        self.assertIn('20150601', res.reason)
        self.assertIn('HTTP 410', res.reason)
        self.assertIn('The Past', res.text)

    def test_archive_opt_out_is_honored(self):
        archive = self.arm_archive(('no-snapshot', ''))

        def _fake(url, timeout_s, max_bytes, user_agent, proxy, started):
            return _res('HTTP 404 — the page is gone', 404, 'dead')

        with mock.patch.object(wf, '_fetch_once', side_effect=_fake):
            res = wf.fetch_url('https://opt.example.org/x', timeout_s=2,
                               archive_fallback=False)
        self.assertFalse(res.ok)
        self.assertEqual(res.category, 'dead')
        self.assertEqual(archive, [])

    def test_loopback_never_gets_the_ladder(self):
        calls = []

        def _fake(url, timeout_s, max_bytes, user_agent, proxy, started):
            calls.append(url)
            return _res('HTTP 404 — the page is gone', 404, 'dead')

        with mock.patch.object(wf, '_fetch_once', side_effect=_fake):
            res = wf.fetch_url('http://127.0.0.1:9/x', timeout_s=2)
        self.assertFalse(res.ok)
        self.assertEqual(res.category, 'dead')
        self.assertEqual(calls, ['http://127.0.0.1:9/x'])


# ---------------------------------------------------------------------------
# 6. the polite circuit breaker (a 429/503 pays Retry-After)
# ---------------------------------------------------------------------------

class TestRatePenalty(unittest.TestCase):

    def setUp(self):
        self._probe = dict(wf._CURL_CFFI_STATE)
        wf._CURL_CFFI_STATE['tried'] = True
        wf._CURL_CFFI_STATE['ok'] = False   # the third door never dials
        self._doh = wf._doh_probe_once
        wf._doh_probe_once = lambda host, proxy: 'unknown'
        wf._DOH_CACHE.clear()

    def tearDown(self):
        wf._CURL_CFFI_STATE.clear()
        wf._CURL_CFFI_STATE.update(self._probe)
        wf._doh_probe_once = self._doh
        wf._DOH_CACHE.clear()

    def test_429_on_both_routes_pays_the_sites_retry_after(self):
        limiter = _FakeLimiter()

        def _fake(url, timeout_s, max_bytes, user_agent, proxy, started):
            return _res('HTTP 429 — rate limited (Retry-After: 120s — '
                        'the site asked for a pause)', 429, 'retry_later',
                        retry_after_s=120.0)

        with mock.patch.object(wf, '_fetch_once', side_effect=_fake):
            res = wf.fetch_url('http://limited.example.net/x', timeout_s=2,
                               proxy=dict(_PROXY), rate_limiter=limiter)
        self.assertFalse(res.ok)
        self.assertIn('proxy: HTTP 429', res.reason)
        self.assertIn('direct: HTTP 429', res.reason)
        self.assertEqual(limiter.penalties,
                         [('limited.example.net', 120.0)])

    def test_429_without_retry_after_pays_the_capped_default(self):
        limiter = _FakeLimiter()

        def _fake(url, timeout_s, max_bytes, user_agent, proxy, started):
            return _res('HTTP 429 — rate limited', 429, 'retry_later')

        with mock.patch.object(wf, '_fetch_once', side_effect=_fake):
            res = wf.fetch_url('http://bare.example.net/x', timeout_s=2,
                               rate_limiter=limiter)
        self.assertFalse(res.ok)
        self.assertEqual(limiter.penalties, [('bare.example.net', 60.0)])

    def test_absurd_retry_after_is_capped_at_five_minutes(self):
        penalty = wf._rate_limit_penalty(
            _res('HTTP 429', 429, 'retry_later', retry_after_s=99999.0))
        self.assertEqual(penalty, 300.0)

    def test_non_rate_failures_pay_nothing(self):
        self.assertIsNone(wf._rate_limit_penalty(_res('HTTP 403', 403)))
        self.assertIsNone(wf._rate_limit_penalty(_res('timeout')))
        self.assertIsNone(wf._rate_limit_penalty(None))

    def test_limiter_wait_honors_a_paid_penalty(self):
        lim = wf.DomainRateLimiter(delay_s=0)
        lim.penalize('slow.example.net', 0.35)
        t0 = time.monotonic()
        lim.wait('slow.example.net')
        self.assertGreater(time.monotonic() - t0, 0.25)
        # other domains are unaffected
        t1 = time.monotonic()
        lim.wait('fast.example.net')
        self.assertLess(time.monotonic() - t1, 0.1)

    def test_penalty_never_raises_and_ignores_junk(self):
        lim = wf.DomainRateLimiter(delay_s=0)
        lim.penalize('', 10)
        lim.penalize('x', -5)
        self.assertEqual(lim._penalty_until, {})


# ---------------------------------------------------------------------------
# 7. category combination (pure unit)
# ---------------------------------------------------------------------------

class TestCombinedCategory(unittest.TestCase):

    def test_dead_truth_beats_the_wall_that_hid_it(self):
        self.assertEqual(
            wf._combined_category(_res('HTTP 403', 403, 'blocked_bot'),
                                  _res('HTTP 404', 404, 'dead')), 'dead')

    def test_paywalled_beats_a_timeout(self):
        self.assertEqual(
            wf._combined_category(_res('HTTP 402', 402, 'paywalled'),
                                  _res('timeout', None, 'retry_later')),
            'paywalled')

    def test_broken_redirect_beats_retry_later(self):
        self.assertEqual(
            wf._combined_category(
                _res('HTTP 307 — broken redirect', 307,
                     'redirect_broken'),
                _res('timeout', None, 'retry_later')),
            'redirect_broken')

    def test_no_categories_is_empty(self):
        self.assertEqual(wf._combined_category(_res('x'), None), '')


# ---------------------------------------------------------------------------
# 8. the pipeline's category gate (the owner's dead-link loop, closed)
# ---------------------------------------------------------------------------

class _FakeLadderFetch:
    """Canned results with v0.46.0 categories, **kwargs-tolerant."""

    def __init__(self, result):
        self.result = result
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.result


class _FakeLLM:
    def __init__(self):
        pass

    def __call__(self, messages, task=None):
        return json.dumps({
            'name': 'Test Site', 'one_line': 'A test page about design.',
            'core_offerings': ['One', 'Two'],
            'best_used_for': 'Use when you need to test the pipeline.',
            'pricing': 'free', 'login_required': 'no',
            'similar_tools': [], 'tags': ['design'],
            'confidence': 'high'})


class _PipelineCase(unittest.TestCase):
    """Shared plumbing: temp vault + state db + pipeline factory."""

    def setUp(self):
        dryrun.disable()
        dryrun.clear()
        self.tmp = tempfile.mkdtemp(prefix='ladder-')
        self.vault = os.path.join(self.tmp, 'websites')
        self.db = wp.WebsiteStateDB(
            db_path=os.path.join(self.tmp, 'cache.db'))
        self.in_vault = set()
        self.logs = []

    def tearDown(self):
        dryrun.disable()
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_pipeline(self, fetch):
        return wp.WebsitePipeline(
            config={'website_vault_path': self.vault,
                    'web_domain_delay_s': 0},
            llm_call=_FakeLLM(),
            vault_index_has=lambda u: u in self.in_vault,
            state=self.db, fetch_fn=fetch,
            log=lambda m, l='info': self.logs.append((l, m)))

    def all_logs(self):
        return '\n'.join(m for (_, m) in self.logs)


class TestPipelineLadderGate(_PipelineCase):

    DEAD = _res('HTTP 404 — the page is gone (variants tried: '
                'trailing-slash, www; no archived copy exists on the '
                'Wayback Machine)', 404, 'dead',
                url='https://dead.example.org/page')

    def test_dead_verdict_never_requeues_and_retires_the_link(self):
        pipe = self.make_pipeline(_FakeLadderFetch(self.DEAD))
        res = pipe.process_link('https://dead.example.org/page')
        self.assertEqual(res['outcome'], 'review')
        # the note exists (no link left behind)…
        self.assertTrue(os.path.exists(res['note_path']))
        # …the retry queue is EMPTY (nothing to wait for)…
        self.assertIsNone(self.db.retry_row('https://dead.example.org/page'))
        # …and the link is auto-dismissed with the verdict as its reason
        row = self.db.dismissed_row('https://dead.example.org/page')
        self.assertIsNotNone(row)
        self.assertIn('auto-verdict: dead', row['reason'])
        self.assertIn('no retry scheduled', self.all_logs())

    def test_retired_link_is_never_fetched_again(self):
        pipe = self.make_pipeline(_FakeLadderFetch(self.DEAD))
        pipe.process_link('https://dead.example.org/page')
        fetch = _FakeLadderFetch(self.DEAD)
        pipe2 = self.make_pipeline(fetch)
        res = pipe2.process_link('https://dead.example.org/page')
        self.assertEqual(res['outcome'], 'skipped')
        self.assertEqual(fetch.calls, [])
        self.assertIn('retired by auto-verdict', res['error'])
        self.assertIn('revive via the graveyard', res['error'])

    def test_paywalled_verdict_retires_without_retries(self):
        paywalled = _res('HTTP 402 — paywalled (payment required; the '
                         'content is behind a paywall, not gone)', 402,
                         'paywalled', url='https://pay.example.org/a')
        pipe = self.make_pipeline(_FakeLadderFetch(paywalled))
        res = pipe.process_link('https://pay.example.org/a')
        self.assertEqual(res['outcome'], 'review')
        self.assertIsNone(self.db.retry_row('https://pay.example.org/a'))
        self.assertIn('paywalled', res['error'])

    def test_healable_failure_keeps_its_retries(self):
        """A blocked_bot 403 (the v0.43/v0.45 story) still queues its
        spaced retries — only the no-heal categories retire."""
        blocked = _res('HTTP 403 — bot defense (server: cloudflare)', 403,
                       'blocked_bot', url='https://walled.example.org/x')
        pipe = self.make_pipeline(_FakeLadderFetch(blocked))
        res = pipe.process_link('https://walled.example.org/x')
        self.assertEqual(res['outcome'], 'review')
        row = self.db.retry_row('https://walled.example.org/x')
        self.assertIsNotNone(row)
        self.assertEqual(row['attempts'], 1)
        self.assertIn('retry scheduled', self.all_logs())

    def test_legacy_fake_without_category_still_requeues(self):
        """Back-compat: injected fetchers that never set a category (the
        pre-v0.46 contract) keep the old enqueue behavior."""
        legacy = _res('HTTP 403', 403, '',
                      url='https://old.example.org/x')
        pipe = self.make_pipeline(_FakeLadderFetch(legacy))
        pipe.process_link('https://old.example.org/x')
        self.assertIsNotNone(self.db.retry_row('https://old.example.org/x'))


class TestPipelineArchivedRescue(_PipelineCase):

    def _archived(self):
        html = ('<html><head><title>The Past Page</title>'
                '<meta name="description" content="An archived page.">'
                '</head><body>Archived body text long enough to work '
                'with.</body></html>')
        return wf.FetchResult(
            'https://past.example.org/a',
            final_url='https://web.archive.org/web/2015/https://'
                      'past.example.org/a',
            status='partial', reason='archived copy — the live page '
            'answered HTTP 404; the Wayback Machine\'s capture (20150601) '
            'served this', http_status=200,
            content_type='text/html', charset='utf-8',
            body=html.encode('utf-8'), text=html, category='archived')

    def test_archived_rescue_files_under_review_with_the_story(self):
        pipe = self.make_pipeline(_FakeLadderFetch(self._archived()))
        res = pipe.process_link('https://past.example.org/a')
        self.assertEqual(res['outcome'], 'review')
        self.assertEqual(res['fetch_status'], 'partial')
        self.assertTrue(os.path.exists(res['note_path']))
        with open(res['note_path'], encoding='utf-8') as f:
            note = f.read()
        self.assertIn('archived copy', note)
        self.assertIn('The Past Page', note)
        # the retry row is resolved (the fetch itself succeeded)
        self.assertIsNone(self.db.retry_row('https://past.example.org/a'))
        self.assertIn('archived copy', self.all_logs())

    def test_archived_note_is_never_re_fetched(self):
        pipe = self.make_pipeline(_FakeLadderFetch(self._archived()))
        pipe.process_link('https://past.example.org/a')
        # the vault index sees the _review note (it is a real note)
        self.in_vault.add('https://past.example.org/a')
        fetch = _FakeLadderFetch(self._archived())
        pipe2 = self.make_pipeline(fetch)
        res = pipe2.process_link('https://past.example.org/a')
        self.assertEqual(res['outcome'], 'skipped')
        self.assertEqual(fetch.calls, [])
        self.assertIn('already in the websites vault', res['error'])


# ---------------------------------------------------------------------------
# 9. release bookkeeping (the house contract)
# ---------------------------------------------------------------------------

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestReleaseBookkeeping(unittest.TestCase):

    def _read(self, *parts):
        with open(os.path.join(_REPO_ROOT, '..', *parts),
                  encoding='utf-8', errors='replace') as fh:
            return fh.read()

    def test_version_is_0460(self):
        self.assertEqual(self._read('VERSION').strip(), '0.64.0')

    def test_changelog_has_the_beat(self):
        text = self._read('CHANGELOG.md')
        self.assertIn('## [0.46.0]', text)
        self.assertIn('ladder', text.lower())
        self.assertIn('Wayback', text)

    def test_ci_and_agents_know_the_module(self):
        ci = self._read('.github', 'workflows', 'ci.yml')
        self.assertIn('tests.test_ladder', ci)
        agents = self._read('AGENTS.md')
        self.assertIn('tests.test_ladder', agents)

    def test_config_documents_the_new_rungs(self):
        example = self._read('app', 'config.example.json')
        self.assertIn('web_archive_fallback', example)
        self.assertIn('web_doh_probe', example)
        constants = self._read('app', 'gitcurator', 'constants.py')
        self.assertIn('web_archive_fallback', constants)
        self.assertIn('web_doh_probe', constants)


if __name__ == '__main__':
    unittest.main()


# 10. v0.47.0 — the origin-down courtesy: a 52x leg (the owner's
# "HTTP 523 — Cloudflare: the site's own server origin is unreachable"
# line) names the Wayback Machine's last good copy in the reason; the
# category and the spaced retry are untouched (the live page comes
# back — the archived copy answers "what did this say?" meanwhile).
class TestOriginDownCourtesy(unittest.TestCase):

    def setUp(self):
        self._archive = wf._archive_lookup
        wf._DOH_CACHE.clear()

    def tearDown(self):
        wf._archive_lookup = self._archive
        wf._DOH_CACHE.clear()

    def arm_archive(self, verdict, timestamp=''):
        calls = []

        def _fake(url, proxy, timeout_s, started):
            calls.append(url)
            return verdict, timestamp

        wf._archive_lookup = _fake
        return calls

    def _verdict(self, legs, url='https://origin.example.net/x',
                 category='blocked_bot'):
        result = wf.FetchResult(
            url, status='failed',
            reason='proxy: HTTP 403 — forbidden | direct: HTTP 523 — '
                   'Cloudflare: the site\'s own server origin is '
                   'unreachable (site-side, retry later — not the '
                   'fetcher\'s fault)',
            category=category)
        return wf._final_verdict(result, '', url, None, None,
                                 'origin.example.net', False, legs)

    def test_52x_leg_names_the_wayback_snapshot(self):
        snap = ('https://web.archive.org/web/20230101120000/'
                'https://origin.example.net/x')
        calls = self.arm_archive(snap, '20230101120000')
        res = self._verdict((_res('HTTP 403', 403),
                             _res('HTTP 523', 523)))
        self.assertIn(f'wayback: an archived copy exists — {snap}',
                      res.reason)
        self.assertIn('(captured 20230101120000)', res.reason)
        self.assertEqual(calls, ['https://origin.example.net/x'])
        # the verdict class is untouched — the spaced retry continues:
        self.assertEqual(res.category, 'blocked_bot')

    def test_no_snapshot_no_suffix(self):
        calls = self.arm_archive('no-snapshot')
        before = ('proxy: HTTP 403 — forbidden | direct: HTTP 523 — '
                  'Cloudflare')
        res = self._verdict((_res('HTTP 403', 403),
                             _res('HTTP 523', 523)))
        self.assertEqual(len(calls), 1)
        self.assertNotIn('wayback', res.reason)

    def test_unreachable_archive_no_suffix(self):
        self.arm_archive('unreachable')
        res = self._verdict((_res('HTTP 403', 403),
                             _res('HTTP 523', 523)))
        self.assertNotIn('wayback', res.reason)

    def test_archive_optout_never_probes(self):
        calls = self.arm_archive('no-snapshot')
        result = wf.FetchResult('https://origin.example.net/x',
                                status='failed', reason='HTTP 523',
                                category='retry_later')
        wf._final_verdict(result, '', 'https://origin.example.net/x',
                          None, None, 'origin.example.net', False,
                          (_res('HTTP 523', 523),),
                          archive_fallback=False)
        self.assertEqual(calls, [])

    def test_no_52x_leg_never_probes(self):
        calls = self.arm_archive('no-snapshot')
        res = self._verdict((_res('HTTP 403', 403),
                             _res('HTTP 429', 429)))
        self.assertEqual(calls, [])
        self.assertNotIn('wayback', res.reason)

    def test_loopback_never_probes(self):
        calls = self.arm_archive('no-snapshot')
        url = 'http://127.0.0.1:9/x'
        result = wf.FetchResult(url, status='failed', reason='HTTP 523',
                                category='retry_later')
        wf._final_verdict(result, '', url, None, None, '127.0.0.1:9',
                          False, (_res('HTTP 523', 523, url=url),))
        self.assertEqual(calls, [])

    def test_single_52x_leg_also_earns_the_courtesy(self):
        snap = 'https://web.archive.org/web/2023/https://solo.example.org'
        calls = self.arm_archive(snap)
        url = 'https://solo.example.org/y'
        result = wf.FetchResult(url, status='failed',
                                reason='HTTP 523 — Cloudflare',
                                category='retry_later')
        res = wf._final_verdict(result, '', url, None, None,
                                'solo.example.org', False,
                                (_res('HTTP 523', 523, url=url),))
        self.assertEqual(len(calls), 1)
        self.assertIn(snap, res.reason)
