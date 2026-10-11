#!/usr/bin/env python3
"""
tests/test_realdoor.py — v0.55.0, THE OWNER'S OWN WINDOW; THE RIGHT ROUTE.

The owner's report (session, verbatim): "it still says under bot
challenge. only 1 site (from 4 test sites) appeared, but app didn't
even succeed to fetch it and generate content for it. if you think it
could help circumvent the bot detection method, the system must be
able to open tabs in my real chrome instance instead, also the app
must automatically open them with proxy on and off, because some
sites needed proxy, while some didn't need and proxy caused problems."

The laws this module pins (pure stdlib; no browser ever launches —
a loopback /json/version server stands in for the owner's Chrome, a
scripted socket for its DevTools WebSocket, a shell script for the
browser binary):

  1. THE VERDICT'S HONESTY — a page with real content that carries a
     challenge marker EMBEDS a widget, it is not stuck on a wall; the
     weak title words only count on an interstitial's short title.
  2. THE OWNER'S OWN WINDOW — a DevTools port that answers is driven
     AS IS (the tabs open in his browser; the door never closes it —
     only its own tabs), and the real-profile rung launches HIS
     profile through the attach link only when his Chrome is closed.
  3. THE RIGHT ROUTE — the two legs (direct first, then the owner's
     proxy), the escalation of the first leg's failures onto the
     second in the same delivery, and the per-site route memory that
     remembers only the route that actually delivered.
  4. THE ATTACH SCRIPT — the one-time chrome-attach.bat that teaches
     the owner's Chrome to answer (pure ASCII + CRLF, the .bat law).
"""

import json
import os
import shutil
import socket
import stat
import subprocess
import tempfile
import threading
import time
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from gitcurator.core import chrome_tabs as ct
from gitcurator.core import dryrun

_URL = 'https://real.example.net/page'

#: the real CDPSocket class, captured at import time (the leg tests
#: patch ct.CDPSocket itself — the crafted sockets still need the
#: genuine class to instantiate).
_REAL_CDP = ct.CDPSocket
_URL2 = 'https://walled.example.org/other'
_URL3 = 'https://proxied.example.com/x'

_PROXY = {'type': 'socks5', 'host': '127.0.0.1', 'port': 10808,
          'username': '', 'password': ''}

#: A rich, REAL page (well past the visible-text floor).
_REAL_HTML = ('<html><head><title>The Real Page</title></head><body>'
              '<nav><a href="/a">A</a> <a href="/b">B</a></nav>'
              '<main><p>' + ('r' * 2000)
              + '</p><div class="content">more real content</div>'
              '</main></body></html>')

#: The same page with a Turnstile WIDGET embedded (the login form's
#: protection) — the v0.55 law: the widget is not the wall.
_WIDGET_HTML = ('<html><head><title>The Real Page</title></head><body>'
                '<nav><a href="/a">A</a> <a href="/b">B</a></nav>'
                '<main><p>' + ('r' * 2000) + '</p>'
                '<form><div class="cf-turnstile" data-sitekey="x">'
                '</div><script src="challenges.cloudflare.com/'
                'turnstile/v0/api.js"></script></form>'
                '</main></body></html>')

#: A THIN page carrying the interstitial machinery — still a wall.
_THIN_CHALLENGE = ('<html><head><title>Some title</title></head><body>'
                   '<script src="/cdn-cgi/challenge-platform/h/b/or.js">'
                   '</script>' + ('j' * 400) + '</body></html>')


# ---------------------------------------------------------------------------
# the scripted-socket kit (the fifthdoor/persistentdoor pattern)
# ---------------------------------------------------------------------------

def _srv_frame(payload: bytes, opcode: int = 0x1) -> bytes:
    head = bytearray([0x80 | opcode])
    n = len(payload)
    if n < 126:
        head.append(n)
    elif n < 65536:
        head.append(126)
        head += n.to_bytes(2, 'big')
    else:
        head.append(127)
        head += n.to_bytes(8, 'big')
    return bytes(head) + payload


_HANDSHAKE = (b'HTTP/1.1 101 Switching Protocols\r\n'
              b'Upgrade: websocket\r\n'
              b'Connection: Upgrade\r\n'
              b'Sec-WebSocket-Accept: abcdefghijklmnopqrstuvwx\r\n\r\n')


class _FakeSock:
    """A scripted socket: recv() pops the next chunk; sendall() records
    what was sent."""

    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.sent = []
        self.timeout = None
        self.closed = False

    def recv(self, n):
        if not self._chunks:
            raise socket.timeout('stalled')
        return self._chunks.pop(0)

    def sendall(self, data):
        self.sent.append(bytes(data))

    def settimeout(self, t):
        self.timeout = t

    def close(self):
        self.closed = True
        self._chunks = []


def _resp(id_, value):
    return _srv_frame(json.dumps(
        {'id': id_, 'result': {'result': {'value': value}}}).encode())


def _probe(id_, state='complete', href=None):
    return _resp(id_, f'{state}|{href or _URL}')


def _dom(id_, html):
    return _resp(id_, html)


#: The tab target the sessions hand the fetch driver.
_TAB = {'id': 'T1', 'webSocketDebuggerUrl':
        'ws://127.0.0.1:9222/devtools/page/T1'}


# ---------------------------------------------------------------------------
# the DevTools endpoint — a real loopback http server (no browser)
# ---------------------------------------------------------------------------

class _VersionHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path != '/json/version':
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps({
            'Browser': 'Chrome/999.0',
            'webSocketDebuggerUrl':
                f'ws://127.0.0.1:{self.server.server_address[1]}'
                f'/devtools/browser/test-uuid',
        }).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_PUT = do_GET

    def log_message(self, *args):
        pass  # a quiet server (the test output stays readable)


class _Endpoint:
    """A threaded /json/version server on a free loopback port."""

    def __init__(self):
        self.srv = ThreadingHTTPServer(('127.0.0.1', 0),
                                        _VersionHandler)
        self.port = self.srv.server_address[1]
        self.thread = threading.Thread(
            target=self.srv.serve_forever, daemon=True,
            name='gitcurator-test-endpoint')
        self.thread.start()

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.thread.join(timeout=5)


