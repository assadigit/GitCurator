"""tests/test_fifthdoor.py — v0.50.0, the fifth door: Chrome fetches
the pages itself.

The owner's report (session): "before finishing the run and fetch, app
must show a modal, 'xx' number of links didn't generate content or
wasn't successful or got error xxx … want to retry them in real
browser? if user said this, app automatically opens them in a new
session of user's own google chrome and start tabs and fetches the
data that way."

Covered here (unit law: temp vaults, patched seams, zero sockets, no
browser ever launches):

* v0.53.0 — THE DOOR WAITS FOR THE PAGE, A CRASH IS NEVER A
  DELIVERY (the owner's report: "it tried to open a chrome tabs, but
  crashed and closed, and app shown false positive of success …
  didn't wait for sites to load and gather their data"):
  ``page_is_real`` (the pure verdict: the tab's own URL law, the
  ERR_* code in the DOM, the crash phrases, the challenge grammar),
  the load watch (complete at an http(s) URL, the settle window with
  its href-change restart, a challenge that passes is waited for, a
  challenge that never passes is named, about:blank and
  never-finishes named), the delivery report's failed_links detail,
  the consume-side re-verification (an auto-delivered error page is
  discarded — the app's own file — the owner's saves trusted), and
  the GUI's end-of-delivery honest modal (the bare-mixin routing);
* the WebSocket frame codec — client frames are MASKED with all three
  length encodings; a split feed yields no frame until it completes;
  server frames (unmasked AND masked) decode; fragmentation splits
  across feeds; a runaway frame length raises the guard;
* the DevTools client — the RFC 6455 handshake (a refusal raises), a
  command round-trips its result, events are skipped, a ping is
  answered with a pong (masked, the client law), a close raises, an
  error response raises, a stalled socket raises the timeout, and
  ``evaluate`` returns the value;
* the candidates — the modal's data: fetch-failed links (review +
  fetch_status failed, and hard failures), deduped by canonical,
  loopback dropped, non-http dropped, auto-retired links never nag
  (they have their doors), the filter's absence offers them all;
* the session helpers — loopback URLs, a free port, the Chrome
  session's stderr endpoint parsing (a fake exe prints the DevTools
  line; a dead exe raises honestly; both erase the temp profile);
* the fetch driver — no Chrome found is an honest per-URL failure
  (nothing raises), loopback never opens, dry-run never launches;
* the delivery — the queue rows are stamped door 'auto' with the
  error as the wall, the pages land at their suggested filenames, the
  report counts, failures are honest, a crashing fetcher never breaks
  the delivery, dry-run launches nothing and writes nothing;
* the consume side — the auto door words the fetch's story (the
  reason names the fifth door), the manual wording is unchanged, the
  queue is stamped consumed, a re-enqueue of a consumed link keeps
  the door;
* the pipeline — an auto-delivered page answers BEFORE any machine
  door (the injected fetcher is never asked), the note is written,
  the retry row resolves, and the full circle (deliver → re-run)
  processes the pages as real fetches;
* the ☠️ law re-verified — a Status cell with ONLY ☠️ (no word at
  all) retires the link AND sweeps its _review placeholder AND never
  fetches again (the owner's exact words this session).

No PyQt import at module level (the libEGL-less sandbox rule)."""

import json
import os
import shutil
import socket
import stat
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock

from gitcurator.core import chrome_tabs as ct
from gitcurator.core import dryrun
from gitcurator.core import hand_delivery as hd
from gitcurator.core import website_pipeline as wp
from gitcurator.core import web_fetch as wf


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------

_URL = 'https://walled.example.net/article'
_ERR = 'HTTP 403 — bot defense (server: cloudflare; challenge)'


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


class _FakeLimiter:
    def wait(self, domain):
        pass

    def penalize(self, domain, seconds):
        pass


class _FakeFetch:
    """Canned result, **kwargs-tolerant, counts its calls."""

    def __init__(self, result):
        self.result = result
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append(url)
        return self.result


def _ok(url, body=b'<html><head><title>The Real Page</title></head>'
        b'<body>the content the live DOM holds</body></html>'):
    return wf.FetchResult(url, final_url=url, status='full',
                          http_status=200, content_type='text/html',
                          charset='utf-8', body=body,
                          text=body.decode('utf-8'))


def _res(reason, http_status=None, category='', url=_URL):
    return wf.FetchResult(url, status='failed', reason=reason,
                          http_status=http_status, category=category)


class _FakeLLM:
    """Prompt-aware fake (the phase-2 pattern): the classify prompt
    gets a REAL taxonomy category, the subcategory prompt its answer,
    the analysis prompt the site card — a delivered page must be able
    to reach 'processed', not die in classification."""

    def __call__(self, messages, task=None):
        text = messages[0]['content']
        if 'filing a website into a personal library' in text:
            return json.dumps({'category': 'Design',
                               'confidence': 'high',
                               'reason': 'testing'})
        if 'was filed under' in text:
            return json.dumps({'subcategory': 'Assets & Resources',
                               'confidence': 'medium'})
        return json.dumps({
            'name': 'Walled Site', 'one_line': 'A page behind a wall.',
            'core_offerings': ['One'],
            'best_used_for': 'Use when you need to beat a bot wall.',
            'pricing': 'free', 'login_required': 'no',
            'similar_tools': [], 'tags': ['design'],
            'confidence': 'high'})


class _FifthCase(unittest.TestCase):
    """Temp vault + state DB, cleaned up."""

    def setUp(self):
        dryrun.disable()
        dryrun.clear()
        self.tmp = tempfile.mkdtemp(prefix='fifth-')
        self.vault = os.path.join(self.tmp, 'websites')
        os.makedirs(self.vault, exist_ok=True)
        self.db = wp.WebsiteStateDB(
            db_path=os.path.join(self.tmp, 'cache.db'))

    def tearDown(self):
        dryrun.disable()
        dryrun.clear()
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def page(self, url, body=b'<html><head><title>Took From Chrome'
              b'</title></head><body>the live DOM after the render'
              b'</body></html>'):
        sug = hd.suggested_filename(url)
        path = os.path.join(hd.hand_delivery_dir(self.vault), sug)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(body)
        return path


class _PipelineCase(_FifthCase):

    def setUp(self):
        super().setUp()
        self.in_vault = set()
        self.logs = []

    def make_pipeline(self, fetch, config=None):
        cfg = {'website_vault_path': self.vault,
               'web_domain_delay_s': 0}
        cfg.update(config or {})
        return wp.WebsitePipeline(
            config=cfg, llm_call=_FakeLLM(),
            vault_index_has=lambda u: u in self.in_vault,
            state=self.db, fetch_fn=fetch,
            rate_limiter=_FakeLimiter(),
            log=lambda m, l='info': self.logs.append((l, m)))

    def all_logs(self):
        return '\n'.join(m for (_, m) in self.logs)


# ---------------------------------------------------------------------------
# 1. the WebSocket frame codec (pure)
# ---------------------------------------------------------------------------

