#!/usr/bin/env python3
"""test_persistentdoor.py — v0.55.0: the door keeps its grip; the
profile remembers.

The owner's report (this session): "this time the links which I defined
by 'hand' emoji opened their tabs in a new google chrome session, but
app didn't actually wait and use those tabs to gather information to
feed LLM and store them properly in obsidian, as soon as they loaded,
app false-positive success, but actually openning was not the success
criteria, openning is just start for LLM to fetch and gather data and
write their information and store into obsidian. also use chrominum
and selenium or some headless browser, so sites cannto see you as
bot."

Three laws, each proven here:

  1. THE DOOR KEEPS ITS GRIP — a DevTools socket that drops mid-watch
     (WinError 10053, a reset, Chrome recycling the connection) is a
     BLIP: the socket is re-connected and the watch RESUMES with the
     remaining budget. Only a tab that refuses its own re-connect is
     "the tab closed itself". Session liveness is the ENDPOINT's
     answer (/json/version), never the launcher's poll — the Windows
     chrome.exe handoff (launcher exits, endpoint perfectly live) is
     NOT "the Chrome session died mid-run".
  2. THE PROFILE REMEMBERS — the throwaway user-data-dir was the bot
     tell (a first-visit identity every delivery: no cookies, no
     clearances — the owner's flightradar24 challenge). The door now
     owns a PERSISTENT dedicated profile: cookies survive the close
     (Browser.close flushes them; a taskkill /T fallback on Windows),
     a Chrome still running with the profile is ADOPTED through its
     DevToolsActivePort (never fought), and the automation blink
     feature is off with navigator.webdriver patched undefined.
  3. OPENING IS NOT THE SUCCESS — the batch scorecard WAITS while
     Chrome is gathering pages (no "✓ All links processed cleanly"
     over loading tabs), and the score that finally shows carries the
     delivery's own numbers (Chrome pages taken / not taken).

Headless-safe and browserless: the Chrome sessions are fake exes that
print the DevTools announcement; the DevTools endpoint is a real
loopback http.server thread; the page sockets are the same scripted
_FakeSock chunks the fifth-door tests ride; the GUI laws run on bare
mixins (routing) and a real offscreen QWidget host (dialog content).
No PyQt import at module level in the pure sections (the
libEGL-less sandbox rule); the dialog section imports PyQt under the
established try/except with the offscreen platform preset."""

import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from gitcurator.core import chrome_tabs as ct

# ---------------------------------------------------------------------------
# shared helpers (the fifth-door vocabulary, copied so the modules stay
# independently readable — the house convention)
# ---------------------------------------------------------------------------

_URL = 'https://walled.example.net/article'

_REAL_HTML = ('<html><head><title>The Real Page</title></head><body>'
              + ('r' * 500) + '</body></html>')


def _srv_frame(payload: bytes, opcode: int = 0x1) -> bytes:
    """A server→client frame: UNMASKED (the RFC's rule for servers)."""
    n = len(payload)
    if n < 126:
        head = bytes((0x80 | opcode, n))
    elif n < 65536:
        head = bytes((0x80 | opcode, 126)) + n.to_bytes(2, 'big')
    else:
        head = bytes((0x80 | opcode, 127)) + n.to_bytes(8, 'big')
    return head + payload


_HANDSHAKE = (b'HTTP/1.1 101 Switching Protocols\r\n'
              b'Upgrade: websocket\r\n'
              b'Sec-WebSocket-Accept: k\r\n\r\n')


class _FakeSock:
    """A scripted socket: recv() pops the next chunk; sendall() records
    what was sent; an optional ``error_on_recv`` makes recv raise (the
    WinError 10053 family) once the script is exhausted."""

    def __init__(self, chunks, error_on_recv=None):
        self._chunks = list(chunks)
        self._error = error_on_recv
        self.sent = []
        self.timeout = None
        self.closed = False

    def recv(self, n):
        if not self._chunks:
            if self._error is not None:
                raise self._error
            raise socket.timeout('stalled')
        return self._chunks.pop(0)

    def sendall(self, data):
        self.sent.append(bytes(data))

    def settimeout(self, t):
        self.timeout = t

    def close(self):
        self.closed = True
        self._chunks = []


class _CloseFrameSock(_FakeSock):
    """A socket whose script ENDS with Chrome's close frame — the
    DevTools connection dropped mid-watch (the blip)."""

    def __init__(self, chunks):
        super().__init__(list(chunks) + [_srv_frame(b'', 0x8)])


def _resp(id_, value):
    return _srv_frame(json.dumps(
        {'id': id_, 'result': {'result': {'value': value}}}).encode())


def _probe(id_, state='complete', href=None):
    return _resp(id_, f'{state}|{href or _URL}')


def _dom(id_, html):
    return _resp(id_, html)


def _err_resp(id_):
    return _srv_frame(json.dumps(
        {'id': id_, 'error': {'message': 'page gone'}}).encode())


class _FakeSession:
    """The session the fetch driver needs (port, close_tab, alive)."""

    def __init__(self, alive=True, port=9222):
        self.port = port
        self.closed = []
        self._alive = alive

    def close_tab(self, target_id):
        self.closed.append(target_id)

    def alive(self):
        return self._alive


_TAB = {'id': 'T1', 'webSocketDebuggerUrl':
        'ws://127.0.0.1:9222/devtools/page/T1'}


# ---------------------------------------------------------------------------
# the DevTools endpoint — a real loopback http server (the adopt/liveness
# probe's honest answer; no browser ever runs)
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

    def port_file_lines(self):
        """The DevToolsActivePort file Chrome itself writes: port on
        line 1, the browser ws path on line 2."""
        return (f'{self.port}\n'
                f'/devtools/browser/test-uuid\n')