def _fake_exe(lines, then='sleep 30', argv_out=''):
    """A fake chrome 'exe' (the fifth-door pattern): a shell script
    that prints the DevTools line Chrome itself prints, then sleeps.
    ``argv_out`` records the exact argv the session launched it with."""
    path = os.path.join(tempfile.mkdtemp(prefix='fakechrome-'),
                        'chrome')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('#!/bin/sh\n')
        if argv_out:
            f.write(f'printf \'%s\\n\' "$@" > "{argv_out}"\n')
        for line in lines:
            f.write(f'echo "{line}" 1>&2\n')
        f.write(then + '\n')
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


_DEVTOOLS_LINE = ('DevTools listening on ws://127.0.0.1:0/devtools/'
                  'browser/none')


# ---------------------------------------------------------------------------
# 1. the verdict's honesty (pure)
# ---------------------------------------------------------------------------

class TestVerdictHonesty(unittest.TestCase):
    """The v0.55 grammar: a marker on a page with real CONTENT is a
    widget, not a wall; the weak title words only count on a short
    (interstitial) title. Everything the v0.53 laws pinned stays."""

    def test_a_rich_page_with_an_embedded_turnstile_is_the_site(self):
        # THE owner's bug: the site loaded (the tab appeared) but the
        # verdict called it a challenge — the login form's Turnstile
        # widget tripped the HTML marker. A real page with real text
        # is the SITE, whatever widget it embeds.
        ok, reason = ct.page_is_real(_WIDGET_HTML, _URL, 'The Real Page')
        self.assertTrue(ok, reason)

    def test_a_rich_page_with_the_challenge_platform_script_is_the_site(self):
        html = ('<html><head><title>The Real Page</title></head><body>'
                '<main><p>' + ('r' * 2000) + '</p></main>'
                '<script src="/cdn-cgi/challenge-platform/h/b/or.js">'
                '</script></body></html>')
        ok, reason = ct.page_is_real(html, _URL, 'The Real Page')
        self.assertTrue(ok, reason)

    def test_a_thin_page_with_the_machinery_is_still_a_challenge(self):
        # the pinned v0.53 law, unchanged: an interstitial is a script
        # plus a sentence — thin AND marked is the wall itself
        ok, reason = ct.page_is_real(_THIN_CHALLENGE, _URL, 'Some title')
        self.assertFalse(ok)
        self.assertIn('challenge', reason)
        self.assertIn('challenge-platform', reason)

    def test_a_thin_page_with_only_a_turnstile_is_a_challenge(self):
        html = ('<html><head><title>Verify</title></head><body>'
                '<div class="cf-turnstile"></div></body></html>')
        ok, reason = ct.page_is_real(html, _URL, 'Verify')
        self.assertFalse(ok)
        self.assertIn('turnstile', reason)

    def test_the_strong_title_is_decisive_even_on_a_rich_page(self):
        # 'Just a moment...' is the interstitial's own name — a page
        # that says it IS the challenge, however much DOM it carries
        ok, reason = ct.page_is_real(_WIDGET_HTML, _URL,
                                     'Just a moment...')
        self.assertFalse(ok)
        self.assertIn('just a moment', reason)

    def test_a_long_real_title_containing_blocked_is_the_site(self):
        # the weak word as a substring of a real page's long title
        # ('Blocked and Reported — the podcast') is not a wall
        ok, reason = ct.page_is_real(_REAL_HTML, _URL,
                                     'Blocked and Reported — episode 42')
        self.assertTrue(ok, reason)
        # even a short-ish REAL title with more than four words
        ok, reason = ct.page_is_real(_REAL_HTML, _URL,
                                     'Site blocked my account settings')
        self.assertTrue(ok, reason)

    def test_a_short_interstitial_title_still_reads_as_the_challenge(self):
        for title in ('Blocked', 'Access denied | site', 'Security check'):
            ok, reason = ct.page_is_real(_REAL_HTML, _URL, title)
            self.assertFalse(ok, title)
            self.assertIn('challenge', reason)

    def test_an_interstitial_title_also_needs_the_word_count(self):
        # the two laws together: short AND few words — an interstitial's
        # title IS the sentence
        ok, _ = ct.page_is_real(_REAL_HTML, _URL,
                                'Access denied: your account was '
                                'suspended for suspicious activity')
        self.assertTrue(ok)

    def test_the_err_code_law_is_unchanged(self):
        html = ('<html><head><title>x</title></head><body>'
                'ERR_CONNECTION_RESET</body></html>')
        ok, reason = ct.page_is_real(html, _URL, 'x')
        self.assertFalse(ok)
        self.assertIn("Chrome's error page", reason)

    def test_the_visible_text_len_strips_scripts_and_styles(self):
        html = ('<html><head><style>.a{color:red}</style></head>'
                '<body><script>var x="challenge-platform";</script>'
                '<p>' + ('t' * 300) + '</p></body></html>')
        self.assertLess(ct._visible_text_len(html), 310)
        self.assertGreaterEqual(ct._visible_text_len(html), 295)

    def test_the_crash_page_law_is_unchanged(self):
        ok, reason = ct.page_is_real(_REAL_HTML, _URL, 'Aw, Snap!')
        self.assertFalse(ok)
        self.assertIn('crashed', reason)


# ---------------------------------------------------------------------------
# 2. the right route — the legs and the memory (pure)
# ---------------------------------------------------------------------------