class TestFrameCodec(unittest.TestCase):

    def test_client_frame_is_masked_small(self):
        f = ct.encode_client_frame(b'hello', 0x1)
        self.assertEqual(f[0] & 0x80, 0x80)   # FIN
        self.assertEqual(f[0] & 0x0F, 0x1)    # text
        self.assertEqual(f[1] & 0x80, 0x80)   # MASK
        self.assertEqual(f[1] & 0x7F, 5)      # length
        frames, rest = ct.decode_frames(f)
        self.assertEqual(frames, [(True, 1, b'hello')])
        self.assertEqual(rest, b'')

    def test_client_frame_16bit_length(self):
        payload = b'x' * 300
        f = ct.encode_client_frame(payload, 0x1)
        self.assertEqual(f[1] & 0x7F, 126)    # 16-bit marker
        frames, rest = ct.decode_frames(f)
        self.assertEqual(frames[0][2], payload)

    def test_client_frame_64bit_length(self):
        payload = b'x' * 70000
        # a raw server-style frame with the 64-bit length encoding
        frames, rest = ct.decode_frames(
            bytes((0x80 | 0x1, 127)) + len(payload).to_bytes(8, 'big')
            + payload)
        self.assertEqual(frames[0][2], payload)
        self.assertEqual(rest, b'')

    def test_split_feed_yields_nothing_until_complete(self):
        f = ct.encode_client_frame(b'{"id": 1}', 0x1)
        cut = len(f) // 2
        frames, rest = ct.decode_frames(f[:cut])
        self.assertEqual(frames, [])
        self.assertEqual(rest, f[:cut])
        frames, rest = ct.decode_frames(rest + f[cut:])
        self.assertEqual(frames[0][2], b'{"id": 1}')

    def test_fragmented_message_and_control_frame(self):
        # a text frame (fin=False), a PING (control frames may ride
        # between fragments), then the continuation (fin=True)
        data = (bytes((0x1, 3)) + b'abc'                     # fragment 1
                + bytes((0x89, 2)) + b'hi'                   # ping
                + bytes((0x80, 3)) + b'def')                  # fragment 2 (FIN)
        frames, rest = ct.decode_frames(data)
        self.assertEqual(frames, [(False, 1, b'abc'),
                                  (True, 9, b'hi'),
                                  (True, 0, b'def')])
        self.assertEqual(rest, b'')

    def test_runaway_length_raises(self):
        with self.assertRaises(ct.CDPError):
            ct.decode_frames(
                bytes((0x80 | 0x1, 127)) + (1 << 40).to_bytes(8, 'big'),
                max_frame=1024)

    def test_close_frame_decodes(self):
        frames, _ = ct.decode_frames(bytes((0x88, 0)))
        self.assertEqual(frames, [(True, 8, b'')])


# ---------------------------------------------------------------------------
# 2. the DevTools client (an injected fake socket — no real socket)
# ---------------------------------------------------------------------------

class _FakeSock:
    """A scripted socket: recv() pops the next chunk; sendall() records
    what was sent (the handshake answer and every frame ride the
    script; a ``stall`` makes recv raise socket.timeout)."""

    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.sent = []
        self.timeout = None

    def recv(self, n):
        if not self._chunks:
            raise socket.timeout('stalled')
        return self._chunks.pop(0)

    def sendall(self, data):
        self.sent.append(bytes(data))

    def settimeout(self, t):
        self.timeout = t

    def close(self):
        self._chunks = []


_HANDSHAKE = (b'HTTP/1.1 101 Switching Protocols\r\n'
              b'Upgrade: websocket\r\n'
              b'Sec-WebSocket-Accept: k\r\n\r\n')


class TestCDPSocket(unittest.TestCase):

    def _sock(self, *chunks):
        return _FakeSock([_HANDSHAKE] + list(chunks))

    def test_handshake_refusal_raises(self):
        sock = _FakeSock([b'HTTP/1.1 403 Forbidden\r\n\r\n'])
        with self.assertRaises(ct.CDPError):
            ct.CDPSocket('127.0.0.1', 9222, '/devtools/page/1', sock=sock)

    def test_handshake_request_shape(self):
        sock = self._sock()
        ct.CDPSocket('127.0.0.1', 9222, '/devtools/page/1', sock=sock)
        req = sock.sent[0].decode('ascii')
        self.assertIn('GET /devtools/page/1 HTTP/1.1', req)
        self.assertIn('Upgrade: websocket', req)
        self.assertIn('Sec-WebSocket-Key:', req)
        self.assertIn('Sec-WebSocket-Version: 13', req)
        # no Origin header — the raw-socket client is not a webpage
        self.assertNotIn('Origin:', req)

    def test_call_round_trips_and_skips_events(self):
        resp = {'id': 1, 'result': {'result': {'value': 'complete'}}}
        event = {'method': 'Page.frameStartedLoading', 'params': {}}
        sock = self._sock(_srv_frame(json.dumps(event).encode()),
                          _srv_frame(json.dumps(resp).encode()))
        ws = ct.CDPSocket('127.0.0.1', 9222, '/devtools/page/1', sock=sock)
        out = ws.call('Runtime.evaluate', {'expression': '1'})
        self.assertEqual(out['result']['value'], 'complete')
        # the command went out as a MASKED text frame carrying the id
        sent = sock.sent[1]
        self.assertEqual(sent[0] & 0x0F, 0x1)
        self.assertEqual(sent[1] & 0x80, 0x80)
        frames, _ = ct.decode_frames(sent)
        self.assertEqual(json.loads(frames[0][2])['id'], 1)

    def test_ping_is_answered_with_masked_pong(self):
        sock = self._sock(_srv_frame(b'beat', 0x9),
                          _srv_frame(json.dumps(
                              {'id': 1, 'result': {}}).encode()))
        ws = ct.CDPSocket('127.0.0.1', 9222, '/devtools/page/1', sock=sock)
        ws.call('Runtime.evaluate')
        pongs = [f for f in sock.sent[1:]
                 if f[0] & 0x0F == 0xA]
        self.assertEqual(len(pongs), 1)
        frames, _ = ct.decode_frames(pongs[0])
        self.assertEqual(frames[0][2], b'beat')

    def test_close_frame_raises(self):
        sock = self._sock(_srv_frame(b'', 0x8))
        ws = ct.CDPSocket('127.0.0.1', 9222, '/devtools/page/1', sock=sock)
        with self.assertRaises(ct.CDPClosedError):
            ws.recv_message()

    def test_error_response_raises(self):
        bad = {'id': 1, 'error': {'message': 'no such target'}}
        sock = self._sock(_srv_frame(json.dumps(bad).encode()))
        ws = ct.CDPSocket('127.0.0.1', 9222, '/devtools/page/1', sock=sock)
        with self.assertRaises(ct.CDPError):
            ws.call('Runtime.evaluate')

    def test_stalled_socket_raises_timeout(self):
        sock = self._sock()   # the handshake answers, then nothing
        ws = ct.CDPSocket('127.0.0.1', 9222, '/devtools/page/1', sock=sock)
        with self.assertRaises(ct.CDPTimeoutError):
            ws.call('Runtime.evaluate', timeout=0.2)

    def test_evaluate_returns_the_value(self):
        resp = {'id': 1, 'result': {'result':
                                    {'type': 'string', 'value': 'full'}}}
        sock = self._sock(_srv_frame(json.dumps(resp).encode()))
        ws = ct.CDPSocket('127.0.0.1', 9222, '/devtools/page/1', sock=sock)
        self.assertEqual(ws.evaluate('document.readyState'), 'full')

    def test_fragmented_response_reassembles(self):
        body = json.dumps({'id': 1, 'result': {}}).encode()
        # a fragment without FIN, then the continuation WITH FIN
        sock = self._sock(bytes((0x1, 2)) + body[:2],
                          bytes((0x80, len(body) - 2)) + body[2:])
        ws = ct.CDPSocket('127.0.0.1', 9222, '/devtools/page/1', sock=sock)
        self.assertEqual(ws.call('Runtime.evaluate'), {})

    def test_mid_navigation_context_destruction_is_loading_not_failure(self):
        # "Execution context was destroyed" mid-navigation is a page
        # STILL LOADING, not a failure: the poll keeps going, and a
        # context swap landing exactly on the final DOM read gets ONE
        # retry. v0.53.0 — the load watch probes readyState AND the
        # tab's URL in one call ('complete|<url>'), and the settle
        # wants one more probe after the first complete (patched tiny
        # here to keep the scripted run fast).
        # ( ids: 1=probe(err) 2=probe(ok) 3=probe(settle) 4=DOM(err)
        # 5=DOM(ok) 6=title )
        _ctx_err = {'id': 0, 'error': {'message':
                                       'Execution context was destroyed'}}

        def _frame(obj, id_):
            _ctx_err['id'] = id_
            return _srv_frame(json.dumps(obj if 'error' not in obj
                                         else _ctx_err).encode())

        def _probe(id_):
            return _srv_frame(json.dumps(
                {'id': id_, 'result': {'result':
                 {'value': f'complete|{_URL}'}}}).encode())

        html = '<html><body>' + 'z' * 300 + '</body></html>'
        sock = self._sock(
            _frame({'error': True}, 1),                      # destroyed
            _probe(2),                                       # loaded
            _probe(3),                                       # the settle
            _frame({'error': True}, 4),                      # destroyed
            _srv_frame(json.dumps({'id': 5, 'result': {'result':
                {'value': html}}}).encode()),                 # the DOM
            _srv_frame(json.dumps({'id': 6, 'result': {'result':
                {'value': 'Retried'}}}).encode()))            # the title
        ws = ct.CDPSocket('127.0.0.1', 9222, '/devtools/page/1', sock=sock)

        class _FakeSession:
            port = 9222

            def __init__(self):
                self.closed = []

            def close_tab(self, target_id):
                self.closed.append(target_id)

        session = _FakeSession()
        tab = {'id': 'T1', 'webSocketDebuggerUrl':
               'ws://127.0.0.1:9222/devtools/page/T1'}
        # the socket already handshaked; the page fetch re-opens its own
        # socket in real life — here we exercise the socket directly
        # through the same evaluate sequence the fetch driver makes
        with mock.patch.object(ct, 'PAGE_SETTLE_S', 0.05), \
                mock.patch.object(ct, 'CDPSocket', return_value=ws):
            page = ct._fetch_opened_tab(session, _URL, tab, 10.0,
                                        lambda *a, **k: None)
        self.assertTrue(page['ok'], page)
        self.assertIn('z' * 100, page['html'])
        self.assertEqual(page['title'], 'Retried')
        self.assertEqual(session.closed, ['T1'])


