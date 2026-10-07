#!/usr/bin/env python3
"""
chrome_tabs.py — v0.54.0, THE FIFTH DOOR: Chrome fetches the pages itself.

The owner's report (this session): "this time the links which I defined
by 'hand' emoji opened their tabs in a new google chrome session, but
app didn't actually wait and use those tabs to gather information to
feed LLM and store them properly and obsidian, as soon as they loaded,
app false-positive success, but actually openning was not the success
criteria, openning is just start for LLM to fetch and gather data and
write their information and store into obsidian. also use chrominum
and selenium or some headless browser, so sites cannto see you as
bot."

The diagnosis — THREE separate tells, all in the owner's own evidence:

    1. THE BATCH SCORECARD FIRED EARLY — "Batch Complete! ✓ All links
       processed cleanly, 0 saved" while the hand links' tabs were
       still loading in the fresh Chrome session (the owner's
       screenshot: the tabs visible behind the celebrating modal).
       OPENING A TAB IS NOT THE SUCCESS — the success is the NOTE IN
       THE VAULT. The GUI (processing_control) now HOLDS the scorecard
       back while pages are being gathered and celebrates only the
       full story (the delivered pages re-processed as real fetches:
       LLM → note → vault) — the v0.54 scorecard-waits law.
    2. THE DEVTOOLS GRIP WAS FRAGILE — the owner's failure modal named
       "[WinError 10053] connection aborted" and "the Chrome session
       died mid-run" for tabs that were STILL OPEN AND LOADED (his
       screenshot shows oldmapsonline fully rendered behind the
       failure modal). A DevTools WebSocket that drops mid-watch (an
       aborted local connection, Chrome recycling the socket) is a
       BLIP, not a verdict; and a session whose LAUNCHER process
       exited (Windows chrome.exe delegates to the browser process
       and exits) is not a dead session while the endpoint still
       answers. v0.54: the socket RECONNECTS (two re-connects per
       tab, the watch resumes with the remaining budget), and
       liveness is the ENDPOINT's answer (``/json/version``), never
       the launcher's poll alone.
    3. THE THROWAWAY PROFILE WAS THE BOT TELL — a brand-new
       ``--user-data-dir`` every delivery is a visitor with no
       cookies, no history, no clearances: exactly the signature the
       bot walls key on (the owner's flightradar24 tab sat on the
       challenge). The owner asked for "chromium and selenium or some
       headless browser so sites cannot see you as bot" — headless
       and Selenium would be MORE visible, not less (headless
       Chrome's fingerprints, chromedriver's ``navigator.webdriver``);
       the app already drives the owner's REAL Chrome — the fix is
       the IDENTITY: a persistent dedicated profile (``<app>/
       chrome-profile``) whose cookies survive every delivery — a
       challenge the owner's Chrome passes once leaves its clearance
       cookie behind for the next run; the automation blink feature
       is disabled (``--disable-blink-features=AutomationControlled``)
       and ``navigator.webdriver`` is patched undefined on every new
       document (the one tell Selenium can never remove, removed
       here). The owner's own running window and profile are STILL
       never touched — the dedicated profile is a second, separate
       Chrome identity the app owns; a session left running by a
       crashed app is ADOPTED (its DevToolsActivePort answers), not
       fought.

The v0.53 law — the door WAITS for the page, and a crash is never a
delivery (kept, unchanged):

The owner's report (the previous session): "it tried to open a chrome
tabs, but crashed and closed, and app shown false positive of success,
in other words it didn't wait for sites to load and gather their data."

The diagnosis. The v0.50 take step judged a tab with two weak tests:
``document.readyState == 'complete'`` and an outerHTML longer than
200 bytes. Both are TRUE on Chrome's OWN failure pages — a tab that
shows "This site can't be reached" (any ERR_* code) is complete with
10 KB of error-page DOM; an "Aw, Snap" crash page passes the same;
a bot-challenge interstitial ("Just a moment…") loads complete and
STAYS — and the DOM read fired the instant readyState flipped, so a
challenge that would resolve (or redirect) two seconds later was
taken as the page. A Chrome that crashed, a site that was down, or
a challenge that hadn't resolved all became "✅ took the page from
your Chrome" — a delivered file, a consumed queue row, a note built
off an error page. A false positive of success, exactly as reported.

The v0.53 law — the door WAITS for the page, and a crash is never a
delivery:

    - the load watch polls ``document.readyState`` AND the tab's own
      ``location.href`` together: a tab that ends on ``about:blank``
      or ``chrome-error://`` never loaded the site, whatever its
      readyState says;
    - a page must stay COMPLETE at ONE http(s) URL for
      ``PAGE_SETTLE_S`` (2.0 s) before the DOM is read — the settle
      restarts on every href change (the challenge→content redirect,
      the late swap), so "waited for the site to load" means the page
      was STILL, not merely finished;
    - the DOM read must pass :func:`page_is_real` — the tab's own
      URL (http/https only, never Chrome's), an ``ERR_*`` code in the
      DOM (locale-independent — Chrome prints the code, not prose),
      the crash phrases, the challenge grammar (title phrases the
      interstitials use + the markers only a challenge page carries);
    - a page that reads as a challenge KEEPS the whole budget — the
      owner's real Chrome may still pass it (that is the door's whole
      point) — and a page that never passes is named honestly
      ("stayed on a bot challenge"), never delivered;
    - a Chrome error page or a crash page is a failure NOW, with the
      page's own name in the error ("the tab showed Chrome's error
      page (ERR_CONNECTION_RESET)"), never a delivered page;
    - the delivery report carries the failures (``failed_links``:
      url + error each) so the GUI's end-of-delivery modal can say
      what the owner asked to be told: "xx number of links didn't
      generate content or wasn't successful or got error xxx" —
      they keep waiting in the retry queue;
    - the consume side (:mod:`gitcurator.core.hand_delivery`)
      re-verifies every page the APP delivered (queue rows stamped
      ``door: 'auto'``): one that fails the same verdict is
      discarded (the app's own file, removed) and the link keeps
      waiting — the owner's Ctrl+S pages stay the owner's verdict.

The original report (v0.50): "before finishing the run and fetch, app
must show a modal, 'xx' number of links didn't generate content or
wasn't successful or got error xxx … want to retry them in real
browser? if user said this, app automatically opens them in a new
session of user's own google chrome and start tabs and fetches the
data that way."

The diagnosis (v0.50). The fourth door (v0.48.0) hands a walled page
back to the OWNER's hand: open in Chrome, Ctrl+S, save into the
folder — three manual moves per link. The owner's ask removes the
hand: when the run ends with links that generated no content, the app
itself opens the owner's REAL Google Chrome in a fresh session,
starts one tab per failed link, waits for each page to load, and
takes the content from the live page — the machine doors never touch
it, the owner's Chrome does the fetching. The delivery still lands
in the SAME folder the fourth door consumes
(``<vault>/_review/hand-delivered/``), so the rest of the pipeline is
unchanged: a delivered page IS a real fetch, the note is written, the
retry row resolves.

The mechanics (no new dependency — pure stdlib, like the whole core):

    1. OFFER — the GUI's end-of-run modal (or the CLI's
       ``--chrome-retry``): the fetch-failed links of THIS run (the
       " - " rows — Status 'unreviewed' in the master table), each
       with its last error. The owner says yes once.
    2. LAUNCH — the app starts the owner's own chrome.exe with
       ``--remote-debugging-port`` and a THROWAWAY user-data-dir (a
       fresh session of the owner's own Chrome — never the owner's
       profile, never a fight with an already-running window), and
       reads the DevTools endpoint off the process's own stderr.
    3. TABS — one tab per link (``/json/new``), in waves so the
       window fills with loading tabs at once, exactly the owner's
       words ("start tabs"); each tab is the REAL Chrome rendering
       the page — its fingerprint, its engine, no impersonation.
    4. TAKE — over each tab's DevTools WebSocket (a ~200-line raw
       socket client: masked client frames, unmasked server frames,
       fragmentation, ping/pong) the app waits for
       ``document.readyState`` to reach 'complete' AT an http(s)
       ``location.href``, holds that state through the settle window
       (a challenge's post-load redirect restarts it), and only then
       reads ``document.documentElement.outerHTML`` — the live DOM,
       after every script and challenge has done its work — and the
       DOM must pass :func:`page_is_real` (a Chrome error page, a
       crash page, a stuck challenge are failures, never pages).
    5. DELIVER — the HTML lands as the link's suggested filename in
       the hand-delivered folder (the queue row was enqueued with the
       'auto' door marker first, so the consume path finds it), the
       tab closes, and when every wave is done the throwaway Chrome
       session is terminated and its temp profile erased.

The law, kept as tight as the other four doors:

    - the owner's Chrome is LAUNCHED, never hijacked (a throwaway
      --user-data-dir means a second, separate Chrome process — the
      owner's running window, profile and cookies are never touched);
    - loopback never opens a browser (the v0.15.1 rule);
    - dry-run NEVER launches (the rehearsed delivery reports what it
      would have fetched and writes nothing);
    - a failed tab is honest ('Chrome would not open a tab', 'the
      page never finished loading', 'the tab showed Chrome's error
      page (ERR_…)', 'the page stayed on a bot challenge', 'the page
      rendered empty') and never raises — the fifth door must never
      break a batch;
    - THE PAGE IS WAITED FOR (v0.53): complete at an http(s) URL,
      still for the settle window, real by the verdict — anything
      less is a named failure, never a delivered page and never a
      false positive of success;
    - delivered pages are OURS (the app wrote them — and the consume
      side re-verifies them, discarding an app-delivered page that
      turns out to be an error page), owner-saved pages stay the
      owner's — the folder's README law is unchanged;
    - the config ``web_browser_retry`` (default ON) opts the modal
      out; ``web_browser_retry_timeout_s`` (45) and
      ``web_browser_retry_wave`` (8) tune the door.

Pure stdlib; the only network is the owner's own Chrome on loopback
(the DevTools endpoint) — no site is ever asked by the machine doors.
No PyQt import (the libEGL-less sandbox rule). No browser ever
launches under test: the WebSocket client, the frame codec, the
target JSON and the delivery seams are all injectable.
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from typing import Callable, Dict, List, Optional
from urllib.parse import quote, urlparse

from gitcurator.core import dryrun
from gitcurator.core.storage import atomic_write_text

# ---------------------------------------------------------------------------
# Configuration (safe to edit)
# ---------------------------------------------------------------------------

#: Config gate — the end-of-run modal + the fifth door itself (default ON).
CONFIG_GATE = 'web_browser_retry'

#: Config knobs — per-page wait (seconds) and tabs per wave.
DEFAULT_PAGE_TIMEOUT_S = 45.0
DEFAULT_WAVE = 8

#: v0.54.0 — the persistent dedicated profile (the anti-bot identity).
#: ON by default: the door's Chrome session uses ONE dedicated profile
#: directory (``<app>/chrome-profile``) whose cookies survive every
#: delivery — the sites see a RETURNING visitor, and a challenge the
#: owner's real Chrome passed once leaves its clearance cookie for the
#: next run (the throwaway profile was a first-visit bot signature:
#: no cookies, no history, a fresh Cloudflare challenge every time).
#: The owner's own running window and default profile are still never
#: touched — this is a second Chrome identity the app owns.
CONFIG_PROFILE_PERSIST = 'web_browser_profile_persist'

#: v0.54.0 — where the dedicated profile lives (optional override;
#: empty = the default ``<app>/chrome-profile`` next to cache.db).
CONFIG_PROFILE_DIR = 'web_browser_profile_dir'

#: How long to wait for Chrome to print its DevTools endpoint (stderr).
CHROME_STARTUP_TIMEOUT_S = 25.0

#: One DevTools message may never exceed this (a runaway page guard).
MAX_MESSAGE_BYTES = 64 * 1024 * 1024

#: Chrome flags for the door's session: the dedicated profile that
#: never fights the owner's running window, first-run noise off, the
#: background-throttling off so occluded tabs still load at full speed,
#: and (v0.54.0) the automation blink feature DISABLED — the one
#: renderer hint a DevTools-driven Chrome still carries
#: (``navigator.webdriver`` stays false without chromedriver, but the
#: blink feature leaks the automation in the fingerprint; off it goes).
_CHROME_FLAGS = (
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-backgrounding-occluded-windows',
    '--disable-renderer-backgrounding',
    '--disable-background-timer-throttling',
    '--disable-blink-features=AutomationControlled',
)

#: The stderr line Chrome prints when the DevTools endpoint is live:
#: "DevTools listening on ws://127.0.0.1:<port>/devtools/browser/<uuid>"
#: (split WITHOUT the ws:// — the endpoint keeps its scheme so the
#: URL parser can read the port; a lesson the tests taught the door.)
_DEVTOOLS_LINE = 'DevTools listening on '

#: v0.53.0 — the settle window: how long a page must stay COMPLETE
#: at ONE http(s) URL before the DOM is read. The settle restarts on
#: every href change, so a challenge's post-load redirect (the very
#: moment the real content takes over) is never mistaken for the
#: finished page. Patchable in tests (the poll cadence keeps the wait
#: honest; a small settle makes each scripted check fast).
PAGE_SETTLE_S = 2.0

#: The load-watch poll cadence (seconds) — also the settle's tick.
_POLL_CADENCE_S = 0.4

#: v0.54.0 — the anti-bot document patch, applied to every tab the
#: door connects to (``Page.enable`` + ``Page.addScriptToEvaluateOnNew
#: Document``): ``navigator.webdriver`` is patched undefined on every
#: new document — the one tell chromedriver can never remove (the
#: owner's Selenium instinct was right, the tell is real — it just
#: isn't set by DevTools; patched anyway, belt and braces: a page that
#: re-navigates after a challenge carries the patched navigator into
#: the content document). A module law, not a config knob; patchable
#: in tests so the scripted-socket scripts stay honest.
_ANTI_BOT_PATCH = True

#: The patch source itself — pure JavaScript, best-effort (a page that
#: refuses it is still fetched; the verdict, not the patch, is the law).
_ANTI_BOT_SCRIPT = (
    "Object.defineProperty(navigator, 'webdriver', "
    "{get: function () { return undefined; }});"
)

#: v0.54.0 — how many times a dropped DevTools socket is re-connected
#: per tab before the failure is named. A socket that drops mid-watch
#: (WinError 10053 — an aborted local connection; a reset; Chrome
#: recycling the DevTools connection) is a BLIP, not a verdict: the tab
#: is still open, the watch resumes with the remaining budget.
_SOCKET_RECONNECTS = 2

#: v0.54.0 — the pause before a re-connect attempt (the endpoint needs
#: a beat to recycle the socket).
_RECONNECT_PAUSE_S = 0.5

#: v0.53.0 — Chrome's own error pages carry an ERR_* code in the DOM
#: (locale-independent: the code, not the prose, is printed for every
#: locale). A page whose DOM shows one is a TAB THAT FAILED, never a
#: delivered page — the exact false positive the owner reported.
_ERR_CODE_RE = re.compile(r'ERR_[A-Z0-9_]{2,}')

#: Chrome's crash phrases (the renderer's own sad-tab pages).
_CRASH_MARKERS = ('aw, snap', "he's dead, jim", 'err_crashed')

#: The bot-challenge grammar, two scopes: the TITLE phrases the
#: interstitials use (a title is a sentence, not prose — safe to
#: match), and the HTML markers only a challenge page carries (the
#: Cloudflare challenge machinery's own script names). A page that
#: reads as a challenge is NOT content: the door keeps waiting (the
#: owner's real Chrome may still pass it) and names it honestly if it
#: never does — but it is never delivered as the site's data.
_CHALLENGE_TITLE_MARKERS = (
    'just a moment', 'checking your browser', 'verify you are human',
    'attention required', 'security check', 'ddos protection',
    'enable javascript and cookies', 'access denied', 'blocked',
)
_CHALLENGE_HTML_MARKERS = (
    'challenge-platform', 'cf-chl', '__cf_chl', 'cf_chl_opt',
    'turnstile', 'cdn-cgi/challenge', 'cf-browser-verification',
)

#: v0.53.0 — the smallest DOM that can be a real page (the floor the
#: v0.50 door already kept; the verdict does the heavy lifting now).
_MIN_REAL_HTML_BYTES = 200


# ---------------------------------------------------------------------------
# Errors (never raised out of this module's public functions)
# ---------------------------------------------------------------------------


class CDPError(Exception):
    """Any DevTools-protocol failure, named honestly for the log."""


class CDPTimeoutError(CDPError):
    """A DevTools call or wait ran out of time."""


class CDPClosedError(CDPError):
    """The DevTools WebSocket closed (Chrome shut down / tab died)."""


# ---------------------------------------------------------------------------
# The WebSocket frame codec (pure functions — the testable heart)
# ---------------------------------------------------------------------------


def encode_client_frame(payload: bytes, opcode: int = 0x1) -> bytes:
    """One client→server WebSocket frame: FIN set, MASKED (the RFC's
    rule for clients). Handles the three length encodings (<126,
    16-bit, 64-bit). Pure — no socket, no randomness beyond the mask."""
    mask = os.urandom(4)
    n = len(payload)
    if n < 126:
        header = bytes((0x80 | opcode, 0x80 | n))
    elif n < 65536:
        header = bytes((0x80 | opcode, 0x80 | 126)) + n.to_bytes(2, 'big')
    else:
        header = bytes((0x80 | opcode, 0x80 | 127)) + n.to_bytes(8, 'big')
    masked = bytes(b ^ mask[i & 3] for i, b in enumerate(payload))
    return header + mask + masked


def decode_frames(data: bytes,
                  max_frame: int = MAX_MESSAGE_BYTES
                  ) -> (List, bytes):
    """Every COMPLETE frame in ``data`` as ``[(fin, opcode, payload),
    ...]`` plus the unparsed remainder (a partial frame — the caller
    feeds it back with the next recv). Server frames are unmasked per
    the RFC, but masked frames are decoded too (so tests can build
    byte streams with :func:`encode_client_frame`). A frame longer
    than ``max_frame`` raises (the runaway-page guard). Pure."""
    frames: List[tuple] = []
    i, end = 0, len(data)
    while True:
        if end - i < 2:
            break
        b1, b2 = data[i], data[i + 1]
        fin = bool(b1 & 0x80)
        opcode = b1 & 0x0F
        masked = bool(b2 & 0x80)
        ln = b2 & 0x7F
        j = i + 2
        if ln == 126:
            if end - j < 2:
                break
            ln = int.from_bytes(data[j:j + 2], 'big')
            j += 2
        elif ln == 127:
            if end - j < 8:
                break
            ln = int.from_bytes(data[j:j + 8], 'big')
            j += 8
        if ln > max_frame:
            raise CDPError(f'a DevTools frame claimed {ln} bytes '
                           f'(guard: {max_frame}) — runaway page?')
        if masked:
            if end - j < 4:
                break
            mask = data[j:j + 4]
            j += 4
        if end - j < ln:
            break
        payload = data[j:j + ln]
        if masked:
            payload = bytes(b ^ mask[k & 3]
                            for k, b in enumerate(payload))
        frames.append((fin, opcode, payload))
        i = j + ln
    return frames, data[i:]


# ---------------------------------------------------------------------------
# The DevTools WebSocket client (~the smallest honest CDP client)
# ---------------------------------------------------------------------------


class CDPSocket:
    """One DevTools WebSocket over a raw socket: the RFC 6455
    handshake, masked client text frames, server frames with
    fragmentation / ping / pong / close. ``sock`` may be injected (a
    pre-handshaked fake — the tests never open a socket)."""

    def __init__(self, host: str, port: int, path: str,
                 sock: Optional[socket.socket] = None,
                 timeout: float = 10.0):
        self._buf = b''
        self._frag_opcode = -1
        self._frag_parts: List[bytes] = []
        self._msgid = 0
        self._timeout = float(timeout)
        if sock is not None:
            # an injected socket (the tests) — it answers the
            # handshake from its script, so the real handshake runs too
            self.sock = sock
            self._handshake(host, int(port), path)
        else:
            self.sock = socket.create_connection((host, int(port)),
                                                 timeout=self._timeout)
            self._handshake(host, int(port), path)

    # -- the RFC 6455 opening handshake ----------------------------------

    def _handshake(self, host: str, port: int, path: str) -> None:
        key = base64.b64encode(os.urandom(16)).decode('ascii')
        req = (f"GET {path} HTTP/1.1\r\n"
               f"Host: {host}:{port}\r\n"
               f"Upgrade: websocket\r\n"
               f"Connection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\n"
               f"Sec-WebSocket-Version: 13\r\n\r\n")
        self.sock.settimeout(self._timeout)
        self.sock.sendall(req.encode('ascii'))
        data = b''
        while b'\r\n\r\n' not in data:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise CDPClosedError('the socket closed during the '
                                     'WebSocket handshake')
            data += chunk
            if len(data) > 65536:
                raise CDPError('the handshake response never ended')
        head, _, rest = data.partition(b'\r\n\r\n')
        status = head.split(b'\r\n', 1)[0]
        if b'101' not in status:
            raise CDPError(f'Chrome refused the WebSocket upgrade: '
                           f'{status[:120]!r}')
        self._buf = rest

    # -- frame I/O ---------------------------------------------------------

    def send_text(self, text: str) -> None:
        self.sock.settimeout(self._timeout)
        self.sock.sendall(encode_client_frame(text.encode('utf-8'), 0x1))

    def _send_pong(self, payload: bytes) -> None:
        try:
            self.sock.sendall(encode_client_frame(payload, 0xA))
        except Exception:
            pass  # a pong that cannot be sent: the next call tells

    def recv_message(self) -> str:
        """One complete text message (fragmentation reassembled; pings
        answered, pongs and events skipped; close raises). Raises
        ``socket.timeout`` on deadline (the caller names it honestly)
        and ``CDPClosedError`` when the tab is gone."""
        while True:
            frames, self._buf = decode_frames(self._buf)
            for fin, opcode, payload in frames:
                if opcode == 0x9:            # ping -> answer, keep reading
                    self._send_pong(payload)
                    continue
                if opcode == 0xA:            # pong -> ignore
                    continue
                if opcode == 0x8:            # close -> the tab is gone
                    raise CDPClosedError('the DevTools WebSocket closed')
                if opcode == 0x0:            # continuation
                    if self._frag_opcode < 0:
                        continue
                    self._frag_parts.append(payload)
                    if fin:
                        msg = b''.join(self._frag_parts)
                        op = self._frag_opcode
                        self._frag_opcode = -1
                        self._frag_parts = []
                        if op in (0x1, 0x2):
                            return msg.decode('utf-8', errors='replace')
                    continue
                # a fresh data frame (text=1 / binary=2)
                if fin:
                    return payload.decode('utf-8', errors='replace')
                self._frag_opcode = opcode
                self._frag_parts = [payload]
            # buffer exhausted without a complete message: recv more
            self.sock.settimeout(self._timeout)
            chunk = self.sock.recv(65536)
            if not chunk:
                raise CDPClosedError('the DevTools WebSocket closed')
            self._buf += chunk

    # -- the CDP command layer ----------------------------------------------

    def call(self, method: str, params: Optional[Dict] = None,
             timeout: Optional[float] = None) -> Dict:
        """One DevTools command → its ``result`` dict. Events that
        arrive mid-wait are skipped; errors raise ``CDPError``; a
        deadline passes as ``CDPTimeoutError``."""
        self._msgid += 1
        msg: Dict = {'id': self._msgid, 'method': method}
        if params:
            msg['params'] = params
        timeout = float(timeout if timeout is not None else self._timeout)
        deadline = time.monotonic() + timeout
        self.sock.settimeout(timeout)
        self.send_text(json.dumps(msg, ensure_ascii=False))
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CDPTimeoutError(f'{method} timed out after '
                                      f'{timeout:.0f}s')
            self.sock.settimeout(remaining)
            try:
                text = self.recv_message()
            except socket.timeout:
                raise CDPTimeoutError(f'{method} timed out after '
                                      f'{timeout:.0f}s')
            try:
                data = json.loads(text)
            except Exception:
                continue  # a non-JSON frame — skip, keep waiting
            if data.get('id') == self._msgid:
                if 'error' in data:
                    raise CDPError(f'{method}: '
                                   f"{(data['error'] or {}).get('message')}")
                return data.get('result') or {}
            # an event or a stale id — ignored

    def evaluate(self, expression: str,
                 timeout: Optional[float] = None) -> str:
        """``Runtime.evaluate`` with ``returnByValue`` → the string
        value ('' when the page has none). The DOM read the fifth
        door is built on."""
        res = self.call('Runtime.evaluate',
                        {'expression': expression, 'returnByValue': True,
                         'awaitPromise': False}, timeout=timeout)
        inner = (res.get('result') or {})
        return str(inner.get('value') or '')

    def close(self) -> None:
        """A polite close frame, then the socket — never raises."""
        try:
            self.sock.settimeout(1.0)
            self.sock.sendall(encode_client_frame(b'', 0x8))
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# The Chrome process (the owner's own binary, the door's own identity)
# ---------------------------------------------------------------------------


def is_loopback_url(url: str) -> bool:
    """True for localhost/127.0.0.1/::1/.local hosts — the v0.15.1 law:
    no door ever escalates off-machine."""
    try:
        host = (urlparse(url or '').hostname or '').lower()
    except Exception:
        return False
    return host in ('localhost', '127.0.0.1', '::1') \
        or (host or '').endswith('.local')


def _pick_free_port() -> int:
    """An unused TCP port on loopback (bind(0), read it, release)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return int(s.getsockname()[1])