class TestProxyLegs(unittest.TestCase):

    def test_the_socks_proxy_becomes_the_chrome_flag(self):
        self.assertEqual(ct.chrome_proxy_flag(_PROXY),
                         ['--proxy-server=socks5://127.0.0.1:10808'])

    def test_the_http_proxy_becomes_the_chrome_flag(self):
        flag = ct.chrome_proxy_flag({'type': 'http', 'host': '10.0.0.2',
                                     'port': 3128})
        self.assertEqual(flag, ['--proxy-server=http://10.0.0.2:3128'])

    def test_no_proxy_is_no_flag(self):
        self.assertEqual(ct.chrome_proxy_flag(None), [])
        self.assertEqual(ct.chrome_proxy_flag({}), [])

    def test_the_mode_normalizes_honestly(self):
        self.assertEqual(ct.normalize_proxy_mode('auto'), 'auto')
        self.assertEqual(ct.normalize_proxy_mode('OFF'), 'off')
        self.assertEqual(ct.normalize_proxy_mode('on'), 'on')
        self.assertEqual(ct.normalize_proxy_mode('junk'), 'auto')
        self.assertEqual(ct.normalize_proxy_mode(None), 'auto')

    def test_auto_with_a_proxy_splits_by_memory(self):
        # unknown + remembered-direct ride the direct leg; the
        # remembered-proxy urls ride the proxy leg; direct first
        memory = {'walled.example.org': 'proxy',
                  'settled.example.net': 'direct'}
        legs = ct.plan_proxy_legs([_URL, _URL2, _URL3], memory, _PROXY)
        self.assertEqual([r for r, _ in legs], ['direct', 'proxy'])
        self.assertEqual(legs[0][1], [_URL, _URL3])
        self.assertEqual(legs[1][1], [_URL2])

    def test_mode_off_never_rides_the_proxy(self):
        legs = ct.plan_proxy_legs([_URL, _URL2], {'walled.example.org':
                                                  'proxy'},
                                  _PROXY, mode='off')
        self.assertEqual(legs, [('direct', [_URL, _URL2])])

    def test_mode_on_rides_only_the_proxy(self):
        legs = ct.plan_proxy_legs([_URL, _URL2], {}, _PROXY, mode='on')
        self.assertEqual(legs, [('proxy', [_URL, _URL2])])

    def test_mode_on_without_a_proxy_degrades_to_direct(self):
        legs = ct.plan_proxy_legs([_URL], {}, None, mode='on')
        self.assertEqual(legs, [('direct', [_URL])])

    def test_auto_without_a_proxy_is_the_direct_leg_only(self):
        legs = ct.plan_proxy_legs([_URL, _URL2], {'walled.example.org':
                                                  'proxy'}, None)
        self.assertEqual(legs, [('direct', [_URL, _URL2])])

    def test_the_domain_key_strips_www(self):
        self.assertEqual(ct.domain_of_url('https://WWW.Example.net/x'),
                         'example.net')
        self.assertEqual(ct.domain_of_url('https://example.net/x'),
                         'example.net')

    def test_remember_writes_only_earned_routes(self):
        memory = {}
        ct.remember_route(memory, 'https://a.example.net/1', 'direct')
        ct.remember_route(memory, 'https://b.example.net/2', 'proxy')
        ct.remember_route(memory, 'https://c.example.net/3', 'junk')
        ct.remember_route(memory, 'not a url', 'direct')
        self.assertEqual(memory, {'a.example.net': 'direct',
                                  'b.example.net': 'proxy'})

    def test_the_memory_file_round_trips(self):
        tmp = tempfile.mkdtemp(prefix='rtmem-')
        try:
            path = os.path.join(tmp, 'mem.json')
            self.assertEqual(ct.load_proxy_memory(path), {})
            self.assertTrue(ct.save_proxy_memory(
                path, {'a.example.net': 'direct'}))
            self.assertEqual(ct.load_proxy_memory(path),
                             {'a.example.net': 'direct'})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_a_corrupt_or_junk_memory_reads_as_empty(self):
        tmp = tempfile.mkdtemp(prefix='rtmem-')
        try:
            path = os.path.join(tmp, 'mem.json')
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write('{not json')
            self.assertEqual(ct.load_proxy_memory(path), {})
            with open(path, 'w', encoding='utf-8') as fh:
                json.dump({'a.example.net': 'junk', 'b': 3}, fh)
            self.assertEqual(ct.load_proxy_memory(path), {})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_an_unwritable_memory_never_raises(self):
        # a parent that is a FILE — no directory can ever be made there
        blocker = os.path.join(tempfile.mkdtemp(prefix='rtblk-'),
                               'blocker')
        with open(blocker, 'w', encoding='utf-8') as fh:
            fh.write('a file, not a dir')
        try:
            self.assertFalse(ct.save_proxy_memory(
                os.path.join(blocker, 'mem.json'), {}))
        finally:
            shutil.rmtree(os.path.dirname(blocker),
                          ignore_errors=True)
        self.assertFalse(ct.save_proxy_memory(None, {}))

    def test_the_default_memory_path_beside_the_identity(self):
        self.assertTrue(ct.proxy_memory_path().endswith(
            ct.PROXY_MEMORY_FILENAME))

    def test_the_direct_leg_is_truly_off(self):
        # the owner's words: "proxy caused problems" — the direct leg
        # bypasses even a SYSTEM proxy, it is not merely "no flag"
        self.assertEqual(ct._DIRECT_LEG_FLAGS, ['--no-proxy-server'])


# ---------------------------------------------------------------------------
# 3. the owner's own window — the attach session (a real loopback
# endpoint stands in for his Chrome; no browser launches)
# ---------------------------------------------------------------------------

