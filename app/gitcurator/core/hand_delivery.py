#!/usr/bin/env python3
"""
hand_delivery.py — v0.48.0, THE FOURTH DOOR: the owner's own Chrome.

The owner's report (session): "I've noticed that for some links, the
fetcher got 403 error, but when I enter them manually on my browser
they work flawlessly, so I think it's about anti-crawling and fetching
that some sites might use. What if we add another layer (as one of the
final procedures) of fetching, which app permits me to open in a real
instance of chrome."

The diagnosis. The ladder's three machine doors (stdlib both-routes,
the route-aimed refusal alternate, curl_cffi's Chrome TLS handshake)
still share one thing the walled site CAN see: they are not a real
Chrome driven by the owner's own hand. The strictest bot defenses
(Cloudflare's managed challenge class) answer exactly that difference —
a challenge that survives the impersonated handshake is, by the
fetcher's own verdict, "needs a live browser". The owner's browser IS
a live browser. So the last rung of the ladder is the owner themself:

    1. ENQUEUE — a walled link (refusal-family / bot-defense /
       TLS-handshake class) is queued for hand-delivery: More ▸
       "🖐 Hand-deliver walled links…" (the picker), the master table's
       Status gesture (set a row to "🖐 hand"), or the CLI
       (--hand-delivery). The queue is a plain JSON file inside the
       vault: ``<vault>/_review/hand-delivered/queue.json`` — owner
       visible, owner editable, never a second source of truth.
    2. OPEN — the app opens the URL in a REAL Chrome (chrome.exe found
       by its well-known paths; the system browser is the honest
       fallback, named in the log) and drops a README into the folder:
       save the page there (Ctrl+S, "Webpage, HTML Only") under the
       suggested filename.
    3. CONSUME — the next batch checks the folder FIRST for every
       link it processes: a delivered page answers the fetch (a REAL
       full result whose reason tells the story — "hand-delivered via
       the owner's real Chrome — the machine doors were walled: …"),
       the queued file is consumed, the retry row resolves, and the
       note lands exactly where every fetched note lands.

The law, kept as tight as the rest of the ladder:

    - the folder is ASKED, never WRITTEN for the link (the app writes
      only queue.json + README.txt; the delivered .html is the owner's
      hand, untouched, moved nowhere — consumption reads it in place);
    - hand-delivery is NOT a retirement: the link keeps waiting while
      queued (a walled link requeues per its category; the queue is a
      memo, not a gate), and "🖐 hand" in the master table never buries
      anything — the row is stamped and the link stays alive;
    - loopback never opens a browser (the v0.15.1 rule: no door
      escalates off-machine);
    - everything here is tolerated: a missing folder, a corrupt queue,
      an unreadable page — the fourth door must never break a batch;
    - the config "web_hand_delivery" (default ON) opts the whole door
      out;
    - v0.53.0 — the pages the APP delivered (queue rows stamped
      ``door: 'auto'`` by the fifth door) are RE-VERIFIED at consume
      time through :func:`gitcurator.core.chrome_tabs.page_is_real`:
      one that turns out to be a Chrome error page, a crash page or a
      stuck challenge (the file a crashed Chrome session could have
      left behind) is DISCARDED — the app's own file, removed — and
      the link keeps waiting. The owner's Ctrl+S pages carry no
      ``door`` stamp and stay the owner's verdict, trusted as always.

Pure stdlib; no network of its own (the browser is the owner's).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import webbrowser
from typing import Callable, Dict, List, Optional

from gitcurator.core import dryrun
from gitcurator.core.storage import atomic_write_text

# ---------------------------------------------------------------------------
# Configuration (safe to edit)
# ---------------------------------------------------------------------------

#: The folder, per vault: ``<vault>/_review/hand-delivered/``
HAND_FOLDER_NAME = 'hand-delivered'

#: The queue file (URL list awaiting hand-delivery), inside the folder.
QUEUE_FILENAME = 'queue.json'

#: The instructions file (plain text, written for the owner).
README_FILENAME = 'README.txt'

#: v0.48.0 — Status-cell markers meaning "queue this link for the
#: fourth door" (substring + case-insensitive, the table's grammar).
#: Deliberately AFTER the retirement markers in the check order: a
#: cell that says both "dead" and "hand" is a DEAD cell (death wins,
#: the same precedence the reviewed/dead pair already uses).
#: v0.52.0 — '✋' joins: the owner's screenshot set the hand gesture
#: with U+270B (the raised hand the legend's own line reads like),
#: a codepoint the 2026 emoji set renders near-identically to 🖐
#: (U+1F590) — both must queue. Bare 'hand' joins for the same
#: reason: the legend's own word, the first thing an owner types.
HAND_MARKERS = ("🖐", "✋", "🫱", "hand-deliver", "hand deliver",
                "hand", "chrome")

#: The reason classes that earn the fourth door: the refusal family,
#: bot-defense markers, the challenge hint, the TLS-handshake walls,
#: and the third door's own "chrome-impersonated:" report line. A
#: 404/paywall/DNS truth never asks the owner's hand — it has its own
#: door.
WALL_MARKERS = (
    'http 403', 'http 405', 'http 429', 'http 451',
    'bot defense', 'challenge', 'blocked_bot', 'refused',
    'ssl', 'handshake', 'chrome-impersonated', 'curl_cffi',
    'fingerprint',
)

#: Windows chrome.exe candidates (most common first).
_WINDOWS_CHROME_PATHS = (
    r'C:\Program Files\Google\Chrome\Application\chrome.exe',
    r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
    os.path.expandvars(
        r'%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe'),
)

#: POSIX executable candidates (shutil.which, first hit wins).
_POSIX_CHROME_NAMES = (
    'google-chrome', 'google-chrome-stable', 'chrome',
    'chromium', 'chromium-browser', 'microsoft-edge', 'msedge',
)

_SLUG_STRIP = set(' /\\?%*:|"<>.~#&=+,@')

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def hand_delivery_dir(vault_path: str) -> str:
    """``<vault>/_review/hand-delivered`` — the fourth door's folder."""
    from gitcurator.core.website_pipeline import REVIEW_FOLDER
    return os.path.join(vault_path or '', REVIEW_FOLDER,
                        HAND_FOLDER_NAME)


