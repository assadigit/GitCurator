#!/usr/bin/env python3
"""
test_bothdoors.py — v0.43.0: the both-doors fetch.

Owner ask (session): "I still get 403, find a robust way so websites
do not deny this system. or this: Fetch failed: proxy: timeout |
direct: connection: timed out. add this option: when fetching
websites, try with both proxy on and proxy off. only use always
proxy for telegram (if proxy was on)."

What this suite pins (all hermetic — mock-level route scripting,
127.0.0.1 sockets, no GUI shown; the one non-loopback end-to-end
skips when the machine has no usable address):

- The refusal family: a 403/405/429/451 through the proxy is aimed
  at the ROUTE's IP (WAF/IP-reputation wall, the method-block in the
  owner's own _review pile, a per-IP rate limit, a geo-block) — not
  an answer about the page. v0.21.0 already alternated to DIRECT on
  connection-class proxy failures; v0.43.0 extends the alternation
  to these route-aimed refusals. A success via the second door is a
  REAL success (status full, 200, wall named in the reason) — it is
  stored properly, not parked in _review.
- Every other HTTP answer (404 gone, 401 auth, 500 broken) is the
  site's truth about the RESOURCE — one attempt, no second door.
- A wall on BOTH doors reports both reasons in the house line
  ("proxy: … | direct: …") — exactly the format the owner quoted.
- No proxy configured / direct_fallback opt-out / loopback target:
  single attempt, the old laws unchanged.
- End to end on REAL sockets: a SOCKS5 server that answers
  403 cloudflare through the tunnel + a plain HTTP server on a
  non-loopback local address — the same fetch comes back FULL via
  the direct door, with the wall named.
- Telegram's law is untouched: the MTProto fetcher keeps its
  always-proxy-when-enabled shape (source contracts — no
  use_for_web gate ever reaches the Telegram side).
"""

import inspect
import ipaddress
import os
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gitcurator.core import web_fetch as wf

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _route(result, http_status=None):
    """A canned _fetch_once answer for one route."""
    return wf.FetchResult(result, status='failed',
                          reason=result, http_status=http_status)


def _full(url):
    return wf.FetchResult(url, final_url=url, status='full',
                          http_status=200, body=b'<html>walled out</html>',
                          text='<html>walled out</html>')


# ---------------------------------------------------------------------------
# 1. The refusal family alternates (mock-level route scripting)
# ---------------------------------------------------------------------------