class TestAttachSession(unittest.TestCase):

    def setUp(self):
        self._endpoint = _Endpoint()
        self._tmp = tempfile.mkdtemp(prefix='rtattach-')

    def tearDown(self):
        self._endpoint.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_alive_is_the_endpoints_answer(self):
        session = ct.AttachedChromeSession(self._endpoint.port)
        self.assertTrue(session.alive())
        self.assertEqual(session.port, self._endpoint.port)
        self.assertIsNone(session.proc)     # nothing was launched

    def test_close_is_gentle_the_endpoint_still_answers(self):
        # THE law: the door NEVER closes the owner's browser. close()
        # only closes the door's OWN tabs (best-effort against an
        # endpoint that does not know them) — the browser lives on.
        session = ct.AttachedChromeSession(self._endpoint.port)
        session._opened = ['T1', 'T2']
        session.close()
        self.assertEqual(session._opened, [])
        self.assertTrue(session.alive())

    def test_close_is_idempotent_and_never_raises(self):
        session = ct.AttachedChromeSession(self._endpoint.port)
        session.close()
        session.close()

    def test_new_tab_against_a_silent_endpoint_is_an_honest_error(self):
        session = ct.AttachedChromeSession(1)   # nothing listens there
        with self.assertRaises(ct.CDPError):
            session.new_tab(_URL)

    def test_the_driver_attaches_before_it_launches_anything(self):
        # the owner's Chrome answers on the attach port → the tabs open
        # in HIS browser: the own-launch ladder is NEVER taken
        launched = []

        def _boom(exe, log, **kw):
            launched.append(exe)
            raise AssertionError('the door must attach, not launch')

        logs = []
        with mock.patch.object(ct, 'ChromeSession', _boom), \
                mock.patch.object(ct, '_launch_identity_session', _boom):
            out = ct.fetch_pages_via_chrome(
                [_URL], log=lambda m, l: logs.append(m),
                chrome_exe='/bin/true',
                attach_port=self._endpoint.port)
        self.assertEqual(launched, [])
        # the ATTACHED story is told, in the owner's own words
        joined = '\n'.join(logs)
        self.assertIn('ATTACHED to your own running Chrome', joined)
        self.assertIn('closes only its own tabs', joined)
        # the endpoint knows no /json/new → the per-link failures are
        # honest, and the endpoint is STILL alive afterwards (gentle)
        self.assertEqual(len(out), 1)
        self.assertFalse(out[0]['ok'])
        self.assertIn('Chrome would not open a tab', out[0]['error'])
        self.assertTrue(ct._probe_version_endpoint(self._endpoint.port))

    def test_the_no_chrome_law_answers_before_the_attach_probe(self):
        # the honest order: the door names the browser it needs first —
        # a machine where no chrome.exe can be found answers 'not
        # found' even when some endpoint answers (the door never
        # drives a browser it cannot name)
        with mock.patch('gitcurator.core.hand_delivery.find_chrome',
                        return_value=None):
            out = ct.fetch_pages_via_chrome(
                [_URL], log=lambda *a, **k: None,
                attach_port=self._endpoint.port)
        self.assertEqual(len(out), 1)
        self.assertFalse(out[0]['ok'])
        self.assertIn('not found', out[0]['error'].lower())

    def test_attach_disabled_falls_to_the_own_session(self):
        made = []

        class _FakeSession:
            port = 1

            def new_tab(self, url):
                made.append(url)
                raise ct.CDPError('no tabs in this test')

            def close_tab(self, tid):
                pass

            def alive(self):
                return True

            def close(self):
                pass

        out = ct.fetch_pages_via_chrome(
            [_URL], log=lambda *a, **k: None, chrome_exe='/bin/true',
            attach=False, attach_port=self._endpoint.port,
            _session_factory=lambda exe, log: _FakeSession())
        self.assertEqual(made, [_URL])       # the factory WAS called
        self.assertFalse(out[0]['ok'])

    def test_a_silent_attach_port_falls_to_the_own_session(self):
        made = []

        class _FakeSession:
            port = 1

            def new_tab(self, url):
                made.append(url)
                raise ct.CDPError('no tabs in this test')

            def close_tab(self, tid):
                pass

            def alive(self):
                return True

            def close(self):
                pass

        out = ct.fetch_pages_via_chrome(
            [_URL], log=lambda *a, **k: None, chrome_exe='/bin/true',
            attach_port=1,                     # nothing answers there
            _session_factory=lambda exe, log: _FakeSession())
        self.assertEqual(made, [_URL])
        self.assertFalse(out[0]['ok'])

    def test_the_attached_story_is_told_in_the_log(self):
        # a SILENT attach port tells the owner honestly — and points
        # at the More ▸ 🪄 action that teaches his Chrome to answer
        logs = []

        class _FakeSession:
            port = 1

            def new_tab(self, url):
                raise ct.CDPError('no tabs in this test')

            def close_tab(self, tid):
                pass

            def alive(self):
                return True

            def close(self):
                pass

        with mock.patch.object(ct, 'ChromeSession', _FakeSession):
            ct.fetch_pages_via_chrome(
                [_URL], log=lambda m, l: logs.append(m),
                chrome_exe='/bin/true', attach_port=1)
        joined = '\n'.join(logs)
        self.assertIn('did not answer', joined)
        self.assertIn('Attach to my Chrome', joined)


# ---------------------------------------------------------------------------
# 4. the real-profile rung (fake exes + a fake real profile; the
# running-Chrome pre-check is the seam)
# ---------------------------------------------------------------------------