def queue_path(vault_path: str) -> str:
    """The queue.json path for one vault."""
    return os.path.join(hand_delivery_dir(vault_path), QUEUE_FILENAME)


def readme_path(vault_path: str) -> str:
    """The README.txt path for one vault."""
    return os.path.join(hand_delivery_dir(vault_path), README_FILENAME)


# ---------------------------------------------------------------------------
# The wall predicate
# ---------------------------------------------------------------------------


def is_walled_reason(reason: str) -> bool:
    """True when a failure REASON names a wall the fourth door answers
    (refusal family, bot defense, challenge, TLS handshake, the
    third door's report line). Case-insensitive substring markers —
    the honest, over-inclusive side of the predicate: a walled link
    the owner can hand-fetch is one dialog away, a missed one is a
    lost note. Resource truths (404/paywall/DNS) never match."""
    s = (reason or '').strip().lower()
    if not s:
        return False
    return any(m in s for m in WALL_MARKERS)


def walled_retry_rows(state) -> List[Dict]:
    """The picker's data: retry-queue rows whose last error names a
    wall (the three machine doors tried and failed). Never raises —
    a state DB hiccup means an empty picker, not a crash."""
    try:
        rows = state.all_retry_rows() or []
    except Exception:
        return []
    out = []
    for row in rows:
        u = (row.get('url') or '').strip()
        if u and is_walled_reason(str(row.get('last_error') or '')):
            out.append({'url': u,
                        'wall': str(row.get('last_error') or ''),
                        'attempts': int(row.get('attempts') or 0)})
    return out


# ---------------------------------------------------------------------------
# The suggested filename (deterministic, collision-safe)
# ---------------------------------------------------------------------------


def suggested_filename(url: str) -> str:
    """``hand-<domain-slug>-<hash8>.html`` — deterministic for one URL
    (the same link suggests the same name on every machine), short
    enough to type, and collision-proof (the md5 of the full URL
    rides along; two pages on one domain can never collide)."""
    try:
        from urllib.parse import urlparse
        netloc = (urlparse(url or '').netloc or 'link').lower()
    except Exception:
        netloc = 'link'
    slug = ''.join('-' if c in _SLUG_STRIP or not c.isalnum() else c
                   for c in netloc).strip('-')[:40] or 'link'
    digest = hashlib.md5((url or '').encode('utf-8',
                                            errors='replace')).hexdigest()[:8]
    return f"hand-{slug}-{digest}.html"