# ---------------------------------------------------------------------------
# 2b. v0.53.0 — the page verdict + the load watch (the owner's report:
# "it tried to open a chrome tabs, but crashed and closed, and app
# shown false positive of success … didn't wait for sites to load")
# ---------------------------------------------------------------------------

_REAL_HTML = ('<html><head><title>The Real Page</title></head><body>'
              + ('r' * 500) + '</body></html>')


class TestPageVerdict(unittest.TestCase):
    """page_is_real — the pure law: a Chrome error page, a crash page
    or a challenge page is NEVER the site's page, whatever its
    readyState said (the false positive the owner reported)."""

    def test_a_real_page_passes(self):
        ok, reason = ct.page_is_real(_REAL_HTML, _URL, 'The Real Page')
        self.assertTrue(ok, reason)
        self.assertEqual(reason, '')

    def test_chrome_error_url_is_not_the_site(self):
        ok, reason = ct.page_is_real(
            '<html><body>' + 'e' * 500 + '</body></html>',
            'chrome-error://chromewebdata/', '')
        self.assertFalse(ok)
        self.assertIn("Chrome's own page", reason)

    def test_about_blank_is_not_the_site(self):
        ok, reason = ct.page_is_real('<html></html>', 'about:blank', '')
        self.assertFalse(ok)
        self.assertIn("Chrome's own page", reason)

    def test_an_empty_href_is_not_the_site(self):
        ok, reason = ct.page_is_real(_REAL_HTML, '', '')
        self.assertFalse(ok)
        self.assertIn('empty URL', reason)

    def test_an_err_code_names_the_error_page(self):
        html = ('<html><head><title>walled.example.net</title></head>'
                '<body><h1>This site can’t be reached</h1>'
                '<p>walled.example.net unexpectedly refused…'
                'ERR_CONNECTION_RESET</p></body></html>')
        ok, reason = ct.page_is_real(html, _URL, 'walled.example.net')
        self.assertFalse(ok)
        self.assertIn('ERR_CONNECTION_RESET', reason)
        self.assertIn("Chrome's error page", reason)

    def test_the_crash_page_is_a_crash(self):
        html = ('<html><head><title>Aw, Snap!</title></head><body>'
                + 'c' * 500 + '</body></html>')
        ok, reason = ct.page_is_real(html, _URL, 'Aw, Snap!')
        self.assertFalse(ok)
        self.assertIn('crashed', reason)

    def test_the_challenge_title_is_a_challenge(self):
        html = '<html><head><title>Just a moment...</title></head>' \
               '<body>' + ('j' * 500) + '</body></html>'
        ok, reason = ct.page_is_real(html, _URL, 'Just a moment...')
        self.assertFalse(ok)
        self.assertIn('challenge', reason)

    def test_the_challenge_machinery_is_a_challenge(self):
        html = ('<html><head><title>Some title</title></head><body>'
                '<script src="/cdn-cgi/challenge-platform/h/b/or.js">'
                '</script>' + ('j' * 400) + '</body></html>')
        ok, reason = ct.page_is_real(html, _URL, 'Some title')
        self.assertFalse(ok)
        self.assertIn('challenge', reason)