class TestRealProfileRung(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix='rtreal-')
        self._data = os.path.join(self._tmp, 'User Data')
        os.makedirs(os.path.join(self._data, 'Default'), exist_ok=True)
        with open(os.path.join(self._data, 'marker.txt'), 'w',
                  encoding='utf-8') as fh:
            fh.write('the owner\u2019s cookies live here')

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _exe(self, argv_out=None):
        return _fake_exe([_DEVTOOLS_LINE], argv_out=argv_out)

    def test_the_rung_launches_his_profile_through_the_link(self):
        argv_out = os.path.join(self._tmp, 'argv.txt')
        exe = self._exe(argv_out)
        link = os.path.join(self._tmp, 'chrome-real-link')
        os.symlink(self._data, link)      # the rung's attach link
        with mock.patch.object(ct, 'real_user_data_dir',
                               return_value=self._data), \
                mock.patch.object(ct, 'real_profile_link',
                                  return_value=link), \
                mock.patch.object(ct, 'chrome_is_running',
                                  return_value=False):
            logs = []
            session = ct._try_real_profile_session(
                exe, lambda m, l: logs.append(m), 10.0,
                ['--no-proxy-server'])
        try:
            self.assertIsNotNone(session)
            # the profile IS the attach link, and the link points at
            # his real data
            self.assertEqual(session.profile, link)
            self.assertEqual(os.path.realpath(link),
                             os.path.realpath(self._data))
            self.assertTrue(any('YOUR REAL PROFILE' in m for m in logs))
            # the leg's flag rode the launch argv
            with open(argv_out, encoding='utf-8') as fh:
                argv = fh.read()
            self.assertIn(f'--user-data-dir={link}', argv)
            self.assertIn('--no-proxy-server', argv)
        finally:
            if session is not None:
                session.close()
        # the owner's data was never erased, and the link survives
        self.assertTrue(os.path.isfile(
            os.path.join(self._data, 'marker.txt')))
        self.assertTrue(os.path.exists(link))

    def test_the_attach_link_is_created_reused_and_repointed(self):
        # the link itself: created in the given dir, reused when it
        # points at the same data, RE-POINTED when it is stale or
        # dangling (a leftover of another run, a moved profile)
        other = os.path.join(self._tmp, 'Other Data')
        os.makedirs(other, exist_ok=True)
        link = ct.real_profile_link(self._data, self._tmp)
        self.assertEqual(link, os.path.join(self._tmp,
                                            'chrome-real-link'))
        self.assertEqual(os.path.realpath(link),
                         os.path.realpath(self._data))
        # reuse: the same data → the same link, no recreation
        self.assertEqual(ct.real_profile_link(self._data, self._tmp),
                         link)
        # re-point: a stale link to OTHER data is fixed, not trusted
        os.unlink(link)
        os.symlink(other, link)
        fixed = ct.real_profile_link(self._data, self._tmp)
        self.assertEqual(os.path.realpath(fixed),
                         os.path.realpath(self._data))
        # dangling: a link to a deleted target is re-pointed too
        os.unlink(link)
        os.symlink(os.path.join(self._tmp, 'gone'), link)
        fixed = ct.real_profile_link(self._data, self._tmp)
        self.assertEqual(os.path.realpath(fixed),
                         os.path.realpath(self._data))
        # the target is never erased by any of it
        self.assertTrue(os.path.isdir(other))
        self.assertTrue(os.path.isfile(
            os.path.join(self._data, 'marker.txt')))

    def test_no_data_dir_means_no_link(self):
        self.assertIsNone(ct.real_profile_link(
            os.path.join(self._tmp, 'nothing-here'), self._tmp))

    def test_the_rung_is_skipped_when_his_chrome_is_running(self):
        # never fought, never killed — the honest log says how to
        # teach it to answer instead
        exe = self._exe()
        with mock.patch.object(ct, 'real_user_data_dir',
                               return_value=self._data), \
                mock.patch.object(ct, 'chrome_is_running',
                                  return_value=True):
            logs = []
            session = ct._try_real_profile_session(
                exe, lambda m, l: logs.append(m), 10.0, [])
        self.assertIsNone(session)
        joined = '\n'.join(logs)
        self.assertIn('will not fight', joined)
        self.assertIn('Attach to my Chrome', joined)

    def test_the_rung_is_skipped_when_no_real_profile_exists(self):
        with mock.patch.object(ct, 'real_user_data_dir',
                               return_value=None), \
                mock.patch.object(ct, 'chrome_is_running',
                                  return_value=False):
            session = ct._try_real_profile_session(
                self._exe(), lambda *a: None, 10.0, [])
        self.assertIsNone(session)

    def test_the_rungs_launch_failure_falls_back_honestly(self):
        with mock.patch.object(ct, 'real_user_data_dir',
                               return_value=self._data), \
                mock.patch.object(ct, 'real_profile_link',
                                  return_value=os.path.join(
                                      self._tmp, 'chrome-real-link')), \
                mock.patch.object(ct, 'chrome_is_running',
                                  return_value=False), \
                mock.patch.object(ct, 'ChromeSession',
                                  side_effect=ct.CDPError('refused')):
            logs = []
            session = ct._try_real_profile_session(
                self._exe(), lambda m, l: logs.append(m), 10.0, [])
        self.assertIsNone(session)
        self.assertTrue(any('own identity' in m for m in logs))

    def test_an_explicit_profile_dir_pins_the_identity(self):
        # the caller's explicit profile_dir IS an identity choice —
        # the real-profile rung does not override it
        calls = {}

        class _FakeSessionCls:
            def __init__(self, exe, log=None, page_timeout_s=45.0,
                         profile_dir=None, persist=True,
                         extra_flags=None, real_profile=False):
                calls['profile_dir'] = profile_dir
                calls['real_profile'] = real_profile
                self.port = 1

            def new_tab(self, url):
                raise ct.CDPError('no tabs in this test')

            def close_tab(self, tid):
                pass

            def alive(self):
                return True

            def close(self):
                pass

        with mock.patch.object(ct, 'ChromeSession', _FakeSessionCls), \
                mock.patch.object(ct, 'real_user_data_dir',
                                  return_value=self._data), \
                mock.patch.object(ct, 'chrome_is_running',
                                  return_value=False):
            ct._launch_identity_session(
                '/bin/true', lambda *a: None, 10.0, '/tmp/somewhere',
                True, True, [])
        self.assertEqual(calls['profile_dir'], '/tmp/somewhere')
        self.assertFalse(calls['real_profile'])

    def test_the_ladder_falls_to_the_dedicated_identity(self):
        calls = {}

        class _FakeSessionCls:
            def __init__(self, exe, log=None, page_timeout_s=45.0,
                         profile_dir=None, persist=True,
                         extra_flags=None, real_profile=False):
                calls['profile_dir'] = profile_dir
                calls['persist'] = persist
                calls['extra_flags'] = list(extra_flags or [])
                self.port = 1

            def new_tab(self, url):
                raise ct.CDPError('no tabs in this test')

            def close_tab(self, tid):
                pass

            def alive(self):
                return True

            def close(self):
                pass

        with mock.patch.object(ct, 'ChromeSession', _FakeSessionCls), \
                mock.patch.object(ct, 'real_user_data_dir',
                                  return_value=None):
            ct._launch_identity_session(
                '/bin/true', lambda *a: None, 10.0, None, True,
                True, ['--no-proxy-server'])
        self.assertIsNone(calls['profile_dir'])
        self.assertTrue(calls['persist'])
        self.assertEqual(calls['extra_flags'], ['--no-proxy-server'])

    def test_the_chrome_is_running_pre_check_is_patchable(self):
        # the seam the tests own (a Windows tasklist on a machine
        # without one must read as False, never raise)
        with mock.patch.object(ct, 'chrome_is_running',
                               return_value=True):
            self.assertTrue(ct.chrome_is_running())
        with mock.patch.object(ct, 'chrome_is_running',
                               return_value=False):
            self.assertFalse(ct.chrome_is_running())


