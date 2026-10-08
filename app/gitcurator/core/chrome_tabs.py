#!/usr/bin/env python3
"""
chrome_tabs.py — v0.55.0, THE FIFTH DOOR: Chrome fetches the pages itself.

The owner's report (this session): "it still says under bot challenge.
only 1 site (from 4 test sites) appeared, but app didn't even succeed
to fetch it and generate content for it. if you think it could help
circumvent the bot detection method, the system must be able to open
tabs in my real chrome instance instead, also the app must
automatically open them with proxy on and off, because some sites
needed proxy, while some didn't need and proxy caused problems."

The diagnosis — the dedicated identity is honest but it is still a
STRANGER: the v0.54 persistent profile has cookies of its own, yet
three of the four test sites sat on their challenges because a
profile with no clearances, no logins and no history is exactly the
first-visit shape the walls key on, and the door's Chrome rode
whatever network path the OS handed it (a system proxy the proxy-
needing sites never configured, a proxy exit the challenge-hating
sites despise). The one site that visibly LOADED was called a
challenge anyway — the verdict's HTML markers fire on pages that
merely EMBED a challenge widget (a Turnstile in a login form, a
Cloudflare script tag on real content), so a delivered-ready page
kept waiting until its budget died. And the owner named the two laws
the door was missing: HIS Chrome (his cookies, his logins, his
clearances — the identity the walls already trust) and the RIGHT
ROUTE per site (some sites need the proxy, some are broken by it).

The v0.55 law — THE OWNER'S OWN WINDOW, THE RIGHT ROUTE:

    - ATTACH: before the door ever launches a Chrome of its own, it
      probes the DevTools port (``web_browser_attach_port``, default
      9222). A Chrome that answers there — the owner's own instance
      started with ``--remote-debugging-port`` (the app writes
      ``chrome-attach.bat`` and More ▸ 🪄 Attach to my Chrome runs
      it) — is DRIVEN AS IS: the tabs open in the owner's own
      window, with his profile, his cookies, his extensions and his
      network path, and the door NEVER closes his browser (it closes
      only the tabs it opened itself).
    - HIS PROFILE: when no port answers but his Chrome is NOT
      running, the door launches the owner's REAL profile through an
      attach link (a junction on Windows, a symlink on POSIX — the
      one honest way past Chrome 136+'s refusal to debug the default
      user-data-dir path) with the port open: his cookies, his
      logins, the strongest identity the door can launch. A Chrome
      already running without the port is never fought, never
      killed, never hijacked — the door falls to its own identity
      and the More ▸ 🪄 action teaches the owner's Chrome to answer.
    - THE RIGHT ROUTE: the door's own launches ride TWO legs —
      direct first (``--no-proxy-server`` — the residential line the
      challenges like), then the owner's configured proxy
      (``--proxy-server=socks5://…`` — the route the censored and
      DNS-poisoned domains need). A link that failed the first leg
      is retried on the second IN THE SAME DELIVERY, and every site
      REMEMBERS the route that worked (a per-domain memory beside
      the door's identity) — the next delivery takes the remembered
      leg first, one try, no guessing.
    - THE VERDICT'S HONESTY: a page with real CONTENT that happens
      to carry a challenge marker (the embedded widget) is the SITE,
      not the wall — only a page that is thin AND marked reads as a
      challenge; the weak title words ('blocked', 'access denied')
      only count on an interstitial's short title, never as
      substrings of a real page's long title.

The v0.54 law — the door KEEPS ITS GRIP; the profile REMEMBERS —
(kept, unchanged): the scorecard waits for the notes to land in the
vault, a dropped DevTools socket is a half-second re-connect, and
the door's dedicated profile persists its cookies (a returning
visitor). The v0.55 ladder simply puts stronger identities in front
of it: the owner's own window when it answers, his profile when his
Chrome is closed, the door's own remembered identity otherwise —
and the dedicated profile remains the honest floor that never
fights the owner's running window.

The owner's report (v0.54): "this time the links which I defined
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

#: v0.55.0 — ATTACH: drive the owner's OWN running Chrome when its
#: DevTools port answers (default ON; ``false`` keeps the door on its
#: own identities only).
CONFIG_ATTACH = 'web_browser_attach'

#: v0.55.0 — the port probed for the owner's own Chrome (the standard
#: DevTools port; the chrome-attach.bat the app writes opens it).
CONFIG_ATTACH_PORT = 'web_browser_attach_port'
DEFAULT_ATTACH_PORT = 9222

#: v0.55.0 — the real-profile rung: when no port answers and the
#: owner's Chrome is NOT running, the door launches the owner's REAL
#: profile (through the attach link) instead of its own identity
#: (default ON; ``false`` pins the door to the dedicated profile).
CONFIG_REAL_PROFILE = 'web_browser_real_profile'

#: v0.55.0 — the proxy legs: ``'auto'`` (default) tries direct first
#: then the configured proxy, remembering per site the route that
#: worked; ``'off'`` never rides the proxy; ``'on'`` always rides it.
CONFIG_PROXY_LEGS = 'web_browser_proxy'

#: v0.55.0 — the per-site route memory lives here (beside the door's
#: identity; ``{domain: 'direct' | 'proxy'}`` — one line of truth per
#: site, written only by a route that actually delivered a page).
PROXY_MEMORY_FILENAME = 'chrome-proxy-memory.json'

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

#: The bot-challenge grammar, v0.55.0 — three scopes, honestly
#: weighted. The TITLE phrases the interstitials use split into the
#: STRONG ones ('just a moment', 'checking your browser' — the
#: interstitial's own name, decisive wherever it appears) and the
#: WEAK ones ('blocked', 'access denied' — real pages carry these
#: words inside long titles; they only count on an interstitial's
#: SHORT title). The HTML markers (the Cloudflare machinery's own
#: script names) are only a wall on a page that is otherwise THIN
#: (``_CHALLENGE_TEXT_FLOOR`` chars of visible text): a page with
#: real content that carries a marker is a page that EMBEDS a
#: challenge widget (a Turnstile in a login form), not a wall — the
#: v0.55 verdict fix for the site that loaded and was still called
#: a challenge.
_CHALLENGE_TITLE_STRONG = (
    'just a moment', 'checking your browser', 'verify you are human',
    'attention required', 'ddos protection',
    'enable javascript and cookies',
)
_CHALLENGE_TITLE_WEAK = (
    'blocked', 'access denied', 'security check',
)
_CHALLENGE_HTML_MARKERS = (
    'challenge-platform', 'cf-chl', '__cf_chl', 'cf_chl_opt',
    'turnstile', 'cdn-cgi/challenge', 'cf-browser-verification',
)

#: v0.55.0 — a weak title word only counts on a title this short
#: (an interstitial's title IS the sentence; a real page's long
#: title merely contains the word).
_WEAK_TITLE_MAX_LEN = 60
#: …and with this few WORDS (the weak words are 1-2 words long; a
#: real page's title carries more than four).
_WEAK_TITLE_MAX_WORDS = 4

#: v0.55.0 — the visible-text floor: below it, a page carrying a
#: challenge marker is the challenge itself (the interstitial is a
#: script plus a sentence); at or above it, the marker rides a
#: widget on a page with real content — the SITE, not the wall.
_CHALLENGE_TEXT_FLOOR = 500

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


def _http_on(port: int, method: str, path: str,
             timeout: float = 15.0) -> (int, bytes):
    """v0.55.0 — one HTTP round trip on a DevTools endpoint (loopback
    only; both the door's own sessions and the attached one speak
    it). Raises on any miss — the callers name it honestly."""
    conn = http.client.HTTPConnection('127.0.0.1', int(port),
                                      timeout=timeout)
    try:
        conn.request(method, path)
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


def _open_tab_http(port: int, url: str) -> Dict:
    """v0.55.0 — open one tab at ``url`` on the endpoint at ``port``
    (PUT — the Chrome 111+ law — with a GET fallback for older
    builds). Returns the target dict (its ``id`` and
    ``webSocketDebuggerUrl``); raises ``CDPError`` on any miss."""
    q = quote(url, safe='')
    last = 'no HTTP answer'
    for method in ('PUT', 'GET'):
        try:
            status, body = _http_on(port, method, f'/json/new?{q}')
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


def _close_tab_http(port: int, target_id: str) -> None:
    """v0.55.0 — best-effort tab close on the endpoint at ``port``
    (GET first; a miss is cosmetic — the door's own sessions are
    terminated at the end anyway, and an attached browser is the
    owner's to keep)."""
    for method in ('GET', 'PUT'):
        try:
            status, _ = _http_on(port, method,
                                 f'/json/close/{target_id}', timeout=8.0)
            if status in (200, 204):
                return
        except Exception:
            continue


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
                 persist: bool = True,
                 extra_flags: Optional[List[str]] = None,
                 real_profile: bool = False):
        log = log or (lambda *a, **k: None)
        self._log = log
        self._page_timeout_s = float(page_timeout_s)
        self._extra_flags = [f for f in (extra_flags or []) if f]
        self._real_profile = bool(real_profile)
        self.proc: Optional[subprocess.Popen] = None
        self._stderr_thread: Optional[threading.Thread] = None
        self.profile = ''
        self.port = 0
        self._browser_ws_path = ''      # for the graceful Browser.close
        # v0.55.0 — the real-profile rung never erases anything (it is
        # the owner's data behind the link); the throwaway law stays
        # the dedicated/throwaway path's own.
        self._profile_is_throwaway = (not bool(persist)) \
            and (not self._real_profile)
        if persist and not self._real_profile:
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
        elif self._real_profile:
            # v0.55.0 — the owner's REAL profile through the attach
            # link: the caller resolved the link and pre-checked that
            # his Chrome is not running; the door never adopts here
            # (a live DevToolsActivePort in the real profile means a
            # Chrome IS running — the rung was skipped for exactly
            # that reason) and never erases the dir on close.
            self.profile = os.path.abspath((profile_dir or '').strip())
            if not self.profile:
                raise CDPError('the real-profile rung needs the attach '
                               'link path')
            os.makedirs(self.profile, exist_ok=True)
        else:
            # the throwaway law: a fresh temp profile (persist=False)
            self.profile = ''
        attempts = (list(_CHROME_FLAGS),
                    list(_CHROME_FLAGS) + list(_CONTAINER_FLAGS),
                    list(_CHROME_FLAGS) + list(_CONTAINER_FLAGS)
                    + list(_HEADLESS_FLAGS))
        last_err: Optional[Exception] = None
        for attempt, extra_flags_ in enumerate(attempts):
            try:
                self._launch_and_announce(exe, extra_flags_)
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
        if self._real_profile:
            log(f"🤖 Chrome session live — YOUR REAL PROFILE through the "
                f"attach link (your cookies, your logins, your "
                f"clearances; DevTools on 127.0.0.1:{self.port}) — the "
                f"door closes only its own tabs when it is done",
                "info")
        elif self._profile_is_throwaway:
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
        decides whether to retry. v0.55.0 — the session's own
        ``extra_flags`` (the proxy leg's argv) ride EVERY attempt."""
        self.port = _pick_free_port()
        self._browser_ws_path = ''
        if not self.profile:
            self.profile = tempfile.mkdtemp(prefix='gitcurator-chrome-')
        else:
            try:
                # exist_ok=True still raises on a DANGLING symlink (the
                # makedirs quirk) — a launch failure, honestly named
                os.makedirs(self.profile, exist_ok=True)
            except Exception as e:
                raise CDPError(f'the profile directory could not be '
                               f'prepared ({e})')
        self._stderr_thread = None
        cmd = [exe, f'--remote-debugging-port={self.port}',
               f'--user-data-dir={self.profile}'] \
            + list(extra_flags) + list(self._extra_flags) \
            + ['about:blank']
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
        return _http_on(self.port, method, path, timeout=timeout)

    def new_tab(self, url: str) -> Dict:
        """Open one tab at ``url`` (PUT — the Chrome 111+ law — with a
        GET fallback for older builds). Returns the target dict (its
        ``id`` and ``webSocketDebuggerUrl``)."""
        return _open_tab_http(self.port, url)

    def close_tab(self, target_id: str) -> None:
        """Best-effort tab close (HTTP first; a miss is cosmetic — the
        whole throwaway session is terminated at the end anyway)."""
        _close_tab_http(self.port, target_id)

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
# v0.55.0 — THE OWNER'S OWN WINDOW: attach, and the real-profile rung
# ---------------------------------------------------------------------------


class AttachedChromeSession:
    """v0.55.0 — the owner's OWN running Chrome, driven through its
    DevTools port: the strongest identity the door can borrow (his
    profile, his cookies, his logins, his extensions, his network
    path — the identity the walls already trust). The tabs the door
    opens land in HIS window; the door NEVER closes his browser —
    ``close()`` closes only the tabs the door itself opened
    (best-effort) and the session stays exactly as it found it.
    ``alive()`` stays the endpoint's answer. The proxy legs do not
    apply here (his Chrome rides whatever route HE gave it) and the
    route memory is not written (his path is not the door's to
    remember)."""

    def __init__(self, port: int, log: Optional[Callable] = None):
        log = log or (lambda *a, **k: None)
        self.port = int(port)
        self.proc = None              # nothing was launched — it's his
        self.profile = ''             # his profile, never the door's
        self._opened: List[str] = []  # the tabs the door opened itself

    def new_tab(self, url: str) -> Dict:
        """Open one tab in the owner's own Chrome (PUT — the Chrome
        111+ law — with a GET fallback). The tab id is remembered so
        close() stays gentle (only the door's own tabs)."""
        tab = _open_tab_http(self.port, url)
        tid = str(tab.get('id') or '')
        if tid:
            self._opened.append(tid)
        return tab

    def close_tab(self, target_id: str) -> None:
        """Best-effort close of ONE tab — the door's own tabs only."""
        _close_tab_http(self.port, target_id)

    def alive(self) -> bool:
        """The endpoint's answer — the owner's Chrome lives exactly as
        long as it lives, whatever the door does."""
        return _probe_version_endpoint(self.port)

    def close(self) -> None:
        """GENTLE: never Browser.close, never a kill — the owner's
        browser is not the door's to close. Only the tabs the door
        opened are closed (best-effort, idempotent, never raises)."""
        for tid in list(self._opened):
            try:
                self.close_tab(tid)
            except Exception:
                pass
        self._opened = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


#: v0.55.0 — where the owner's real Chrome keeps its user data, when
#: it can be found (Chrome stable first; best-effort, never raises).
_REAL_DATA_DIRS_WINDOWS = (
    r'Google\Chrome\User Data',
)
_REAL_DATA_DIRS_POSIX = (
    '.config/google-chrome',
    '.config/chromium',
    'Library/Application Support/Google/Chrome',
)


def real_user_data_dir() -> Optional[str]:
    """v0.55.0 — the owner's REAL Chrome user-data directory, when one
    exists on this machine (Windows %LOCALAPPDATA%\\Google\\Chrome\\
    User Data; POSIX ~/.config/google-chrome & friends). None when no
    real profile is found (the rung is skipped honestly). Pure path
    probing; never raises."""
    try:
        home = os.path.expanduser('~')
        if os.name == 'nt':
            local = os.environ.get('LOCALAPPDATA') or os.path.join(
                home, 'AppData', 'Local')
            for rel in _REAL_DATA_DIRS_WINDOWS:
                cand = os.path.join(local, rel)
                if os.path.isdir(cand):
                    return cand
            return None
        for rel in _REAL_DATA_DIRS_POSIX:
            cand = os.path.join(home, rel)
            if os.path.isdir(cand):
                return cand
        return None
    except Exception:
        return None


def real_profile_link(data_dir: Optional[str] = None,
                      link_dir: Optional[str] = None) -> Optional[str]:
    """v0.55.0 — the ATTACH LINK: a junction (Windows) or symlink
    (POSIX) at ``<app>/chrome-real-link`` pointing at the owner's real
    user-data dir. Chrome 136+ refuses ``--remote-debugging-port`` on
    the DEFAULT user-data-dir path — a link is a different path to
    the same data, the one honest way to debug the owner's real
    profile with his own Chrome. A link that already points at
    ``data_dir`` is reused; one that is DANGLING or points elsewhere
    (a moved profile, a leftover of another run) is re-pointed —
    removed and recreated, best-effort. Best-effort throughout: a
    failure returns None (the rung is skipped, the door falls to its
    own identity). Never raises, never erases the TARGET."""
    try:
        data_dir = data_dir or real_user_data_dir()
        if not data_dir or not os.path.isdir(data_dir):
            return None
        if not link_dir:
            link_dir = os.path.dirname(default_profile_dir())
        link = os.path.join(link_dir, 'chrome-real-link')
        wants_recreate = False
        if os.path.exists(link) or os.path.islink(link):
            try:
                if os.path.realpath(link) != os.path.realpath(data_dir):
                    wants_recreate = True   # stale or dangling — re-point
            except Exception:
                wants_recreate = True
            if not wants_recreate:
                return link           # a link that already points there
        else:
            os.makedirs(link_dir, exist_ok=True)
        if wants_recreate:
            if not _remove_link(link):
                return None           # a wrong link we cannot fix — skip
        if os.name == 'nt':
            # mklink /J — a directory junction needs no admin rights
            r = subprocess.run(
                ['cmd', '/c', 'mklink', '/J', link, data_dir],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=15)
            if r.returncode == 0 and os.path.exists(link):
                return link
            return None
        try:
            os.symlink(data_dir, link)
            return link
        except Exception:
            return None
    except Exception:
        return None


def _remove_link(link: str) -> bool:
    """Remove a junction/symlink WITHOUT touching its target (the
    owner's real data). Windows: ``rmdir`` on the reparse point;
    POSIX: ``os.unlink``. Best-effort; False when it could not."""
    try:
        if not (os.path.exists(link) or os.path.islink(link)):
            return True
        if os.name == 'nt':
            r = subprocess.run(
                ['cmd', '/c', 'rmdir', link],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=10)
            return r.returncode == 0 and not os.path.exists(link)
        if os.path.isdir(link) and not os.path.islink(link):
            return False      # a real directory is never the door's to remove
        os.unlink(link)
        return True
    except Exception:
        return False


#: v0.55.0 — the executables counted as "a Chrome is running" on
#: POSIX (pgrep -x over these names; anything else falls to the
#: honest launch failure).
_RUNNING_BROWSER_NAMES = (
    'chrome', 'chromium', 'chromium-browser',
    'google-chrome', 'google-chrome-stable',
)


def chrome_is_running() -> bool:
    """v0.55.0 — is a Chrome (any of the usual binaries) running right
    now? Windows asks tasklist; POSIX asks pgrep -x over the usual
    names. Best-effort by law: an unaskable question answers False
    (the real-profile launch attempt then fails honestly — Chrome
    hands off to the running instance and never announces — and the
    ladder falls to the door's own identity). Never raises. Patchable
    in tests (the pre-check seam)."""
    try:
        if os.name == 'nt':
            r = subprocess.run(
                ['tasklist', '/FI', 'IMAGENAME eq chrome.exe',
                 '/FO', 'CSV', '/NH'],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=10)
            out = r.stdout.decode('utf-8', errors='replace').lower()
            return 'chrome.exe' in out
        import shutil as _sh
        if not _sh.which('pgrep'):
            return False
        for name in _RUNNING_BROWSER_NAMES:
            r = subprocess.run(['pgrep', '-x', name],
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=5)
            if r.returncode == 0:
                return True
        return False
    except Exception:
        return False


def _try_real_profile_session(exe: str, log: Callable,
                              page_timeout_s: float,
                              extra_flags: List[str]
                              ) -> Optional[ChromeSession]:
    """v0.55.0 — the real-profile rung: launch the owner's REAL
    profile (through the attach link) with the DevTools port open —
    his cookies, his logins, the strongest identity the door can
    launch. Honest gates, in order: no real profile found → None; a
    Chrome already running → None (never fought, never killed — the
    log says how to teach it to answer); no attach link → None; a
    launch that fails (Chrome 136+ still refuses, a locked profile, a
    dead binary) → None (the caller falls to the dedicated identity).
    The caller OWNS the returned session's close() (a graceful
    Browser.close — it is the door's own launch)."""
    data_dir = real_user_data_dir()
    if not data_dir:
        return None            # no real profile on this machine
    if chrome_is_running():
        log("🤖 Your Chrome is already running without its debug port — "
            "the door will not fight or restart it on its own. More ▸ "
            "🪄 Attach to my Chrome restarts it WITH the port (your tabs "
            "come back); this delivery uses the door's own identity.",
            "info")
        return None
    link = real_profile_link(data_dir)
    if not link:
        log("🤖 The attach link to your real Chrome profile could not be "
            "created — the door uses its own identity this time",
            "warning")
        return None
    try:
        return ChromeSession(
            exe, log, page_timeout_s=page_timeout_s,
            profile_dir=link, persist=True, real_profile=True,
            extra_flags=extra_flags)
    except Exception as e:
        log(f"🤖 Your real profile could not be launched with the debug "
            f"port ({e}) — the door uses its own identity this time "
            f"(Chrome 136+ guards the default profile tightly; More ▸ 🪄 "
            f"Attach to my Chrome is the reliable route)",
            "warning")
        return None


def _launch_identity_session(exe: str, log: Callable,
                             page_timeout_s: float,
                             profile_dir: Optional[str],
                             persist: bool,
                             real_profile: bool,
                             extra_flags: List[str],
                             _session_factory: Optional[Callable] = None
                             ) -> Optional[object]:
    """v0.55.0 — the door's identity LADDER for one proxy leg: the
    real-profile rung first (the owner's own cookies — the strongest
    identity the door can launch; skipped when the caller pinned an
    explicit profile dir, which IS an identity choice), then the
    dedicated persistent profile (the v0.54 returning visitor). The
    tests' ``_session_factory`` seam bypasses the ladder entirely
    (the scripted session IS the chosen identity). Returns None only
    from the factory's own failure path — never raises."""
    if _session_factory is not None:
        return _session_factory(exe, log)
    if real_profile and not (profile_dir or '').strip():
        rung = _try_real_profile_session(exe, log, page_timeout_s,
                                         extra_flags)
        if rung is not None:
            return rung
    return ChromeSession(exe, log, page_timeout_s=page_timeout_s,
                         profile_dir=profile_dir, persist=persist,
                         extra_flags=extra_flags)


def write_chrome_attach_bat(path: str,
                            chrome_exe: Optional[str] = None,
                            port: int = DEFAULT_ATTACH_PORT,
                            data_dir: Optional[str] = None,
                            link_dir: Optional[str] = None
                            ) -> Optional[str]:
    """v0.55.0 — write ``chrome-attach.bat``: the ONE-TIME setup that
    teaches the owner's own Chrome to answer the door — it closes
    Chrome, points an attach link at the real user-data dir (the
    Chrome 136+ honest route) and relaunches Chrome WITH
    ``--remote-debugging-port`` and ``--restore-last-session`` (his
    tabs come back). The next delivery (or More ▸ 🖐 Scrape hand rows
    via Chrome) attaches to HIS window automatically. Pure ASCII +
    CRLF (the .bat law); best-effort, never raises; returns the path
    or None. The app runs it only after the owner's explicit OK."""
    try:
        chrome_exe = chrome_exe or ''
        if not chrome_exe:
            from gitcurator.core import hand_delivery as _hd
            chrome_exe = _hd.find_chrome() or ''
        data_dir = data_dir or real_user_data_dir() or ''
        if not (chrome_exe and data_dir):
            return None
        link = real_profile_link(data_dir, link_dir) or ''
        if not link:
            return None
        lines = [
            '@echo off',
            'REM GitCurator v0.57.0 - attach the fifth door to YOUR Chrome',
            'REM (restarts Chrome with its DevTools port open; your tabs',
            'REM  come back; GitCurator closes only ITS OWN tabs; to undo,',
            'REM  just relaunch Chrome normally)',
            'taskkill /IM chrome.exe /T /F >nul 2>&1',
            'timeout /t 2 /nobreak >nul',
            f'if not exist "{link}" mklink /J "{link}" "{data_dir}"',
            f'start "" "{chrome_exe}" '
            f'--remote-debugging-port={int(port)} '
            f'--user-data-dir="{link}" --restore-last-session',
            '',
        ]
        text = '\r\n'.join(lines)
        text.encode('ascii')          # the .bat law: pure ASCII
        os.makedirs(os.path.dirname(os.path.abspath(path)) or '.',
                    exist_ok=True)
        with open(path, 'w', encoding='ascii', newline='') as fh:
            fh.write(text)
        return path
    except Exception:
        return None


# ---------------------------------------------------------------------------
# v0.55.0 — THE RIGHT ROUTE: the proxy legs + the per-site memory
# ---------------------------------------------------------------------------


def chrome_proxy_flag(proxy: Optional[Dict]) -> List[str]:
    """The Chrome argv for one configured proxy (``['--proxy-server=
    socks5://127.0.0.1:10808']``; an http proxy rides ``http://``) —
    the 'proxy ON' leg. An absent proxy is an EMPTY list (there is
    nothing to turn on; the caller skips the leg honestly). Pure."""
    if not proxy:
        return []
    ptype = str(proxy.get('type') or 'socks5').strip().lower()
    host = str(proxy.get('host') or '').strip()
    port = proxy.get('port')
    if not host or not port:
        return []
    if ptype in ('socks5', 'socks4', 'http'):
        return [f'--proxy-server={ptype}://{host}:{int(port)}']
    return [f'--proxy-server=http://{host}:{int(port)}']


#: v0.55.0 — the direct leg's own flag: TRULY off, even where the OS
#: has a system proxy set (the owner's exact words: "proxy caused
#: problems" — some sites must be asked on the local line, not
#: whatever the system routes through).
_DIRECT_LEG_FLAGS = ['--no-proxy-server']

#: The remembered route values.
ROUTE_DIRECT = 'direct'
ROUTE_PROXY = 'proxy'


def proxy_memory_path() -> str:
    """v0.55.0 — where the per-site route memory lives:
    ``<APP_DIR>/chrome-proxy-memory.json`` (beside the door's
    identity; falls back next to this module like the profile dir)."""
    try:
        from gitcurator.constants import APP_DIR
        return os.path.join(APP_DIR, PROXY_MEMORY_FILENAME)
    except Exception:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            PROXY_MEMORY_FILENAME)