class TestLoadWatch(unittest.TestCase):
    """The v0.53 load watch, driven through the same scripted sockets
    the DevTools client tests use (no socket, no browser). The settle
    is patched tiny — the LAW is what is tested, not the clock."""

    def _sock(self, *chunks):
        return _FakeSock([_HANDSHAKE] + list(chunks))

    def _resp(self, id_, value):
        return _srv_frame(json.dumps(
            {'id': id_, 'result': {'result': {'value': value}}}).encode())

    def _probe(self, id_, state='complete', href=None):
        return self._resp(id_, f'{state}|{href or _URL}')

    def _dom(self, id_, html):
        return self._resp(id_, html)

    def _session(self):
        class _FakeSession:
            port = 9222

            def __init__(self):
                self.closed = []

            def close_tab(self, target_id):
                self.closed.append(target_id)
        s = _FakeSession()
        return s, {'id': 'T1', 'webSocketDebuggerUrl':
                   'ws://127.0.0.1:9222/devtools/page/T1'}

    def _fetch(self, sock, timeout=10.0, settle=0.05):
        ws = ct.CDPSocket('127.0.0.1', 9222, '/devtools/page/T1',
                          sock=sock)
        session, tab = self._session()
        # v0.54 — the anti-bot document patch is off for these scripts
        # (its Page.enable/addScript acks would shift every id; the
        # patch's own laws live in tests/test_persistentdoor.py)
        with mock.patch.object(ct, 'PAGE_SETTLE_S', settle), \
                mock.patch.object(ct, '_ANTI_BOT_PATCH', False), \
                mock.patch.object(ct, '_RECONNECT_PAUSE_S', 0.01), \
                mock.patch.object(ct, 'CDPSocket', return_value=ws):
            page = ct._fetch_opened_tab(session, _URL, tab, timeout,
                                        lambda *a, **k: None)
        return page, session

    def test_a_waited_real_page_is_delivered(self):
        # probe, settle probe, then the DOM + title — the happy path
        page, session = self._fetch(self._sock(
            self._probe(1), self._probe(2),
            self._dom(3, _REAL_HTML), self._resp(4, 'The Real Page')))
        self.assertTrue(page['ok'], page)
        self.assertIn('r' * 100, page['html'])
        self.assertEqual(page['title'], 'The Real Page')
        self.assertEqual(session.closed, ['T1'])

    def test_a_chrome_error_page_is_never_a_delivery(self):
        # the EXACT false positive the owner reported: readyState
        # 'complete', 500 bytes of DOM — and the DOM is Chrome's own
        # "This site can't be reached" page with its ERR_ code
        err_html = ('<html><head><title>walled.example.net</title></head>'
                    '<body><h1>This site can’t be reached</h1>'
                    '<p>ERR_CONNECTION_RESET</p></body></html>')
        page, session = self._fetch(self._sock(
            self._probe(1), self._probe(2),
            self._dom(3, err_html), self._resp(4, 'walled.example.net')))
        self.assertFalse(page['ok'])
        self.assertNotIn('r', page.get('html') or '')
        self.assertIn('ERR_CONNECTION_RESET', page['error'])
        self.assertIn("Chrome's error page", page['error'])
        self.assertEqual(session.closed, ['T1'])

    def test_a_crash_page_is_never_a_delivery(self):
        crash_html = ('<html><head><title>Aw, Snap!</title></head>'
                      '<body><div id="crash">' + 'c' * 500
                      + '</div></body></html>')
        page, _ = self._fetch(self._sock(
            self._probe(1), self._probe(2),
            self._dom(3, crash_html), self._resp(4, 'Aw, Snap!')))
        self.assertFalse(page['ok'])
        self.assertIn('crashed', page['error'])

    def test_a_challenge_that_passes_is_waited_for(self):
        # the door's whole point: the tab loads a challenge, the owner's
        # REAL Chrome passes it (the redirect swaps the page) — the
        # settle restarts on the href change and the REAL page is taken.
        # Script: probe, settle, challenge DOM+title → reset → probe,
        # settle, real DOM+title.
        challenge = ('<html><head><title>Just a moment...</title>'
                     '</head><body>' + ('j' * 500) + '</body></html>')
        page, session = self._fetch(self._sock(
            self._probe(1), self._probe(2),
            self._dom(3, challenge), self._resp(4, 'Just a moment...'),
            self._probe(5), self._probe(6),
            self._dom(7, _REAL_HTML), self._resp(8, 'The Real Page')))
        self.assertTrue(page['ok'], page)
        self.assertIn('r' * 100, page['html'])
        self.assertEqual(page['title'], 'The Real Page')
        self.assertEqual(session.closed, ['T1'])

    def test_a_challenge_that_never_passes_is_named(self):
        # the challenge stays and stays — the budget runs out and the
        # honest sentence says it never got past it (never a delivery)
        challenge = ('<html><head><title>Just a moment...</title>'
                     '</head><body>' + ('j' * 500) + '</body></html>')
        page, _ = self._fetch(self._sock(
            self._probe(1), self._probe(2),
            self._dom(3, challenge), self._resp(4, 'Just a moment...'),
            self._probe(5), self._probe(6),
            self._dom(7, challenge), self._resp(8, 'Just a moment...')),
            timeout=1.5)
        self.assertFalse(page['ok'])
        self.assertIn('challenge', page['error'])
        self.assertNotIn('r', page.get('html') or '')

    def test_a_page_that_never_finishes_is_named(self):
        page, _ = self._fetch(self._sock(
            self._probe(1, state='loading'),
            self._probe(2, state='loading'),
            self._probe(3, state='loading'),
            self._probe(4, state='loading')), timeout=1.5)
        self.assertFalse(page['ok'])
        self.assertIn('never finished loading', page['error'])

    def test_a_tab_stuck_on_about_blank_is_named(self):
        # the tab opened but the navigation never committed — Chrome's
        # own page, honestly named, never delivered
        page, _ = self._fetch(self._sock(
            self._probe(1, href='about:blank'),
            self._probe(2, href='about:blank'),
            self._probe(3, href='about:blank'),
            self._probe(4, href='about:blank')), timeout=1.5)
        self.assertFalse(page['ok'])
        self.assertIn("Chrome's own page", page['error'])

    def test_the_settle_restarts_on_a_href_change(self):
        # a late redirect after 'complete': the first URL settles
        # halfway, the href swaps — the settle restarts and the page
        # at the FINAL url is what gets taken (settle 1.2s so the swap
        # lands mid-settle, exactly the real-world shape; the settle
        # needs its probes at the final URL before the DOM is read)
        final_url = 'https://walled.example.net/article?page=2'
        page, _ = self._fetch(self._sock(
            self._probe(1),                       # complete at _URL
            self._probe(2),                       # settle…
            self._probe(3, href=final_url),       # the swap (reset)
            self._probe(4, href=final_url),       # settling…
            self._probe(5, href=final_url),       # settling…
            self._probe(6, href=final_url),       # settle met
            self._dom(7, _REAL_HTML), self._resp(8, 'The Real Page')),
            settle=1.2)
        self.assertTrue(page['ok'], page)
        self.assertIn('r' * 100, page['html'])

    def test_an_empty_dom_is_not_a_delivery(self):
        page, _ = self._fetch(self._sock(
            self._probe(1), self._probe(2),
            self._dom(3, '<html></html>'), self._resp(4, '')))
        self.assertFalse(page['ok'])
        self.assertIn('rendered empty', page['error'])


# ---------------------------------------------------------------------------
# 3. the candidates — which links earn the offer
# ---------------------------------------------------------------------------

