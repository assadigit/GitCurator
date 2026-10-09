"""tests/test_thirddoor.py — v0.45.0, the third door: the handshake.

The owner's ask (session): "Also find a workaround for this specific
block: Fetch failed: proxy: HTTP 403 | direct: HTTP 403 — bot defense
(Cloudflare: challenge)." That is the both-doors-walled line v0.43.0
reports when the wall is aimed at the REQUESTER's TLS fingerprint —
something urllib can never present. v0.45.0's answer: curl_cffi
(curl-impersonate) re-asks the URL with Chrome's own TLS/HTTP2
handshake, one attempt per allowed route.

Covered here (unit law: hand-built walled FetchResults + a fake
curl_cffi session, zero sockets; integration: a real local wall on a
NON-LOOPBACK address, the bothdoors pattern — loopback never gets any
door's escalation, so its servers can never prove this door):

* the trigger law — the third door fires ONLY on refusal-family walls
  (403/405/429/451); a resource truth (404) and connection-class
  failures never ask Chrome for a handshake; loopback never escalates;
  the ``impersonate_fallback`` opt-out is honored; the routes list the
  CALLER allows is never widened (the direct opt-out confines the door
  to the proxy route).
* the missing library — the probe off → the honest reason gains the
  "pip install curl_cffi" hint, no session is ever created; the probe
  is cached and never raises.
* the verdicts — a success via the third door is a REAL success (full,
  the wall it went around named); a challenge that survives the Chrome
  handshake gets the three-leg honest line plus the "needs a live
  browser" verdict (the owner's graveyard decision, informed); two
  allowed routes earn exactly two asks, the first through the proxy
  (socks5h — DNS at the proxy), the second direct.
* fetch_url's wiring end-to-end — the single-door walled case (a
  no-proxy install) and the both-doors walled case both reach the third
  door with the right routes.
* the REAL library (skipped when curl_cffi is not installed — CI
  installs it): the impersonated fetch against a live local server
  returns a properly-shaped FetchResult, and the full path against a
  Cloudflare-marked wall really dials it (the honest three-leg line).

No PyQt import at module level (the libEGL-less sandbox rule).
"""

import ipaddress
import os
import socket
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from gitcurator.core import web_fetch as wf


# The REAL library probe (top of file — the skip decorators below read
# it at class-definition time; CI installs it via requirements.txt).
try:
    from curl_cffi import requests as _real_creq  # noqa: F401
    _HAS_REAL = True
except Exception:
    _HAS_REAL = False


# ---------------------------------------------------------------------------
# The fakes — a curl_cffi session/response stand-in (the injected-fetch
# pattern, one level down: the factory is module-level for exactly this)
# ---------------------------------------------------------------------------

class _FakeImpResponse:
    """The shape curl_cffi's Response gives _impersonated_fetch_once."""

    def __init__(self, status_code=200,
                 body=b'<html><body>ok</body></html>',
                 headers=None, url='', charset='utf-8'):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {'Content-Type': 'text/html; charset=utf-8'}
        self.url = url
        self.charset_encoding = charset

    def iter_content(self):
        yield self._body

    def close(self):
        pass