def load_proxy_memory(path: Optional[str]) -> Dict:
    """The remembered routes, ``{domain: 'direct' | 'proxy'}``. A
    missing, unreadable or corrupt file reads as EMPTY (the ladder
    simply tries both legs again — the memory is an optimization,
    never a gate). Pure file read; never raises."""
    if not path:
        return {}
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return {}
        return {str(k): str(v) for k, v in data.items()
                if str(v) in (ROUTE_DIRECT, ROUTE_PROXY)}
    except Exception:
        return {}


def save_proxy_memory(path: Optional[str], memory: Dict) -> bool:
    """Write the remembered routes back (atomic; best-effort, never
    raises). A path that cannot be written is logged by the caller,
    never a failure — the next delivery simply tries both legs."""
    if not path:
        return False
    try:
        from gitcurator.core.storage import atomic_write_text
        atomic_write_text(path, json.dumps(memory, indent=2,
                                           ensure_ascii=False))
        return True
    except Exception:
        return False


def domain_of_url(url: str) -> str:
    """The route memory's key: the host, lowercased, 'www.' stripped —
    the wall and the route are the SITE's, not the page's. Pure."""
    try:
        host = (urlparse(url or '').hostname or '').lower()
    except Exception:
        return ''
    if host.startswith('www.'):
        host = host[4:]
    return host