class TestCandidates(unittest.TestCase):

    def test_fetch_failed_review_rows_are_candidates(self):
        out = ct.collect_failed_fetch_links([
            {'url': _URL, 'outcome': 'review', 'fetch_status': 'failed',
             'error': f'fetch failed: {_ERR}'}])
        self.assertEqual(out, [{'url': _URL,
                                'error': f'fetch failed: {_ERR}'}])

    def test_hard_failures_are_candidates(self):
        out = ct.collect_failed_fetch_links(
            [{'url': 'https://x.example/a', 'outcome': 'failed',
              'error': 'boom'}])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['error'], 'boom')

    def test_successes_and_partials_are_not(self):
        out = ct.collect_failed_fetch_links([
            {'url': 'https://ok.example/a', 'outcome': 'processed'},
            {'url': 'https://low.example/b', 'outcome': 'review',
             'fetch_status': 'full', 'error': 'low confidence'},
            {'url': 'https://arch.example/c', 'outcome': 'review',
             'fetch_status': 'partial', 'error': 'archived copy'},
            {'url': 'https://skip.example/d', 'outcome': 'skipped'}])
        self.assertEqual(out, [])

    def test_dedupe_by_canonical_and_loopback_dropped(self):
        out = ct.collect_failed_fetch_links([
            {'url': _URL, 'outcome': 'review', 'fetch_status': 'failed',
             'error': 'e1'},
            {'url': _URL + '?utm_source=bot', 'outcome': 'review',
             'fetch_status': 'failed', 'error': 'e2'},
            {'url': 'http://localhost:8080/x', 'outcome': 'review',
             'fetch_status': 'failed', 'error': 'e3'},
            {'url': 'not-a-link', 'outcome': 'review',
             'fetch_status': 'failed', 'error': 'e4'}])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['url'], _URL)
        self.assertEqual(out[0]['error'], 'e1')

    def test_dismissed_links_never_nag(self):
        out = ct.collect_failed_fetch_links(
            [{'url': _URL, 'outcome': 'review', 'fetch_status': 'failed',
              'error': 'fetch failed: HTTP 404'}],
            is_dismissed=lambda u: True)
        self.assertEqual(out, [])

    def test_dismissed_check_failure_offers_them(self):
        def _boom(u):
            raise RuntimeError('no db')
        out = ct.collect_failed_fetch_links(
            [{'url': _URL, 'outcome': 'review', 'fetch_status': 'failed',
              'error': 'e'}], is_dismissed=_boom)
        self.assertEqual(len(out), 1)


# ---------------------------------------------------------------------------
# 4. the session helpers + the Chrome process (a fake exe, POSIX only)
# ---------------------------------------------------------------------------

class TestSessionHelpers(unittest.TestCase):

    def test_loopback_law(self):
        for u in ('http://localhost:3000/x', 'http://127.0.0.1:9/x',
                  'https://machine.local/y'):
            self.assertTrue(ct.is_loopback_url(u), u)
        self.assertFalse(ct.is_loopback_url(_URL))

    def test_pick_free_port(self):
        p = ct._pick_free_port()
        self.assertIsInstance(p, int)
        self.assertTrue(0 < p < 65536)


@unittest.skipIf(os.name == 'nt' or not os.path.exists('/bin/sh'),
                 'the fake-exe session test needs POSIX sh')
class TestChromeSession(unittest.TestCase):
    """A fake chrome 'exe': a shell script that prints the DevTools
    line Chrome itself prints, then sleeps. No network is ever made —
    new_tab is never called; only the endpoint parsing + the
    lifecycle (terminate, profile erase) are exercised."""

    def _fake_exe(self, lines, then='sleep 30', require=''):
        path = os.path.join(tempfile.mkdtemp(prefix='fakechrome-'),
                            'chrome')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('#!/bin/sh\n')
            if require:
                # a Chromium that refuses to start without the tier's
                # flag — the ladder's proof
                f.write(f'case " $* " in\n'
                        f'  *" {require} "*) ;;\n'
                        f'  *) exit 7 ;;\n'
                        f'esac\n')
            for line in lines:
                # the DevTools line rides STDERR (stdout is DEVNULL in
                # the session launcher — exactly like real Chrome)
                f.write(f'echo "{line}" 1>&2\n')
            f.write(then + '\n')
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
        return path

    def test_endpoint_line_is_parsed_and_profile_erased(self):
        exe = self._fake_exe(
            ['DevTools listening on ws://127.0.0.1:59999/devtools/'
             'browser/abc-123'])
        logs = []
        session = ct.ChromeSession(exe, log=lambda m, l: logs.append(m),
                                   persist=False)
        try:
            self.assertEqual(session.port, 59999)
            self.assertTrue(session.alive())
            self.assertTrue(any('Chrome session live' in m for m in logs))
        finally:
            profile = session.profile
            session.close()
        self.assertIsNotNone(profile)
        self.assertFalse(os.path.isdir(profile))   # the temp profile erased
        self.assertFalse(session.alive())

    def test_dead_exe_raises_honestly(self):
        exe = self._fake_exe([], then='exit 0')
        with self.assertRaises(ct.CDPError):
            session = ct.ChromeSession(exe, log=lambda *a: None,
                                       persist=False)
            session.close()

    def test_silent_exe_raises_honestly(self):
        exe = self._fake_exe(['nothing useful'], then='exit 1')
        with self.assertRaises(ct.CDPError):
            session = ct.ChromeSession(
                exe, log=lambda *a: None, persist=False)
            session.close()
        # the session that failed to start still cleaned its profile
        leftovers = [d for d in os.listdir(tempfile.gettempdir())
                     if d.startswith('gitcurator-chrome-')]
        for d in leftovers:
            shutil.rmtree(os.path.join(tempfile.gettempdir(), d),
                          ignore_errors=True)

    def test_container_flag_ladder_second_attempt_wins(self):
        # a Chromium that cannot use its SUID sandbox (exit 7 unless
        # --no-sandbox rides the command line — the container case):
        # the first attempt dies, the container-flag attempt comes up
        logs = []
        exe = self._fake_exe(
            ['DevTools listening on ws://127.0.0.1:59998/devtools/'
             'browser/tier-2'], require='--no-sandbox')
        session = ct.ChromeSession(exe, log=lambda m, l: logs.append(m),
                                   persist=False)
        try:
            self.assertEqual(session.port, 59998)
            joined = '\n'.join(logs)
            self.assertIn('container flags', joined)
            self.assertNotIn('HEADLESS', joined)
        finally:
            session.close()

    def test_headless_ladder_third_attempt_wins(self):
        # a display-less machine (no X — the window can never open):
        # standard and container attempts die, the HEADLESS attempt
        # comes up (the tabs are not visible, the DOM is identical)
        logs = []
        exe = self._fake_exe(
            ['DevTools listening on ws://127.0.0.1:59997/devtools/'
             'browser/tier-3'], require='--headless=new')
        session = ct.ChromeSession(exe, log=lambda m, l: logs.append(m),
                                   persist=False)
        try:
            self.assertEqual(session.port, 59997)
            joined = '\n'.join(logs)
            self.assertIn('container flags', joined)
            self.assertIn('HEADLESS', joined)
        finally:
            session.close()


# ---------------------------------------------------------------------------
# 5. the fetch driver's honest misses (no browser ever launches)
# ---------------------------------------------------------------------------

class TestFetchDriverHonesty(unittest.TestCase):

    def test_no_chrome_is_a_per_url_failure(self):
        with mock.patch.object(hd, 'find_chrome', return_value=None):
            out = ct.fetch_pages_via_chrome([_URL])
        self.assertEqual(len(out), 1)
        self.assertFalse(out[0]['ok'])
        self.assertIn('not found', out[0]['error'].lower())

    def test_loopback_and_non_http_never_open(self):
        with mock.patch.object(hd, 'find_chrome', return_value=None):
            out = ct.fetch_pages_via_chrome(
                ['http://localhost:1/x', 'ftp://nope.example/f'])
        self.assertEqual(len(out), 2)
        self.assertIn('loopback', out[0]['error'])
        self.assertIn('http(s)', out[1]['error'])

    def test_dry_run_never_launches(self):
        try:
            dryrun.enable()
            boom = mock.MagicMock(side_effect=AssertionError('launched'))
            with mock.patch.object(ct, 'ChromeSession', boom):
                out = ct.fetch_pages_via_chrome([_URL])
            self.assertFalse(out[0]['ok'])
            self.assertIn('dry-run', out[0]['error'])
        finally:
            dryrun.disable()
            dryrun.clear()