# ---------------------------------------------------------------------------
# 5. the leg driver — the two routes in one delivery (scripted
# sessions record their urls; a scripted socket delivers a page)
# ---------------------------------------------------------------------------

class _RecorderSession:
    """A session the leg tests can see through: new_tab records the
    url. When ``tab`` is set the wave proceeds into _fetch_opened_tab
    (whose CDPSocket is patched to a scripted socket); otherwise the
    tab open fails honestly."""

    def __init__(self, tab=None, open_error=None):
        self.port = 9222
        self.urls = []
        self.closed = False
        self._tab = tab
        self._open_error = open_error
        self.closed_tabs = []

    def new_tab(self, url):
        self.urls.append(url)
        if self._open_error is not None:
            raise ct.CDPError(self._open_error)
        return dict(self._tab or _TAB)

    def close_tab(self, target_id):
        self.closed_tabs.append(target_id)

    def alive(self):
        return True

    def close(self):
        self.closed = True


class TestLegDriver(unittest.TestCase):

    def _driver(self, sessions, urls, proxy=_PROXY, mode='auto',
                memory_path=None, memory=None, log=None):
        """Drive fetch_pages_via_chrome with a factory that hands out
        the scripted sessions one per leg (recording what each leg
        was asked to gather)."""
        made = list(sessions)
        calls = {'n': 0}
        logs = log if log is not None else []

        def _factory(exe, lg):
            calls['n'] += 1
            return made.pop(0) if made else _RecorderSession(
                open_error='no more sessions')

        with mock.patch.object(ct, 'CDPSocket', new=self._cdp_socks), \
                mock.patch.object(ct, 'PAGE_SETTLE_S', 0.01), \
                mock.patch.object(ct, '_ANTI_BOT_PATCH', False), \
                mock.patch.object(ct, '_RECONNECT_PAUSE_S', 0.01):
            out = ct.fetch_pages_via_chrome(
                list(urls), log=lambda m, l: logs.append(m),
                chrome_exe='/bin/true', proxy=proxy,
                proxy_mode=mode, attach=False,
                _session_factory=_factory,
                memory_path=memory_path)
        if memory is not None and memory_path:
            loaded = ct.load_proxy_memory(memory_path)
            memory.update(loaded)
        return out, made, calls['n'], logs

    def _cdp_socks(self, host, port, path, sock=None, timeout=10.0):
        # a crafted CDPSocket over a scripted socket — no handshake
        # (the __init__ path never runs; the script starts at the
        # first DevTools answer). The REAL class is captured at import
        # time — under the patch ct.CDPSocket is this function itself.
        ws = _REAL_CDP.__new__(_REAL_CDP)
        ws.sock = _FakeSock([_probe(1), _probe(2),
                             _dom(3, _REAL_HTML),
                             _resp(4, 'The Real Page')])
        ws._buf = b''
        ws._frag_opcode = -1
        ws._frag_parts = []
        ws._msgid = 0
        ws._timeout = 5.0
        return ws

    def test_the_unknowns_ride_direct_then_escalate_to_the_proxy(self):
        # the owner's law: a link that fails the direct leg is retried
        # through the proxy IN THE SAME delivery
        s1 = _RecorderSession(open_error='refused (direct)')
        s2 = _RecorderSession(open_error='refused (proxied too)')
        out, _, legs, logs = self._driver([s1, s2], [_URL, _URL3])
        self.assertEqual(legs, 2)
        self.assertEqual(s1.urls, [_URL, _URL3])
        self.assertEqual(s2.urls, [_URL, _URL3])   # both escalated
        self.assertTrue(any('retried through your proxy' in m
                            for m in logs))
        # the failure carries BOTH legs' sentences
        self.assertEqual(len(out), 2)
        for r in out:
            self.assertFalse(r['ok'])
            self.assertIn('direct leg: ', r['error'])
            self.assertIn('proxy leg: ', r['error'])
        self.assertIn(' | ', out[0]['error'])

    def test_the_remembered_proxy_site_rides_only_the_proxy_leg(self):
        tmp = tempfile.mkdtemp(prefix='rtleg-')
        try:
            path = os.path.join(tmp, 'mem.json')
            ct.save_proxy_memory(path, {'walled.example.org': 'proxy',
                                        'proxied.example.com': 'proxy'})
            s1 = _RecorderSession(open_error='refused')
            s2 = _RecorderSession(open_error='refused')
            out, _, legs, _ = self._driver([s1, s2],
                                           [_URL, _URL2, _URL3],
                                           memory_path=path)
            self.assertEqual(legs, 2)
            self.assertEqual(s1.urls, [_URL])
            self.assertEqual(s2.urls, [_URL2, _URL3, _URL])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_a_direct_success_is_remembered_and_never_re_asked(self):
        tmp = tempfile.mkdtemp(prefix='rtleg-')
        try:
            path = os.path.join(tmp, 'mem.json')
            s1 = _RecorderSession(tab=_TAB)       # the page delivers
            out, _, legs, _ = self._driver([s1], [_URL],
                                           memory_path=path)
            self.assertEqual(legs, 1)             # only the direct leg
            self.assertEqual(s1.urls, [_URL])
            self.assertEqual(len(out), 1)
            self.assertTrue(out[0]['ok'], out[0])
            self.assertEqual(out[0]['title'], 'The Real Page')
            # the route that DELIVERED is remembered
            self.assertEqual(ct.load_proxy_memory(path),
                             {'real.example.net': 'direct'})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_a_proxy_leg_success_is_remembered(self):
        tmp = tempfile.mkdtemp(prefix='rtleg-')
        try:
            path = os.path.join(tmp, 'mem.json')
            # the direct leg refuses; the proxy leg delivers
            s1 = _RecorderSession(open_error='the proxy exit is blocked')
            s2 = _RecorderSession(tab=_TAB)
            out, _, legs, _ = self._driver([s1, s2], [_URL],
                                           memory_path=path)
            self.assertEqual(legs, 2)
            self.assertEqual(s1.urls, [_URL])
            self.assertEqual(s2.urls, [_URL])     # the escalation
            self.assertEqual(len(out), 1)
            self.assertTrue(out[0]['ok'], out[0])
            self.assertEqual(ct.load_proxy_memory(path),
                             {'real.example.net': 'proxy'})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_a_remembered_route_can_change_when_it_fails(self):
        # the memory is an optimization, never a cage: a remembered
        # 'proxy' whose leg now fails is retried... on the proxy leg
        # it was pinned to (no third leg exists) — and a remembered
        # 'direct' that fails escalates onto the proxy leg, where a
        # success RE-PINS the site to 'proxy'
        tmp = tempfile.mkdtemp(prefix='rtleg-')
        try:
            path = os.path.join(tmp, 'mem.json')
            ct.save_proxy_memory(path, {'real.example.net': 'direct'})
            s1 = _RecorderSession(open_error='walled now')
            s2 = _RecorderSession(tab=_TAB)
            out, _, legs, _ = self._driver([s1, s2], [_URL],
                                           memory_path=path)
            self.assertEqual(legs, 2)
            self.assertTrue(out[0]['ok'])
            self.assertEqual(ct.load_proxy_memory(path),
                             {'real.example.net': 'proxy'})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_mode_off_never_opens_the_proxy_leg(self):
        s1 = _RecorderSession(open_error='refused')
        out, made, legs, logs = self._driver([s1], [_URL], mode='off')
        self.assertEqual(legs, 1)
        self.assertFalse(any('YOUR PROXY' in m for m in logs))

    def test_mode_on_rides_the_proxy_leg_alone(self):
        s1 = _RecorderSession(open_error='refused')
        out, made, legs, logs = self._driver([s1], [_URL], mode='on')
        self.assertEqual(legs, 1)
        self.assertTrue(any('through YOUR PROXY' in m for m in logs))
        self.assertFalse(any('Chrome leg: DIRECT' in m for m in logs))

    def test_auto_without_a_proxy_says_the_honest_skip(self):
        s1 = _RecorderSession(open_error='refused')
        out, made, legs, logs = self._driver([s1], [_URL], proxy=None)
        self.assertEqual(legs, 1)
        self.assertTrue(any('No proxy is configured' in m
                            for m in logs))

    def test_the_results_keep_the_work_order(self):
        s1 = _RecorderSession(open_error='refused')
        s2 = _RecorderSession(open_error='refused')
        out, _, _, _ = self._driver([s1, s2], [_URL, _URL3])
        self.assertEqual([r['url'] for r in out], [_URL, _URL3])