def _fake_exe(lines, then='sleep 30', require='', argv_out=''):
    """A fake chrome 'exe' (the fifth-door pattern): a shell script
    that prints the DevTools line Chrome itself prints, then sleeps.
    ``require`` makes the script EXIT 7 unless the flag rides the
    command line (the ladder proof); ``argv_out`` records the exact
    argv the session launched it with."""
    path = os.path.join(tempfile.mkdtemp(prefix='fakechrome-'),
                        'chrome')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('#!/bin/sh\n')
        if argv_out:
            f.write(f'printf \'%s\\n\' "$@" > "{argv_out}"\n')
        if require:
            f.write(f'case " $* " in\n'
                    f'  *" {require} "*) ;;\n'
                    f'  *) exit 7 ;;\n'
                    f'esac\n')
        for line in lines:
            f.write(f'echo "{line}" 1>&2\n')
        f.write(then + '\n')
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


# ---------------------------------------------------------------------------
# 1. the persistent dedicated profile (fake exes + the real endpoint)
# ---------------------------------------------------------------------------

class TestPersistentProfile(unittest.TestCase):
    """The door's identity: ONE dedicated profile directory whose
    cookies survive every delivery — the anti-bot law. The fake exes
    never run a browser; the endpoint answers are real."""

    def setUp(self):
        self._endpoint = _Endpoint()
        self._dirs = []

    def tearDown(self):
        self._endpoint.stop()
        for d in self._dirs:
            shutil.rmtree(d, ignore_errors=True)

    def _dir(self, prefix='gcprofile-'):
        d = tempfile.mkdtemp(prefix=prefix)
        self._dirs.append(d)
        return d

    def test_default_profile_dir_lands_next_to_the_app(self):
        # <APP_DIR>/chrome-profile — the app's own folder, next to
        # cache.db (the profile travels with the app)
        path = ct.default_profile_dir()
        base = os.path.basename(path)
        self.assertEqual(base, 'chrome-profile')
        parent = os.path.basename(os.path.dirname(path))
        self.assertEqual(parent, 'app')

    def test_the_dedicated_profile_is_never_erased_on_close(self):
        # the cookie the "Chrome" left behind survives the close — the
        # whole anti-bot point (a returning visitor)
        profile = self._dir()
        marker = os.path.join(profile, 'Cookies')
        with open(marker, 'w', encoding='utf-8') as f:
            f.write('the clearance cookie')
        exe = _fake_exe(
            ['DevTools listening on ws://127.0.0.1:0/devtools/'
             'browser/none'])
        logs = []
        session = ct.ChromeSession(exe, log=lambda m, l: logs.append(m),
                                   profile_dir=profile)
        session.close()
        self.assertTrue(os.path.isdir(profile))
        self.assertTrue(os.path.isfile(marker))
        self.assertTrue(any('DEDICATED profile' in m for m in logs))

    def test_persist_false_is_the_old_throwaway_law(self):
        # the explicit opt-out: a temp profile, erased on close
        exe = _fake_exe(
            ['DevTools listening on ws://127.0.0.1:0/devtools/'
             'browser/none'])
        session = ct.ChromeSession(exe, log=lambda *a: None,
                                   persist=False)
        profile = session.profile
        self.assertTrue(profile.startswith(
            tempfile.gettempdir() + os.sep))
        self.assertIn('gitcurator-chrome-', os.path.basename(profile))
        session.close()
        self.assertFalse(os.path.isdir(profile))

    def test_adopt_never_launches_when_the_endpoint_answers(self):
        # a Chrome still running with the dedicated profile (a crashed
        # app's leftover) is ADOPTED through DevToolsActivePort — the
        # fake exe exits 7 if it is ever run, and adoption must not
        # run it
        profile = self._dir()
        with open(os.path.join(profile, 'DevToolsActivePort'), 'w',
                  encoding='utf-8') as f:
            f.write(self._endpoint.port_file_lines())
        exe = _fake_exe([], then='exit 7')
        logs = []
        session = ct.ChromeSession(exe, log=lambda m, l: logs.append(m),
                                   profile_dir=profile)
        try:
            self.assertEqual(session.port, self._endpoint.port)
            self.assertIsNone(session.proc)       # nothing was launched
            self.assertTrue(session.alive())      # the endpoint answers
            self.assertTrue(any('Adopted' in m for m in logs))
        finally:
            session.close()

    def test_a_stale_port_file_never_adopts(self):
        # the port file of a DEAD Chrome (a port nothing answers) must
        # not lie its way in — the session launches for real
        profile = self._dir()
        dead_port = ct._pick_free_port()          # bound, read, released
        with open(os.path.join(profile, 'DevToolsActivePort'), 'w',
                  encoding='utf-8') as f:
            f.write(f'{dead_port}\n/devtools/browser/dead\n')
        exe = _fake_exe(
            ['DevTools listening on ws://127.0.0.1:0/devtools/'
             'browser/fresh'])
        session = ct.ChromeSession(exe, log=lambda *a: None,
                                   profile_dir=profile)
        try:
            self.assertIsNotNone(session.proc)    # the launch happened
            self.assertNotEqual(session.port, dead_port)
        finally:
            session.close()

    def test_the_automation_flag_and_profile_ride_the_command(self):
        # --disable-blink-features=AutomationControlled + the dedicated
        # --user-data-dir ride the launch command (the anti-bot tells)
        profile = self._dir()
        argv_out = os.path.join(self._dir('gcargv-'), 'argv.txt')
        exe = _fake_exe(
            ['DevTools listening on ws://127.0.0.1:0/devtools/'
             'browser/argv'],
            argv_out=argv_out)
        session = ct.ChromeSession(exe, log=lambda *a: None,
                                   profile_dir=profile)
        try:
            with open(argv_out, encoding='utf-8') as f:
                argv = f.read().splitlines()
            self.assertIn('--disable-blink-features='
                          'AutomationControlled', argv)
            self.assertIn(f'--user-data-dir={profile}', argv)
        finally:
            session.close()

    def test_alive_is_the_endpoint_answer_after_the_launcher_exits(self):
        # THE v0.53 false-death fix: on Windows chrome.exe delegates to
        # the browser process and EXITS — the launcher's poll says
        # dead while the endpoint is perfectly live. alive() must say
        # True (the owner's oldmapsonline tab was loading just fine).
        profile = self._dir()
        port = self._endpoint.port
        exe = _fake_exe(
            [f'DevTools listening on ws://127.0.0.1:{port}/devtools/'
             f'browser/handoff'],
            then='exit 0')
        session = ct.ChromeSession(exe, log=lambda *a: None,
                                   profile_dir=profile)
        try:
            # the launcher exits right after the announcement…
            for _ in range(50):
                if session.proc is not None \
                        and session.proc.poll() is not None:
                    break
                time.sleep(0.1)
            self.assertIsNotNone(session.proc.poll())  # the handoff
            # …and the session is still ALIVE (the endpoint answers).
            # v0.55 — one transient retry: a loaded CI runner can
            # starve the loopback server for a beat (the 2026-10-08
            # tag-run flake); the LAW is the endpoint's answer, and a
            # busy server still answers on the second knock.
            ok = session.alive()
            if not ok:
                time.sleep(0.5)
                ok = session.alive()
            self.assertTrue(ok)
        finally:
            session.close()

    def test_a_dead_endpoint_is_a_dead_session(self):
        # the honest half of the same law: nothing answers anywhere —
        # alive() says False (the true "session died mid-run")
        profile = self._dir()
        exe = _fake_exe(
            ['DevTools listening on ws://127.0.0.1:1/devtools/'
             'browser/dead'],
            then='exit 0')
        session = ct.ChromeSession(exe, log=lambda *a: None,
                                   profile_dir=profile)
        try:
            for _ in range(50):
                if session.proc is not None \
                        and session.proc.poll() is not None:
                    break
                time.sleep(0.1)
            self.assertFalse(session.alive())
        finally:
            session.close()

    def test_the_announcement_path_is_kept_for_the_graceful_close(self):
        # the launch parses the browser ws path out of the DevTools
        # announcement — the graceful Browser.close needs it
        profile = self._dir()
        exe = _fake_exe(
            ['DevTools listening on ws://127.0.0.1:0/devtools/'
             'browser/uuid-close'])
        session = ct.ChromeSession(exe, log=lambda *a: None,
                                   profile_dir=profile)
        try:
            self.assertEqual(session._browser_ws_path,
                             '/devtools/browser/uuid-close')
        finally:
            session.close()

    def test_graceful_close_says_browser_close(self):
        # close() speaks Browser.close over the browser's own WebSocket
        # FIRST (Chrome flushes its cookies — the persistent profile's
        # whole value) — the tree kill is only the fallback
        profile = self._dir()
        exe = _fake_exe(
            ['DevTools listening on ws://127.0.0.1:1/devtools/'
             'browser/uuid-close'])
        session = ct.ChromeSession(exe, log=lambda *a: None,
                                   profile_dir=profile)
        calls = []

        class _FakeWS:
            def __init__(self, host, port, path, timeout=10.0):
                calls.append(('connect', path))

            def call(self, method, params=None, timeout=None):
                calls.append(('call', method))
                return {}

            def close(self):
                calls.append(('close',))

        with mock.patch.object(ct, 'CDPSocket', _FakeWS):
            session.close()
        self.assertIn(('connect', '/devtools/browser/uuid-close'), calls)
        self.assertIn(('call', 'Browser.close'), calls)
        # and the profile survived the graceful close
        self.assertTrue(os.path.isdir(profile))