# ---------------------------------------------------------------------------
# The queue (write side)
# ---------------------------------------------------------------------------


def _read_queue(vault_path: str) -> Dict:
    """The queue's shape: ``{"links": {url: {"wall", "queued",
    "suggested"}}}`` — a URL-keyed object keeps re-enqueueing a
    no-op by construction. Corrupt/missing → the empty queue (the
    door never breaks on a bad file)."""
    p = queue_path(vault_path)
    if not os.path.isfile(p):
        return {'links': {}}
    try:
        with open(p, 'r', encoding='utf-8', errors='replace') as f:
            data = json.load(f)
        if not isinstance(data, dict) \
                or not isinstance(data.get('links'), dict):
            return {'links': {}}
        return data
    except Exception:
        return {'links': {}}


def _readme_text(queue: Dict) -> str:
    """The instructions file: what the owner does with each queued
    link (open in Chrome, save as the suggested name, into THIS
    folder). Plain text, ASCII-safe anchors, regenerated on every
    enqueue — the file IS the dialog."""
    lines = [
        "GitCurator - the fourth door: hand-delivered pages",
        "=" * 52,
        "",
        "These links were walled for every machine door (403 / bot",
        "defense / TLS fingerprint). Your real Chrome can open them.",
        "",
        "For each link below:",
        "  1. open it in Chrome (More > 'Hand-deliver walled links'",
        "     does it for you),",
        "  2. save the page here: Ctrl+S, format",
        "     'Webpage, HTML Only', filename = the suggested name,",
        "  3. run the next batch (SYNC) - the page is consumed as a",
        "     real fetch, the note is written, the retry clears.",
        "",
        "Queued links:",
    ]
    for url, meta in sorted((queue.get('links') or {}).items()):
        sug = (meta or {}).get('suggested') or suggested_filename(url)
        wall = str((meta or {}).get('wall') or '')[:120]
        lines.append(f"  - {url}")
        lines.append(f"    save as: {sug}")
        if wall:
            lines.append(f"    the wall: {wall}")
    lines += [
        "",
        "Saved pages already consumed are marked in queue.json",
        "(consumed: true) - do not delete them; they are the record.",
        "Delete this README freely - it regenerates on the next",
        "hand-delivery enqueue.",
        "",
    ]
    return '\n'.join(lines)


def enqueue_hand_delivery(vault_path: str, urls: List[str],
                          walls: Optional[Dict[str, str]] = None,
                          log: Optional[Callable] = None,
                          open_chrome: bool = False,
                          door: Optional[str] = None
                          ) -> Dict:
    """Queue links for hand-delivery (the write side of the door).

    Merges into queue.json (an already-queued URL is refreshed, never
    duplicated); rewrites README.txt with the full current queue;
    optionally opens each NEW link in the real Chrome. Dry-run aware
    (the queue and README are recorded, not written). v0.50.0 —
    ``door`` (e.g. 'auto') stamps which door queued the link, so the
    consume side words the fetch's story honestly (the fifth door
    delivered it, not the owner's Ctrl+S). Returns ``{'added',
    'queued_total', 'opened', 'folder'}`` — never raises (a queue the
    app cannot write is a hint in the log, not a crash).
    """
    log = log or (lambda *a, **k: None)
    report = {'added': 0, 'queued_total': 0, 'opened': 0,
              'folder': hand_delivery_dir(vault_path)}
    if not vault_path or not urls:
        return report
    folder = hand_delivery_dir(vault_path)
    walls = walls or {}
    try:
        dryrun.makedirs(folder, exist_ok=True)
    except Exception as e:
        log(f"⚠️ Hand-delivery folder could not be created "
            f"({folder}): {e}", "warning")
        return report
    queue = _read_queue(vault_path)
    links = queue.setdefault('links', {})
    now = time.strftime('%Y-%m-%d %H:%M')
    added = []
    for url in urls:
        u = (url or '').strip()
        if not u or not u.lower().startswith(('http://', 'https://')):
            continue
        if u in links and links[u].get('consumed'):
            # a re-enqueue of a consumed link resets it (the owner may
            # hand-deliver again — e.g. the page changed)
            links[u]['consumed'] = False
            links[u]['requeued'] = now
            if door:
                links[u]['door'] = door
            added.append(u)
            continue
        if u in links:
            continue  # already queued — a no-op, never a duplicate
        meta = {'wall': walls.get(u, ''), 'queued': now,
                'suggested': suggested_filename(u)}
        if door:
            meta['door'] = door
        links[u] = meta
        added.append(u)
    if added:
        queue['links'] = links
        try:
            dryrun.write_text(
                queue_path(vault_path),
                json.dumps(queue, indent=2, ensure_ascii=False))
            dryrun.write_text(readme_path(vault_path),
                              _readme_text(queue))
        except Exception as e:
            log(f"⚠️ Hand-delivery queue could not be written: {e}",
                "warning")
            return report
        report['added'] = len(added)
        report['queued_total'] = len(links)
        log(f"🖐 Hand-delivery: {len(added)} link(s) queued — save each "
            f"page (Ctrl+S, 'Webpage, HTML Only') as its suggested "
            f"name into {folder}; the next batch consumes them",
            "info")
        if open_chrome:
            for u in added:
                how = open_in_chrome(u, log=log)
                if how:
                    report['opened'] += 1
    else:
        report['queued_total'] = len(links)
    return report