def normalize_proxy_mode(mode: Optional[str]) -> str:
    """'auto' (the both-ways ladder) | 'off' (never the proxy) |
    'on' (always the proxy). Anything unreadable reads as 'auto' —
    the owner's words were the default, not a preference."""
    m = str(mode or '').strip().lower()
    return m if m in ('auto', 'off', 'on') else 'auto'


def plan_proxy_legs(urls: List[str], memory: Dict,
                    proxy: Optional[Dict],
                    mode: str = 'auto') -> List[tuple]:
    """v0.55.0 — the static leg plan (pure): which urls ride which
    leg BEFORE anyone fails anything. ``[('direct', [...]), ('proxy',
    [...])]`` in run order — direct first (the residential line the
    challenges like), proxy second. The laws: ``mode 'off'`` → every
    url on the direct leg (never the proxy); ``mode 'on'`` → every
    url on the proxy leg (a missing proxy degrades honestly to the
    direct leg — nothing else can be asked); ``'auto'`` → unknown +
    remembered-direct urls on the direct leg, remembered-proxy urls
    on the proxy leg (a link that fails the first leg is ESCALATED
    onto the second by the driver, in the same delivery); no proxy
    configured + 'auto' → the direct leg only (the honest skip —
    there is nothing to turn on). Never raises."""
    urls = [u for u in (urls or []) if u]
    mode = normalize_proxy_mode(mode)
    have_proxy = bool(chrome_proxy_flag(proxy))
    if mode == 'off' or (mode == 'auto' and not have_proxy) \
            or (mode == 'on' and not have_proxy):
        return [(ROUTE_DIRECT, list(urls))] if urls else []
    if mode == 'on':
        return [(ROUTE_PROXY, list(urls))] if urls else []
    # auto, with a proxy: the remembered split — and the proxy leg is
    # ALWAYS planned (even empty): it is the ESCALATION's landing pad
    # (a link that fails the direct leg is retried on it, in the same
    # delivery — the driver fills it before it skips an empty leg)
    direct_leg: List[str] = []
    proxy_leg: List[str] = []
    for u in urls:
        route = str((memory or {}).get(domain_of_url(u)) or '')
        if route == ROUTE_PROXY:
            proxy_leg.append(u)
        else:
            direct_leg.append(u)
    legs = []
    if direct_leg:
        legs.append((ROUTE_DIRECT, direct_leg))
    legs.append((ROUTE_PROXY, proxy_leg))
    return legs