# ---------------------------------------------------------------------------
# 6. the attach script (the .bat law: pure ASCII + CRLF)
# ---------------------------------------------------------------------------

class TestBatWriter(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix='rtbat-')
        self._data = os.path.join(self._tmp, 'User Data')
        os.makedirs(self._data, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_the_bat_is_written_ascii_crlf_with_the_full_dance(self):
        path = os.path.join(self._tmp, 'chrome-attach.bat')
        chrome = r'C:\Program Files\Google\Chrome\Application\chrome.exe'
        written = ct.write_chrome_attach_bat(
            path, chrome_exe=chrome, port=9222, data_dir=self._data,
            link_dir=self._tmp)
        self.assertEqual(written, path)
        with open(path, 'rb') as fh:
            raw = fh.read()
        raw.decode('ascii')                       # the .bat law
        self.assertIn(b'\r\n', raw)
        self.assertNotIn(b'\r\r', raw)
        text = raw.decode('ascii')
        self.assertIn('taskkill /IM chrome.exe /T /F', text)
        self.assertIn('mklink /J', text)
        self.assertIn('--remote-debugging-port=9222', text)
        self.assertIn('--restore-last-session', text)
        self.assertIn(chrome, text)
        self.assertIn(str(ct.real_profile_link(self._data,
                                               self._tmp)), text)

    def test_the_bat_is_idempotent(self):
        path = os.path.join(self._tmp, 'chrome-attach.bat')
        chrome = r'C:\Program Files\Google\Chrome\Application\chrome.exe'
        self.assertIsNotNone(ct.write_chrome_attach_bat(
            path, chrome_exe=chrome, data_dir=self._data,
            link_dir=self._tmp))
        self.assertIsNotNone(ct.write_chrome_attach_bat(
            path, chrome_exe=chrome, data_dir=self._data,
            link_dir=self._tmp))

    def test_no_chrome_or_no_profile_writes_nothing(self):
        path = os.path.join(self._tmp, 'chrome-attach.bat')
        # find_chrome mocked empty — the CI runners DO ship a Chrome
        # (the fallback would find it and write the .bat; the law
        # under test is the EMPTY-answer, not the machine's luck)
        with mock.patch('gitcurator.core.hand_delivery.find_chrome',
                        return_value=None):
            self.assertIsNone(ct.write_chrome_attach_bat(
                path, chrome_exe='', data_dir=self._data,
                link_dir=self._tmp))
        self.assertIsNone(ct.write_chrome_attach_bat(
            path, chrome_exe='/bin/true', data_dir='',
            link_dir=self._tmp))
        self.assertFalse(os.path.exists(path))


# ---------------------------------------------------------------------------
# 7. the delivery plumbing — the v0.55 knobs read from the config
# ---------------------------------------------------------------------------

class TestDeliveryPlumbing(unittest.TestCase):

    def test_the_v055_knobs_read_from_the_config_dict(self):
        # the delivery layer's own reads (the dict-lookup law)
        cfg = {'web_browser_attach': False,
               'web_browser_attach_port': 9333,
               'web_browser_real_profile': False,
               'web_browser_proxy': 'off'}
        self.assertFalse(cfg.get(ct.CONFIG_ATTACH, True))
        self.assertEqual(int(cfg.get(ct.CONFIG_ATTACH_PORT, 9222)), 9333)
        self.assertFalse(cfg.get(ct.CONFIG_REAL_PROFILE, True))
        self.assertEqual(ct.normalize_proxy_mode(
            str(cfg.get(ct.CONFIG_PROXY_LEGS, 'auto'))), 'off')
        # the defaults: attach ON, port 9222, real profile ON, auto
        self.assertTrue(({}).get(ct.CONFIG_ATTACH, True))
        self.assertEqual(int(({}).get(ct.CONFIG_ATTACH_PORT, 9222)),
                         9222)
        self.assertTrue(({}).get(ct.CONFIG_REAL_PROFILE, True))
        self.assertEqual(({}).get(ct.CONFIG_PROXY_LEGS, 'auto'), 'auto')

    def test_the_delivery_threads_the_knobs_into_the_driver(self):
        from gitcurator.core import dryrun
        from gitcurator.core import hand_delivery as hd
        dryrun.disable()
        tmp = tempfile.mkdtemp(prefix='rtplumb-')
        try:
            vault = os.path.join(tmp, 'vault')
            os.makedirs(vault, exist_ok=True)
            seen = {}

            def _recorder(urls, log=None, chrome_exe=None,
                          page_timeout_s=45.0, wave=8,
                          should_continue=None, profile_dir=None,
                          persist=True, proxy=None, proxy_mode='auto',
                          attach=True, attach_port=9222,
                          real_profile=True, memory_path=None):
                seen.update(proxy=proxy, proxy_mode=proxy_mode,
                            attach=attach, attach_port=attach_port,
                            real_profile=real_profile,
                            memory_path=memory_path)
                return [{'url': u, 'ok': False, 'html': '', 'title': '',
                         'error': 'refused'} for u in urls]

            cfg = {'web_browser_attach': True,
                   'web_browser_attach_port': 9222,
                   'web_browser_real_profile': True,
                   'web_browser_proxy': 'auto',
                   'proxy': {'enabled': True, 'type': 'socks5',
                             'host': '127.0.0.1', 'port': 10808}}
            with mock.patch.object(ct, 'fetch_pages_via_chrome',
                                   _recorder):
                ct.deliver_pages_via_chrome(
                    vault, [{'url': _URL, 'error': 'wall'}],
                    log=lambda *a, **k: None, config=cfg,
                    chrome_exe='/bin/true')
            self.assertEqual(seen['proxy'], {
                'type': 'socks5', 'host': '127.0.0.1', 'port': 10808,
                'username': '', 'password': ''})
            self.assertEqual(seen['proxy_mode'], 'auto')
            self.assertTrue(seen['attach'])
            self.assertEqual(seen['attach_port'], 9222)
            self.assertTrue(seen['real_profile'])
            self.assertEqual(seen['memory_path'],
                             ct.proxy_memory_path())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_the_v055_story_is_logged_when_a_proxy_is_configured(self):
        from gitcurator.core import dryrun
        from gitcurator.core import hand_delivery as hd
        dryrun.disable()
        tmp = tempfile.mkdtemp(prefix='rtplumb-')
        try:
            vault = os.path.join(tmp, 'vault')
            os.makedirs(vault, exist_ok=True)
            logs = []
            with mock.patch.object(
                    ct, 'fetch_pages_via_chrome',
                    lambda urls, **kw: []):
                ct.deliver_pages_via_chrome(
                    vault, [{'url': _URL, 'error': 'wall'}],
                    log=lambda m, l: logs.append(m),
                    config={'proxy': {'enabled': True, 'type': 'socks5',
                                      'host': '127.0.0.1',
                                      'port': 10808}},
                    chrome_exe='/bin/true')
            joined = '\n'.join(logs)
            self.assertIn('v0.55', joined)
            self.assertIn('DIRECT first', joined)
            self.assertIn('route that worked', joined)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_the_knob_names_are_the_config_example_names(self):
        root = os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))
        with open(os.path.join(root, 'config.example.json'),
                  encoding='utf-8') as fh:
            text = fh.read()
        self.assertIn(f'"{ct.CONFIG_ATTACH}": true', text)
        self.assertIn(f'"{ct.CONFIG_ATTACH_PORT}": 9222', text)
        self.assertIn(f'"{ct.CONFIG_REAL_PROFILE}": true', text)
        self.assertIn(f'"{ct.CONFIG_PROXY_LEGS}": "auto"', text)