def default_profile_dir() -> str:
    """v0.54.0 — where the door's dedicated Chrome identity lives:
    ``<APP_DIR>/chrome-profile`` (the app's own folder, next to
    cache.db — the profile travels with the app, the cookies persist
    between deliveries, and the owner's own profile is never touched).
    Pure-stdlib import; any failure falls back next to this module."""
    try:
        from gitcurator.constants import APP_DIR
        return os.path.join(APP_DIR, 'chrome-profile')
    except Exception:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'chrome-profile')


def _apply_anti_bot(ws: 'CDPSocket') -> None:
    """v0.54.0 — the de-automation patch on one tab's socket:
    ``Page.enable`` + ``Page.addScriptToEvaluateOnNewDocument`` with
    the ``navigator.webdriver`` erasure. Best-effort by law (a page
    that refuses the patch is still fetched — the verdict, not the
    patch, is the door's law); skipped when ``_ANTI_BOT_PATCH`` is
    off (the scripted-socket tests keep their honest scripts)."""
    if not _ANTI_BOT_PATCH:
        return
    try:
        ws.call('Page.enable', timeout=5.0)
        ws.call('Page.addScriptToEvaluateOnNewDocument',
                {'source': _ANTI_BOT_SCRIPT}, timeout=5.0)
    except CDPError:
        pass  # a page that refuses the patch is still a fetchable page