# ---------------------------------------------------------------------------
# 2. the reconnect law (scripted sockets — the door keeps its grip)
# ---------------------------------------------------------------------------

class TestReconnectLaw(unittest.TestCase):
    """A DevTools socket that drops mid-watch is a BLIP, not a verdict:
    the socket re-connects (the tab is still open) and the watch
    resumes with the remaining budget. Only a tab that refuses its own
    re-connect is 'the tab closed itself'. The anti-bot patch is off
    in these scripts (its law has its own tests below)."""

    def _fetch(self, sockets, timeout=10.0, settle=0.05,
               session=None):
        ws_list = [ct.CDPSocket('127.0.0.1', 9222, '/devtools/page/T1',
                                sock=s) for s in sockets]
        session = session or _FakeSession()
        factory = mock.MagicMock(side_effect=ws_list)
        with mock.patch.object(ct, 'PAGE_SETTLE_S', settle), \
                mock.patch.object(ct, '_ANTI_BOT_PATCH', False), \
                mock.patch.object(ct, '_RECONNECT_PAUSE_S', 0.01), \
                mock.patch.object(ct, 'CDPSocket', factory):
            page = ct._fetch_opened_tab(session, _URL, _TAB, timeout,
                                        lambda *a, **k: None)
        return page, session, factory

    def _fetch_refusing(self, first_ws, refuse_after=1, timeout=10.0,
                        settle=0.05):
        """A CDPSocket factory that hands out ``first_ws`` (once, or
        ``refuse_after`` times) and then REFUSES — Chrome 404s the
        target's WebSocket (the tab is gone)."""
        session = _FakeSession()
        state = {'n': 0}

        def factory(*args, **kwargs):
            state['n'] += 1
            if state['n'] <= refuse_after:
                return first_ws
            raise ct.CDPError('Chrome refused the WebSocket upgrade: '
                              'HTTP/1.1 404 Not Found')

        with mock.patch.object(ct, 'PAGE_SETTLE_S', settle), \
                mock.patch.object(ct, '_ANTI_BOT_PATCH', False), \
                mock.patch.object(ct, '_RECONNECT_PAUSE_S', 0.01), \
                mock.patch.object(ct, 'CDPSocket', factory):
            page = ct._fetch_opened_tab(session, _URL, _TAB, timeout,
                                        lambda *a, **k: None)
        return page, session, state['n']

    def test_a_socket_drop_mid_watch_is_a_blip_not_a_verdict(self):
        # the owner's marinetraffic story: the watch starts, the
        # connection aborts (a close frame) — the door RE-CONNECTS and
        # takes the page from the still-open tab
        dying = _CloseFrameSock([_HANDSHAKE, _probe(1)])
        fresh = _FakeSock([_HANDSHAKE, _probe(1), _probe(2),
                           _dom(3, _REAL_HTML), _resp(4, 'The Real '
                                                      'Page')])
        page, session, factory = self._fetch([dying, fresh])
        self.assertTrue(page['ok'], page)
        self.assertIn('r' * 100, page['html'])
        self.assertEqual(session.closed, ['T1'])
        self.assertEqual(factory.call_count, 2)   # one re-connect

    def test_a_winerror_10053_reset_is_a_blip(self):
        # the exact Windows error the owner's modal named: the recv
        # raises ConnectionResetError mid-watch — re-connect, resume,
        # the page is taken
        reset = _FakeSock([_HANDSHAKE, _probe(1)],
                          error_on_recv=ConnectionResetError(
                              '[WinError 10053] An established '
                              'connection was aborted by the software '
                              'in your host machine'))
        fresh = _FakeSock([_HANDSHAKE, _probe(1), _probe(2),
                           _dom(3, _REAL_HTML), _resp(4, 'Real')])
        page, _, factory = self._fetch([reset, fresh])
        self.assertTrue(page['ok'], page)
        self.assertEqual(factory.call_count, 2)

    def test_a_tab_that_refuses_the_reconnect_is_named(self):
        # the tab is GONE: the re-connect to its own WebSocket is
        # refused (Chrome 404s the target) — that, and only that, is
        # "the tab closed itself"
        dying = _CloseFrameSock([_HANDSHAKE, _probe(1)])
        ws1 = ct.CDPSocket('127.0.0.1', 9222, '/devtools/page/T1',
                           sock=dying)
        page, _, n = self._fetch_refusing(ws1, refuse_after=1)
        self.assertFalse(page['ok'])
        self.assertIn('closed itself', page['error'])
        self.assertIn('refused', page['error'])
        self.assertEqual(n, 2)                    # one try + one refuse

    def test_two_reconnects_then_the_honest_sentence(self):
        # the budget is two re-connects; every connection drops; the
        # failure names the socket honestly (never a delivery)
        socks = [_CloseFrameSock([_HANDSHAKE, _probe(1)])
                 for _ in range(3)]
        page, _, factory = self._fetch(socks)
        self.assertFalse(page['ok'])
        self.assertEqual(factory.call_count, 3)   # 1 + 2 re-connects
        self.assertIn('socket', page['error'])

    def test_a_tab_gone_from_the_start_is_named(self):
        # the FIRST connect is refused — the tab never had a socket
        page, _, n = self._fetch_refusing(None, refuse_after=0)
        self.assertFalse(page['ok'])
        self.assertIn('closed itself', page['error'])
        self.assertEqual(n, 1)

    def test_the_budget_keeps_running_across_the_reconnect(self):
        # the deadline is a wall clock: the first socket burns time
        # watching a challenge, drops, and the re-connect has only the
        # REMAINING budget — an honest failure if it too never settles
        # (timeout 1.5s total, no delivery)
        challenge = ('<html><head><title>Just a moment...</title>'
                     '</head><body>' + ('j' * 500) + '</body></html>')
        dying = _CloseFrameSock(
            [_HANDSHAKE, _probe(1), _probe(2),
             _dom(3, challenge), _resp(4, 'Just a moment...')])
        stalling = _FakeSock([_HANDSHAKE] + [_probe(i) for i in
                                             range(1, 40)])
        stalling2 = _FakeSock([_HANDSHAKE] + [_probe(i) for i in
                                              range(1, 40)])
        page, _, _ = self._fetch([dying, stalling, stalling2],
                                 timeout=1.5)
        # the budget is a wall clock — the reconnect got only what
        # remained, and the failure is one of the door's honest
        # sentences (never a delivery)
        self.assertFalse(page['ok'])
        self.assertTrue(page['error'], page)
        self.assertNotIn('r' * 100, page.get('html') or '')

    def test_the_reconnect_log_line_tells_the_blip(self):
        dying = _CloseFrameSock([_HANDSHAKE, _probe(1)])
        fresh = _FakeSock([_HANDSHAKE, _probe(1), _probe(2),
                           _dom(3, _REAL_HTML), _resp(4, 'Real')])
        logs = []
        ws_list = [ct.CDPSocket('127.0.0.1', 9222, '/devtools/page/T1',
                                sock=s) for s in (dying, fresh)]
        with mock.patch.object(ct, 'PAGE_SETTLE_S', 0.05), \
                mock.patch.object(ct, '_ANTI_BOT_PATCH', False), \
                mock.patch.object(ct, '_RECONNECT_PAUSE_S', 0.01), \
                mock.patch.object(ct, 'CDPSocket',
                                  mock.MagicMock(side_effect=ws_list)):
            ct._fetch_opened_tab(_FakeSession(), _URL, _TAB, 10.0,
                                 lambda m, l='info': logs.append(m))
        self.assertTrue(any('re-connecting' in m for m in logs))