# ---------------------------------------------------------------------------
# 8. release bookkeeping
# ---------------------------------------------------------------------------

class TestReleaseBookkeeping(unittest.TestCase):

    def setUp(self):
        self.root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    def _read(self, *parts):
        with open(os.path.join(self.root, *parts), 'r',
                  encoding='utf-8') as f:
            return f.read()

    def test_version_is_0550(self):
        self.assertEqual(self._read('VERSION').strip(), '0.65.0')

    def test_changelog_has_the_v055_beat(self):
        text = self._read('CHANGELOG.md')
        self.assertIn('## [0.55.0]', text)
        self.assertIn('real chrome instance', text.lower())
        self.assertIn('right route', text.lower())

    def test_ci_runs_this_module(self):
        text = self._read('.github', 'workflows', 'ci.yml')
        self.assertIn('tests.test_realdoor', text)

    def test_agents_md_lists_this_module(self):
        text = self._read('AGENTS.md')
        self.assertIn('test_realdoor', text)

    def test_ci_counts_this_module(self):
        text = self._read('.github', 'workflows', 'ci.yml')
        # v0.56.0 — tests.test_handharvest now closes the -v tail; the
        # presence is the law)
        self.assertIn('tests.test_realdoor', text)

    def test_the_older_release_pins_follow(self):
        # the established convention: the previous releases' bookkeeping
        # tests re-pin to the current version
        for mod in ('test_ladder', 'test_iconcolumn',
                    'test_handdelivery', 'test_masterretry',
                    'test_decommission', 'test_reviewtable',
                    'test_mastertable', 'test_autopip', 'test_fifthdoor',
                    'test_persistentdoor', 'test_bothdoors'):
            src = self._read('app', 'tests', f'{mod}.py')
            self.assertIn('0.65.0', src,
                          f'{mod} must re-pin to 0.65.0')


if __name__ == '__main__':
    unittest.main()