#: The container/hardened-OS fallback: a Chromium that cannot use its
#: SUID sandbox (containers, some hardened Linux boxes) exits before
#: the DevTools endpoint — the session relaunches ONCE with these.
_CONTAINER_FLAGS = (
    '--no-sandbox',
    '--disable-gpu',
    '--disable-dev-shm-usage',
)
#: The last resort: a display-less machine (a server, a container
#: without X) gets the SAME real Chromium headless — the tabs are
#: not visible, but the engine, the fingerprint and the DOM are
#: identical, and the door still opens. The owner's desktop sees a
#: window; this tier exists so the door never fails outright.
_HEADLESS_FLAGS = ('--headless=new',)


def _probe_version_endpoint(port: int, timeout: float = 3.0) -> bool:
    """v0.54.0 — the one honest liveness test: does the DevTools HTTP
    endpoint on ``port`` answer ``GET /json/version`` with 200? A
    port file can be stale, a launcher process can exit after
    handing off (Windows chrome.exe delegates) — an ANSWER cannot
    lie. Pure stdlib, loopback only."""
    try:
        conn = http.client.HTTPConnection('127.0.0.1', int(port),
                                          timeout=timeout)
        try:
            conn.request('GET', '/json/version')
            resp = conn.getresponse()
            resp.read()
            return resp.status == 200
        finally:
            conn.close()
    except Exception:
        return False