def remember_route(memory: Dict, url: str, route: str) -> Dict:
    """The memory write law (pure): a page that actually LANDED on a
    leg writes that leg's name for its domain — one line of truth
    earned by a delivery, never a guess. A url with no host is
    ignored; the dict is returned for chaining."""
    try:
        host = domain_of_url(url)
        if host and route in (ROUTE_DIRECT, ROUTE_PROXY):
            memory[host] = route
    except Exception:
        pass
    return memory


# ---------------------------------------------------------------------------
# The fetch driver — one tab per failed link, content from the live DOM
# ---------------------------------------------------------------------------


def _gather_through_session(session, work: List[str],
                            page_timeout_s: float, wave: int,
                            should_continue: Optional[Callable],
                            log: Callable) -> List[Dict]:
    """The wave engine (the v0.50-v0.54 law, unchanged): one tab per
    link opened in waves so tabs load together, each page WAITED FOR
    (the load watch) and taken from the live DOM. v0.55.0 — extracted
    so the attach session and every proxy leg drive the SAME engine.
    Returns one result dict per url (never raises; a dead session is
    a per-url honest failure)."""
    results: List[Dict] = []
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
    return results


def proxy_label_for_log(proxy: Optional[Dict]) -> str:
    """"socks5 127.0.0.1:10808" — the leg's log label (never a secret:
    a local listener's name, the same one the Settings page shows)."""
    if not proxy:
        return 'none'
    t = str(proxy.get('type') or 'socks5').upper()
    return f"{t} {proxy.get('host')}:{proxy.get('port')}"