# ---------------------------------------------------------------------------
# The real Chrome
# ---------------------------------------------------------------------------


def find_chrome() -> Optional[str]:
    """The real Chrome's executable, when one can be found:
    well-known chrome.exe paths on Windows, ``shutil.which`` over the
    usual suspects on POSIX (Chromium and Edge count — the door is
    'a real browser the owner drives', and they render the same
    challenge pages). None when nothing is found (the caller falls
    back to the system default browser, honestly named)."""
    if os.name == 'nt':
        for p in _WINDOWS_CHROME_PATHS:
            if p and os.path.isfile(p):
                return p
        return None
    for name in _POSIX_CHROME_NAMES:
        found = shutil.which(name)
        if found:
            return found
    return None


def open_in_chrome(url: str, log: Optional[Callable] = None) -> str:
    """Open ``url`` in the owner's REAL Chrome. The honest ladder:
    the found Chrome (detached — the app never waits for the browser
    to close), else the system default browser via ``webbrowser``
    (named as the fallback in the log — the owner deserves to know
    which door opened). Loopback NEVER opens (the v0.15.1 rule).
    Returns 'chrome' / 'browser' / '' — never raises."""
    log = log or (lambda *a, **k: None)
    url = (url or '').strip()
    if not url or not url.lower().startswith(('http://', 'https://')):
        return ''
    try:
        from urllib.parse import urlparse
        host = (urlparse(url).hostname or '').lower()
        if host in ('localhost', '127.0.0.1', '::1') \
                or (host or '').endswith('.local'):
            log(f"🖐 Hand-delivery: loopback URL never opens a "
                f"browser ({url})", "info")
            return ''
    except Exception:
        pass
    exe = find_chrome()
    try:
        if exe:
            subprocess.Popen(
                [exe, url],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                close_fds=True, cwd=os.path.expanduser('~'))
            log(f"🖐 Opened in your real Chrome: {url}", "info")
            return 'chrome'
        opened = webbrowser.open(url)
        if opened:
            log(f"🖐 Opened in your default browser (no Chrome found — "
                f"the honest fallback): {url}", "info")
            return 'browser'
        log(f"⚠️ No browser could be opened for {url} — open it by "
            f"hand", "warning")
        return ''
    except Exception as e:
        log(f"⚠️ Chrome could not be launched ({e}) — open {url} by "
            f"hand", "warning")
        return ''


# ---------------------------------------------------------------------------
# The consume side
# ---------------------------------------------------------------------------