# ---------------------------------------------------------------------------
# 3. the anti-bot patch (the frames a tab's socket receives)
# ---------------------------------------------------------------------------

class TestAntiBotPatch(unittest.TestCase):

    @staticmethod
    def _sent_texts(sock):
        """The text messages a client sent — its frames are MASKED
        (the RFC's rule for clients): the handshake head is skipped,
        each frame is unmasked with its own key."""
        data = b''.join(sock.sent)
        _, _, data = data.partition(b'\r\n\r\n')   # skip the handshake
        out = []
        i = 0
        while i + 2 <= len(data):
            b2 = data[i + 1]
            i += 2
            masked = bool(b2 & 0x80)
            ln = b2 & 0x7F
            if ln == 126:
                ln = int.from_bytes(data[i:i + 2], 'big')
                i += 2
            elif ln == 127:
                ln = int.from_bytes(data[i:i + 8], 'big')
                i += 8
            mask = b''
            if masked:
                mask = data[i:i + 4]
                i += 4
            payload = data[i:i + ln]
            i += ln
            if mask:
                payload = bytes(payload[k] ^ mask[k % 4]
                                for k in range(len(payload)))
            out.append(payload.decode('utf-8', errors='replace'))
        return out

    def _sock_with_acks(self, *rest):
        # ack for Page.enable (id 1) + ack for the new-document script
        # (id 2), then whatever the watch itself needs
        return _FakeSock([_HANDSHAKE, _resp(1, ''), _resp(2, '')]
                         + list(rest))

    def test_the_patch_frames_ride_the_socket(self):
        # Page.enable + Page.addScriptToEvaluateOnNewDocument with the
        # navigator.webdriver erasure ride every tab the door connects
        sock = self._sock_with_acks(
            _probe(3), _probe(4),
            _dom(5, _REAL_HTML), _resp(6, 'The Real Page'))
        ws = ct.CDPSocket('127.0.0.1', 9222, '/devtools/page/T1',
                          sock=sock)
        with mock.patch.object(ct, 'PAGE_SETTLE_S', 0.05), \
                mock.patch.object(ct, 'CDPSocket', return_value=ws):
            page = ct._fetch_opened_tab(_FakeSession(), _URL, _TAB,
                                        10.0, lambda *a, **k: None)
        sent = '\n'.join(self._sent_texts(sock))
        self.assertIn('Page.enable', sent)
        self.assertIn('Page.addScriptToEvaluateOnNewDocument', sent)
        self.assertIn('navigator', sent)
        self.assertIn('webdriver', sent)
        self.assertTrue(page['ok'], page)  # and the page was still taken

    def test_a_page_that_refuses_the_patch_is_still_fetched(self):
        # best-effort by law: the Page.enable ack is an ERROR — the
        # patch gives up silently and the page is still taken
        sock = _FakeSock([_HANDSHAKE, _err_resp(1), _err_resp(2),
                          _probe(3), _probe(4),
                          _dom(5, _REAL_HTML), _resp(6, 'Real Page')])
        ws = ct.CDPSocket('127.0.0.1', 9222, '/devtools/page/T1',
                          sock=sock)
        with mock.patch.object(ct, 'PAGE_SETTLE_S', 0.05), \
                mock.patch.object(ct, 'CDPSocket', return_value=ws):
            page = ct._fetch_opened_tab(_FakeSession(), _URL, _TAB,
                                        10.0, lambda *a, **k: None)
        self.assertTrue(page['ok'], page)

    def test_the_patch_is_off_for_the_scripted_laws(self):
        # the module flag is the seam the scripted tests ride — its
        # default is ON (the law)
        self.assertTrue(ct._ANTI_BOT_PATCH)

    def test_the_anti_bot_script_is_pure_javascript(self):
        # the source erases navigator.webdriver (the one tell
        # chromedriver can never remove)
        self.assertIn('navigator', ct._ANTI_BOT_SCRIPT)
        self.assertIn('webdriver', ct._ANTI_BOT_SCRIPT)
        self.assertIn('undefined', ct._ANTI_BOT_SCRIPT)