# ---------------------------------------------------------------------------
# 6. the delivery — pages into the fourth door's folder, 'auto' stamped
# ---------------------------------------------------------------------------

class TestDelivery(_FifthCase):

    def _pages(self, urls, log=None):
        def _fetcher(urls, log=None):
            return [{'url': u, 'ok': True, 'title': 'T',
                     'html': '<html><body>' + ('x' * 400) + '</body></html>',
                     'error': ''} for u in urls]
        return _fetcher

    def test_delivery_writes_queue_and_pages(self):
        pages = self._pages(None)
        report = ct.deliver_pages_via_chrome(
            self.vault, [{'url': _URL, 'error': _ERR}],
            log=lambda *a, **k: None, _fetcher=pages)
        self.assertEqual(report['delivered'], 1)
        self.assertEqual(report['urls'], [_URL])
        # the queue row exists, stamped with the auto door + the wall
        with open(hd.queue_path(self.vault), encoding='utf-8') as f:
            queue = json.load(f)
        meta = queue['links'][_URL]
        self.assertEqual(meta['door'], 'auto')
        self.assertEqual(meta['wall'], _ERR)
        # the page landed at the suggested filename
        path = os.path.join(report['folder'],
                            hd.suggested_filename(_URL))
        self.assertTrue(os.path.isfile(path))
        with open(path, encoding='utf-8') as f:
            self.assertIn('<body>', f.read())

    def test_delivery_counts_failures_honestly(self):
        def _fetcher(urls, log=None):
            return [{'url': u, 'ok': False, 'title': '', 'html': '',
                     'error': 'the page never finished loading in 45s'}
                    for u in urls]
        report = ct.deliver_pages_via_chrome(
            self.vault, [{'url': _URL, 'error': _ERR}],
            log=lambda *a, **k: None, _fetcher=_fetcher)
        self.assertEqual(report['delivered'], 0)
        self.assertEqual(report['failed'], 1)
        # the queue row was still written (the link is queued for the
        # fourth door — an owner may still deliver it by hand)
        self.assertIn(_URL, hd._read_queue(self.vault)['links'])

    def test_failed_links_detail_rides_the_report(self):
        # v0.53.0 — the owner's ask ("xx links didn't generate content
        # … got error xxx"): every failure carries its url + error so
        # the end-of-delivery modal can name them
        def _fetcher(urls, log=None):
            return [
                {'url': 'https://a.example.net/1', 'ok': True,
                 'title': 'A', 'html': '<html><body>'
                 + ('x' * 400) + '</body></html>', 'error': ''},
                {'url': 'https://b.example.net/2', 'ok': False,
                 'title': '', 'html': '', 'error': "the tab showed "
                 "Chrome's error page (ERR_CONNECTION_RESET)"},
            ]
        report = ct.deliver_pages_via_chrome(
            self.vault, [{'url': 'https://a.example.net/1', 'error': ''},
                         {'url': 'https://b.example.net/2', 'error': ''}],
            log=lambda *a, **k: None, _fetcher=_fetcher)
        self.assertEqual(report['delivered'], 1)
        self.assertEqual(report['failed'], 1)
        self.assertEqual(len(report['failed_links']), 1)
        self.assertEqual(report['failed_links'][0]['url'],
                         'https://b.example.net/2')
        self.assertIn('ERR_CONNECTION_RESET',
                      report['failed_links'][0]['error'])

    def test_an_error_page_from_any_fetch_path_is_never_written(self):
        # v0.53.0 — the verdict is the DELIVERY's law too: a fetch path
        # that hands back a Chrome error page (the exact shape a
        # crashed tab produced before the fix) is a named failure —
        # no file, no consumed row, nothing delivered
        err_page = ('<html><head><title>x.example</title></head><body>'
                    '<h1>This site can’t be reached</h1>'
                    '<p>ERR_TIMED_OUT</p></body></html>')
        report = ct.deliver_pages_via_chrome(
            self.vault, [{'url': _URL, 'error': _ERR}],
            log=lambda *a, **k: None,
            _fetcher=lambda urls, log=None: [
                {'url': _URL, 'ok': True, 'title': 'x.example',
                 'html': err_page, 'error': ''}])
        self.assertEqual(report['delivered'], 0)
        self.assertEqual(report['failed'], 1)
        self.assertIn('ERR_TIMED_OUT', report['failed_links'][0]['error'])
        self.assertFalse(os.path.exists(os.path.join(
            report['folder'], hd.suggested_filename(_URL))))

    def test_crashing_fetcher_never_breaks_the_delivery(self):
        def _boom(urls, log=None):
            raise RuntimeError('chrome exploded')
        report = ct.deliver_pages_via_chrome(
            self.vault, [{'url': _URL, 'error': _ERR}],
            log=lambda *a, **k: None, _fetcher=_boom)
        self.assertEqual(report['delivered'], 0)
        self.assertEqual(report['failed'], 1)

    def test_dry_run_launches_nothing_and_writes_nothing(self):
        try:
            dryrun.enable()
            called = []
            report = ct.deliver_pages_via_chrome(
                self.vault, [{'url': _URL, 'error': _ERR}],
                log=lambda *a, **k: None,
                _fetcher=lambda u, log=None: called.append(u) or [])
            self.assertEqual(report['delivered'], 0)
            self.assertEqual(called, [])
            self.assertFalse(os.path.exists(hd.queue_path(self.vault)))
            self.assertFalse(os.path.exists(
                hd.hand_delivery_dir(self.vault)))
        finally:
            dryrun.disable()
            dryrun.clear()

    def test_empty_links_and_missing_vault_are_quiet(self):
        report = ct.deliver_pages_via_chrome(
            self.vault, [], log=lambda *a, **k: None)
        self.assertEqual(report['delivered'], 0)
        report = ct.deliver_pages_via_chrome(
            '', [{'url': _URL, 'error': ''}],
            log=lambda *a, **k: None)
        self.assertEqual(report['delivered'], 0)


# ---------------------------------------------------------------------------
# 7. the consume side — the auto door's story
# ---------------------------------------------------------------------------

class TestConsumeWording(_FifthCase):

    def test_auto_door_words_the_story(self):
        res = hd.hand_fetch_result(_URL, b'<html></html>', _ERR,
                                   door='auto')
        self.assertEqual(res.status, 'full')
        self.assertIn('auto-delivered via the owner\'s own Chrome',
                      res.reason)
        self.assertIn(_ERR, res.reason)

    def test_manual_wording_unchanged(self):
        res = hd.hand_fetch_result(_URL, b'<html></html>', _ERR)
        self.assertIn('hand-delivered via the owner\'s real Chrome',
                      res.reason)
        self.assertNotIn('auto-delivered', res.reason)

    def test_take_hand_delivered_stamps_and_tells_the_auto_story(self):
        hd.enqueue_hand_delivery(self.vault, [_URL], walls={_URL: _ERR},
                                 log=lambda *a, **k: None, door='auto')
        self.page(_URL)
        res = hd.take_hand_delivered(self.vault, _URL)
        self.assertIsNotNone(res)
        self.assertIn('auto-delivered', res.reason)
        with open(hd.queue_path(self.vault), encoding='utf-8') as f:
            queue = json.load(f)
        self.assertTrue(queue['links'][_URL]['consumed'])

    def test_reenqueue_of_consumed_keeps_the_door(self):
        hd.enqueue_hand_delivery(self.vault, [_URL], walls={_URL: _ERR},
                                 log=lambda *a, **k: None, door='auto')
        self.page(_URL)
        hd.take_hand_delivered(self.vault, _URL)
        hd.enqueue_hand_delivery(self.vault, [_URL],
                                 log=lambda *a, **k: None, door='auto')
        with open(hd.queue_path(self.vault), encoding='utf-8') as f:
            meta = json.load(f)['links'][_URL]
        self.assertFalse(meta.get('consumed'))
        self.assertEqual(meta.get('door'), 'auto')