def fetch_pages_via_chrome(urls: List[str],
                           log: Optional[Callable] = None,
                           chrome_exe: Optional[str] = None,
                           page_timeout_s: float = DEFAULT_PAGE_TIMEOUT_S,
                           wave: int = DEFAULT_WAVE,
                           should_continue: Optional[Callable] = None,
                           _session_factory: Optional[Callable] = None,
                           profile_dir: Optional[str] = None,
                           persist: bool = True,
                           proxy: Optional[Dict] = None,
                           proxy_mode: str = 'auto',
                           attach: bool = True,
                           attach_port: int = DEFAULT_ATTACH_PORT,
                           real_profile: bool = True,
                           memory_path: Optional[str] = None
                           ) -> List[Dict]:
    """The fifth door's engine, v0.55.0 — the owner's own window when
    it answers, the right route when the door must launch its own
    Chrome. The ladder: ATTACH (his running Chrome's DevTools port,
    ``web_browser_attach_port`` — his profile, his cookies, his
    network path; the tabs open in HIS window and the door never
    closes his browser) → THE LEGS on the door's own identities: the
    real-profile rung (his cookies, launched through the attach link
    when his Chrome is closed) or the dedicated persistent profile
    (the v0.54 returning visitor), each leg riding ONE route —
    direct first (``--no-proxy-server``), then the configured proxy
    (``--proxy-server=…``) — with a link that failed the first leg
    retried on the second IN THE SAME delivery, and every site
    remembering the route that worked (``memory_path``). One tab per
    link, in waves; the content read from the live DOM. Returns one
    ``{'url', 'ok', 'html', 'title', 'error'}`` per URL — never
    raises (no Chrome found, a refused launch and every per-tab miss
    are honest per-URL failures; a both-legs failure carries both
    legs' sentences). Dry-run never reaches here (the delivery layer
    guards it), and ``_session_factory`` lets the tests drive a
    scripted session without a browser (the scripted session IS the
    ladder's answer — no attach probe, one leg)."""
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

    # v0.55.0 — THE OWNER'S OWN WINDOW: his running Chrome's DevTools
    # port answers → the tabs open in HIS browser (his profile, his
    # cookies, his route), and the door never closes it. The scripted
    # tests' factory bypasses the probe (hermetic by law).
    if attach and _session_factory is None and attach_port:
        if _probe_version_endpoint(int(attach_port)):
            log(f"🤖 ATTACHED to your own running Chrome (DevTools on "
                f"127.0.0.1:{int(attach_port)}) — the tabs open in YOUR "
                f"window: your profile, your cookies, your logins, your "
                f"network path. GitCurator closes only its own tabs.",
                "info")
            session = AttachedChromeSession(int(attach_port), log)
            try:
                results.extend(_gather_through_session(
                    session, work, page_timeout_s, wave,
                    should_continue, log))
            finally:
                session.close()   # gentle — only the door's own tabs
            return results
        log(f"🤖 Your Chrome's debug port (127.0.0.1:{int(attach_port)}) "
            f"did not answer — the door drives its own identities (More ▸ "
            f"🪄 Attach to my Chrome teaches your Chrome to answer; it "
            f"needs to be running with the port open, or closed so the "
            f"door can launch your real profile)", "info")

    # v0.55.0 — THE RIGHT ROUTE: the legs. Direct first (the local
    # line the challenges like), then the owner's proxy (the route
    # the censored and DNS-poisoned domains need); a link that fails
    # the first leg is escalated onto the second in the same
    # delivery, and every site remembers the route that delivered.
    memory = load_proxy_memory(memory_path) if memory_path else {}
    memory_dirty = False
    legs = plan_proxy_legs(work, memory, proxy, proxy_mode)
    mode = normalize_proxy_mode(proxy_mode)
    escalate = mode == 'auto' and bool(chrome_proxy_flag(proxy))
    if mode == 'auto' and not chrome_proxy_flag(proxy):
        log("🤖 No proxy is configured (Settings → Proxy) — the proxy "
            "leg is skipped; every page rides the direct line",
            "info")
    outcomes: Dict[str, Dict] = {}
    leg_errors: Dict[str, List[str]] = {}
    stopped = False
    for route, leg_urls in legs:
        if escalate and route == ROUTE_PROXY:
            escalated = [u for u in work
                         if u not in outcomes and u in leg_errors
                         and u not in leg_urls]
            if escalated:
                log(f"🤖 {len(escalated)} link(s) that failed the direct "
                    f"leg are retried through your proxy now (the same "
                    f"delivery, the other route — the owner's law: some "
                    f"sites need the proxy, some are broken by it)",
                    "info")
                leg_urls = leg_urls + escalated
        if not leg_urls:
            continue          # the landing pad stayed empty — nothing to ride
        flags = _DIRECT_LEG_FLAGS if route == ROUTE_DIRECT \
            else chrome_proxy_flag(proxy)
        if route == ROUTE_PROXY and not flags:
            continue            # nothing to turn on — already logged
        if route == ROUTE_DIRECT:
            log(f"🤖 Chrome leg: DIRECT (no proxy — the local line) for "
                f"{len(leg_urls)} link(s)", "info")
        else:
            log(f"🤖 Chrome leg: through YOUR PROXY "
                f"({proxy_label_for_log(proxy)}) for {len(leg_urls)} "
                f"link(s)", "info")
        session = None
        try:
            session = _launch_identity_session(
                chrome, log, page_timeout_s, profile_dir, persist,
                real_profile, flags, _session_factory)
        except Exception as e:
            log(f"🤖 Fifth door could not open a Chrome session: {e}",
                "warning")
            for u in leg_urls:
                leg_errors.setdefault(u, []).append(str(e))
            continue
        if session is None:
            for u in leg_urls:
                leg_errors.setdefault(u, []).append(
                    'no Chrome session could be opened')
            continue
        try:
            pages = _gather_through_session(
                session, leg_urls, page_timeout_s, wave,
                should_continue, log)
        finally:
            try:
                session.close()
            except Exception:
                pass
        for page in pages:
            u = page.get('url') or ''
            if page.get('ok'):
                outcomes[u] = page
                if memory_path is not None:
                    remember_route(memory, u, route)
                    memory_dirty = True
            else:
                err = str(page.get('error') or 'no content').strip()
                if route == ROUTE_DIRECT:
                    leg_errors.setdefault(u, []).append(
                        f"direct leg: {err}")
                else:
                    leg_errors.setdefault(u, []).append(
                        f"proxy leg: {err}")
        if should_continue is not None and not should_continue():
            stopped = True
            break
    if memory_dirty:
        save_proxy_memory(memory_path, memory)
    # the final story, in work order: the leg that delivered, or
    # every leg's honest sentence together
    for u in work:
        if u in outcomes:
            results.append(outcomes[u])
            continue
        errs = leg_errors.get(u)
        if errs:
            results.append({'url': u, 'ok': False, 'html': '',
                            'title': '', 'error': ' | '.join(errs)})
        else:
            results.append({'url': u, 'ok': False, 'html': '', 'title': '',
                            'error': 'stopped by the user' if stopped
                                     else 'the link was not tried this '
                                          'run'})
    return results