def collect_delivered(vault_path: str) -> List[Dict]:
    """The queued links whose suggested page file HAS been delivered
    (the owner saved it into the folder, or the fifth door wrote it).
    Returns ``[{'url', 'wall', 'suggested', 'file', 'door'}]`` — a pure
    read, nothing is consumed yet (the batch does that). ``door`` is
    'auto' for pages the app itself delivered (v0.53: those are
    re-verified at consume time — an error page is discarded, never
    consumed), empty for the owner's own Ctrl+S saves. Orphan files
    (an .html in the folder matching no queue row) are IGNORED — the
    README is the contract, a stray file is the owner's business."""
    queue = _read_queue(vault_path)
    links = queue.get('links') or {}
    folder = hand_delivery_dir(vault_path)
    out = []
    for url, meta in links.items():
        if (meta or {}).get('consumed'):
            continue
        suggested = (meta or {}).get('suggested') \
            or suggested_filename(url)
        path = os.path.join(folder, suggested)
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            out.append({'url': url,
                        'wall': str((meta or {}).get('wall') or ''),
                        'suggested': suggested, 'file': path,
                        'door': str((meta or {}).get('door') or '')})
    return out


def _decode_delivered(path: str) -> bytes:
    """Read a delivered page as bytes (the pipeline's extractor
    re-decodes with the page's own charset logic — the same law as
    every fetched body)."""
    with open(path, 'rb') as f:
        return f.read()


def _auto_page_is_real(url: str, body: bytes) -> (bool, str):
    """v0.53.0 — the fifth door's own verdict, asked again at consume
    time for the pages the APP delivered: a file whose DOM carries a
    Chrome ``ERR_`` code, a crash phrase or a challenge grammar is a
    page a crashed or walled Chrome session left behind — never the
    site's content. The URL passes as the page's href (the queue's own
    canonical link — the protocol law holds for every enqueued row).
    Pure; never raises."""
    try:
        from gitcurator.core.chrome_tabs import page_is_real
        text = (body or b'').decode('utf-8', errors='replace')
        ok, reason = page_is_real(text, url, '')
        return ok, reason
    except Exception as e:
        return True, ''   # a verdict that cannot be asked is not a wall


def consume_delivered(vault_path: str, log: Optional[Callable] = None
                      ) -> List[Dict]:
    """Take the delivered pages: read each one, mark it consumed in
    queue.json (the FILE stays — it is the record), and return
    ``[{'url', 'wall', 'body', 'file'}]`` for the pipeline to feed
    through the normal extract/classify/write path. v0.53.0 — a page
    the APP delivered (door 'auto') is re-verified first: one that
    fails the fifth door's verdict (a Chrome error page, a crash
    page, a stuck challenge — what a crashed Chrome session leaves
    behind) is DISCARDED (the app's own file, removed) and its link
    keeps waiting; the owner's saved pages are trusted as always.
    Dry-run aware (nothing is marked, the pages are still read — a
    dry-run batch SHOWS the hand-delivery without consuming it).
    Never raises."""
    log = log or (lambda *a, **k: None)
    try:
        delivered = collect_delivered(vault_path)
    except Exception:
        return []
    if not delivered:
        return []
    out = []
    for item in delivered:
        try:
            body = _decode_delivered(item['file'])
        except Exception as e:
            log(f"⚠️ Hand-delivered page unreadable "
                f"({item['suggested']}): {e} — the link keeps waiting",
                "warning")
            continue
        if not body.strip():
            log(f"⚠️ Hand-delivered page is empty "
                f"({item['suggested']}) — the link keeps waiting",
                "warning")
            continue
        if item.get('door') == 'auto':
            ok, reason = _auto_page_is_real(item['url'], body)
            if not ok:
                _discard_auto_page(item['file'])
                log(f"⚠️ The auto-delivered page for {item['url']} was "
                    f"NOT the site ({reason}) — discarded (the app's "
                    f"own file), the link keeps waiting for its doors",
                    "warning")
                continue
        out.append({'url': item['url'], 'wall': item['wall'],
                    'body': body, 'file': item['file']})
    if not out:
        return []
    if not dryrun.is_enabled():
        queue = _read_queue(vault_path)
        for item in out:
            meta = (queue.get('links') or {}).get(item['url'])
            if meta is not None:
                meta['consumed'] = time.strftime('%Y-%m-%d %H:%M')
        try:
            atomic_write_text(
                queue_path(vault_path),
                json.dumps(queue, indent=2, ensure_ascii=False))
        except Exception as e:
            log(f"⚠️ Hand-delivery queue could not be stamped: {e}",
                "warning")
    log(f"🖐 Hand-delivery: {len(out)} delivered page(s) taken — "
        f"processing them as real fetches", "info")
    return out