class TestRefusalAlternates(unittest.TestCase):
    """403/405/429/451 through the proxy → the DIRECT door opens."""

    PROXY = {"type": "socks5", "host": "127.0.0.1", "port": 1,
             "username": "", "password": ""}

    def _scripted(self, first, second=None, first_status=None,
                  second_status=None):
        """Patch _fetch_once: the proxied call answers ``first``; the
        direct call answers ``second``. Returns the call recorder."""
        calls = []

        def _fake(url, timeout_s, max_bytes, user_agent, proxy, started):
            calls.append(proxy)
            if proxy is not None:
                if isinstance(first, wf.FetchResult):
                    return first
                return _route(first, http_status=first_status)
            if isinstance(second, wf.FetchResult):
                return second
            if second is None:
                return _route('connection: refused', http_status=None)
            return _route(second, http_status=second_status)

        patcher = mock.patch.object(wf, '_fetch_once', side_effect=_fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls

    def test_403_through_proxy_falls_back_and_succeeds(self):
        calls = self._scripted('HTTP 403 — bot defense (server: cloudflare)',
                               second=_full('https://walled.test/a'),
                               first_status=403)
        res = wf.fetch_url('https://walled.test/a', timeout_s=2,
                           proxy=dict(self.PROXY))
        self.assertTrue(res.ok, res.reason)
        self.assertEqual(res.status, 'full')
        self.assertEqual(res.http_status, 200)
        self.assertEqual(res.body, b'<html>walled out</html>')
        self.assertEqual(calls, [self.PROXY, None])  # both doors, in order
        self.assertIn('direct fallback', res.reason)
        self.assertIn('HTTP 403', res.reason)
        self.assertIn('cloudflare', res.reason)

    def test_405_the_owners_named_wall_alternates(self):
        """405 on a GET is a WAF's method-block — in the owner's own
        _review pile next to the 403s. Same alternation."""
        calls = self._scripted('HTTP 405', second=_full('https://w.test/b'),
                               first_status=405)
        res = wf.fetch_url('https://w.test/b', timeout_s=2,
                           proxy=dict(self.PROXY))
        self.assertTrue(res.ok, res.reason)
        self.assertEqual(res.status, 'full')
        self.assertEqual(calls, [self.PROXY, None])

    def test_429_per_ip_rate_limit_alternates(self):
        """A 429 is aimed at the route's IP quota — the other route has
        its own."""
        calls = self._scripted('HTTP 429', second=_full('https://w.test/c'),
                               first_status=429)
        res = wf.fetch_url('https://w.test/c', timeout_s=2,
                           proxy=dict(self.PROXY))
        self.assertTrue(res.ok, res.reason)
        self.assertEqual(calls, [self.PROXY, None])

    def test_451_geo_wall_alternates(self):
        """451 is a geo-block: the other route is in another country."""
        calls = self._scripted('HTTP 451', second=_full('https://w.test/d'),
                               first_status=451)
        res = wf.fetch_url('https://w.test/d', timeout_s=2,
                           proxy=dict(self.PROXY))
        self.assertTrue(res.ok, res.reason)
        self.assertEqual(calls, [self.PROXY, None])

    def test_walled_on_both_doors_reports_both(self):
        calls = self._scripted('HTTP 403 — bot defense (server: cloudflare)',
                               second='HTTP 405',
                               first_status=403, second_status=405)
        res = wf.fetch_url('https://w.test/e', timeout_s=2,
                           proxy=dict(self.PROXY))
        self.assertFalse(res.ok)
        self.assertEqual(calls, [self.PROXY, None])
        self.assertIn('proxy: HTTP 403', res.reason)
        self.assertIn('bot defense', res.reason)
        self.assertIn('direct: HTTP 405', res.reason)
        self.assertEqual(res.http_status, 405)   # the second door's answer

    def test_timeout_still_alternates_v021_guard(self):
        """The v0.21.0 rule (connection-class failure → one direct
        attempt) is untouched by the v0.43.0 extension."""
        calls = self._scripted('timeout', second=_full('https://w.test/f'))
        res = wf.fetch_url('https://w.test/f', timeout_s=2,
                           proxy=dict(self.PROXY))
        self.assertTrue(res.ok, res.reason)
        self.assertEqual(calls, [self.PROXY, None])
        self.assertIn('timeout', res.reason)

    def test_both_connection_dead_reason_format_unchanged(self):
        """The owner's quoted line, byte for byte in shape: both doors
        dead at the connection level still report both reasons."""
        calls = self._scripted('timeout',
                               second='connection: timed out')
        res = wf.fetch_url('https://w.test/g', timeout_s=2,
                           proxy=dict(self.PROXY))
        self.assertFalse(res.ok)
        self.assertEqual(calls, [self.PROXY, None])
        self.assertEqual(res.reason,
                         'proxy: timeout | direct: connection: timed out')

    def test_success_on_the_first_door_never_dials_the_second(self):
        calls = self._scripted(_full('https://w.test/h'))
        res = wf.fetch_url('https://w.test/h', timeout_s=2,
                           proxy=dict(self.PROXY))
        self.assertTrue(res.ok)
        self.assertEqual(calls, [self.PROXY])
        self.assertEqual(res.reason, '')


# ---------------------------------------------------------------------------
# 2. The site's own truth — no second door
# ---------------------------------------------------------------------------

class TestResourceAnswers(unittest.TestCase):
    """404/401/500 answer about the PAGE, not the route: one attempt."""

    PROXY = {"type": "socks5", "host": "127.0.0.1", "port": 1,
             "username": "", "password": ""}

    def _single(self, code, reason):
        calls = []

        def _fake(url, timeout_s, max_bytes, user_agent, proxy, started):
            calls.append(proxy)
            return _route(reason, http_status=code)

        with mock.patch.object(wf, '_fetch_once', side_effect=_fake):
            res = wf.fetch_url(f'https://gone.test/{code}', timeout_s=2,
                               proxy=dict(self.PROXY))
        self.assertFalse(res.ok)
        self.assertEqual(calls, [self.PROXY])    # the site ANSWERED — truth
        self.assertEqual(res.http_status, code)
        self.assertEqual(res.reason, reason)

    def test_404_gone_is_the_truth(self):
        """v0.46.0 — the dead page's truth never opens the SECOND ROUTE,
        but it now climbs the rescue ladder first: the variant asks and
        the Wayback probe all ride the SAME (primary) route, and the
        verdict is category 'dead' when nothing rescues the link."""
        calls = []

        def _fake(url, timeout_s, max_bytes, user_agent, proxy, started):
            calls.append(proxy)
            return _route('HTTP 404', http_status=404)

        with mock.patch.object(wf, '_fetch_once', side_effect=_fake):
            res = wf.fetch_url('https://gone.test/404', timeout_s=2,
                               proxy=dict(self.PROXY))
        self.assertFalse(res.ok)
        # every ask rode the PRIMARY route — the site answered, no
        # second door: the original + two variants + the Wayback probe
        self.assertEqual(len(calls), 4)
        self.assertTrue(all(c == self.PROXY for c in calls),
                        f'unexpected route: {calls}')
        self.assertEqual(res.http_status, 404)
        self.assertEqual(res.category, 'dead')
        self.assertIn('variants tried', res.reason)

    def test_401_auth_is_the_truth(self):
        self._single(401, 'HTTP 401')

    def test_500_broken_is_the_truth(self):
        self._single(500, 'HTTP 500')


# ---------------------------------------------------------------------------
# 3. The old laws unchanged: no proxy, opt-out, loopback
# ---------------------------------------------------------------------------

class TestOldLaws(unittest.TestCase):

    PROXY = {"type": "socks5", "host": "127.0.0.1", "port": 1,
             "username": "", "password": ""}

    def test_no_proxy_configured_single_attempt_even_on_403(self):
        """No second door exists without a first door: proxy=None means
        the direct line is the only route (the owner with the proxy
        block off, or use_for_web unticked)."""
        calls = []

        def _fake(url, timeout_s, max_bytes, user_agent, proxy, started):
            calls.append(proxy)
            return _route('HTTP 403', http_status=403)

        with mock.patch.object(wf, '_fetch_once', side_effect=_fake):
            res = wf.fetch_url('https://walled.test/x', timeout_s=2,
                               proxy=None)
        self.assertFalse(res.ok)
        self.assertEqual(calls, [None])
        self.assertEqual(res.http_status, 403)

    def test_direct_fallback_opt_out_single_attempt(self):
        calls = []

        def _fake(url, timeout_s, max_bytes, user_agent, proxy, started):
            calls.append(proxy)
            return _route('HTTP 403', http_status=403)

        with mock.patch.object(wf, '_fetch_once', side_effect=_fake):
            res = wf.fetch_url('https://walled.test/y', timeout_s=2,
                               proxy=dict(self.PROXY),
                               direct_fallback=False)
        self.assertFalse(res.ok)
        self.assertEqual(calls, [self.PROXY])

    def test_loopback_never_alternates(self):
        """The v0.15.1 rule: loopback is never proxied — and with no
        second route there is nothing to alternate to."""
        calls = []

        def _fake(url, timeout_s, max_bytes, user_agent, proxy, started):
            calls.append(proxy)
            return _route('HTTP 403', http_status=403)

        with mock.patch.object(wf, '_fetch_once', side_effect=_fake):
            res = wf.fetch_url('http://127.0.0.1:9/z', timeout_s=2,
                               proxy=dict(self.PROXY))
        self.assertFalse(res.ok)
        self.assertEqual(calls, [self.PROXY])    # one attempt, forced direct


# ---------------------------------------------------------------------------
# 4. Source contracts — the wiring, the docs, the release bookkeeping
# ---------------------------------------------------------------------------

class TestSourceContracts(unittest.TestCase):

    def test_refusal_family_is_exactly_the_route_aimed_four(self):
        self.assertEqual(set(wf.ROUTE_REFUSAL_STATUSES),
                         {403, 405, 429, 451})
        for code in (403, 405, 429, 451):
            self.assertIn(code, wf.ROUTE_REFUSAL_STATUSES)

    def test_fetch_url_consults_the_family(self):
        src = inspect.getsource(wf.fetch_url)
        self.assertIn('ROUTE_REFUSAL_STATUSES', src)
        self.assertIn('first.ok', src)                    # success: no 2nd
        self.assertIn('_is_loopback_url(url)', src)       # the old law

    def test_pipeline_log_names_both_doors(self):
        from gitcurator.core import website_pipeline as wp_mod
        src = inspect.getsource(
            wp_mod.WebsitePipeline.__init__)
        self.assertIn('both doors', src)
        self.assertIn('403/405/429/451', src)

    def test_settings_checkbox_documents_both_doors(self):
        # File-text contract (not an import): the Settings checkbox is
        # GUI territory — reading the source keeps this test runnable
        # wherever PyQt cannot load.
        path = os.path.join(_REPO_ROOT, 'gitcurator', 'gui', 'main_window',
                            'ui.py')
        with open(path, encoding='utf-8', errors='replace') as fh:
            src = fh.read()
        self.assertIn('Use this proxy for web fetches too', src)
        self.assertIn('the direct line stays the fallback', src)
        self.assertIn('NEVER proxied', src)
        self.assertIn('Telegram is unchanged', src)

    def test_telegram_keeps_its_own_law_file_contract(self):
        """MTProto always rides the proxy when one is enabled — the
        Telegram fetch path must never grow a use_for_web gate or a
        both-doors alternation."""
        path = os.path.join(_REPO_ROOT, 'gitcurator', 'integrations',
                            'telegram_fetch_worker.py')
        with open(path, encoding='utf-8', errors='replace') as fh:
            src = fh.read()
        self.assertNotIn('use_for_web', src)
        self.assertIn("proxy_config.get('enabled')", src)

    def test_release_bookkeeping(self):
        # v0.53.0 — the version pin follows the release (the fifth
        # door's wait owns the headline now; the both-doors beat stays
        # in the log below the new heads — the window grew with them
        # again).
        with open(os.path.join(_REPO_ROOT, '..', 'VERSION'),
                  encoding='utf-8') as fh:
            self.assertEqual(fh.read().strip(), '0.54.0')
        with open(os.path.join(_REPO_ROOT, '..', 'CHANGELOG.md'),
                  encoding='utf-8', errors='replace') as fh:
            head = fh.read(72000)
        self.assertIn('[0.47.0]', head)
        self.assertIn('[0.46.0]', head)
        self.assertIn('[0.45.0]', head)
        self.assertIn('[0.44.0]', head)
        self.assertIn('[0.43.0]', head)
        self.assertIn('both doors', head.lower())


# ---------------------------------------------------------------------------
# 5. End to end on REAL sockets — the wall answers through the tunnel,
#    the page answers direct
# ---------------------------------------------------------------------------

class _WalledSocks5Server:
    """A minimal SOCKS5 server that answers every HTTP request through
    the tunnel with a 403 + a visible cloudflare marker — the owner's
    wall, on the owner's route. Records each tunneled request."""

    def __init__(self):
        self.seen = []
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(('127.0.0.1', 0))
        self._srv.listen(4)
        self.port = self._srv.getsockname()[1]
        self._stop = threading.Event()
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,),
                             daemon=True).start()

    def _handle(self, conn):
        rec = {}
        self.seen.append(rec)
        try:
            conn.settimeout(10)
            greeting = conn.recv(64)
            if not greeting or greeting[0] != 5:
                raise AssertionError(f'bad greeting {greeting!r}')
            conn.sendall(b'\x05\x00')                    # no auth required
            req = conn.recv(512)
            if req[0] != 5 or req[1] != 1:
                raise AssertionError(f'not a CONNECT request {req!r}')
            conn.sendall(b'\x05\x00\x00\x01' + b'\x00' * 6)  # success
            http_req = conn.recv(4096)
            rec['request_line'] = http_req.split(b'\r\n')[0].decode()
            body = b'<html><head><title>Blocked</title></head></html>'
            resp = (b'HTTP/1.1 403 Forbidden\r\n'
                    b'Server: cloudflare\r\n'
                    b'Content-Type: text/html\r\n'
                    b'Content-Length: %d\r\n\r\n' % len(body)) + body
            conn.sendall(resp)
        except Exception:
            rec['error'] = True
        finally:
            try:
                conn.close()
            except Exception:
                pass

    @property
    def proxy(self):
        return {'type': 'socks5', 'host': '127.0.0.1', 'port': self.port,
                'username': '', 'password': ''}

    def stop(self):
        self._stop.set()
        try:
            self._srv.close()
        except Exception:
            pass