# ---------------------------------------------------------------------------
# v0.53.0 — the page verdict: is this tab the SITE, or Chrome's own
# answer to a failure? (pure — the testable heart of the fix)
# ---------------------------------------------------------------------------


def _visible_text_len(html: str) -> int:
    """v0.55.0 — how much VISIBLE text a DOM carries: script, style,
    noscript, template and comment blocks stripped, tags stripped,
    entities collapsed, whitespace squeezed. The challenge verdict's
    thinness signal — a challenge interstitial is a script plus a
    sentence (well under the floor); a real page carries hundreds of
    characters of its own text even when it embeds a challenge
    widget. Pure, best-effort, never raises."""
    try:
        text = re.sub(r'(?is)<(script|style|noscript|template)\b[^>]*>.*?</\1\s*>',
                      ' ', html or '')
        text = re.sub(r'(?s)<!--.*?-->', ' ', text)
        text = re.sub(r'(?s)<[^>]*>', ' ', text)
        text = re.sub(r'&[a-zA-Z#0-9]{1,10};', ' ', text)
        return len(re.sub(r'\s+', ' ', text).strip())
    except Exception:
        return len(html or '')   # an unmeasurable page is not a thin one


def page_is_real(html: str, href: str, title: str = '') -> (bool, str):
    """True when the tab is showing the SITE's page. The laws, in
    order (the strongest sentence wins): the tab's own URL must be
    http(s) — a tab that ended on ``chrome-error://`` or ``about:`` is
    Chrome's page, not the site's; the DOM must not carry an ``ERR_``
    code (Chrome prints the code, not prose, in every locale — a
    network-error page's own signature); it must not read as a crash
    page ('Aw, Snap'); it must not read as a bot challenge — the
    v0.55 grammar: a STRONG interstitial title phrase is decisive
    wherever it appears; a WEAK word ('blocked', 'access denied')
    only counts on a SHORT title (an interstitial's title IS the
    sentence — a real page's long title merely contains the word);
    an HTML marker only counts on a page that is otherwise THIN
    (``_CHALLENGE_TEXT_FLOOR`` visible characters — a page with real
    content that carries the marker EMBEDS a challenge widget, it is
    not stuck on one). Returns ``(ok, reason)`` — ``reason`` is the
    honest sentence the log, the report and the modal all carry; ''
    when ok. Pure — no socket, no browser."""
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
    for marker in _CHALLENGE_TITLE_STRONG:
        if marker in t:
            return False, (f"the page stayed on a bot challenge "
                           f"(title: {marker!r})")
    if len(t) <= _WEAK_TITLE_MAX_LEN and len(t.split()) <= _WEAK_TITLE_MAX_WORDS:
        for marker in _CHALLENGE_TITLE_WEAK:
            if marker in t:
                return False, (f"the page stayed on a bot challenge "
                               f"(title: {marker!r})")
    if _visible_text_len(html) < _CHALLENGE_TEXT_FLOOR:
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
    every page through the owner's real Chrome — v0.55.0: HIS OWN
    running Chrome when its DevTools port answers (attach), his real
    profile through the attach link when his Chrome is closed, the
    door's own identity on the proxy legs (direct first, then the
    owner's proxy, per-site route memory) otherwise — WAITING for each page
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
    # v0.55.0 — THE OWNER'S OWN WINDOW + THE RIGHT ROUTE: attach to his
    # running Chrome when its DevTools port answers; launch his real
    # profile when his Chrome is closed; ride the legs (direct first,
    # then his configured proxy) with the per-site route memory.
    attach = config.get(CONFIG_ATTACH, True)
    attach = True if attach is None else bool(attach)
    try:
        attach_port = int(config.get(CONFIG_ATTACH_PORT,
                                     DEFAULT_ATTACH_PORT)
                          or DEFAULT_ATTACH_PORT)
    except (TypeError, ValueError):
        attach_port = DEFAULT_ATTACH_PORT
    real_profile = config.get(CONFIG_REAL_PROFILE, True)
    real_profile = True if real_profile is None else bool(real_profile)
    proxy_mode = normalize_proxy_mode(
        str(config.get(CONFIG_PROXY_LEGS, 'auto') or 'auto'))
    proxy = None
    try:
        from gitcurator.core.web_fetch import proxy_from_config
        proxy = proxy_from_config(config)
    except Exception:
        proxy = None            # an unreadable proxy block rides no leg
    memory_path = proxy_memory_path()
    if proxy and proxy_mode == 'auto':
        log(f"🤖 Fifth door, v0.55: your own Chrome when its debug port "
            f"answers (127.0.0.1:{attach_port}); otherwise the door's own "
            f"identity — DIRECT first, then through your proxy "
            f"({proxy_label_for_log(proxy)}) — and every site remembers "
            f"the route that worked", "info")
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
                profile_dir=profile_dir, persist=persist,
                proxy=proxy, proxy_mode=proxy_mode,
                attach=attach, attach_port=attach_port,
                real_profile=real_profile, memory_path=memory_path)
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
    'CONFIG_ATTACH', 'CONFIG_ATTACH_PORT', 'CONFIG_REAL_PROFILE',
    'CONFIG_PROXY_LEGS', 'DEFAULT_ATTACH_PORT', 'PROXY_MEMORY_FILENAME',
    'ROUTE_DIRECT', 'ROUTE_PROXY',
    'DEFAULT_PAGE_TIMEOUT_S', 'DEFAULT_WAVE',
    'MAX_MESSAGE_BYTES', 'PAGE_SETTLE_S', 'CDPError', 'CDPTimeoutError',
    'CDPClosedError', 'encode_client_frame', 'decode_frames', 'CDPSocket',
    'is_loopback_url', 'default_profile_dir', 'ChromeSession',
    'AttachedChromeSession', 'real_user_data_dir', 'real_profile_link',
    'chrome_is_running', 'write_chrome_attach_bat',
    'chrome_proxy_flag', 'proxy_memory_path', 'load_proxy_memory',
    'save_proxy_memory', 'domain_of_url', 'normalize_proxy_mode',
    'plan_proxy_legs', 'remember_route', 'proxy_label_for_log',
    'fetch_pages_via_chrome',
    'page_is_real', 'collect_failed_fetch_links',
    'deliver_pages_via_chrome',
]