def _discard_auto_page(path: str) -> None:
    """Remove a page the app itself delivered that failed its own
    verdict — the file is OURS (the fifth door wrote it), never the
    owner's save, and leaving it would re-fail every consume pass.
    Best-effort; never raises."""
    try:
        if os.path.isfile(path):
            os.remove(path)
    except Exception:
        pass


def hand_fetch_result(url: str, body: bytes, wall: str,
                      door: Optional[str] = None):
    """Build the FetchResult a delivered page becomes: a REAL full
    fetch whose reason tells the story (the honest line the note, the
    master table, and the run report all carry). v0.50.0 — ``door ==
    'auto'`` words the fifth door's story (the app took the page from
    the owner's Chrome itself); the default wording stays the fourth
    door's (the owner saved the page by hand)."""
    from gitcurator.core import web_fetch as _wf
    try:
        text = body.decode('utf-8', errors='replace')
    except Exception:
        text = ''
    wall = (wall or 'the machine doors were walled').strip()
    if door == 'auto':
        reason = (f"auto-delivered via the owner's own Chrome (the fifth "
                  f"door's tab retry — one tab per failed link, the live "
                  f"DOM taken after the page loaded) — the machine doors "
                  f"failed: {wall}")
    else:
        reason = (f"hand-delivered via the owner's real Chrome — the "
                  f"machine doors were walled: {wall}")
    return _wf.FetchResult(
        url, final_url=url, status='full', http_status=200,
        content_type='text/html', charset='utf-8', body=body, text=text,
        reason=reason)


def take_hand_delivered(vault_path: str, canonical: str,
                        log: Optional[Callable] = None,
                        allow_consumed: bool = False
                        ) -> Optional[object]:
    """The per-link check the pipeline makes BEFORE every fetch: is
    there a delivered page for THIS link? Returns a FetchResult (the
    real thing — status 'full', the story in the reason) or None.
    Consumption is the same one-movement law as
    :func:`consume_delivered` (queue stamped, file kept). v0.53.0 —
    a page the APP delivered (door 'auto') is re-verified first: one
    that fails the fifth door's verdict is discarded (the app's own
    file, removed) and the link keeps waiting — None, honestly.

    v0.57.0 — THE NOTE IS THE SUCCESS, the redo-consume:
    ``allow_consumed=True`` re-reads a page whose queue row was
    already stamped consumed — the owner's law ("the app must refetch
    and generate notes, if they notes aren't properly stored … false
    success and must be redo"). The delivered page is the RECORD (it
    stays in the folder), so a redo reads it in place: no new Chrome
    tab, no re-stamp of the queue (the first consume date is the
    truth), the page simply answers the fetch again while the LLM is
    asked to build the proper, categorized note this time. The
    pipeline passes the flag exactly when the row carries the 🖐
    gesture (a link with a proper note never reaches the fetch — the
    dedupe gate answers first)."""
    if not vault_path or not canonical:
        return None
    try:
        queue = _read_queue(vault_path)
    except Exception:
        return None
    meta = (queue.get('links') or {}).get(canonical)
    if not meta:
        return None
    if meta.get('consumed') and not allow_consumed:
        return None
    suggested = meta.get('suggested') or suggested_filename(canonical)
    path = os.path.join(hand_delivery_dir(vault_path), suggested)
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        return None
    try:
        body = _decode_delivered(path)
    except Exception as e:
        log and log(f"⚠️ Hand-delivered page unreadable for "
                    f"{canonical}: {e}", "warning")
        return None
    if not body.strip():
        return None
    if (meta.get('door') or '') == 'auto':
        ok, reason = _auto_page_is_real(canonical, body)
        if not ok:
            _discard_auto_page(path)
            log and log(f"⚠️ The auto-delivered page for {canonical} "
                        f"was NOT the site ({reason}) — discarded (the "
                        f"app's own file), the link keeps waiting for "
                        f"its doors", "warning")
            return None
    if not dryrun.is_enabled() and not meta.get('consumed'):
        try:
            queue['links'][canonical]['consumed'] = \
                time.strftime('%Y-%m-%d %H:%M')
            atomic_write_text(
                queue_path(vault_path),
                json.dumps(queue, indent=2, ensure_ascii=False))
        except Exception as e:
            log and log(f"⚠️ Hand-delivery queue could not be "
                        f"stamped: {e}", "warning")
    return hand_fetch_result(canonical, body,
                             str(meta.get('wall') or ''),
                             door=meta.get('door'))


