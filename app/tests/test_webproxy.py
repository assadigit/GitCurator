#!/usr/bin/env python3
"""
test_webproxy.py — v0.19.0: web fetches through the owner's proxy.

Owner's log 2026-09-29 (v0.18.0 run on Windows): every x.com / t.co /
youtu.be link failed with ``[WinError 10061] No connection could be made
because the target machine actively refused it`` while gooseworks.ai
fetched fine — the blocked-web signature (poisoned local DNS refuses the
connection) on a line where Telegram already works through the app's
SOCKS5 proxy (v2rayN 127.0.0.1:10808). The Websites pipeline fetcher went
DIRECT and never rode that proxy.

v0.19.0 routes it: ``web_fetch.fetch_url(proxy=...)`` forces every fetch
through the configured proxy with DNS resolved AT the proxy (rdns=True),
the pipeline pre-flights the proxy and binds a proxied fetcher, exhausted
retry queues are re-armed once per proxy epoch, and Settings → 🌐 Proxy
grows the "use this proxy for web fetches too" toggle (default ON).

All against LOCAL sockets / temp dirs / monkeypatched recorders — no
network beyond 127.0.0.1, no GUI shown.
"""

import inspect
import os
import socket
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gitcurator.core import web_fetch as wf
from gitcurator.core import website_pipeline as wp


# ---------------------------------------------------------------------------
# Fakes: a REAL SOCKS5 server (handshake only) + a plain HTTP target
# ---------------------------------------------------------------------------

class _FakeSocks5Server:
    """A minimal SOCKS5 server: no-auth greeting, CONNECT with the
    DOMAINNAME address type, then a canned HTTP response through the
    tunnel. Records what the client asked it to reach — the rdns proof:
    a correct client sends the HOSTNAME (never resolves locally)."""

    def __init__(self, body=b'<html><head><title>Tunnel</title></head>'
                            b'<body>ok</body></html>'):
        self.body = body
        self.seen = []          # list of dicts per connection
        self.errors = []
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(('127.0.0.1', 0))
        self._srv.listen(4)
        self.port = self._srv.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

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
            conn.sendall(b'\x05\x00')          # no auth required
            req = conn.recv(512)
            if req[0] != 5 or req[1] != 1:
                raise AssertionError(f'not a CONNECT request {req!r}')
            atyp = req[3]
            if atyp == 3:                       # domainname — the rdns proof
                ln = req[4]
                rec['host'] = req[5:5 + ln].decode()
                rec['port'] = int.from_bytes(req[5 + ln:7 + ln], 'big')
                rec['dns_at_proxy'] = True
            elif atyp == 1:                     # client resolved locally
                rec['host'] = socket.inet_ntoa(req[4:8])
                rec['port'] = int.from_bytes(req[8:10], 'big')
                rec['dns_at_proxy'] = False
            else:
                raise AssertionError(f'unexpected ATYP {atyp}')
            conn.sendall(b'\x05\x00\x00\x01' + b'\x00' * 6)  # success
            http_req = conn.recv(4096)
            rec['request_line'] = http_req.split(b'\r\n')[0].decode()
            resp = (b'HTTP/1.1 200 OK\r\n'
                    b'Content-Type: text/html; charset=utf-8\r\n'
                    b'Content-Length: %d\r\n\r\n' % len(self.body)) + self.body
            conn.sendall(resp)
        except Exception as e:                  # pragma: no cover
            rec['error'] = repr(e)
            self.errors.append(rec)
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