class _FakeImpSession:
    """Records the dial; answers from a scriptable queue."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append({'url': url, **kwargs})
        r = self.responses.pop(0) if self.responses \
            else _FakeImpResponse(status_code=403, body=b'')
        return r


_WALL_URL = 'http://walled.example.net/page'   # non-loopback by construction

_OWNER_LINE = ("proxy: HTTP 403 — bot defense (Cloudflare: challenge) | "
               "direct: HTTP 403 — bot defense (Cloudflare: challenge)")

_PROXY = {'type': 'socks5', 'host': 'p.example.net', 'port': 10808,
          'username': '', 'password': ''}


def _walled(reason=_OWNER_LINE, http_status=403):
    return wf.FetchResult(_WALL_URL, status='failed', reason=reason,
                          http_status=http_status)


class _ThirdDoorCase(unittest.TestCase):
    """Shared plumbing: the probe forced ON, the session factory
    swappable, both restored after every test."""

    def setUp(self):
        self._probe = dict(wf._CURL_CFFI_STATE)
        wf._CURL_CFFI_STATE['tried'] = True
        wf._CURL_CFFI_STATE['ok'] = True
        self._factory = wf._new_impersonation_session
        self._session_global = wf._IMP_SESSION
        wf._IMP_SESSION = None
        self.factory_sessions = []

    def tearDown(self):
        wf._CURL_CFFI_STATE.clear()
        wf._CURL_CFFI_STATE.update(self._probe)
        wf._new_impersonation_session = self._factory
        wf._IMP_SESSION = self._session_global

    def arm_fake_session(self, *responses):
        def factory():
            s = _FakeImpSession(responses)
            self.factory_sessions.append(s)
            return s

        wf._IMP_SESSION = None   # the cached session must not outlive a
        wf._new_impersonation_session = factory  # re-arming

    def ask(self, walled=None, routes=(None,), enabled=True):
        return wf._maybe_impersonate(
            _WALL_URL, 5, 2_000_000, list(routes), enabled,
            walled if walled is not None else _walled(),
            time.monotonic())


class TestTriggerLaw(_ThirdDoorCase):

    def test_the_owners_exact_line_opens_the_third_door(self):
        self.arm_fake_session(_FakeImpResponse(status_code=200))
        res = self.ask()
        self.assertTrue(res.ok, res.reason)
        self.assertEqual(res.status, 'full')
        self.assertIn('via impersonated Chrome', res.reason)
        self.assertIn(_OWNER_LINE, res.reason)
        # one dial, direct (the no-proxy route list):
        self.assertEqual(len(self.factory_sessions), 1)
        call = self.factory_sessions[0].calls[0]
        self.assertEqual(call['url'], _WALL_URL)
        self.assertFalse(call.get('proxies'))

    def test_every_refusal_family_status_earns_the_door(self):
        for code in (403, 405, 429, 451):
            self.arm_fake_session(_FakeImpResponse(status_code=200))
            res = self.ask(walled=_walled(f'HTTP {code}', http_status=code))
            self.assertTrue(res.ok, f'{code}: {res.reason}')

    def test_resource_truth_never_asks_chrome_for_a_handshake(self):
        self.arm_fake_session(_FakeImpResponse(status_code=200))
        res = self.ask(walled=_walled('HTTP 404', http_status=404))
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, 'HTTP 404')
        self.assertEqual(len(self.factory_sessions), 0)

    def test_connection_class_failure_never_asks_chrome(self):
        # A timeout is a network truth, not a fingerprint verdict.
        self.arm_fake_session(_FakeImpResponse(status_code=200))
        res = self.ask(walled=_walled('proxy: timeout | direct: '
                                      'connection: timed out',
                                      http_status=None))
        self.assertFalse(res.ok)
        self.assertIn('timed out', res.reason)
        self.assertNotIn('chrome-impersonated', res.reason)
        self.assertEqual(len(self.factory_sessions), 0)

    def test_opt_out_flag_is_honored(self):
        self.arm_fake_session(_FakeImpResponse(status_code=200))
        res = self.ask(enabled=False)
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, _OWNER_LINE)
        self.assertEqual(len(self.factory_sessions), 0)

    def test_loopback_never_gets_any_doors_escalation(self):
        self.arm_fake_session(_FakeImpResponse(status_code=200))
        res = wf._maybe_impersonate(
            'http://127.0.0.1:8080/x', 5, 2_000_000, [None], True,
            _walled(), time.monotonic())
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, _OWNER_LINE)
        self.assertEqual(len(self.factory_sessions), 0)


class TestMissingLibrary(_ThirdDoorCase):

    def test_missing_library_gives_the_install_hint(self):
        wf._CURL_CFFI_STATE['ok'] = False
        res = self.ask()
        self.assertFalse(res.ok)
        self.assertIn(_OWNER_LINE, res.reason)
        self.assertIn('pip install curl_cffi', res.reason)
        self.assertEqual(len(self.factory_sessions), 0)

    def test_probe_is_cached_and_never_raises(self):
        # The cached state short-circuits the import probe — flipping
        # the flag flips the answer without any re-probe:
        wf._CURL_CFFI_STATE['tried'] = True
        wf._CURL_CFFI_STATE['ok'] = False
        self.assertFalse(wf.curl_cffi_available())
        wf._CURL_CFFI_STATE['ok'] = True
        self.assertTrue(wf.curl_cffi_available())

    @unittest.skipUnless(_HAS_REAL, 'curl_cffi not installed')
    def test_real_probe_finds_the_installed_library(self):
        wf._CURL_CFFI_STATE['tried'] = False
        wf._CURL_CFFI_STATE['ok'] = False
        self.assertTrue(wf.curl_cffi_available())
        self.assertTrue(wf._CURL_CFFI_STATE['tried'])


class TestVerdicts(_ThirdDoorCase):

    def test_success_via_the_third_door_is_a_real_success(self):
        body = (b'<html><head><title>The Real Page</title></head>'
                b'<body>fix live</body></html>')
        self.arm_fake_session(_FakeImpResponse(status_code=200, body=body))
        res = self.ask()
        self.assertTrue(res.ok)
        self.assertEqual(res.status, 'full')
        self.assertEqual(res.http_status, 200)
        self.assertIn(b'fix live', res.body)
        self.assertIn('bot defense (Cloudflare: challenge)', res.reason)

    def test_challenge_that_survives_gets_the_three_leg_line(self):
        challenged = _FakeImpResponse(
            status_code=403, body=b'',
            headers={'Server': 'cloudflare', 'cf-mitigated': 'challenge'})
        self.arm_fake_session(challenged)
        res = self.ask()
        self.assertFalse(res.ok)
        self.assertIn(_OWNER_LINE, res.reason)
        self.assertIn('chrome-impersonated:', res.reason)
        self.assertIn('HTTP 403 — bot defense (Cloudflare: challenge)',
                      res.reason)
        self.assertIn('needs a live browser', res.reason)

    def test_two_routes_two_asks_proxy_first_then_direct(self):
        challenged = _FakeImpResponse(
            status_code=403, body=b'',
            headers={'Server': 'cloudflare', 'cf-mitigated': 'challenge'})
        self.arm_fake_session(challenged, challenged)
        res = self.ask(routes=(_PROXY, None))
        self.assertFalse(res.ok)
        calls = self.factory_sessions[0].calls
        self.assertEqual(len(calls), 2)
        # first ask through the proxy (socks5h — DNS at the proxy):
        self.assertEqual(calls[0]['proxies']['https'],
                         'socks5h://p.example.net:10808')
        # second ask direct:
        self.assertFalse(calls[1].get('proxies'))
        # one honest line, both legs:
        self.assertEqual(res.reason.count('chrome-impersonated:'), 1)

    def test_plain_403_without_marker_still_reports_cleanly(self):
        walled = _FakeImpResponse(status_code=403, body=b'')
        self.arm_fake_session(walled)
        res = self.ask(walled=_walled('proxy: HTTP 403 | direct: HTTP 403'))
        self.assertFalse(res.ok)
        self.assertIn('| chrome-impersonated: HTTP 403', res.reason)
        self.assertNotIn('needs a live browser', res.reason)


class TestProxyUrlLaw(unittest.TestCase):

    def test_socks_becomes_socks5h_dns_at_the_proxy(self):
        self.assertEqual(
            wf._proxy_url_for_curl(_PROXY),
            'socks5h://p.example.net:10808')

    def test_socks4_http_and_auth(self):
        self.assertEqual(wf._proxy_url_for_curl(
            {'type': 'socks4', 'host': 'h', 'port': 1}), 'socks4://h:1')
        self.assertEqual(wf._proxy_url_for_curl(
            {'type': 'http', 'host': 'h', 'port': 8080,
             'username': 'u', 'password': 'p'}),
            'http://u:p@h:8080')
        self.assertIsNone(wf._proxy_url_for_curl(None))
        self.assertIsNone(wf._proxy_url_for_curl(
            {'type': 'bogus', 'host': 'h', 'port': 1}))


# ---------------------------------------------------------------------------
# fetch_url's wiring — a real wall on a NON-LOOPBACK address (loopback
# never escalates any door, so its servers cannot prove this one; the
# bothdoors e2e pattern, skipped where no such address exists)
# ---------------------------------------------------------------------------

class _WalledPage(BaseHTTPRequestHandler):
    """Answers 403 with the exact Cloudflare markers the owner's line
    names — every requester, stdlib or impersonated."""

    def do_GET(self):
        body = (b'<html><head><title>Just a moment...</title></head>'
                b'<body>challenge</body></html>')
        self.send_response(403)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Server', 'cloudflare')
        self.send_header('cf-mitigated', 'challenge')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def _non_loopback_ip():
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if ipaddress.ip_address(ip).is_loopback:
            return None
        return ip
    except (socket.gaierror, ValueError):
        return None


@unittest.skipIf(_non_loopback_ip() is None,
                 'no non-loopback local address')
class TestFetchUrlWiring(_ThirdDoorCase):

    def setUp(self):
        super().setUp()
        self.ip = _non_loopback_ip()
        self.httpd = HTTPServer((self.ip, 0), _WalledPage)
        self.port = self.httpd.server_address[1]
        # one request per door: stdlib, then the (fake) third door
        for _ in range(2):
            threading.Thread(target=self.httpd.handle_request,
                             daemon=True).start()

    def tearDown(self):
        try:
            self.httpd.server_close()
        except Exception:
            pass
        super().tearDown()

    @property
    def url(self):
        return f'http://{self.ip}:{self.port}/page'

    def test_single_door_walled_end_to_end_success(self):
        # A no-proxy install walls on its only route → the third door
        # dials (through the fake session) and the page comes back.
        body = (b'<html><head><title>The Real Page</title></head>'
                b'<body>fix live</body></html>')
        self.arm_fake_session(_FakeImpResponse(status_code=200, body=body,
                                               url=self.url))
        res = wf.fetch_url(self.url, timeout_s=8)
        self.assertTrue(res.ok, res.reason)
        self.assertEqual(res.status, 'full')
        self.assertIn(b'fix live', res.body)
        self.assertIn('via impersonated Chrome', res.reason)
        self.assertIn('HTTP 403 — bot defense (Cloudflare: challenge)',
                      res.reason)

    def test_direct_optout_confines_the_third_door_to_the_proxy(self):
        # direct_fallback=False + a dead proxy: the routes list handed
        # to the door must be proxy-only — an opt-out is never widened.
        dead_proxy = {'type': 'socks5', 'host': '127.0.0.1',
                      'port': 1, 'username': '', 'password': ''}
        routes_probe = {}
        real_maybe = wf._maybe_impersonate

        def spy(url, timeout_s, max_bytes, routes, enabled, walled,
                started, **kwargs):
            routes_probe['routes'] = routes
            return real_maybe(url, timeout_s, max_bytes, routes, enabled,
                              walled, started, **kwargs)

        wf._maybe_impersonate = spy
        self.arm_fake_session(_FakeImpResponse(status_code=200))
        try:
            wf.fetch_url(self.url, timeout_s=8, proxy=dead_proxy,
                         direct_fallback=False)
        finally:
            wf._maybe_impersonate = real_maybe
        self.assertEqual(routes_probe.get('routes'), [dead_proxy])

    def test_both_doors_walled_gets_both_routes(self):
        # A dead proxy (connection-class on door one) + the walled
        # direct door: the both-doors branch ran, the third door earns
        # BOTH routes.
        dead_proxy = {'type': 'socks5', 'host': '127.0.0.1',
                      'port': 1, 'username': '', 'password': ''}
        routes_probe = {}
        real_maybe = wf._maybe_impersonate

        def spy(url, timeout_s, max_bytes, routes, enabled, walled,
                started, **kwargs):
            routes_probe['routes'] = routes
            return real_maybe(url, timeout_s, max_bytes, routes, enabled,
                              walled, started, **kwargs)

        wf._maybe_impersonate = spy
        self.arm_fake_session(_FakeImpResponse(status_code=200),
                              _FakeImpResponse(status_code=200))
        try:
            wf.fetch_url(self.url, timeout_s=8, proxy=dead_proxy)
        finally:
            wf._maybe_impersonate = real_maybe
        self.assertEqual(routes_probe.get('routes'), [dead_proxy, None])


class TestSourceContracts(unittest.TestCase):
    """The house source-contract tests."""

    def setUp(self):
        self.root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    def _read(self, *parts):
        with open(os.path.join(self.root, *parts), 'r',
                  encoding='utf-8') as f:
            return f.read()

    def test_version_is_0450(self):
        self.assertEqual(self._read('VERSION').strip(), '0.60.1')

    def test_changelog_has_the_beat(self):
        text = self._read('CHANGELOG.md')
        self.assertIn('## [0.45.0]', text)
        self.assertIn('third door', text.lower())

    def test_ci_and_agents_know_the_module(self):
        ci = self._read('.github', 'workflows', 'ci.yml')
        self.assertIn('tests.test_thirddoor', ci)
        agents = self._read('AGENTS.md')
        self.assertIn('tests.test_thirddoor', agents)

    def test_requirements_name_the_library(self):
        req = self._read('app', 'requirements.txt')
        self.assertIn('curl_cffi', req)

    def test_pipeline_default_on_and_opt_out(self):
        src = self._read('app', 'gitcurator', 'core',
                         'website_pipeline.py')
        self.assertIn("'web_impersonate_fallback') is not False", src)

    def test_bothdoors_pin_follows_the_release(self):
        src = self._read('app', 'tests', 'test_bothdoors.py')
        self.assertIn("'0.60.1'", src)


# ---------------------------------------------------------------------------
# The REAL library — skipped when curl_cffi is not importable (CI
# installs it via requirements.txt; the probe at the top keeps this
# honest).
# ---------------------------------------------------------------------------


@unittest.skipUnless(_HAS_REAL, 'curl_cffi not installed')
class TestRealCurlCffi(unittest.TestCase):

    def setUp(self):
        self._probe = dict(wf._CURL_CFFI_STATE)
        wf._CURL_CFFI_STATE['tried'] = True
        wf._CURL_CFFI_STATE['ok'] = True
        self._factory = wf._new_impersonation_session
        self._session_global = wf._IMP_SESSION
        wf._IMP_SESSION = None

    def tearDown(self):
        wf._CURL_CFFI_STATE.clear()
        wf._CURL_CFFI_STATE.update(self._probe)
        wf._new_impersonation_session = self._factory
        wf._IMP_SESSION = self._session_global

    def test_real_impersonated_fetch_shapes_a_fetch_result(self):
        import socketserver
        body = (b'<html><head><title>Real Imp Page</title></head>'
                b'<body>handshake ok</body></html>')

        class _Page(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Type',
                                 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        httpd = socketserver.TCPServer(('127.0.0.1', 0), _Page)
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            res = wf._impersonated_fetch_once(
                f'http://127.0.0.1:{port}/p', 5, 2_000_000, None,
                time.monotonic())
        finally:
            httpd.shutdown()
            httpd.server_close()
        self.assertTrue(res.ok, res.reason)
        self.assertEqual(res.status, 'full')
        self.assertEqual(res.http_status, 200)
        self.assertIn(b'handshake ok', res.body)
        self.assertIn('Real Imp Page', res.text)
        self.assertEqual(res.charset, 'utf-8')
        self.assertGreaterEqual(res.elapsed_s, 0.0)

    @unittest.skipIf(_non_loopback_ip() is None,
                     'no non-loopback local address')
    def test_full_path_dials_the_real_third_door(self):
        # The owner's line on real sockets: a non-loopback wall that
        # answers 403 cloudflare-challenge to everyone. curl_cffi
        # cannot solve a real challenge — but it dials, and the honest
        # three-leg line comes back naming it.
        ip = _non_loopback_ip()
        served = {'n': 0}

        class _CountingWall(_WalledPage):
            def do_GET(self):
                served['n'] += 1
                super().do_GET()

        httpd = HTTPServer((ip, 0), _CountingWall)
        port = httpd.server_address[1]
        # exactly two requests: the stdlib door, then the third door
        for _ in range(2):
            threading.Thread(target=httpd.handle_request,
                             daemon=True).start()
        try:
            res = wf.fetch_url(f'http://{ip}:{port}/page', timeout_s=8)
        finally:
            httpd.server_close()
        self.assertFalse(res.ok)
        self.assertIn('HTTP 403 — bot defense (Cloudflare: challenge)',
                      res.reason)
        self.assertIn('chrome-impersonated:', res.reason)
        self.assertIn('needs a live browser', res.reason)
        self.assertEqual(served['n'], 2)


if __name__ == '__main__':
    unittest.main()