# ---------------------------------------------------------------------------
# The master-table gesture
# ---------------------------------------------------------------------------


def _status_is_hand(status: str) -> bool:
    """True when a gesture reads as the fourth door's (the owner's 🖐
    hand). The precedence law: revived is checked first (a cell that
    says both 'hand' and 'revived' is a REVIVED cell — the old law);
    then ANY hand marker wins — v0.52.0 changed the guard order for
    the combined gesture (icon + Status cells): a 🖐 icon over an
    'unreviewed' Status cell is still a HAND row (the owner set the
    emoji in the # column and left the Status default — the exact
    shape his screenshot showed); death and reviewed are still the
    CALLER's first checks (they retire, the stronger sentences)."""
    s = (status or '').strip().lower()
    if not s:
        return False
    if any(m in s for m in ('♻️', 'revived', 'restored')):
        return False
    return any(m in s for m in HAND_MARKERS)


def pending_hand_links(vault_path: str) -> List[Dict]:
    """v0.52.0 — the queued hand links whose page has NOT landed yet:
    ``[{'url', 'wall'}]`` for every queue.json entry that is neither
    consumed nor sitting in the hand-delivered folder as its suggested
    file. This is the fifth door's to-do list — the Chrome scraping
    pass (the end-of-run auto-delivery, the caught-up check, the CLI)
    reads it to know which walled links still owe the owner's browser
    a tab. Pure file reads; never raises (a broken queue reads as
    empty — the table's own scan,
    :func:`gitcurator.core.website_pipeline.scan_master_hand_rows`,
    is the fuller source that also catches never-enqueued rows)."""
    out: List[Dict] = []
    try:
        queue = _read_queue(vault_path)
        links = queue.get('links') or {}
        folder = hand_delivery_dir(vault_path)
        for url, meta in links.items():
            if (meta or {}).get('consumed'):
                continue
            suggested = (meta or {}).get('suggested') \
                or suggested_filename(url)
            if os.path.isfile(os.path.join(folder, suggested)):
                continue    # the page landed — it is delivered
            out.append({'url': url,
                        'wall': str((meta or {}).get('wall') or '')})
    except Exception:
        return []
    return out


def stamp_hand_rows(vault_path: str, urls: List[str],
                    log: Optional[Callable] = None) -> int:
    """Rewrite the chosen rows' Status cells to the stamped hand
    gesture (``🖐 hand — queued <date>``) — the table's own feedback
    loop, so a hand-set cell is confirmed the same way dead/reviewed
    cells are. Pure file edit; never raises. Returns rows stamped."""
    log = log or (lambda *a, **k: None)
    from gitcurator.core.website_pipeline import (
        decommission_table_path, _parse_decommission_rows)
    path = decommission_table_path(vault_path)
    if not vault_path or not os.path.isfile(path):
        return 0
    wanted = set(urls or [])
    if not wanted:
        return 0
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.read().splitlines()
        rows = _parse_decommission_rows(path)
    except Exception as e:
        log(f"⚠️ Hand stamp skipped — the table could not be read: "
            f"{e}", "warning")
        return 0
    date_str = time.strftime('%Y-%m-%d')
    stamped = 0
    for row in rows:
        if row['url'] not in wanted:
            continue
        parts = row['raw'].split('|')
        if len(parts) < 8:
            continue
        parts[6] = f" 🖐 hand — queued {date_str} "
        lines[row['line']] = '|'.join(parts)
        stamped += 1
    if not stamped:
        return 0
    now = time.strftime('%Y-%m-%d %H:%M')
    lines = [f"> Last updated: {now}"
             if line.startswith('> Last updated:') else line
             for line in lines]
    try:
        atomic_write_text(path, '\n'.join(lines).rstrip('\n') + '\n')
    except Exception as e:
        log(f"⚠️ Hand stamp could not be written: {e}", "warning")
        return 0
    return stamped