class _DeadPort:
    """Context manager yielding a (host, port) that NOTHING listens on."""

    def __enter__(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(('127.0.0.1', 0))
        self.port = s.getsockname()[1]
        s.close()
        return ('127.0.0.1', self.port)

    def __exit__(self, *a):
        return False


class _AliveSocket:
    """Context manager yielding a port that IS listening (TCP only)."""

    def __enter__(self):
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(('127.0.0.1', 0))
        self._srv.listen(1)
        self.port = self._srv.getsockname()[1]
        return self.port

    def __exit__(self, *a):
        try:
            self._srv.close()
        except Exception:
            pass
        return False


def _proxy(port, ptype='socks5', **extra):
    d = {'type': ptype, 'host': '127.0.0.1', 'port': port,
         'username': '', 'password': ''}
    d.update(extra)
    return d


# ---------------------------------------------------------------------------
# proxy_from_config — the config contract
# ---------------------------------------------------------------------------

class TestProxyFromConfig(unittest.TestCase):

    def test_missing_proxy_block(self):
        self.assertIsNone(wf.proxy_from_config({}))
        self.assertIsNone(wf.proxy_from_config(None))

    def test_disabled_proxy(self):
        self.assertIsNone(wf.proxy_from_config(
            {'proxy': {'enabled': False, 'type': 'socks5',
                       'host': '127.0.0.1', 'port': 10808}}))

    def test_use_for_web_false_opts_out(self):
        self.assertIsNone(wf.proxy_from_config(
            {'proxy': {'enabled': True, 'type': 'socks5',
                       'host': '127.0.0.1', 'port': 10808,
                       'use_for_web': False}}))

    def test_use_for_web_MISSING_MEANS_ON(self):
        """THE fix contract: the owner's existing config.json (v0.18.0,
        no use_for_web key) gets proxied web fetches with no Settings
        visit."""
        p = wf.proxy_from_config(
            {'proxy': {'enabled': True, 'type': 'socks5',
                       'host': '127.0.0.1', 'port': 10808}})
        self.assertEqual(p, {'type': 'socks5', 'host': '127.0.0.1',
                             'port': 10808, 'username': '',
                             'password': ''})

    def test_use_for_web_true(self):
        p = wf.proxy_from_config(
            {'proxy': {'enabled': True, 'use_for_web': True}})
        self.assertEqual(p['type'], 'socks5')
        self.assertEqual(p['port'], 10808)

    def test_types_preserved(self):
        for t in ('socks5', 'socks4', 'http'):
            p = wf.proxy_from_config(
                {'proxy': {'enabled': True, 'type': t}})
            self.assertEqual(p['type'], t)

    def test_bad_type_rejected(self):
        self.assertIsNone(wf.proxy_from_config(
            {'proxy': {'enabled': True, 'type': 'mtproto'}}))

    def test_bad_port_rejected(self):
        for port in ('nope', 0, 70000, -1):
            self.assertIsNone(wf.proxy_from_config(
                {'proxy': {'enabled': True, 'port': port}}), port)

    def test_credentials_carried(self):
        p = wf.proxy_from_config(
            {'proxy': {'enabled': True, 'username': 'u', 'password': 'p'}})
        self.assertEqual((p['username'], p['password']), ('u', 'p'))

    def test_proxy_block_not_a_dict(self):
        self.assertIsNone(wf.proxy_from_config({'proxy': 'socks'}))
        self.assertIsNone(wf.proxy_from_config({'proxy': [1, 2]}))

    def test_host_defaults_and_blank(self):
        p = wf.proxy_from_config({'proxy': {'enabled': True}})
        self.assertEqual(p['host'], '127.0.0.1')
        self.assertIsNone(wf.proxy_from_config(
            {'proxy': {'enabled': True, 'host': '   '}}))

    def test_hostile_block_never_raises(self):
        class Boom:
            def get(self, *a):
                raise RuntimeError('boom')
        self.assertIsNone(wf.proxy_from_config(Boom()))


# ---------------------------------------------------------------------------
# The SOCKS pipe — end to end against the fake SOCKS5 server
# ---------------------------------------------------------------------------

class TestSocksPipe(unittest.TestCase):

    def setUp(self):
        self.srv = _FakeSocks5Server()

    def tearDown(self):
        self.srv.stop()

    def test_fetch_full_through_socks5(self):
        r = wf.fetch_url('http://example.test/page', timeout_s=8,
                         rate_limiter=None, proxy=self.srv.proxy)
        self.assertEqual(r.status, 'full', r.reason)
        self.assertEqual(r.http_status, 200)
        self.assertIn(b'Tunnel', r.body)
        self.assertEqual(len(self.srv.seen), 1)
        rec = self.srv.seen[0]
        self.assertEqual(rec['host'], 'example.test')
        self.assertEqual(rec['port'], 80)
        self.assertTrue(rec['request_line'].startswith('GET /page '))

    def test_dns_resolved_at_the_proxy_not_locally(self):
        """The rdns contract: the client must send the HOSTNAME
        (ATYP=3). example.test does not exist in DNS — a local resolve
        would fail with EAI_NONAME (exactly the owner's bug class)."""
        r = wf.fetch_url('http://example.test/rdns', timeout_s=8,
                         rate_limiter=None, proxy=self.srv.proxy)
        self.assertEqual(r.status, 'full', r.reason)
        self.assertTrue(self.srv.seen[0]['dns_at_proxy'])

    def test_dead_proxy_is_a_clear_failed_result(self):
        with _DeadPort() as (host, port):
            r = wf.fetch_url('http://example.test/x', timeout_s=4,
                             rate_limiter=None, proxy=_proxy(port))
        self.assertEqual(r.status, 'failed')
        self.assertIn('proxy', r.reason.lower())
        self.assertIn(f'{host}:{port}', r.reason)

    def test_loopback_never_rides_the_proxy(self):
        """v0.15.1 rule: 127.0.0.1 targets go DIRECT even when a proxy is
        configured — proven with a SEPARATE plain HTTP server on loopback:
        the request must land there verbatim, and the fake SOCKS5 server
        must see NOTHING new (no greeting, no CONNECT)."""
        import http.server

        seen_plain = {}

        class _Plain(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen_plain['request_line'] = self.requestline
                body = b'<html><head><title>Local</title></head></html>'
                self.send_response(200)
                self.send_header('Content-Type', 'text/html')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        plain = http.server.HTTPServer(('127.0.0.1', 0), _Plain)
        import threading
        t = threading.Thread(target=plain.handle_request, daemon=True)
        t.start()
        socks_seen_before = len(self.srv.seen)
        try:
            r = wf.fetch_url(f'http://127.0.0.1:{plain.server_port}/loop',
                             timeout_s=8, rate_limiter=None,
                             proxy=self.srv.proxy)
            self.assertEqual(r.status, 'full', r.reason)
            self.assertIn(b'Local', r.body)
            self.assertEqual(seen_plain.get('request_line'),
                             f'GET /loop HTTP/1.1')
            # The SOCKS server saw no new connection at all.
            self.assertEqual(len(self.srv.seen), socks_seen_before)
        finally:
            plain.server_close()
            t.join(timeout=2)

    def test_preflight_alive_and_dead(self):
        ok, why = wf.web_proxy_preflight(self.srv.proxy, timeout_s=2)
        self.assertTrue(ok, why)
        self.assertEqual(why, '')
        with _DeadPort() as (host, port):
            ok2, why2 = wf.web_proxy_preflight(_proxy(port), timeout_s=1)
        self.assertFalse(ok2)
        self.assertIn(f'{host}:{port}', why2)
        self.assertIn('unreachable', why2)

    def test_preflight_none_and_missing_pysocks(self):
        self.assertEqual(wf.web_proxy_preflight(None),
                         (False, 'no proxy configured'))
        with mock.patch.object(wf, '_socks_module',
                               side_effect=wf.WebProxyError(
                                   'PySocks is not installed')):
            ok, why = wf.web_proxy_preflight(self.srv.proxy)
        self.assertFalse(ok)
        self.assertIn('PySocks', why)


# ---------------------------------------------------------------------------
# The HTTP-type proxy + opener wiring
# ---------------------------------------------------------------------------

class TestHttpProxyAndOpeners(unittest.TestCase):

    def test_http_proxy_handler_absolute_uri(self):
        """The http-type proxy rides urllib's ProxyHandler: the request
        line carries the FULL absolute URI (proxy-side DNS — the same
        protection as rdns=True)."""
        import http.server

        seen = {}

        class _ProxyHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen['request_line'] = self.requestline
                body = b'<html><head><title>Via HTTP proxy</title></head></html>'
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(('127.0.0.1', 0), _ProxyHandler)
        import threading
        t = threading.Thread(target=srv.handle_request, daemon=True)
        t.start()
        try:
            r = wf.fetch_url('http://example.test/via-proxy', timeout_s=8,
                             rate_limiter=None,
                             proxy=_proxy(srv.server_port, ptype='http'))
            self.assertEqual(r.status, 'full', r.reason)
            self.assertIn(b'Via HTTP proxy', r.body)
            self.assertEqual(seen.get('request_line'),
                             'GET http://example.test/via-proxy HTTP/1.1')
        finally:
            srv.server_close()
            t.join(timeout=2)

    def test_http_proxy_handler_url_with_credentials(self):
        h = wf._proxy_handler_for(_proxy(3128, ptype='http',
                                         username='u', password='p'))
        self.assertIsInstance(h, __import__(
            'urllib.request', fromlist=['ProxyHandler']).ProxyHandler)
        self.assertEqual(h.proxies['http'],
                         'http://u:p@127.0.0.1:3128')
        self.assertEqual(h.proxies['https'],
                         'http://u:p@127.0.0.1:3128')

    def test_build_opener_variants(self):
        import ssl
        ctx = ssl.create_default_context()
        # no proxy: today's opener (redirect cap + ssl, no forced handler)
        op = wf._build_opener(None, ctx)
        self.assertTrue(any(isinstance(h, wf._RedirectCap)
                            for h in op.handlers))
        self.assertFalse(any(isinstance(h, wf._SocksHandler)
                             for h in op.handlers))
        # socks proxy: the SOCKS handler rides along
        op2 = wf._build_opener(_proxy(10808), ctx)
        self.assertTrue(any(isinstance(h, wf._SocksHandler)
                            for h in op2.handlers))
        # loopback (force_direct): an EMPTY ProxyHandler is forced and no
        # SOCKS handler exists — never ANY proxy for 127.0.0.1
        op3 = wf._build_opener(_proxy(10808), ctx, force_direct=True)
        self.assertFalse(any(isinstance(h, wf._SocksHandler)
                             for h in op3.handlers))
        # An empty ProxyHandler SUPPRESSES the default (system-proxy)
        # handler and registers no *_open of its own — urllib quirk: it
        # never appears in .handlers. The deterministic assertion is that
        # NO proxying handler of ANY kind is present.
        import urllib.request
        self.assertFalse(
            [h for h in op3.handlers
             if isinstance(h, urllib.request.ProxyHandler)],
            [type(h).__name__ for h in op3.handlers])

    def test_connection_rebinds_create_connection(self):
        """http.client binds socket.create_connection as an INSTANCE
        attribute — the socks classes must re-bind AFTER super().__init__
        (the shadowing trap this test pins)."""
        conn = wf._SocksHTTPConnection(('x', '127.0.0.1', 1, '', ''),
                                       'example.test', 80)
        self.assertIsNotNone(conn._socks_proxy_args)
        self.assertEqual(conn._create_connection.__func__,
                         wf._SocksConnectionMixin._socks_create_connection)
        self.assertNotEqual(conn._create_connection,
                            socket.create_connection)
        conn_s = wf._SocksHTTPSConnection(('x', '127.0.0.1', 1, '', ''),
                                          'example.test', 443)
        self.assertEqual(
            conn_s._create_connection.__func__,
            wf._SocksConnectionMixin._socks_create_connection)

    def test_socks_connect_records_set_proxy_call(self):
        """set_proxy must be asked for rdns=True (send the hostname)."""
        calls = {}

        class _FakeSock:
            def set_proxy(self, *a, **kw):
                calls['args'] = a
                calls['kwargs'] = kw
            def settimeout(self, t):
                calls['timeout'] = t
            def connect(self, address):
                calls['address'] = address
                return None
            def close(self):
                pass

        class _FakeSocksMod:
            SOCKS5, SOCKS4, HTTP = 1, 2, 3
            socksocket = staticmethod(lambda: _FakeSock())

        with mock.patch.object(wf, '_socks_module',
                               return_value=_FakeSocksMod):
            sock = wf._socks_connect((1, '127.0.0.1', 10808, '', ''),
                                     ('example.test', 80), timeout=7)
        self.assertIs(sock, calls.get('_sock') or sock)
        self.assertEqual(calls['args'][0], _FakeSocksMod.SOCKS5)
        self.assertEqual(calls['args'][1], '127.0.0.1')
        self.assertEqual(calls['args'][2], 10808)
        self.assertTrue(calls['kwargs']['rdns'])
        self.assertEqual(calls['address'], ('example.test', 80))
        self.assertEqual(calls['timeout'], 7)


# ---------------------------------------------------------------------------
# WebsiteStateDB — meta + re-arm
# ---------------------------------------------------------------------------

class TestStateDbMetaRearm(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='wpxmeta-')
        self.db = wp.WebsiteStateDB(
            db_path=os.path.join(self.tmp, 'cache.db'))

    def tearDown(self):
        self.db.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_meta_roundtrip(self):
        self.assertIsNone(self.db.get_meta('nope'))
        self.db.set_meta('web_proxy_epoch', 'SOCKS5 127.0.0.1:10808')
        self.assertEqual(self.db.get_meta('web_proxy_epoch'),
                         'SOCKS5 127.0.0.1:10808')
        self.db.set_meta('web_proxy_epoch', 'HTTP 127.0.0.1:3128')
        self.assertEqual(self.db.get_meta('web_proxy_epoch'),
                         'HTTP 127.0.0.1:3128')

    def test_rearm_retries_resets_all_rows(self):
        self.db.enqueue_retry('https://a.test/1', 'refused')
        self.db.enqueue_retry('https://b.test/2', 'refused')
        with self.db._lock:
            self.db.conn.execute(
                "UPDATE website_retry_queue SET attempts=3,"
                " next_attempt_at='2099-01-01T00:00:00'")
            self.db.conn.commit()
        # exhausted: nothing due
        self.assertEqual(self.db.due_retries(), [])
        count = self.db.rearm_retries()
        self.assertEqual(count, 2)
        for url in ('https://a.test/1', 'https://b.test/2'):
            row = self.db.retry_row(url)
            self.assertEqual(row['attempts'], 0)
        self.assertEqual(len(self.db.due_retries()), 2)

    def test_rearm_empty_queue(self):
        self.assertEqual(self.db.rearm_retries(), 0)


# ---------------------------------------------------------------------------
# WebsitePipeline — the proxy wiring, end to end on the constructor
# ---------------------------------------------------------------------------

class _PipeProxyCase(unittest.TestCase):
    """Temp vault + state db + a REAL listening socket for preflight."""

    def setUp(self):
        from gitcurator.core import dryrun
        dryrun.disable()
        dryrun.clear()
        self.tmp = tempfile.mkdtemp(prefix='wpxpipe-')
        self.db = wp.WebsiteStateDB(
            db_path=os.path.join(self.tmp, 'cache.db'))
        self.logs = []
        self._alive = _AliveSocket()
        self.alive_port = self._alive.__enter__()

    def tearDown(self):
        from gitcurator.core import dryrun
        dryrun.disable()
        self._alive.__exit__(None, None, None)
        self.db.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _config(self, **proxy_extra):
        proxy = {'enabled': True, 'type': 'socks5',
                 'host': '127.0.0.1', 'port': self.alive_port}
        proxy.update(proxy_extra)
        return {'website_vault_path': os.path.join(self.tmp, 'w'),
                'web_domain_delay_s': 0, 'proxy': proxy}

    def _make(self, config, fetch_fn=None):
        return wp.WebsitePipeline(
            config=config, llm_call=lambda *a, **k: '{}',
            vault_index_has=lambda u: False, state=self.db,
            log=lambda m, l='info': self.logs.append((l, m)),
            fetch_fn=fetch_fn)

    def _all_logs(self):
        return '\n'.join(m for (_, m) in self.logs)

    def _record_fetch(self):
        """Patch web_fetch.fetch_url (module attr — the pipeline's
        proxied closure resolves it at CALL time) with a recorder that
        returns a canned failed result (no network)."""
        rec = mock.Mock()
        rec.return_value = wf.FetchResult(
            url='https://example.test/', status='failed',
            reason='recorder')
        patcher = mock.patch.object(wp._web_fetch, 'fetch_url', rec)
        patcher.start()
        self.addCleanup(patcher.stop)
        return rec


class TestPipelineProxy(_PipeProxyCase):

    def test_proxy_active_binds_proxied_fetcher(self):
        rec = self._record_fetch()
        pipe = self._make(self._config())
        self.assertIsNotNone(pipe.web_proxy)
        self.assertIn('via SOCKS5 127.0.0.1', self._all_logs())
        self.assertIn('Settings → 🌐 Proxy', self._all_logs())
        pipe.fetch_fn('https://example.test/a', timeout_s=3,
                      rate_limiter=None)
        self.assertEqual(rec.call_count, 1)
        kwargs = rec.call_args.kwargs
        self.assertEqual(kwargs.get('proxy', {}).get('type'), 'socks5')
        self.assertEqual(kwargs.get('proxy', {}).get('port'),
                         self.alive_port)

    def test_proxy_dead_falls_back_direct_with_warning(self):
        with _DeadPort() as (host, port):
            rec = self._record_fetch()
            pipe = self._make(self._config(port=port))
        self.assertIsNone(pipe.web_proxy)
        logs = self._all_logs()
        self.assertIn('NOT reachable', logs)
        self.assertIn('fetching DIRECT', logs)
        pipe.fetch_fn('https://example.test/b', timeout_s=3,
                      rate_limiter=None)
        self.assertEqual(rec.call_count, 1)
        self.assertIsNone(rec.call_args.kwargs.get('proxy'))

    def test_use_for_web_false_never_touches_the_proxy(self):
        rec = self._record_fetch()
        pipe = self._make(self._config(use_for_web=False))
        self.assertIsNone(pipe.web_proxy)
        self.assertNotIn('proxy', self._all_logs().lower())
        pipe.fetch_fn('https://example.test/c', timeout_s=3,
                      rate_limiter=None)
        self.assertIsNone(rec.call_args.kwargs.get('proxy'))

    def test_injected_fetch_fn_stays_verbatim_offline(self):
        """The golden-run guarantee: an injected fetcher is used AS-IS —
        no proxy, no preflight, no network of any kind."""
        calls = []

        def injected(url, **kw):
            calls.append((url, kw))
            return wf.FetchResult(url=url, status='failed',
                                  reason='injected')

        pipe = self._make(self._config(), fetch_fn=injected)
        self.assertIsNone(pipe.web_proxy)
        self.assertEqual(pipe.fetch_fn, injected)
        self.assertNotIn('proxy', self._all_logs().lower())
        r = pipe.fetch_fn('https://example.test/d', timeout_s=1)
        self.assertEqual(r.reason, 'injected')
        self.assertEqual(calls[0][0], 'https://example.test/d')

    def test_disabled_proxy_config(self):
        rec = self._record_fetch()
        cfg = self._config()
        cfg['proxy']['enabled'] = False
        pipe = self._make(cfg)
        self.assertIsNone(pipe.web_proxy)
        pipe.fetch_fn('https://example.test/e', timeout_s=3,
                      rate_limiter=None)
        self.assertIsNone(rec.call_args.kwargs.get('proxy'))


class TestPipelineRearm(_PipeProxyCase):

    def _exhaust(self, *urls):
        for u in urls:
            self.db.enqueue_retry(u, 'connection refused')
        with self.db._lock:
            self.db.conn.execute(
                "UPDATE website_retry_queue SET attempts=3,"
                " next_attempt_at='2099-01-01T00:00:00'")
            self.db.conn.commit()

    def test_first_active_proxy_rearms_exhausted_queue(self):
        self._exhaust('https://x.com/1', 'https://x.com/2')
        self._record_fetch()
        pipe = self._make(self._config())
        logs = self._all_logs()
        self.assertIn('re-armed 2 queued retry(ies)', logs)
        for u in ('https://x.com/1', 'https://x.com/2'):
            self.assertEqual(self.db.retry_row(u)['attempts'], 0)
        self.assertEqual(len(self.db.due_retries()), 2)

    def test_same_epoch_never_rearms_twice(self):
        self._exhaust('https://x.com/1')
        self._record_fetch()
        self._make(self._config())                 # epoch stored, re-armed
        self.db.enqueue_retry('https://x.com/2', 'refused')
        self.logs.clear()
        pipe = self._make(self._config())          # SAME proxy epoch
        self.assertNotIn('re-armed', self._all_logs())
        # the second link keeps its natural backoff attempts
        self.assertEqual(self.db.retry_row('https://x.com/2')['attempts'], 1)

    def test_changed_epoch_rearms_again(self):
        self._exhaust('https://x.com/1')
        self._record_fetch()
        self._make(self._config())                 # SOCKS5 port A
        with _AliveSocket() as other_port:
            self._exhaust('https://x.com/2')
            self.logs.clear()
            self._make(self._config(port=other_port))   # new epoch
        self.assertIn('re-armed', self._all_logs())

    def test_rearm_failure_is_tolerated(self):
        class _BoomState(self.db.__class__):
            def get_meta(self, key):
                raise sqlite3.OperationalError('boom')
        pipe = self._make(self._config())
        pipe.state = _BoomState.__new__(_BoomState)
        pipe.state.db_path = self.db.db_path
        pipe._maybe_rearm_retries()               # must not raise
        self.assertIn('Retry re-arm skipped', self._all_logs())


# ---------------------------------------------------------------------------
# GUI wiring — the Settings toggle (source assertions, no GUI shown)
# ---------------------------------------------------------------------------

class TestGuiWiring(unittest.TestCase):

    def test_proxy_tab_checkbox(self):
        import gitcurator.gui.app as gui_app
        src = inspect.getsource(gui_app.MainWindow.initUI)
        self.assertIn('self.proxy_use_for_web = QCheckBox(', src)
        self.assertIn('Use this proxy for web fetches too', src)

    def test_checkbox_defaults_on(self):
        import gitcurator.gui.app as gui_app
        src = inspect.getsource(gui_app.MainWindow.initUI)
        self.assertIn(".get('use_for_web', True)", src)

    def test_save_config_carries_use_for_web(self):
        import gitcurator.gui.app as gui_app
        src = inspect.getsource(gui_app.MainWindow.save_config)
        self.assertIn('"use_for_web": self.proxy_use_for_web.isChecked()',
                      src)

    def test_get_proxy_dict_carries_use_for_web(self):
        import gitcurator.gui.app as gui_app
        src = inspect.getsource(gui_app.MainWindow._get_proxy_dict)
        self.assertIn('"use_for_web": self.proxy_use_for_web.isChecked()',
                      src)

    def test_tooltip_names_loopback_rule(self):
        import gitcurator.gui.app as gui_app
        src = inspect.getsource(gui_app.MainWindow.initUI)
        self.assertIn('NEVER proxied', src)

    def test_widget_referenced_by_tab_layout(self):
        import gitcurator.gui.app as gui_app
        src = inspect.getsource(gui_app.MainWindow.initUI)
        self.assertIn('proxy_layout.addRow(self.proxy_use_for_web)', src)


# ---------------------------------------------------------------------------
# Defaults + packaging
# ---------------------------------------------------------------------------

class TestDefaultsAndPackaging(unittest.TestCase):

    def test_constants_default_on(self):
        from gitcurator import constants as c1
        from gitcurator.gui import constants as c2
        for mod in (c1, c2):
            self.assertIs(mod.CONFIG_EXAMPLE['proxy']['use_for_web'], True,
                          mod.__name__)

    def test_requirements_lists_pysocks(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, 'requirements.txt'),
                  encoding='utf-8', errors='replace') as f:
            req = f.read()
        self.assertIn('PySocks', req)

    def test_user_agent_version_bumped(self):
        self.assertIn('GitCurator/0.19', wf.USER_AGENT)


if __name__ == '__main__':
    unittest.main()