# ---------------------------------------------------------------------------
# 4. the scorecard waits (bare-mixin routing — opening is not the success)
# ---------------------------------------------------------------------------

_SUMMARY = {
    'stopped': False, 'github_processed': 0, 'github_total': 0,
    'new_notes': 0, 'websites': {'processed': 0, 'review': 0,
                                 'skipped': 780, 'failed': 0},
    'failed_links': 0, 'report_path': '/v/_processing_report_x.md',
    'summary_path': '',
}


def _routing_window(worker, config=None):
    """A bare ProcessingControlMixin with every collaborator stubbed —
    the batch-finish pattern: only the ROUTING is under test."""
    from gitcurator.gui.main_window.processing_control import (
        ProcessingControlMixin)
    win = ProcessingControlMixin()
    win.calls = []
    win.worker = worker
    win.config = dict(config or {})
    win.start_btn = types.SimpleNamespace(setEnabled=lambda b: None)
    win._batch_running = True
    win.progress_bar = types.SimpleNamespace(setFormat=lambda f: None)
    win._bot_queue_urls = []
    win._pending_last_processed_update = 0
    win.log_message = lambda msg, level='info': win.calls.append(
        ('log', level, msg))
    win._set_hero_state = lambda s: win.calls.append(('hero', s))
    win._refresh_pipeline_counter = lambda: None
    win._release_telegram_lock = lambda src: None
    win._set_pipeline_state = lambda s: win.calls.append(('state', s))
    win._schedule_progress_hide = lambda delay_ms=2500: None
    win._show_custom_message_box = lambda title, message, success=True: \
        win.calls.append(('box', title, success))
    win._celebrate_batch = lambda summary, elapsed='': win.calls.append(
        ('celebrate', summary, elapsed))
    return win