class ChromeSession:
    """The owner's real Chrome as a fetch engine: launched with a
    throwaway ``--user-data-dir`` (a FRESH session of the owner's own
    binary — their running window and profile are never touched) and
    ``--remote-debugging-port``; the DevTools endpoint is read off
    the process's own stderr, so port collisions are impossible. A
    Chromium that dies before its endpoint climbs ONE honest ladder:
    the container flags (no SUID sandbox), then headless (a machine
    with no display at all). ``exe`` may be injected (tests pass a
    script that prints the endpoint line). Termination is the
    caller's ``close()``.

    v0.54.0 — the door's own IDENTITY: by default (``persist=True``)
    the session uses a PERSISTENT dedicated profile directory (the
    caller's ``profile_dir``, or ``<app>/chrome-profile``) whose
    cookies survive every delivery — the anti-bot law (a returning
    visitor, a challenge passed once stays passed). A Chrome still
    running with that profile (a crashed app's leftover, or a
    still-loading previous delivery) is ADOPTED through its
    ``DevToolsActivePort`` file — never fought, never duplicated;
    the owner's own running window and profile are untouched either
    way. ``persist=False`` is the old throwaway law (a temp profile,
    erased on close) — the explicit opt-out. """

    def __init__(self, exe: str, log: Optional[Callable] = None,
                 page_timeout_s: float = DEFAULT_PAGE_TIMEOUT_S,
                 profile_dir: Optional[str] = None,
                 persist: bool = True):
        log = log or (lambda *a, **k: None)
        self._log = log
        self._page_timeout_s = float(page_timeout_s)
        self.proc: Optional[subprocess.Popen] = None
        self._stderr_thread: Optional[threading.Thread] = None
        self.profile = ''
        self.port = 0
        self._browser_ws_path = ''      # for the graceful Browser.close
        self._profile_is_throwaway = not bool(persist)
        if persist:
            self.profile = os.path.abspath(
                (profile_dir or '').strip() or default_profile_dir())
            try:
                os.makedirs(self.profile, exist_ok=True)
            except Exception as e:
                log(f"🤖 The dedicated Chrome profile could not be created "
                    f"({e}) — falling back to a throwaway session (the "
                    f"cookies will not persist this time)", "warning")
                self.profile = ''
                self._profile_is_throwaway = True
            else:
                if self._try_adopt():
                    log(f"🤖 Adopted the still-running Chrome session of "
                        f"the dedicated profile (its cookies stay warm; "
                        f"DevTools on 127.0.0.1:{self.port}) — no new "
                        f"window was opened", "info")
                    return
        attempts = (list(_CHROME_FLAGS),
                    list(_CHROME_FLAGS) + list(_CONTAINER_FLAGS),
                    list(_CHROME_FLAGS) + list(_CONTAINER_FLAGS)
                    + list(_HEADLESS_FLAGS))
        last_err: Optional[Exception] = None
        for attempt, extra_flags in enumerate(attempts):
            try:
                self._launch_and_announce(exe, extra_flags)
                break
            except CDPError as e:
                last_err = e
                self.close()   # this attempt's process + throwaway profile
                if attempt == 0:
                    log("🤖 Chrome died before its DevTools endpoint — "
                        "retrying with the container flags "
                        "(--no-sandbox --disable-gpu — the SUID-sandbox "
                        "fallback for containers and hardened boxes)",
                        "warning")
                elif attempt == 1:
                    log("🤖 Chrome still cannot open its window (no "
                        "display?) — last resort: the same real Chrome "
                        "HEADLESS (the tabs are not visible, but the "
                        "engine and the live DOM are identical)",
                        "warning")
        else:
            raise last_err or CDPError(
                'Chrome never announced its DevTools endpoint')
        self._drain_stderr()
        if self._profile_is_throwaway:
            log(f"🤖 Chrome session live (your own Chrome, a fresh "
                f"throwaway profile — your running window is untouched; "
                f"DevTools on 127.0.0.1:{self.port})", "info")
        else:
            log(f"🤖 Chrome session live (your own Chrome, the door's "
                f"DEDICATED profile — a returning visitor the sites "
                f"remember, your own window and profile untouched; "
                f"DevTools on 127.0.0.1:{self.port})", "info")

    # -- v0.54.0 — the adoption: a Chrome already holding the profile --

    def _try_adopt(self) -> bool:
        """A Chrome still running with the dedicated profile is the
        door's own leftover (a crashed app, a still-loading delivery) —
        Chrome writes ``DevToolsActivePort`` into the user-data-dir the
        moment its endpoint is live (first line: the port; second: the
        browser WebSocket path). Adoption succeeds only when the port
        ANSWERS ``/json/version`` — a stale file from a dead Chrome
        never lies its way in. On success ``self.port`` and the
        browser WebSocket path are set and no process is launched;
        ``alive()`` becomes the endpoint's answer and ``close()`` the
        graceful ``Browser.close``."""
        port_file = os.path.join(self.profile, 'DevToolsActivePort')
        try:
            with open(port_file, 'r', encoding='utf-8',
                      errors='replace') as fh:
                lines = [l.strip() for l in fh.read().splitlines()
                         if l.strip()]
            port = int(lines[0])
            ws_path = lines[1] if len(lines) > 1 else ''
        except Exception:
            return False
        if not _probe_version_endpoint(port):
            return False
        self.port = port
        self._browser_ws_path = ws_path or self._lookup_browser_ws() or ''
        return True

    def _lookup_browser_ws(self) -> str:
        """``/json/version`` → the browser's ``webSocketDebuggerUrl``
        path (for the graceful close when the announcement was not
        parsed). Best-effort; '' on any miss."""
        try:
            status, body = self._http('GET', '/json/version', timeout=3.0)
            if status != 200:
                return ''
            ws = str((json.loads(body.decode('utf-8', errors='replace'))
                      or {}).get('webSocketDebuggerUrl') or '')
            return urlparse(ws).path or ''
        except Exception:
            return ''

    def _launch_and_announce(self, exe: str, extra_flags: List[str]
                             ) -> None:
        """One launch attempt: start the process (the dedicated profile
        when persist is on, else a fresh throwaway temp dir; its own
        process group on POSIX), then read the DevTools endpoint off
        its stderr. Raises ``CDPError`` on any miss — the constructor
        decides whether to retry."""
        self.port = _pick_free_port()
        self._browser_ws_path = ''
        if not self.profile:
            self.profile = tempfile.mkdtemp(prefix='gitcurator-chrome-')
        else:
            os.makedirs(self.profile, exist_ok=True)
        self._stderr_thread = None
        cmd = [exe, f'--remote-debugging-port={self.port}',
               f'--user-data-dir={self.profile}'] \
            + list(extra_flags) + ['about:blank']
        try:
            self.proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL, close_fds=True,
                # v0.50.0 — the throwaway session gets its OWN process
                # group on POSIX, so close() can kill the whole tree
                # (Chrome's renderers; a test's shell child) in one
                # signal — never the app's own group.
                **({'start_new_session': True} if os.name == 'posix'
                   else {}))
        except Exception as e:
            raise CDPError(f'Chrome could not be launched ({e})')
        # Chrome announces "DevTools listening on ws://127.0.0.1:<port>/…"
        # on stderr — read it here; the drain thread takes over after.
        deadline = time.monotonic() + CHROME_STARTUP_TIMEOUT_S
        endpoint = ''
        while time.monotonic() < deadline:
            line = self.proc.stderr.readline()
            if not line:
                if self.proc.poll() is not None:
                    raise CDPError('Chrome exited before its DevTools '
                                   'endpoint came up')
                time.sleep(0.05)  # EOF on a pipe the OS has not
                continue          # reaped yet — never a busy spin
            text = line.decode('utf-8', errors='replace')
            if _DEVTOOLS_LINE in text:
                endpoint = text.split(_DEVTOOLS_LINE, 1)[1].strip()
                break
        if not endpoint:
            raise CDPError('Chrome never announced its DevTools endpoint')
        try:
            ws_url = endpoint.split()[0]
            parsed = urlparse(ws_url)
            self.port = int(parsed.port or self.port)
            # v0.54.0 — the browser's own WebSocket path rides the same
            # announcement (…/devtools/browser/<uuid>): the graceful
            # Browser.close needs it, and the adopt path reads the same
            # pair from Chrome's DevToolsActivePort file.
            self._browser_ws_path = parsed.path or ''
        except Exception:
            pass  # keep the picked port — the announcement had no ws URL

    def _drain_stderr(self) -> None:
        """Own the stderr pipe for the session's life: keep reading
        so a full pipe can never block the browser, and CLOSE it on
        EOF (the pipe is this thread's — close() never touches it:
        closing a file another thread is blocked reading deadlocks,
        the lesson the fake-exe test taught the door)."""
        proc = self.proc
        if proc is None:
            return

        def _drain():
            try:
                while True:
                    if not proc.stderr.readline():
                        return
            except Exception:
                pass
            finally:
                try:
                    proc.stderr.close()
                except Exception:
                    pass
        self._stderr_thread = threading.Thread(
            target=_drain, daemon=True, name='gitcurator-chrome-stderr')
        self._stderr_thread.start()

    # -- the DevTools HTTP endpoints --------------------------------------

    def _http(self, method: str, path: str,
              timeout: float = 15.0) -> (int, bytes):
        conn = http.client.HTTPConnection('127.0.0.1', self.port,
                                          timeout=timeout)
        try:
            conn.request(method, path)
            resp = conn.getresponse()
            return resp.status, resp.read()
        finally:
            conn.close()

    def new_tab(self, url: str) -> Dict:
        """Open one tab at ``url`` (PUT — the Chrome 111+ law — with a
        GET fallback for older builds). Returns the target dict (its
        ``id`` and ``webSocketDebuggerUrl``)."""
        q = quote(url, safe='')
        last = 'no HTTP answer'
        for method in ('PUT', 'GET'):
            try:
                status, body = self._http(method, f'/json/new?{q}')
            except Exception as e:
                last = str(e)
                continue
            if status == 200:
                try:
                    return json.loads(body.decode('utf-8', errors='replace'))
                except Exception as e:
                    raise CDPError(f'the new-tab answer was unreadable: {e}')
            last = f'HTTP {status}'
        raise CDPError(f'Chrome would not open a tab ({last})')

    def close_tab(self, target_id: str) -> None:
        """Best-effort tab close (HTTP first; a miss is cosmetic — the
        whole throwaway session is terminated at the end anyway)."""
        for method in ('GET', 'PUT'):
            try:
                status, _ = self._http(method, f'/json/close/{target_id}',
                                       timeout=8.0)
                if status in (200, 204):
                    return
            except Exception:
                continue

    def alive(self) -> bool:
        """v0.54.0 — the honest liveness: the launcher process alive
        OR the DevTools endpoint ANSWERING. The launcher exiting is
        NOT session death — on Windows chrome.exe delegates to the
        browser process and exits with the endpoint perfectly live
        (the v0.53 'the Chrome session died mid-run' false failure was
        exactly that, while the owner's tabs were still loading);
        an adopted session (no proc at all) lives exactly as long as
        its endpoint answers."""
        if self.proc is not None and self.proc.poll() is None:
            return True
        if self.port:
            return _probe_version_endpoint(self.port)
        return False

    def close(self) -> None:
        """Shut the door's Chrome down and keep its identity. v0.54.0:
        the GRACEFUL way first — ``Browser.close`` over the browser's
        own WebSocket lets Chrome flush its cookie store (the whole
        value of the persistent profile: a clearance cookie must
        survive the delivery), then any survivor is tree-killed (the
        whole process GROUP on POSIX; ``taskkill /T`` on Windows — a
        plain terminate() kills only the launcher and leaves the
        browser as a zombie holding the profile, the v0.50 note's
        'zombie Chrome'). The throwaway profile (persist off) is
        erased; the DEDICATED profile is NEVER erased. Never raises;
        safe to call twice. The stderr pipe is the drain thread's to
        close (see _drain_stderr) — except when the session died
        before the drain ever started, where close() owns it."""
        self._graceful_browser_close()
        proc, self.proc = self.proc, None
        if proc is not None and proc.poll() is None:
            self._signal_tree(proc)
        if proc is not None and self._stderr_thread is None:
            # the launch failed before the drain thread existed — the
            # pipe is ours to close (nothing else ever read it)
            try:
                proc.stderr.close()
            except Exception:
                pass
        self._cleanup()

    def _graceful_browser_close(self) -> None:
        """v0.54.0 — ``Browser.close`` over the browser's own WebSocket:
        Chrome shuts down CLEANLY (cookies flushed — the persistent
        profile's whole value) instead of being killed mid-write.
        Best-effort by law: any miss (no ws path, a refused socket, a
        timeout) falls through to the tree kill. An ~8s wait for the
        process to exit after the command."""
        ws_path = self._browser_ws_path or ''
        if not ws_path and self.port:
            ws_path = self._lookup_browser_ws()
        if not (ws_path and self.port):
            return
        try:
            ws = CDPSocket('127.0.0.1', self.port, ws_path, timeout=5.0)
        except Exception:
            return
        try:
            ws.call('Browser.close', timeout=5.0)
        except Exception:
            pass  # the tree kill is the fallback
        finally:
            try:
                ws.close()
            except Exception:
                pass
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.wait(timeout=8.0)
            except Exception:
                pass  # still alive — the tree kill takes it

    def _signal_tree(self, proc: subprocess.Popen) -> None:
        import signal as _signal
        if os.name == 'nt':
            # v0.54.0 — Windows: terminate() kills only the LAUNCHER;
            # Chrome's browser + renderers survive as zombies, and a
            # zombie holding the DEDICATED profile would break the
            # next launch (chrome.exe delegates to it and never
            # announces). taskkill /T kills the tree — the law.
            for args in (['taskkill', '/PID', str(proc.pid), '/T'],
                         ['taskkill', '/PID', str(proc.pid), '/T', '/F']):
                try:
                    subprocess.run(args, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, timeout=10)
                    try:
                        proc.wait(timeout=5)
                        return
                    except Exception:
                        continue
                except Exception:
                    continue
            try:
                proc.wait(timeout=3)
            except Exception:
                pass  # a zombie Chrome — the endpoint probe tells the truth
            return
        for sig in ('SIGTERM', 'SIGKILL'):
            try:
                try:
                    os.killpg(os.getpgid(proc.pid),
                              getattr(_signal, sig))
                except Exception:
                    getattr(proc, 'terminate' if sig == 'SIGTERM'
                            else 'kill')()
                try:
                    proc.wait(timeout=5)
                    return
                except Exception:
                    continue  # escalate
            except Exception:
                continue
        try:
            proc.wait(timeout=3)
        except Exception:
            pass  # a zombie Chrome — the endpoint probe tells the truth

    def _cleanup(self) -> None:
        """v0.54.0 — a throwaway profile is erased (the persist-off
        law); the DEDICATED profile is NEVER erased — its cookies are
        the door's identity, the whole anti-bot point."""
        profile, self.profile = self.profile, ''
        if self._profile_is_throwaway and profile \
                and os.path.isdir(profile):
            shutil.rmtree(profile, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


# ---------------------------------------------------------------------------
# The fetch driver — one tab per failed link, content from the live DOM
# ---------------------------------------------------------------------------


def fetch_pages_via_chrome(urls: List[str],
                           log: Optional[Callable] = None,
                           chrome_exe: Optional[str] = None,
                           page_timeout_s: float = DEFAULT_PAGE_TIMEOUT_S,
                           wave: int = DEFAULT_WAVE,
                           should_continue: Optional[Callable] = None,
                           _session_factory: Optional[Callable] = None,
                           profile_dir: Optional[str] = None,
                           persist: bool = True
                           ) -> List[Dict]:
    """The fifth door's engine: every URL fetched through the owner's
    REAL Chrome (the door's dedicated persistent identity by default
    — the anti-bot law; one tab per link, in waves so tabs load
    together), the content read from the live DOM. Returns one
    ``{'url', 'ok', 'html', 'title', 'error'}`` per URL — never
    raises (no Chrome found, a refused launch and every per-tab miss
    are honest per-URL failures). Dry-run never reaches here (the
    delivery layer guards it), and ``_session_factory`` lets the
    tests drive a scripted session without a browser."""
    log = log or (lambda *a, **k: None)
    results: List[Dict] = []
    work: List[str] = []
    for u in urls or []:
        u = (u or '').strip()
        if not u.lower().startswith(('http://', 'https://')):
            results.append({'url': u, 'ok': False, 'html': '', 'title': '',
                            'error': 'not an http(s) link'})
            continue
        if is_loopback_url(u):
            results.append({'url': u, 'ok': False, 'html': '', 'title': '',
                            'error': 'loopback URL — no browser opens '
                                     '(the v0.15.1 rule)'})
            continue
        work.append(u)
    if not work:
        return results
    if dryrun.is_enabled():  # belt and braces — the delivery layer guards
        for u in work:
            results.append({'url': u, 'ok': False, 'html': '', 'title': '',
                            'error': 'dry-run — Chrome is never launched'})
        return results
    chrome = chrome_exe
    if not chrome:
        from gitcurator.core import hand_delivery as _hd
        chrome = _hd.find_chrome()
    if not chrome:
        for u in work:
            results.append({'url': u, 'ok': False, 'html': '', 'title': '',
                            'error': 'Google Chrome was not found on this '
                                     'machine (the fifth door needs the '
                                     'real browser)'})
        log("🤖 Fifth door: no Chrome found — nothing was auto-fetched",
            "warning")
        return results
    wave = max(1, int(wave or DEFAULT_WAVE))
    session = None
    try:
        if _session_factory is not None:
            session = _session_factory(chrome, log)
        else:
            session = ChromeSession(chrome, log,
                                    page_timeout_s=page_timeout_s,
                                    profile_dir=profile_dir,
                                    persist=persist)
    except Exception as e:
        for u in work:
            results.append({'url': u, 'ok': False, 'html': '', 'title': '',
                            'error': str(e)})
        log(f"🤖 Fifth door could not open a Chrome session: {e}",
            "warning")
        return results
    try:
        pending = list(work)
        while pending:
            if should_continue is not None and not should_continue():
                log("⏹️ Chrome tab retry stopped — the remaining links "
                    "keep waiting in the retry queue", "warning")
                for u in pending:
                    results.append({'url': u, 'ok': False, 'html': '',
                                    'title': '',
                                    'error': 'stopped by the user'})
                break
            batch = pending[:wave]
            pending = pending[wave:]
            log(f"🤖 Chrome wave: opening {len(batch)} tab(s) in your "
                f"real Chrome…", "info")
            # open every tab of the wave first (parallel loading), then
            # take each page in turn
            opened: List[tuple] = []
            for u in batch:
                try:
                    tabs_info = session.new_tab(u)
                except Exception as e:
                    results.append({'url': u, 'ok': False, 'html': '',
                                    'title': '', 'error': str(e)})
                    continue
                opened.append((u, tabs_info))
                log(f"🤖 Tab opened: {u}", "info")
            for u, tabs_info in opened:
                if not session.alive():
                    results.append({'url': u, 'ok': False, 'html': '',
                                    'title': '',
                                    'error': 'the Chrome session died '
                                             'mid-run (its DevTools '
                                             'endpoint stopped answering)'
                                             })
                    continue
                # fetch this page through its already-open tab
                page = _fetch_opened_tab(session, u, tabs_info,
                                         page_timeout_s, log)
                results.append(page)
                if page['ok']:
                    log(f"✅ {u}: took the page from your Chrome "
                        f"(\"{page['title'][:60]}\", "
                        f"{len(page['html']) // 1024} KB of live DOM)",
                        "success")
                else:
                    log(f"⚠️ {u}: {page['error']}", "warning")
    finally:
        session.close()
    return results


# ---------------------------------------------------------------------------
# v0.53.0 — the page verdict: is this tab the SITE, or Chrome's own
# answer to a failure? (pure — the testable heart of the fix)
# ---------------------------------------------------------------------------


def page_is_real(html: str, href: str, title: str = '') -> (bool, str):
    """True when the tab is showing the SITE's page. The four laws, in
    order (the strongest sentence wins): the tab's own URL must be
    http(s) — a tab that ended on ``chrome-error://`` or ``about:`` is
    Chrome's page, not the site's; the DOM must not carry an ``ERR_``
    code (Chrome prints the code, not prose, in every locale — a
    network-error page's own signature); it must not read as a crash
    page ('Aw, Snap'); it must not read as a bot challenge (the title
    phrases the interstitials use + the markers only a challenge page
    carries). Returns ``(ok, reason)`` — ``reason`` is the honest
    sentence the log, the report and the modal all carry; '' when ok.
    Pure — no socket, no browser."""
    href = (href or '').strip()
    html = html or ''
    lowered = html.lower()
    t = (title or '').strip().lower()
    try:
        proto = (urlparse(href).scheme or '').lower()
    except Exception:
        proto = ''
    if proto not in ('http', 'https'):
        shown = href or 'an empty URL'
        return False, (f"the tab ended on {shown!r} — Chrome's own "
                       f"page, not the site's")
    m = _ERR_CODE_RE.search(html)
    if m:
        return False, (f"the tab showed Chrome's error page "
                       f"({m.group(0)}) — the site never loaded")
    for marker in _CRASH_MARKERS:
        if marker in lowered or marker in t:
            return False, ("the tab crashed ('Aw, Snap' — the renderer "
                           "died)")
    for marker in _CHALLENGE_TITLE_MARKERS:
        if marker in t:
            return False, (f"the page stayed on a bot challenge "
                           f"(title: {marker!r})")
    for marker in _CHALLENGE_HTML_MARKERS:
        if marker in lowered:
            return False, (f"the page stayed on a bot challenge "
                           f"({marker})")
    return True, ''


def _is_site_url(href: str) -> bool:
    """True for an http(s) URL with a host — the load watch's own
    cheap check (a tab on ``about:blank`` or ``chrome-error://`` is
    still LOADING as far as the door is concerned)."""
    try:
        u = urlparse(href or '')
        return (u.scheme or '').lower() in ('http', 'https') \
            and bool(u.netloc)
    except Exception:
        return False


def _split_probe(probe: str) -> (str, str):
    """``'complete|https://…'`` → ``('complete', 'https://…')`` — the
    load watch reads readyState and the tab's URL in ONE DevTools
    call (one round trip, one atomic observation)."""
    if '|' in (probe or ''):
        state, href = probe.split('|', 1)
        return state.strip(), href.strip()
    return (probe or '').strip(), ''


def _read_dom(ws: 'CDPSocket') -> (str, str):
    """The live DOM + the title, with the one context-swap retry the
    v0.50 door learned (a navigation landing exactly on the final read
    gets a second chance). Returns ('', '') when both reads fail —
    the caller's verdict names the page honestly."""
    html = ''
    for retry in (0, 1):
        try:
            html = ws.evaluate('document.documentElement.outerHTML',
                               timeout=10.0)
            break
        except CDPError:
            if retry:
                break
            time.sleep(1.0)
    title = ''
    try:
        title = ws.evaluate('document.title', timeout=5.0)
    except CDPError:
        pass  # a titleless page is still a delivered page
    return html or '', title or ''


def _read_and_verdict(ws: 'CDPSocket', href: str) -> Dict:
    """One DOM read judged by :func:`page_is_real` — the take step's
    own gate. Returns ``{'ok', 'html', 'title', 'href', 'error'}``;
    ``error`` carries the verdict's honest sentence on failure."""
    html, title = _read_dom(ws)
    ok, reason = page_is_real(html, href, title)
    if not ok:
        return {'ok': False, 'html': '', 'title': '', 'href': href,
                'error': reason}
    if not html or len(html.strip()) < _MIN_REAL_HTML_BYTES:
        return {'ok': False, 'html': '', 'title': title, 'href': href,
                'error': 'the page rendered empty'}
    return {'ok': True, 'html': html, 'title': title, 'href': href,
            'error': ''}


def _take_live_page(ws: 'CDPSocket', page_timeout_s: float) -> Dict:
    """v0.53.0 — the load watch. The door WAITS for the page: the poll
    observes ``document.readyState`` and the tab's ``location.href``
    together (one DevTools call); a page counts as loaded only when it
    is 'complete' AT an http(s) URL AND has stayed there for
    ``PAGE_SETTLE_S`` (every href change or readyState drop restarts
    the settle — the challenge→content redirect, the late swap); only
    then is the DOM read — and the DOM must pass
    :func:`page_is_real`. A page that reads as a CHALLENGE keeps the
    whole remaining budget (the owner's real Chrome may still pass it
    — that is the door's whole point); a page that reads as a Chrome
    error page or a crash is a named failure immediately. At the
    deadline the last observation is told honestly. Returns
    ``{'ok', 'html', 'title', 'href', 'error'}``; raises only
    ``CDPClosedError`` (the tab is GONE — the caller names it)."""
    deadline = time.monotonic() + float(page_timeout_s)
    probe = ''
    complete_at = None      # when (complete + site URL) first held
    settled_href = ''
    last_reason = ''        # the last challenge verdict (told at deadline)
    while time.monotonic() < deadline:
        try:
            probe = ws.evaluate(
                'document.readyState + "|" + location.href',
                timeout=5.0)
        except CDPTimeoutError:
            pass            # a busy page — the last observation stands
        except CDPClosedError:
            raise           # the tab is GONE — fail fast
        except CDPError:
            probe = ''      # mid-navigation context swap — loading
        state, href = _split_probe(probe)
        if state == 'complete' and _is_site_url(href):
            if complete_at is None or href != settled_href:
                complete_at = time.monotonic()
                settled_href = href
            if time.monotonic() - complete_at >= PAGE_SETTLE_S:
                # the page has been STILL — read it and judge it
                page = _read_and_verdict(ws, settled_href)
                if page['ok'] or 'challenge' not in page['error']:
                    return page
                # a challenge: the owner's Chrome may still pass it —
                # restart the settle and keep the remaining budget
                last_reason = page['error']
                complete_at = None
                settled_href = ''
                probe = ''
        else:
            complete_at = None
            settled_href = ''
        time.sleep(_POLL_CADENCE_S)
    # the budget ran out — tell what the tab was LAST showing
    if last_reason and 'challenge' in last_reason:
        return {'ok': False, 'html': '', 'title': '', 'href': '',
                'error': last_reason.replace(
                    'stayed on a bot challenge',
                    'never got past the bot challenge')}
    state, href = _split_probe(probe)
    if state != 'complete':
        return {'ok': False, 'html': '', 'title': '', 'href': href,
                'error': f'the page never finished loading in '
                         f'{float(page_timeout_s):.0f}s'}
    page = _read_and_verdict(ws, href or settled_href)
    if page['ok']:
        return page     # a real page that settled but missed its window
    return page


def _socket_failure_sentence(e: Exception) -> str:
    """The honest sentence for a DevTools socket that could not be
    re-established: a closed socket names the TAB loss (the target
    refused its own WebSocket — it is gone); anything else names the
    socket's own error (WinError 10053, a reset, a timeout)."""
    text = str(e)
    if isinstance(e, CDPClosedError) or 'closed' in text.lower():
        return f'the DevTools socket closed and the tab was gone ({e})'
    return f'the DevTools socket failed: {e}'


def _fetch_opened_tab(session: 'ChromeSession', url: str, tab: Dict,
                      page_timeout_s: float,
                      log: Callable) -> Dict:
    """Take one page through a tab that is ALREADY open (the wave
    opened it): connect its DevTools socket, WAIT for the page (the
    v0.53 load watch — complete at an http(s) URL, still for the
    settle, real by the verdict), read the live DOM, close the tab.
    Same result shape, never raises.

    v0.54.0 — the door KEEPS ITS GRIP: a DevTools socket that drops
    mid-watch (WinError 10053 — an aborted local connection; a reset;
    Chrome recycling the connection) is a BLIP, not a verdict: the
    tab is still open, so the socket is RE-CONNECTED (up to
    ``_SOCKET_RECONNECTS`` times) and the watch RESUMES with the
    remaining budget — the owner's marinetraffic failure (a live tab
    the app gave up on) becomes a half-second pause. A tab that is
    really GONE refuses its own WebSocket on the re-connect — that,
    and only that, is 'the tab closed itself'. The anti-bot patch is
    (re-)applied on every connection."""
    target_id = str(tab.get('id') or '')
    ws_url = str(tab.get('webSocketDebuggerUrl') or '')
    try:
        if not ws_url:
            return {'url': url, 'ok': False, 'html': '', 'title': '',
                    'error': 'the tab reported no DevTools socket'}
        parts = urlparse(ws_url)
        host = parts.hostname or '127.0.0.1'
        port = parts.port or session.port
        path = parts.path or '/'
        deadline = time.monotonic() + float(page_timeout_s)
        reconnects = int(_SOCKET_RECONNECTS)
        page = None
        reason = ''
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                reason = (f'the page never finished loading in '
                          f'{float(page_timeout_s):.0f}s')
                break
            try:
                ws = CDPSocket(host, port, path)
            except Exception as e:
                # the tab's own WebSocket refused — the TARGET is gone
                reason = f'the tab closed itself ({e})'
                break
            try:
                _apply_anti_bot(ws)
                page = _take_live_page(ws, remaining)
                break
            except Exception as e:
                try:
                    ws.close()
                except Exception:
                    pass
                if reconnects > 0:
                    reconnects -= 1
                    log(f"🤖 {url}: the DevTools socket dropped mid-watch "
                        f"({e}) — re-connecting to the still-open tab "
                        f"(the watch resumes; the page budget keeps "
                        f"running)", "info")
                    time.sleep(_RECONNECT_PAUSE_S)
                    continue
                reason = _socket_failure_sentence(e)
                break
        if page is not None:
            if not page.get('ok'):
                return {'url': url, 'ok': False, 'html': '',
                        'title': page.get('title') or '',
                        'error': page.get('error') or 'the page never loaded'}
            return {'url': url, 'ok': True, 'html': page['html'],
                    'title': page.get('title') or '', 'error': ''}
        return {'url': url, 'ok': False, 'html': '', 'title': '',
                'error': reason or 'the page never loaded'}
    except Exception as e:
        return {'url': url, 'ok': False, 'html': '', 'title': '',
                'error': f'unexpected: {e}'}
    finally:
        if target_id:
            session.close_tab(target_id)


# ---------------------------------------------------------------------------
# The candidates — which links earn the offer (pure)
# ---------------------------------------------------------------------------


def collect_failed_fetch_links(results: List[Dict],
                                is_dismissed: Optional[Callable] = None
                                ) -> List[Dict]:
    """The modal's data, pure: the links whose FETCH failed this run —
    ``outcome 'review'`` with ``fetch_status 'failed'`` (the
    placeholder-in-_review pile — the master table's " - " rows) or
    ``outcome 'failed'`` (a hard pipeline miss). Deduped by canonical
    URL (first wins), loopback dropped, and a link the fetcher itself
    retired (auto-verdict: dead / paywalled / refused — the ladder's
    own final answers, checked through ``is_dismissed`` when the
    caller can) never nags: those have their doors already. Returns
    ``[{'url', 'error'}]`` in run order."""
    from gitcurator.core.website_pipeline import normalize_website_url
    out: List[Dict] = []
    seen = set()
    for r in results or []:
        if not isinstance(r, dict):
            continue
        outcome = r.get('outcome')
        fetch_failed = (outcome == 'review'
                        and str(r.get('fetch_status') or '') == 'failed')
        hard_failed = outcome == 'failed'
        if not (fetch_failed or hard_failed):
            continue
        url = (r.get('url') or '').strip()
        if not url.lower().startswith(('http://', 'https://')):
            continue
        if is_loopback_url(url):
            continue
        canonical = normalize_website_url(url)
        if not canonical or canonical in seen:
            continue
        if is_dismissed is not None:
            try:
                if is_dismissed(canonical):
                    continue  # the fetcher's own verdict already answered
            except Exception:
                pass  # a state question that cannot be asked: offer it
        seen.add(canonical)
        out.append({'url': canonical,
                    'error': str(r.get('error') or '').strip()})
    return out


# ---------------------------------------------------------------------------
# The delivery — pages into the fourth door's folder, 'auto' stamped
# ---------------------------------------------------------------------------


def deliver_pages_via_chrome(vault_path: str, links: List[Dict],
                             log: Optional[Callable] = None,
                             config: Optional[Dict] = None,
                             chrome_exe: Optional[str] = None,
                             should_continue: Optional[Callable] = None,
                             _fetcher: Optional[Callable] = None
                             ) -> Dict:
    """The fifth door, end to end: queue the failed links (the same
    queue.json the fourth door reads, stamped ``door: 'auto'``), fetch
    every page through the owner's real Chrome — WAITING for each page
    (the v0.53 load watch: complete at an http(s) URL, still for the
    settle, real by the verdict — an error page or a crash page is a
    named failure, never a delivered page) — and write each one as its
    suggested filename into ``<vault>/_review/hand-delivered/``, so the
    next pipeline pass (or the caller's immediate re-run) consumes it
    as a REAL fetch with the auto-delivery story in the reason.
    Dry-run launches nothing and writes nothing (the offer is
    rehearsed, honestly counted). Returns ``{'delivered', 'failed',
    'urls', 'failed_links', 'folder'}`` — ``failed_links`` is
    ``[{'url', 'error'}]`` for every page that could not be gathered
    (the GUI's end-of-delivery modal words the owner's own ask:
    "xx links didn't generate content or wasn't successful or got
    error xxx"). Never raises."""
    from gitcurator.core import hand_delivery as _hd
    log = log or (lambda *a, **k: None)
    report = {'delivered': 0, 'failed': 0, 'urls': [], 'failed_links': [],
              'folder': _hd.hand_delivery_dir(vault_path)}
    if not vault_path or not links:
        return report
    candidates = [l for l in links
                  if (l.get('url') or '').lower().startswith(
                      ('http://', 'https://'))]
    if not candidates:
        return report
    if dryrun.is_enabled():
        log(f"🤖 Dry-run: the fifth door would open your real Chrome "
            f"with {len(candidates)} tab(s) and deliver each page into "
            f"{report['folder']} — nothing was launched, nothing was "
            f"written", "info")
        return report
    config = config or {}
    page_timeout_s = float(config.get('web_browser_retry_timeout_s',
                                      DEFAULT_PAGE_TIMEOUT_S)
                           or DEFAULT_PAGE_TIMEOUT_S)
    wave = int(config.get('web_browser_retry_wave', DEFAULT_WAVE)
               or DEFAULT_WAVE)
    # v0.54.0 — the door's identity knobs: the persistent dedicated
    # profile is ON by default (the anti-bot law — a returning visitor
    # the sites remember); the explicit dir override is honored when set.
    persist = config.get(CONFIG_PROFILE_PERSIST, True)
    persist = True if persist is None else bool(persist)
    profile_dir = str(config.get(CONFIG_PROFILE_DIR) or '').strip() or None
    urls = [l['url'] for l in candidates]
    walls = {l['url']: l.get('error') or '' for l in candidates}
    try:
        _hd.enqueue_hand_delivery(vault_path, urls, walls=walls,
                                  log=log, door='auto')
    except Exception as e:
        log(f"⚠️ Fifth-door queue write skipped: {e}", "warning")
        # continue anyway — an already-queued link's page still lands
    if _fetcher is not None:
        try:
            pages = _fetcher(urls, log=log)
        except Exception as e:  # the door never breaks anything
            log(f"⚠️ The Chrome tab fetch failed outright: {e}", "warning")
            pages = [{'url': u, 'ok': False, 'html': '', 'title': '',
                      'error': f'chrome exploded: {e}'} for u in urls]
    else:
        try:
            pages = fetch_pages_via_chrome(
                urls, log=log, chrome_exe=chrome_exe,
                page_timeout_s=page_timeout_s, wave=wave,
                should_continue=should_continue,
                profile_dir=profile_dir, persist=persist)
        except Exception as e:  # belt and braces — the promise is absolute
            log(f"⚠️ The Chrome tab fetch failed outright: {e}", "warning")
            pages = [{'url': u, 'ok': False, 'html': '', 'title': '',
                      'error': f'chrome exploded: {e}'} for u in urls]
    folder = report['folder']
    try:
        from gitcurator.core.storage import atomic_write_bytes as _awb
    except Exception:
        _awb = None
    for page in pages:
        url = page.get('url') or ''
        if not page.get('ok') or not page.get('html'):
            report['failed'] += 1
            report['failed_links'].append(
                {'url': url,
                 'error': str(page.get('error') or 'no content').strip()})
            continue
        # v0.53.0 — the verdict is the DELIVERY's law too (the fetch
        # driver already judges its own tabs; this guard keeps the
        # delivered count honest whatever the fetch path): a page that
        # reads as a Chrome error page, a crash or a challenge is a
        # named failure here, never a written file
        ok, reason = page_is_real(page['html'], url,
                                  page.get('title') or '')
        if not ok:
            report['failed'] += 1
            report['failed_links'].append({'url': url, 'error': reason})
            log(f"⚠️ {url}: {reason}", "warning")
            continue
        suggested = _hd.suggested_filename(url)
        path = os.path.join(folder, suggested)
        content = page['html']
        try:
            if _awb is not None:
                _awb(path, content.encode('utf-8'))
            else:
                atomic_write_text(path, content)
            report['delivered'] += 1
            report['urls'].append(url)
            log(f"🤖 Delivered {suggested} ({len(content) // 1024} KB of "
                f"live DOM from your Chrome) — it is a real fetch now",
                "success")
        except Exception as e:
            report['failed'] += 1
            report['failed_links'].append(
                {'url': url, 'error': f'could not write the page: {e}'})
            log(f"⚠️ Could not write the delivered page for {url}: {e}",
                "warning")
    if report['delivered']:
        log(f"🤖 Fifth door: {report['delivered']} page(s) delivered from "
            f"your real Chrome into {folder}"
            + (f", {report['failed']} failed" if report['failed'] else "")
            + " — they will be processed as real fetches", "success")
    elif report['failed']:
        log(f"🤖 Fifth door: every tab failed ({report['failed']}) — the "
            f"links keep waiting in the retry queue", "warning")
    if report['failed_links'] and 0 < report['delivered']:
        # v0.53.0 — a PARTIAL delivery is told too: the owner asked for
        # "xx number of links didn't generate content or wasn't
        # successful or got error xxx", not silence after the fanfare
        for fl in report['failed_links'][:5]:
            log(f"⚠️ {fl['url']}: {fl['error']}", "warning")
    return report


__all__ = [
    'CONFIG_GATE', 'CONFIG_PROFILE_DIR', 'CONFIG_PROFILE_PERSIST',
    'DEFAULT_PAGE_TIMEOUT_S', 'DEFAULT_WAVE',
    'MAX_MESSAGE_BYTES', 'PAGE_SETTLE_S', 'CDPError', 'CDPTimeoutError',
    'CDPClosedError', 'encode_client_frame', 'decode_frames', 'CDPSocket',
    'is_loopback_url', 'default_profile_dir', 'ChromeSession',
    'fetch_pages_via_chrome',
    'page_is_real', 'collect_failed_fetch_links',
    'deliver_pages_via_chrome',
]