def stamp_hand_delivered_rows(vault_path: str, urls: List[str],
                              log: Optional[Callable] = None) -> int:
    """v0.56.0 — THE HAND'S HARVEST: retire the 🖐 gesture into the
    green checkbox when its link's PROPER note has landed.

    The owner's report (session, verbatim): "when newly fetched
    website with hand (🖐) are fetched and stored correctly, the
    system must automatically turn the hand emoji to green checkbox
    and remove their half-fetched items from _review, because now
    they have A Proper and categorized note."

    Only a row whose Status still reads HAND
    (:func:`_status_is_hand`, with death / reviewed / revived winning
    first — the table's precedence law) is rewritten, to
    `` ✅ hand-delivered — fetched <date> `` — the table's own success
    verdict, so the retired row never waits, never re-queues, never
    asks the fifth door for a tab again. URLs are matched by their
    canonical form (the table's hand-edited cells keep whatever shape
    the owner typed). Pure file edit, atomic + dry-run aware (the
    house table-writer law); never raises. Returns rows retired."""
    log = log or (lambda *a, **k: None)
    from gitcurator.core.website_pipeline import (
        decommission_table_path, _parse_decommission_rows,
        normalize_website_url, _status_is_dead, _status_is_reviewed,
        _status_is_revived)
    path = decommission_table_path(vault_path)
    if not vault_path or not os.path.isfile(path):
        return 0
    wanted = set()
    for u in (urls or []):
        try:
            c = normalize_website_url(u)
            if c:
                wanted.add(c)
        except Exception:
            wanted.add(str(u or ''))
    if not wanted:
        return 0
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.read().splitlines()
        rows = _parse_decommission_rows(path)
    except Exception as e:
        log(f"⚠️ Hand-harvest stamp skipped — the table could not be "
            f"read: {e}", "warning")
        return 0
    date_str = time.strftime('%Y-%m-%d')
    stamped = 0
    for row in rows:
        try:
            canonical = normalize_website_url(row['url'])
        except Exception:
            canonical = row['url']
        if canonical not in wanted and row['url'] not in wanted:
            continue
        s = row['status']
        if _status_is_dead(s) or _status_is_reviewed(s) \
                or _status_is_revived(s):
            continue    # a stronger verdict owns the cell — never touched
        if not _status_is_hand(s):
            continue    # not the gesture — the waiting family's writers
                        # ('📁 stored', stamp_stored_rows) own that row
        parts = row['raw'].split('|')
        if len(parts) < 8:
            continue
        parts[6] = f" ✅ hand-delivered — fetched {date_str} "
        lines[row['line']] = '|'.join(parts)
        stamped += 1
    if not stamped:
        return 0
    now = time.strftime('%Y-%m-%d %H:%M')
    lines = [f"> Last updated: {now}"
             if line.startswith('> Last updated:') else line
             for line in lines]
    try:
        atomic_write_text(path, '\n'.join(lines).rstrip('\n') + '\n')
    except Exception as e:
        log(f"⚠️ Hand-harvest stamp could not be written: {e}",
            "warning")
        return 0
    log(f"✅ {stamped} hand row(s) retired to the green checkbox "
        f"(✅ hand-delivered) — their proper, categorized notes are "
        f"in the vault", "info")
    return stamped


__all__ = [
    'HAND_FOLDER_NAME', 'QUEUE_FILENAME', 'README_FILENAME',
    'HAND_MARKERS', 'WALL_MARKERS',
    'hand_delivery_dir', 'queue_path', 'readme_path',
    'is_walled_reason', 'walled_retry_rows', 'suggested_filename',
    'enqueue_hand_delivery', 'find_chrome', 'open_in_chrome',
    'collect_delivered', 'consume_delivered', 'hand_fetch_result',
    'take_hand_delivered', 'pending_hand_links', '_status_is_hand',
    'stamp_hand_rows', 'stamp_hand_delivered_rows',
]