class TestScorecardWaits(unittest.TestCase):

    def _worker(self, summary):
        return types.SimpleNamespace(
            batch_summary=dict(summary), link_tracker=None,
            _bot_source=False)

    def test_a_delivery_in_flight_holds_the_scorecard_back(self):
        # the owner's exact screenshot: tabs loading in Chrome while
        # the scorecard says "✓ All links processed cleanly" — now
        # the scorecard WAITS (stashed, a log line tells why)
        win = _routing_window(self._worker(_SUMMARY))
        win._chrome_delivery_pending = 5
        win.processing_finished(True, 'Processed 780 links.')
        self.assertFalse([c for c in win.calls if c[0] == 'celebrate'])
        self.assertIsNotNone(win._stashed_batch_summary)
        waits = [c for c in win.calls if c[0] == 'log'
                 and 'scorecard waits' in c[2]]
        self.assertEqual(len(waits), 1)
        self.assertIn('5 link(s)', waits[0][2])

    def test_the_flush_shows_the_stashed_scorecard_with_chrome_rows(self):
        # when the delivery ends (nothing delivered), the stashed
        # scorecard is FLUSHED — with the delivery's numbers riding in
        win = _routing_window(self._worker(_SUMMARY))
        win._chrome_delivery_pending = 5
        win.processing_finished(True, 'Processed 780 links.')
        win._chrome_retry_worker = types.SimpleNamespace(
            _report={'delivered': 0, 'failed': 5, 'urls': [],
                     'failed_links': [{'url': _URL, 'error': 'boom'}]})
        win._chrome_delivery_pending = 0
        win._flush_stashed_batch_summary()
        celebrates = [c for c in win.calls if c[0] == 'celebrate']
        self.assertEqual(len(celebrates), 1)
        summary = celebrates[0][1]
        self.assertEqual(summary['chrome_delivered'], 0)
        self.assertEqual(summary['chrome_failed'], 5)

    def test_the_flush_is_idempotent(self):
        win = _routing_window(self._worker(_SUMMARY))
        win._stashed_batch_summary = (dict(_SUMMARY), ' in 1m')
        win._chrome_retry_worker = types.SimpleNamespace(
            _report={'delivered': 1, 'failed': 0})
        win._flush_stashed_batch_summary()
        win._flush_stashed_batch_summary()      # the second is a no-op
        self.assertEqual(
            len([c for c in win.calls if c[0] == 'celebrate']), 1)

    def test_no_stash_is_a_quiet_no_op(self):
        win = _routing_window(None)
        win._flush_stashed_batch_summary()
        self.assertEqual(
            len([c for c in win.calls if c[0] == 'celebrate']), 0)

    def test_the_rerun_merges_into_the_stashed_scorecard(self):
        # the delivered pages' re-run finishes → ONE scorecard tells
        # the whole story: the batch's numbers + the re-run's saved +
        # the delivery's taken/not-taken
        base = dict(_SUMMARY, websites=dict(
            _SUMMARY['websites'], processed=0, skipped=780))
        win = _routing_window(self._worker(_SUMMARY))
        win._chrome_delivery_pending = 4
        win.processing_finished(True, 'Processed 780 links.')
        # the delivery delivered 4, the re-run saved 4 notes
        win._chrome_retry_worker = types.SimpleNamespace(
            _report={'delivered': 4, 'failed': 0, 'urls': [_URL],
                     'failed_links': []})
        rerun_summary = dict(
            _SUMMARY, websites={'processed': 4, 'review': 0,
                                'skipped': 0, 'failed': 0},
            new_notes=4)
        win._delivery_rerun_pending = True
        win._chrome_delivery_pending = 0
        win.worker = self._worker(rerun_summary)
        win.processing_finished(True, 'Processed 4 delivered pages.')
        celebrates = [c for c in win.calls if c[0] == 'celebrate']
        self.assertEqual(len(celebrates), 1)    # ONE scorecard, merged
        summary = celebrates[0][1]
        self.assertEqual(summary['chrome_delivered'], 4)
        self.assertEqual(summary['chrome_failed'], 0)
        self.assertEqual(summary['websites']['processed'], 4)
        self.assertEqual(summary['websites']['skipped'], 780)
        self.assertEqual(summary['new_notes'], 4)

    def test_a_rerun_without_a_stash_celebrates_its_own(self):
        # the honest fallback: the stash was already told — the re-run
        # celebrates on its own
        win = _routing_window(self._worker(_SUMMARY))
        win._delivery_rerun_pending = True
        win._chrome_delivery_pending = 0
        win.processing_finished(True, 'Processed 4 delivered pages.')
        celebrates = [c for c in win.calls if c[0] == 'celebrate']
        self.assertEqual(len(celebrates), 1)

    def test_a_stale_stash_is_dropped_not_shown_stale(self):
        # the delivery chain died without flushing — the next batch
        # drops the old stash with an honest line (never shows stale
        # numbers on top of the new batch)
        win = _routing_window(self._worker(_SUMMARY))
        win._stashed_batch_summary = (dict(_SUMMARY), ' in 1m')
        win._chrome_delivery_pending = 0
        win.processing_finished(True, 'Processed 3 links.')
        celebrates = [c for c in win.calls if c[0] == 'celebrate']
        self.assertEqual(len(celebrates), 1)
        self.assertEqual(celebrates[0][1]['github_processed'], 0)
        dropped = [c for c in win.calls if c[0] == 'log'
                   and 'released without showing' in c[2]]
        self.assertEqual(len(dropped), 1)

    def test_a_clean_batch_without_chrome_celebrates_as_before(self):
        # the regression law: no delivery, no stash — the old fanfare,
        # byte for byte
        win = _routing_window(self._worker(_SUMMARY))
        win.processing_finished(True, 'Processed 780 links.')
        celebrates = [c for c in win.calls if c[0] == 'celebrate']
        self.assertEqual(len(celebrates), 1)