# ---------------------------------------------------------------------------
# 7b. v0.53.0 — the consume-side re-verification (an auto-delivered
# error page is discarded, never consumed; the owner's saves are
# trusted as always)
# ---------------------------------------------------------------------------

_ERR_PAGE = (b'<html><head><title>walled.example.net</title></head>'
             b'<body><h1>This site can\xe2\x80\x99t be reached</h1>'
             b'<p>ERR_CONNECTION_RESET</p></body></html>')

_CHALLENGE_PAGE = (b'<html><head><title>Just a moment...</title></head>'
                   b'<body><script '
                   b'src="/cdn-cgi/challenge-platform/h/b/or.js">'
                   b'</script></body></html>')


class TestConsumeVerify(_FifthCase):

    def test_an_auto_error_page_is_discarded_not_consumed(self):
        # what a crashed Chrome session leaves behind: the fifth door
        # wrote the file (door 'auto'), but it is Chrome's own error
        # page — the consume side discards it, the link keeps waiting
        hd.enqueue_hand_delivery(self.vault, [_URL], walls={_URL: _ERR},
                                 log=lambda *a, **k: None, door='auto')
        path = self.page(_URL, body=_ERR_PAGE)
        logs = []
        res = hd.take_hand_delivered(self.vault, _URL,
                                     log=lambda m, l='info':
                                     logs.append((l, m)))
        self.assertIsNone(res)
        self.assertFalse(os.path.exists(path))    # the app's own file
        meta = hd._read_queue(self.vault)['links'][_URL]
        self.assertFalse(meta.get('consumed'))    # keeps waiting
        self.assertIn('NOT the site', '\n'.join(m for _, m in logs))

    def test_an_auto_challenge_page_is_discarded_not_consumed(self):
        hd.enqueue_hand_delivery(self.vault, [_URL], walls={_URL: _ERR},
                                 log=lambda *a, **k: None, door='auto')
        path = self.page(_URL, body=_CHALLENGE_PAGE)
        res = hd.take_hand_delivered(self.vault, _URL)
        self.assertIsNone(res)
        self.assertFalse(os.path.exists(path))

    def test_an_auto_real_page_is_consumed_as_before(self):
        hd.enqueue_hand_delivery(self.vault, [_URL], walls={_URL: _ERR},
                                 log=lambda *a, **k: None, door='auto')
        path = self.page(_URL)             # the real-DOM default body
        res = hd.take_hand_delivered(self.vault, _URL)
        self.assertIsNotNone(res)
        self.assertTrue(os.path.exists(path))    # the record stays

    def test_an_owner_saved_error_page_is_trusted(self):
        # no door stamp — the owner's Ctrl+S is the owner's verdict:
        # even an error-page save is consumed, exactly as before
        hd.enqueue_hand_delivery(self.vault, [_URL], walls={_URL: _ERR},
                                 log=lambda *a, **k: None)
        path = self.page(_URL, body=_ERR_PAGE)
        res = hd.take_hand_delivered(self.vault, _URL)
        self.assertIsNotNone(res)
        self.assertTrue(os.path.exists(path))
        self.assertTrue(hd._read_queue(self.vault)
                        ['links'][_URL]['consumed'])

    def test_consume_delivered_discards_auto_bad_pages(self):
        # the batch-level pass: the same law for the table consume
        hd.enqueue_hand_delivery(
            self.vault, [_URL, 'https://ok.example.net/real'],
            walls={_URL: _ERR}, log=lambda *a, **k: None, door='auto')
        self.page(_URL, body=_ERR_PAGE)
        self.page('https://ok.example.net/real')
        out = hd.consume_delivered(self.vault,
                                   log=lambda *a, **k: None)
        self.assertEqual([i['url'] for i in out],
                         ['https://ok.example.net/real'])
        self.assertFalse(os.path.exists(
            os.path.join(hd.hand_delivery_dir(self.vault),
                         hd.suggested_filename(_URL))))

    def test_pending_hand_links_reasks_after_a_discard(self):
        # the discarded auto page leaves the queue row unconsumed and
        # the file gone — the fifth door's to-do list asks for it again
        hd.enqueue_hand_delivery(self.vault, [_URL], walls={_URL: _ERR},
                                 log=lambda *a, **k: None, door='auto')
        self.page(_URL, body=_ERR_PAGE)
        before = hd.pending_hand_links(self.vault)
        hd.take_hand_delivered(self.vault, _URL)
        after = hd.pending_hand_links(self.vault)
        self.assertEqual([l['url'] for l in after], [_URL])
        self.assertEqual(len(before), 0)   # the file "landed" until judged


# ---------------------------------------------------------------------------
# 8. the pipeline — the fifth door answers before the machine doors
# ---------------------------------------------------------------------------

class TestPipelineFifthDoor(_PipelineCase):

    def test_auto_delivered_page_answers_before_any_machine_door(self):
        self.db.enqueue_retry(_URL, _ERR)
        hd.enqueue_hand_delivery(self.vault, [_URL], walls={_URL: _ERR},
                                 log=lambda *a, **k: None, door='auto')
        self.page(_URL)
        fetch = _FakeFetch(_res(_ERR, 403, 'blocked_bot'))
        pipe = self.make_pipeline(fetch)
        res = pipe.process_link(_URL)
        self.assertEqual(fetch.calls, [])       # never asked
        self.assertEqual(res['outcome'], 'processed')
        self.assertIn('auto-delivered', self.all_logs())
        self.assertIsNone(self.db.retry_row(_URL))

    def test_the_full_circle_deliver_then_rerun(self):
        # 1. the run fails the link (the machine doors walled)
        fetch = _FakeFetch(_res(_ERR, 403, 'blocked_bot'))
        pipe = self.make_pipeline(fetch)
        res = pipe.process_link(_URL)
        self.assertEqual(res['outcome'], 'review')
        self.assertEqual(res['fetch_status'], 'failed')
        candidates = ct.collect_failed_fetch_links(
            pipe.last_results, is_dismissed=lambda u: False)
        self.assertEqual([c['url'] for c in candidates], [_URL])
        # 2. the fifth door delivers the page (a scripted fetcher —
        #    the real one launches the owner's Chrome; never here)
        report = ct.deliver_pages_via_chrome(
            self.vault, candidates, log=lambda *a, **k: None,
            _fetcher=lambda urls, log=None: [
                {'url': u, 'ok': True, 'title': 'The Real Page',
                 'html': '<html><head><title>The Real Page</title>'
                         '</head><body>' + ('y' * 500) + '</body></html>',
                 'error': ''} for u in urls])
        self.assertEqual(report['delivered'], 1)
        # 3. the re-run processes the delivered page as a REAL fetch
        results = pipe.run(report['urls'])
        self.assertEqual([r['outcome'] for r in results], ['processed'])
        self.assertEqual(fetch.calls, [_URL])   # only the failed attempt
        self.assertIn('auto-delivered', self.all_logs())
        self.assertIsNone(self.db.retry_row(_URL))

    def test_auto_page_still_consumed_with_door_disabled_by_hand(self):
        # an owner-saved page (no door marker) keeps the manual story
        self.db.enqueue_retry(_URL, _ERR)
        hd.enqueue_hand_delivery(self.vault, [_URL], walls={_URL: _ERR},
                                 log=lambda *a, **k: None)
        self.page(_URL, body=b'<html><head><title>By Hand</title>'
                  b'</head><body>owner saved me</body></html>')
        fetch = _FakeFetch(_res(_ERR, 403, 'blocked_bot'))
        pipe = self.make_pipeline(fetch)
        res = pipe.process_link(_URL)
        self.assertEqual(res['outcome'], 'processed')
        self.assertIn('hand-delivered', self.all_logs())