class TestBothDoorsEndToEnd(unittest.TestCase):
    """The owner's exact situation on real sockets: the site answers
    403 cloudflare to the PROXIED route and 200 to the DIRECT one —
    one fetch_url call must come back FULL, with the wall named."""

    def test_403_tunnel_direct_page_comes_back_full(self):
        try:
            ip = socket.gethostbyname(socket.gethostname())
            if ipaddress.ip_address(ip).is_loopback:
                raise ValueError()
        except (socket.gaierror, ValueError):
            self.skipTest('no non-loopback local address')

        served = {}

        class _Page(BaseHTTPRequestHandler):
            def do_GET(self):
                served['request_line'] = self.requestline
                body = b'<html><head><title>The Real Page</title></head>' \
                       b'<body>fix live</body></html>'
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        wall = _WalledSocks5Server()
        httpd = HTTPServer((ip, 0), _Page)
        port = httpd.server_address[1]
        t = threading.Thread(target=httpd.handle_request, daemon=True)
        t.start()
        try:
            res = wf.fetch_url(f'http://{ip}:{port}/page', timeout_s=8,
                               proxy=wall.proxy)
        finally:
            wall.stop()
            t.join(timeout=5)
            httpd.server_close()

        self.assertTrue(res.ok, res.reason)
        self.assertEqual(res.status, 'full')
        self.assertEqual(res.http_status, 200)
        self.assertIn(b'fix live', res.body)
        self.assertIn('direct fallback', res.reason)
        self.assertIn('HTTP 403', res.reason)
        self.assertIn('cloudflare', res.reason)
        # Both doors were really dialed, exactly once each.
        self.assertEqual(len(wall.seen), 1)
        self.assertTrue(wall.seen[0]['request_line'].startswith(
            'GET /page '))
        self.assertEqual(served.get('request_line'),
                         f'GET /page HTTP/1.1')


if __name__ == '__main__':
    unittest.main()