# ---------------------------------------------------------------------------
# 5. the scorecard's honest rows (real offscreen dialog content)
# ---------------------------------------------------------------------------

try:
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PyQt6.QtWidgets import (QApplication, QLabel, QMainWindow,
                                 QPushButton)
    _APP = QApplication.instance() or QApplication([])
    _PYQT = True
except Exception:  # pragma: no cover — CI installs PyQt6
    _PYQT = False


@unittest.skipUnless(_PYQT, 'PyQt6 not installed — GUI checks skipped')
class TestScorecardContent(unittest.TestCase):

    def _host(self):
        from gitcurator.gui.main_window.processing_control import (
            ProcessingControlMixin)
        host = type('_ModalHost',
                    (QMainWindow, ProcessingControlMixin), {})()
        host.config = {}
        host._closing = False
        host.calls = []
        host.log_message = lambda msg, level='info': host.calls.append(
            ('log', level, msg))
        host._style_btn = lambda btn, kind: btn
        host._animate_dialog = lambda d: None
        host.show()
        return host

    def _modal(self, host, summary, elapsed=' in 1m 58s'):
        return host._build_batch_success_dialog(summary, elapsed)

    @staticmethod
    def _texts(dialog):
        return [l.text() for l in dialog.findChildren(QLabel)]

    def test_chrome_rows_tell_the_taken_and_not_taken(self):
        host = self._host()
        dlg = self._modal(host, dict(_SUMMARY, chrome_delivered=2,
                                     chrome_failed=3))
        texts = self._texts(dlg)
        self.assertIn('Chrome pages', texts)
        self.assertTrue(any('2 taken from your Chrome' in t
                            for t in texts))
        self.assertTrue(any('3 not taken (they keep waiting)' in t
                            for t in texts))
        # and the clean verdict is NOT said while links keep waiting
        self.assertNotIn('✓ All links processed cleanly', texts)
        self.assertTrue(any('didn’t generate content' in t
                            or "didn't generate content" in t
                            for t in texts))

    def test_all_taken_still_celebrates(self):
        host = self._host()
        dlg = self._modal(host, dict(_SUMMARY, chrome_delivered=4,
                                     chrome_failed=0))
        texts = self._texts(dlg)
        self.assertIn('Chrome pages', texts)
        self.assertTrue(any('4 taken' in t and 'all taken' in t
                            for t in texts))
        self.assertIn('✓ All links processed cleanly', texts)

    def test_gathering_now_holds_the_clean_verdict(self):
        # the dialog is built while a delivery is STILL running (the
        # pending count rides the host): the hourglass row, no party
        host = self._host()
        host._chrome_delivery_pending = 5
        dlg = self._modal(host, dict(_SUMMARY))
        texts = self._texts(dlg)
        self.assertNotIn('✓ All links processed cleanly', texts)
        self.assertTrue(any('gathering' in t and '5 link(s)' in t
                            for t in texts))
        # the header: ⏳, no party heading
        self.assertNotIn(' Batch Complete!', texts)
        self.assertTrue(any('still gathering' in t for t in texts))

    def test_no_chrome_story_is_unchanged(self):
        # the regression law: a plain clean batch's dialog, byte for
        # byte the v0.39 shape
        host = self._host()
        dlg = self._modal(host, dict(_SUMMARY))
        texts = self._texts(dlg)
        self.assertIn('✓ All links processed cleanly', texts)
        self.assertNotIn('Chrome pages', texts)
        self.assertIn(' Batch Complete!', texts)

    def test_the_chime_agrees_with_the_chrome_failures(self):
        # _celebrate_batch: a Chrome page not taken plays the softer
        # retry tone, not the success arpeggio
        host = self._host()
        host._play_batch_sound = lambda needs_retry=False: host.calls.append(
            ('sound', needs_retry))
        host._show_batch_success_modal = lambda s, e='': None
        host._celebrate_batch(dict(_SUMMARY, chrome_failed=3))
        sounds = [c for c in host.calls if c[0] == 'sound']
        self.assertEqual(sounds, [('sound', True)])
        host.calls.clear()
        host._celebrate_batch(dict(_SUMMARY, chrome_failed=0))
        self.assertEqual(
            [c for c in host.calls if c[0] == 'sound'], [('sound', False)])


# ---------------------------------------------------------------------------
# 6. the delivery layer's profile plumbing (config → session)
# ---------------------------------------------------------------------------