# ---------------------------------------------------------------------------
# 9. the ☠️ law — the owner's exact words this session, re-verified
# ---------------------------------------------------------------------------

class TestSkullLaw(_PipelineCase):
    """"when user paste ☠️ on a row, app must not fetch it again …
    its note from _review must be deleted in next fetch and run, and
    never be fetched again" — the v0.44/v0.49 law, verified against
    the exact gesture: a Status cell with ONLY the emoji."""

    def _table_with_skull(self):
        path = wp.decommission_table_path(self.vault)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write('# Review Master Table\n\n'
                    '| # | Date | URL | Domain | Source | Status | Notes |\n'
                    '|---|------|-----|--------|--------|--------|-------|\n'
                    f'| - | 2026-10-09 | {_URL} | walled.example.net '
                    '| test | ☠️ | |\n')

    def test_skull_alone_buries_sweeps_and_never_fetches(self):
        # the link fails once — its placeholder waits in _review
        fetch = _FakeFetch(_res('HTTP 403 — bot defense', 403,
                                'blocked_bot'))
        pipe = self.make_pipeline(fetch)
        res = pipe.process_link(_URL)
        placeholder = res['note_path']
        self.assertTrue(os.path.exists(placeholder))
        # the owner pastes ☠️ on the row (and NOTHING else)
        self._table_with_skull()
        # the next run: the row is consumed BEFORE anything fetches
        pipe2 = self.make_pipeline(_FakeFetch(_ok(_URL)))
        res2 = pipe2.process_link(_URL)
        self.assertEqual(res2['outcome'], 'skipped')
        self.assertIn('decommission', res2['error'].lower())
        self.assertEqual(pipe2.state.is_dismissed(
            wp.normalize_website_url(_URL)), True)
        # …the placeholder note is GONE from _review…
        self.assertFalse(os.path.exists(placeholder))
        # …and the never-fetch gate holds on a THIRD run
        pipe3 = self.make_pipeline(_FakeFetch(_ok(_URL)))
        res3 = pipe3.process_link(_URL)
        self.assertEqual(res3['outcome'], 'skipped')
        self.assertEqual(pipe3.state.retry_row(
            wp.normalize_website_url(_URL)), None)


# ---------------------------------------------------------------------------
# 9b. v0.53.0 — the end-of-delivery honest modal (the bare-mixin
# routing: the owner's false-positive-of-success report ends in a
# modal that names what could not be gathered)
# ---------------------------------------------------------------------------


class TestDeliveryFailureModal(unittest.TestCase):

    def _win(self, visible=True):
        from gitcurator.gui.main_window.processing_control \
            import ProcessingControlMixin
        win = ProcessingControlMixin()
        win.logs = []
        win.modals = []
        win.log_message = lambda msg, level='info': win.logs.append(
            (level, msg))
        win._show_custom_message_box = \
            lambda title, text, success=True: win.modals.append(
                (title, text, success))
        win._closing = False
        win.isVisible = lambda: visible
        return win

    def test_failures_open_the_honest_modal(self):
        win = self._win()
        win._on_delivery_failed([
            {'url': _URL, 'error': "the tab showed Chrome's error page "
             '(ERR_CONNECTION_RESET)'}])
        self.assertEqual(len(win.modals), 1)
        title, text, success = win.modals[0]
        self.assertIn('not taken', title)
        self.assertFalse(success)
        self.assertIn('1 link(s)', text)
        self.assertIn(_URL, text)
        self.assertIn('ERR_CONNECTION_RESET', text)
        self.assertIn('retry queue', text)
        # the log carries the honest summary line too
        self.assertIn("couldn't be gathered",
                      '\n'.join(m for _, m in win.logs))

    def test_the_first_five_errors_and_the_count(self):
        win = self._win()
        failed = [{'url': f'https://x{i}.example.net/', 'error': f'e{i}'}
                  for i in range(7)]
        win._on_delivery_failed(failed)
        self.assertEqual(len(win.modals), 1)
        text = win.modals[0][1]
        self.assertIn('7 link(s)', text)
        self.assertIn('… and 2 more', text)
        self.assertIn('https://x0.example.net/', text)
        self.assertNotIn('https://x6.example.net/', text)

    def test_no_failures_no_modal(self):
        win = self._win()
        win._on_delivery_failed([])
        self.assertEqual(win.modals, [])
        self.assertEqual(win.logs, [])

    def test_an_invisible_window_logs_only(self):
        # the shutdown guard: a modal no one can dismiss never opens
        win = self._win(visible=False)
        win._on_delivery_failed([{'url': _URL, 'error': 'boom'}])
        self.assertEqual(win.modals, [])
        self.assertEqual(len(win.logs), 1)

    def test_junk_payload_is_tolerated(self):
        win = self._win()
        win._on_delivery_failed(None)
        win._on_delivery_failed(['not-a-dict'])
        self.assertEqual(win.modals, [])


# ---------------------------------------------------------------------------
# 10. release bookkeeping
# ---------------------------------------------------------------------------

class TestReleaseBookkeeping(unittest.TestCase):
    """The fifth door ships as a real release: the VERSION pin, the
    CHANGELOG beat, the CI registration, the AGENTS.md listing, the
    config key documented where every key is."""

    def setUp(self):
        self.root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    def _read(self, *parts):
        with open(os.path.join(self.root, *parts), 'r',
                  encoding='utf-8') as f:
            return f.read()

    def test_version_is_0500(self):
        self.assertEqual(self._read('VERSION').strip(), '0.60.1')

    def test_changelog_has_the_beat(self):
        text = self._read('CHANGELOG.md')
        self.assertIn('## [0.50.0]', text)
        self.assertIn('fifth door', text.lower())

    def test_changelog_has_the_v053_beat(self):
        text = self._read('CHANGELOG.md')
        self.assertIn('## [0.53.0]', text)
        self.assertIn('waits for the page', text.lower())
        self.assertIn('false positive', text.lower())

    def test_ci_runs_this_module(self):
        text = self._read('.github', 'workflows', 'ci.yml')
        self.assertIn('tests.test_fifthdoor', text)

    def test_agents_md_lists_this_module(self):
        text = self._read('AGENTS.md')
        self.assertIn('test_fifthdoor', text)

    def test_config_documents_the_key(self):
        text = self._read('app', 'config.example.json')
        self.assertIn('"web_browser_retry": true', text)
        self.assertIn('"web_browser_retry_timeout_s": 45', text)

    def test_compile_list_has_the_module(self):
        text = self._read('.github', 'workflows', 'ci.yml')
        self.assertIn('gitcurator/core/chrome_tabs.py', text)


if __name__ == '__main__':
    unittest.main()