class TestProfilePlumbing(unittest.TestCase):

    def test_the_knobs_flow_from_the_config(self):
        # deliver_pages_via_chrome reads web_browser_profile_persist /
        # web_browser_profile_dir and hands them to the fetch driver
        # (asserted through the fetcher seam: the delivery never
        # launches Chrome here)
        from gitcurator.core import dryrun
        from gitcurator.core import hand_delivery as hd
        dryrun.disable()
        tmp = tempfile.mkdtemp(prefix='plumb-')
        try:
            vault = os.path.join(tmp, 'vault')
            os.makedirs(vault, exist_ok=True)
            seen = {}

            def _fetcher(urls, log=None):
                seen['urls'] = list(urls)
                return [{'url': u, 'ok': False, 'html': '', 'title': '',
                         'error': 'refused'} for u in urls]

            # the fetcher path never builds a ChromeSession — the
            # plumbing is proven at the driver level instead (below);
            # here the delivery's own defaults stay honest
            report = ct.deliver_pages_via_chrome(
                vault, [{'url': _URL, 'error': 'wall'}],
                log=None,
                config={'web_browser_profile_persist': True,
                        'web_browser_profile_dir': '/tmp/x',
                        'web_browser_retry': True},
                _fetcher=_fetcher)
            self.assertEqual(report['failed'], 1)
            self.assertEqual(seen['urls'], [_URL])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_the_driver_passes_the_knobs_to_the_session(self):
        # fetch_pages_via_chrome → ChromeSession(profile_dir, persist)
        # v0.55 grammar: the session also takes the leg's extra_flags
        # (the proxy argv — empty on the direct leg's default here)
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

        with mock.patch.object(ct, 'ChromeSession', _FakeSessionCls):
            ct.fetch_pages_via_chrome(
                [_URL], log=lambda *a: None, chrome_exe='/bin/true',
                profile_dir='/tmp/somewhere', persist=False)
        self.assertEqual(calls['profile_dir'], '/tmp/somewhere')
        self.assertFalse(calls['persist'])
        # v0.55 — the direct leg's own flag rides the argv (TRULY off,
        # even where the OS has a system proxy set)
        self.assertEqual(calls['extra_flags'], ['--no-proxy-server'])
        # and the defaults flow too (persist on, dir auto)
        calls.clear()
        with mock.patch.object(ct, 'ChromeSession', _FakeSessionCls):
            ct.fetch_pages_via_chrome(
                [_URL], log=lambda *a: None, chrome_exe='/bin/true')
        self.assertIsNone(calls['profile_dir'])
        self.assertTrue(calls['persist'])
        self.assertEqual(calls['extra_flags'], ['--no-proxy-server'])

    def test_the_knob_names_are_the_config_example_names(self):
        root = os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))
        with open(os.path.join(root, 'config.example.json'),
                  encoding='utf-8') as fh:
            text = fh.read()
        self.assertIn(f'"{ct.CONFIG_PROFILE_PERSIST}": true', text)
        self.assertIn(f'"{ct.CONFIG_PROFILE_DIR}": ""', text)

    def test_the_knobs_read_from_the_config_dict(self):
        # the delivery layer's own reads (the dict lookup law)
        cfg = {'web_browser_profile_persist': False,
               'web_browser_profile_dir': 'C:/profiles/curator'}
        persist = cfg.get(ct.CONFIG_PROFILE_PERSIST, True)
        persist = True if persist is None else bool(persist)
        profile_dir = str(cfg.get(ct.CONFIG_PROFILE_DIR) or '') or None
        self.assertFalse(persist)
        self.assertEqual(profile_dir, 'C:/profiles/curator')
        # defaults: ON + auto
        persist = ({}).get(ct.CONFIG_PROFILE_PERSIST, True)
        self.assertTrue(persist)
        self.assertIsNone(str(({}).get(ct.CONFIG_PROFILE_DIR)
                              or '') or None)


# ---------------------------------------------------------------------------
# 7. release bookkeeping
# ---------------------------------------------------------------------------

class TestReleaseBookkeeping(unittest.TestCase):

    def setUp(self):
        self.root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    def _read(self, *parts):
        with open(os.path.join(self.root, *parts), 'r',
                  encoding='utf-8') as f:
            return f.read()

    def test_version_is_0540(self):
        self.assertEqual(self._read('VERSION').strip(), '0.66.0')

    def test_changelog_has_the_v054_beat(self):
        text = self._read('CHANGELOG.md')
        self.assertIn('## [0.55.0]', text)
        self.assertIn('opening is not the success', text.lower())
        self.assertIn('profile', text.lower())

    def test_ci_runs_this_module(self):
        text = self._read('.github', 'workflows', 'ci.yml')
        self.assertIn('tests.test_persistentdoor', text)

    def test_agents_md_lists_this_module(self):
        text = self._read('AGENTS.md')
        self.assertIn('test_persistentdoor', text)

    def test_ci_counts_this_module(self):
        text = self._read('.github', 'workflows', 'ci.yml')
        # v0.55 — the module rides the unittest line (test_realdoor
        # now closes the -v tail; the presence is the law)
        self.assertIn('tests.test_persistentdoor', text)

    def test_the_older_release_pins_follow(self):
        # the established convention: the previous releases' bookkeeping
        # tests re-pin to the current version
        for mod in ('test_ladder', 'test_iconcolumn',
                    'test_handdelivery', 'test_masterretry',
                    'test_decommission', 'test_reviewtable',
                    'test_mastertable', 'test_autopip', 'test_fifthdoor'):
            src = self._read('app', 'tests', f'{mod}.py')
            self.assertIn("'0.66.0'", src,
                          f'{mod} did not re-pin to 0.63.3')


if __name__ == '__main__':
    unittest.main()
