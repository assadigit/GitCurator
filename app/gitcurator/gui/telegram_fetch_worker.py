#!/usr/bin/env python3
"""
Telegram Fetch Worker - runs in a SEPARATE PROCESS (like test.py).

Reads JSON config from stdin, does the Telegram fetch, writes JSON result
to stdout. Progress logs go to stderr (streamed to GUI log).

INTERACTIVE AUTH SUPPORT:
  If the session is invalid/expired, the worker prints __NEED_CODE__ to
  stderr, then blocks reading stdin until the parent process sends the
  login code. Same for 2FA password with __NEED_PASSWORD__.
"""

import sys
import os
import json
import asyncio
import re
import socket

# v30 — Fix (Standardize link parsing): import the SINGLE source of truth
# for the GitHub/non-GitHub link regexes from links.py (same directory).
# The local copies were identical to the most permissive dialect, so this
# is a pure de-duplication — no behavior change, one implementation.
_APP_DIR = os.path.dirname(os.path.abspath(__file__))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)
# v32 — package bootstrap: this script runs as a subprocess (or
# directly), so put the app/ root back on sys.path for `gitcurator.*`.
import os as _os, sys as _sys
_PKG_ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
if _PKG_ROOT not in _sys.path:
    _sys.path.insert(0, _PKG_ROOT)
from gitcurator.core import links as _links

__VERSION__ = "3.4 (v0.07.1: proxy pre-flight, own connect-retry policy with DC rotation, session-copy tip, actionable network errors)"
print(f"[telegram_fetch_worker] version {__VERSION__}", file=sys.stderr, flush=True)

from telethon import TelegramClient, errors
import socks


def build_proxy(proxy_config):
    """Build a proxy 5-tuple exactly like test.py."""
    print(f"[worker] build_proxy received: {proxy_config}", file=sys.stderr, flush=True)
    if not proxy_config:
        return None
    if not proxy_config.get('enabled'):
        print(f"[worker] build_proxy: enabled={proxy_config.get('enabled')!r} -> None", file=sys.stderr, flush=True)
        return None
    ptype_str = str(proxy_config.get('type', 'socks5')).lower()
    ptype = {
        'socks5': socks.SOCKS5,
        'socks4': socks.SOCKS4,
        'http': socks.HTTP,
    }.get(ptype_str, socks.SOCKS5)
    host = proxy_config.get('host', '127.0.0.1')
    port = int(proxy_config.get('port', 10808))
    username = proxy_config.get('username') or ''
    password = proxy_config.get('password') or ''
    result = (ptype, host, port, username, password)
    print(f"[worker] build_proxy: returning {result}", file=sys.stderr, flush=True)
    return result


def read_code_from_stdin(prompt_type="CODE"):
    """Signal the parent process that we need a code/password, then block
    reading stdin until the parent sends it.

    Protocol:
      1. Print __NEED_CODE__ or __NEED_PASSWORD__ to stderr (flushed)
      2. Block on sys.stdin.readline()
      3. Return the stripped line (or "" if stdin was closed)
    """
    marker = f"__NEED_{prompt_type}__"
    print(marker, file=sys.stderr, flush=True)
    sys.stderr.flush()
    try:
        line = sys.stdin.readline()
        if not line:
            # stdin closed (parent died or cancelled)
            return ""
        return line.strip()
    except Exception:
        return ""


# v0.07.1 — alternate Telegram DCs for retry rotation. ONLY used when the
# session file does not exist yet (auth keys are DC-bound — rotating an
# authorized session would invalidate it). DC4/DC1 are Telegram's public
# production endpoints (the default first attempt is DC2).
_ALT_DCS = [
    (4, '149.154.167.91', 443),
    (1, '149.154.175.53', 443),
]


def _make_client(config, session_file, proxy):
    """One construction site for the TelegramClient (v0.07.1).

    connection_retries is deliberately LOW (2, delay 2s): v0.07.1 owns the
    retry policy in _connect_with_retries() — 3 attempts with escalating
    sleeps, clear logging and DC rotation — instead of Telethon's opaque
    internal loop that cannot be seen or cancelled.
    """
    return TelegramClient(session_file, int(config['api_id']),
                          config['api_hash'], proxy=proxy,
                          timeout=45, connection_retries=2,
                          retry_delay=2, request_retries=3)


async def _connect_with_retries(client, session_file):
    """Connect with OUR retry policy (v0.07.1).

    Why: Telethon's internal connection_retries only retries
    ConnectionError subclasses. An IncompleteReadError ("Server closed
    the connection: 0 bytes read on a total of 8 expected bytes") — the
    classic DPI / exit-node reset on censored networks — aborted
    immediately with ZERO retries. Now: 3 attempts, 3s/6s sleeps, an
    alternate DC on attempts 2-3 when the session is fresh, and a plain
    hint on what a reset usually means.
    """
    fresh_session = not os.path.exists(session_file + '.session')
    last_err = None
    for attempt in (1, 2, 3):
        if attempt > 1 and fresh_session and _ALT_DCS:
            dc_id, dc_addr, dc_port = _ALT_DCS[(attempt - 2) % len(_ALT_DCS)]
            try:
                client.session.set_dc(dc_id, dc_addr, dc_port)
                print(f"[worker] Retrying on alternate DC{dc_id} ({dc_addr}:{dc_port})...",
                      file=sys.stderr, flush=True)
            except Exception:
                pass  # telethon without set_dc: retry on the default DC
        try:
            await asyncio.wait_for(client.connect(), timeout=90)
            print("[worker] Connected to Telegram server.", file=sys.stderr, flush=True)
            return
        except asyncio.TimeoutError:
            last_err = RuntimeError(
                "Telegram connect timed out after 90s — check the proxy (v2rayN) "
                "and try again.")
            print(f"[worker] Connect attempt {attempt}/3 timed out (90s).",
                  file=sys.stderr, flush=True)
        except Exception as e:
            last_err = e
            ename = type(e).__name__
            print(f"[worker] Connect attempt {attempt}/3 failed: {ename}: {e}",
                  file=sys.stderr, flush=True)
            if 'IncompleteRead' in ename or 'ConnectionError' in ename \
                    or 'Reset' in ename:
                print("[worker] 💡 Telegram closed the connection — in censored "
                      "networks this is usually the proxy EXIT NODE being blocked. "
                      "Try a different v2ray/xray node.", file=sys.stderr, flush=True)
        if attempt < 3:
            await asyncio.sleep(3 * attempt)  # 3s, then 6s
    raise last_err


async def main(config):
    proxy = build_proxy(config.get('proxy'))
    session_file = config.get('session_file', 'session')
    phone = config['phone']

    print(f"[worker] Proxy: {proxy}", file=sys.stderr, flush=True)
    print(f"[worker] Session: {session_file}", file=sys.stderr, flush=True)

    # v0.07.1 — pre-flight the proxy BEFORE touching Telegram. A refused
    # local SOCKS port (v2rayN not running) used to burn 4 cryptic
    # "ConnectionRefusedError [WinError 1225]" retries before failing; now
    # it fails in 2 seconds with an actionable message. Never silently fall
    # back to a direct connection: it is blocked in censored regions and it
    # leaks Telegram usage to the ISP.
    _px = config.get('proxy') or {}
    if _px.get('enabled'):
        _h, _p = str(_px.get('host', '127.0.0.1')), int(_px.get('port', 10808))
        try:
            _s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            _s.settimeout(2)
            _s.connect((_h, _p))
            _s.close()
            print(f"[worker] Proxy pre-flight OK ({_h}:{_p}).",
                  file=sys.stderr, flush=True)
        except OSError as e:
            print(f"[worker] ❌ Proxy at {_h}:{_p} is unreachable "
                  f"({type(e).__name__}). Is v2rayN running? Aborting BEFORE the "
                  f"Telegram attempt — a direct connection is blocked and would "
                  f"leak Telegram usage to your ISP.", file=sys.stderr, flush=True)
            raise RuntimeError(
                f"Proxy at {_h}:{_p} is unreachable — start v2rayN (or fix the "
                f"proxy host/port in Settings → Proxy) and try again.")
    else:
        print("[worker] ⚠️ Proxy is DISABLED — attempting a DIRECT connection. "
              "Direct Telegram access is blocked in Iran; enable the proxy in "
              "Settings → Proxy.", file=sys.stderr, flush=True)

    # Remember whether a session file existed at start (for the login tip).
    had_session_file = os.path.exists(session_file + '.session')

    # ---- Explicit auth flow (more robust than client.start) ----
    # client.start() is opaque — it calls send_code_request internally but
    # we can't see if it succeeded. This explicit flow logs each step so
    # we know exactly where it fails.
    #
    # CRITICAL: is_user_authorized() only checks the LOCAL session file —
    # it returns True even if the session is expired server-side. So we
    # ALSO call get_me() to verify the session is actually valid. If get_me()
    # fails, we delete the stale session and do fresh auth.
    #
    # v0.06 — Fix (zero network timeouts): timeout=45 bounds every Telegram
    # request (connect, get_me, send_code, message fetches). Previously a
    # dead SOCKS proxy could park this child forever, which — combined with
    # the parent's unbounded stderr reader — stuck the GUI's Telegram lock
    # permanently (the "another operation is already running" forever-bug).
    # The parent (subprocess_runner) now also enforces idle/hard deadlines
    # as a second safety net.
    client = _make_client(config, session_file, proxy)
    await _connect_with_retries(client, session_file)

    # Check if the existing session is still valid (locally)
    try:
        authorized = await client.is_user_authorized()
        print(f"[worker] is_user_authorized() = {authorized}", file=sys.stderr, flush=True)
    except Exception as e:
        print(f"[worker] Session check failed ({e}). Session may be corrupted.", file=sys.stderr, flush=True)
        authorized = False

    # If locally authorized, verify with the SERVER by calling get_me()
    if authorized:
        print("[worker] Verifying session with Telegram server (get_me)...", file=sys.stderr, flush=True)
        try:
            me = await client.get_me()
            print(f"[worker] ✅ Session is valid server-side! Logged in as: "
                  f"{getattr(me, 'first_name', '?')} (@{getattr(me, 'username', None)})",
                  file=sys.stderr, flush=True)
        except Exception as e:
            print(f"[worker] ❌ Session is EXPIRED server-side ({e}).", file=sys.stderr, flush=True)
            print(f"[worker] Deleting stale session and doing fresh auth...", file=sys.stderr, flush=True)
            authorized = False
            await client.disconnect()
            session_path = session_file + '.session'
            try:
                os.remove(session_path)
                print(f"[worker] Deleted stale session: {session_path}", file=sys.stderr, flush=True)
            except OSError:
                pass
            # Reconnect with fresh session (v0.07.1 — shared client factory
            # + our own retry policy, same as the first connect)
            client = _make_client(config, session_file, proxy)
            await _connect_with_retries(client, session_file)
            print("[worker] Reconnected with fresh session.", file=sys.stderr, flush=True)

    if authorized:
        print("[worker] Session is valid. Already authorized.", file=sys.stderr, flush=True)
    else:
        if not had_session_file:
            print("[worker] 💡 No session file in this app folder. If you already "
                  "logged in with a previous GitCurator version on this machine, copy "
                  "its 'session.session' file into this app folder to skip the login "
                  "code entirely.", file=sys.stderr, flush=True)
        print(f"[worker] Session invalid or missing. Sending login code to {phone}...",
              file=sys.stderr, flush=True)
        try:
            sent_code = await client.send_code_request(phone)
            # Log the FULL sent_code object so we can see exactly what
            # Telegram returned (type, phone_code_hash, next_type, timeout).
            print(f"[worker] send_code_request() returned: {sent_code}", file=sys.stderr, flush=True)

            code_type = getattr(sent_code, 'type', 'unknown')
            if hasattr(code_type, 'name'):
                code_type = code_type.name
            print(f"[worker] ✅ Login code sent via {code_type}!",
                  file=sys.stderr, flush=True)
            print(f"[worker] 📱 CHECK YOUR TELEGRAM APP: Look for a 'Login Code' "
                  f"notification at the TOP of your chat list (it appears as a "
                  f"special system notification, NOT in Saved Messages).",
                  file=sys.stderr, flush=True)
            print(f"[worker] 📱 If you don't see it in 30 seconds, check SMS on "
                  f"your phone ({phone}).", file=sys.stderr, flush=True)
            print(f"[worker] 📱 Also check ANY OTHER DEVICE logged into your "
                  f"Telegram account — Telegram may deliver the code there.",
                  file=sys.stderr, flush=True)
        except errors.FloodWaitError as e:
            print(f"[worker] ❌ Telegram rate-limited: wait {e.seconds}s before retrying.",
                  file=sys.stderr, flush=True)
            raise
        except Exception as e:
            print(f"[worker] ❌ Failed to send code: {type(e).__name__}: {e}",
                  file=sys.stderr, flush=True)
            if "121" in str(e) or "timeout" in str(e).lower():
                print(f"[worker] 💡 TIP: This looks like a proxy issue. Check that v2rayN is running and port 10808 is listening.", file=sys.stderr, flush=True)
            raise

        # Ask the GUI for the code
        print("[worker] Waiting for code from GUI...", file=sys.stderr, flush=True)
        code = read_code_from_stdin("CODE")
        if not code:
            raise RuntimeError("Login code was not provided (cancelled or timed out).")

        print("[worker] Code received. Signing in...", file=sys.stderr, flush=True)
        try:
            await client.sign_in(phone=phone, code=code)
        except errors.SessionPasswordNeededError:
            print("[worker] 2FA password required. Requesting from GUI...",
                  file=sys.stderr, flush=True)
            password = read_code_from_stdin("PASSWORD")
            if not password:
                raise RuntimeError("2FA password was not provided.")
            await client.sign_in(password=password)
        except errors.PhoneCodeInvalidError:
            raise RuntimeError("Invalid login code. Please try again.")
        except errors.PhoneCodeExpiredError:
            raise RuntimeError("Login code expired. Please request a new one.")

    print("[worker] Connected and authenticated.", file=sys.stderr, flush=True)

    try:
        me = await client.get_me()
        dialog = await client.get_input_entity(me.id)

        # ---- Keyword/marker search mode ----
        # User edits messages in Saved Messages containing start/end keywords
        # (or a single marker hash pasted into two messages).
        # We search all messages for those keywords and return the IDs.
        #
        # SPECIAL CASE: if start_kw == end_kw (same marker hash), we find
        # the FIRST occurrence for start, then continue searching to find
        # the SECOND occurrence for end. This is the "marker" workflow.
        if config.get('keyword_search'):
            start_kw = config.get('keyword_start', '').lower()
            end_kw = config.get('keyword_end', '').lower()
            same_marker = (start_kw == end_kw and start_kw != '')
            print(f"[worker] Searching Saved Messages for keywords: "
                  f"start='{start_kw}' end='{end_kw}'"
                  f"{' (same marker - finding 1st & 2nd occurrence)' if same_marker else ''}",
                  file=sys.stderr, flush=True)

            start_id = None
            end_id = None
            start_msg_preview = None
            end_msg_preview = None
            # When using the same marker for start and end, track how many
            # times we've seen it so we can grab the 2nd occurrence for end.
            marker_match_count = 0

            # Iterate ALL messages (limit=None) and search for keywords.
            # iter_messages returns newest first by default, so the "first"
            # occurrence we find is actually the NEWEST message containing
            # the marker. For a range, the user pastes the marker into the
            # first (oldest) and last (newest) messages — so we need to
            # collect ALL matches and then pick oldest=start, newest=end.
            all_start_matches = []
            all_end_matches = []

            search_count = 0
            async for msg in client.iter_messages(dialog, limit=None):
                search_count += 1
                if search_count % 500 == 0:
                    print(f"[worker] Searched {search_count} messages so far...",
                          file=sys.stderr, flush=True)
                if not msg.text:
                    continue
                text_lower = msg.text.lower()

                if same_marker:
                    # Same marker: collect ALL matches
                    if start_kw in text_lower:
                        all_start_matches.append((msg.id, msg.text[:100]))
                        marker_match_count += 1
                        if marker_match_count <= 5:
                            print(f"[worker] Found marker in message ID {msg.id}",
                                  file=sys.stderr, flush=True)
                else:
                    # Different keywords: find first match for each
                    if start_id is None and start_kw and start_kw in text_lower:
                        start_id = msg.id
                        start_msg_preview = msg.text[:100]
                        print(f"[worker] ✅ Found start keyword in message ID {start_id}: "
                              f"\"{start_msg_preview}\"", file=sys.stderr, flush=True)
                    if end_id is None and end_kw and end_kw in text_lower:
                        end_id = msg.id
                        end_msg_preview = msg.text[:100]
                        print(f"[worker] ✅ Found end keyword in message ID {end_id}: "
                              f"\"{end_msg_preview}\"", file=sys.stderr, flush=True)
                    if start_id is not None and end_id is not None:
                        break  # found both, stop searching

            print(f"[worker] Search complete. Searched {search_count} messages.",
                  file=sys.stderr, flush=True)

            # For same-marker mode: pick the OLDEST match as start and the
            # NEWEST match as end. iter_messages returns newest-first, so
            # all_start_matches[0] is newest and all_start_matches[-1] is oldest.
            if same_marker:
                print(f"[worker] Marker found in {len(all_start_matches)} message(s).",
                      file=sys.stderr, flush=True)
                if len(all_start_matches) >= 1:
                    # Oldest = last in the list (iter_messages is newest-first)
                    start_id, start_msg_preview = all_start_matches[-1]
                    print(f"[worker] ✅ Start (oldest occurrence): message ID {start_id}",
                          file=sys.stderr, flush=True)
                if len(all_start_matches) >= 2:
                    # Newest = first in the list
                    end_id, end_msg_preview = all_start_matches[0]
                    print(f"[worker] ✅ End (newest occurrence): message ID {end_id}",
                          file=sys.stderr, flush=True)
                elif len(all_start_matches) == 1:
                    print(f"[worker] ℹ️ Only one occurrence found (no end marker).",
                          file=sys.stderr, flush=True)

            if start_id is None and start_kw:
                print(f"[worker] ❌ Start keyword '{start_kw}' not found in any message.",
                      file=sys.stderr, flush=True)
            if end_id is None and end_kw and not same_marker:
                print(f"[worker] ❌ End keyword '{end_kw}' not found in any message.",
                      file=sys.stderr, flush=True)

            return {
                "success": True,
                "keyword_search": True,
                "start_id": start_id,
                "end_id": end_id,
                "start_preview": start_msg_preview,
                "end_preview": end_msg_preview,
                "searched_count": search_count,
                "match_count": len(all_start_matches) if same_marker else None,
            }

        # ---- Bot Queue mode ----
        # Read messages from the user's dedicated bot chat.
        # The user forwards GitHub repo messages to the bot; we read them here.
        # Uses Telethon (through proxy) to resolve the bot by username —
        # NO Bot API call needed (api.telegram.org is blocked in Iran).
        if config.get('bot_queue'):
            bot_username = config.get('bot_username', '')
            mark_read = config.get('mark_read', False)
            # v25 pre-flight: when set, only fetch messages with id > min_id.
            # Used by the "📬 Process New" button to skip messages that
            # were already processed in a previous run. 0 means "fetch all"
            # (the default "Check Queue" behaviour).
            min_id = int(config.get('min_id', 0) or 0)

            print(f"[worker] Bot queue mode — fetching messages from @{bot_username}"
                  f"{f' (min_id={min_id})' if min_id > 0 else ''}",
                  file=sys.stderr, flush=True)

            if not bot_username:
                return {"success": False, "error": "No bot username provided"}

            # Resolve the bot entity via Telethon (goes through the proxy)
            try:
                bot_entity = await client.get_entity(bot_username)
                print(f"[worker] ✅ Found bot: @{getattr(bot_entity, 'username', bot_username)}", file=sys.stderr, flush=True)
            except Exception as e:
                print(f"[worker] ❌ Could not find bot @{bot_username}: {e}", file=sys.stderr, flush=True)
                print(f"[worker] 💡 Send /start to your bot first, then try again.", file=sys.stderr, flush=True)
                return {"success": False, "error": f"Bot chat not found: {e}. Send /start to your bot first."}

            # Read ALL messages from the bot chat (no limit cap).
            # v25.1 fix: previously limit=100, which silently dropped messages
            # when the user had 200+ links in the bot. Now we iterate in
            # batches of 200 until no more messages are found.
            all_messages = []

            # v0.06 — progress heartbeat every 500 messages: the parent's
            # subprocess runner kills children that stay silent too long
            # (idle timeout). Without this heartbeat a large bot chat could
            # be murdered mid-fetch even though it was healthy.
            fetch_count = 0
            def _heartbeat():
                if fetch_count % 500 == 0 and fetch_count > 0:
                    print(f"[worker] Fetched {fetch_count} messages so far...",
                          file=sys.stderr, flush=True)

            if min_id > 0:
                # Only fetch messages newer than min_id
                print(f"[worker] Fetching messages newer than ID {min_id}...", file=sys.stderr, flush=True)
                async for msg in client.iter_messages(bot_entity, limit=None, min_id=min_id):
                    fetch_count += 1
                    _heartbeat()
                    all_messages.append(msg)
            else:
                # Fetch ALL messages (no limit)
                print(f"[worker] Fetching ALL messages from bot (no limit)...", file=sys.stderr, flush=True)
                async for msg in client.iter_messages(bot_entity, limit=None):
                    fetch_count += 1
                    _heartbeat()
                    all_messages.append(msg)

            messages = all_messages
            print(f"[worker] Found {len(messages)} total messages in bot chat",
                  file=sys.stderr, flush=True)

            # v26 — Fix 3: Telethon's iter_messages returns messages newest-
            # first by default. We want the LAST forwarded link to be the
            # LAST processed, so reverse the list to oldest-first BEFORE
            # extracting URLs. The URL list then comes out oldest-first and
            # the processing loop processes them in that order.
            # NOTE: callers in main.py (check_bot_queue, process_new_bot_queue,
            # export_all_bot_links) MUST NOT re-reverse the URL list — that
            # would double-reverse back to newest-first.
            messages.reverse()

            # Extract GitHub URLs + non-GitHub links.
            # v25 pre-flight (Feature 7): track raw link count vs unique
            # count so the GUI can show "🔄 N duplicate URL(s) removed".
            # v0.06 — Perf: membership checks now use SETS alongside the
            # ordered lists. The old `x not in list` checks were O(n²) — a
            # 5,000-link bot chat meant ~25M string comparisons per fetch.
            # Output order/content is byte-identical; only the cost changed.
            urls = []
            non_github_urls = []
            _seen_github = set()
            _seen_links = set()
            raw_link_count = 0
            # GitHub URL pattern — v30: delegated to links.py (single source
            # of truth; allows www + dots in owner/repo names).
            github_pattern = _links.GITHUB_URL_PATTERN
            all_links_pattern = _links.ALL_LINKS_PATTERN

            for msg in messages:
                if msg.text:
                    # First, find all links and add to non_github_urls
                    for link in all_links_pattern.findall(msg.text):
                        raw_link_count += 1
                        link = link.rstrip('.,);')
                        if link not in _seen_links and link not in _seen_github:
                            _seen_links.add(link)
                            non_github_urls.append(link)
                    # Then, find GitHub URLs and move them from non_github to github
                    for owner, repo in github_pattern.findall(msg.text):
                        # Don't double-count if we already counted this as a raw link
                        url = f"https://github.com/{owner}/{repo}"
                        if url not in _seen_github:
                            _seen_github.add(url)
                            urls.append(url)

            # Remove GitHub URLs from the non-github list
            # Also check for partial matches (github.com/owner/repo/issues → github.com/owner/repo)
            # v0.06 — Perf: the old inner `for gu in urls` startswith loop was
            # O(N×G). Exact-match goes through the set; anything containing
            # github.com falls through to owner/repo extraction, which covers
            # the old prefix behavior AND fixes its prefix-collision quirk
            # (owner/repository was wrongly treated as a path of owner/repo;
            # it now registers as its own repo).
            filtered_non_github = []
            for u in non_github_urls:
                is_github = u in _seen_github
                # Also check if it's a github.com URL we might have missed
                if not is_github and 'github.com' in u:
                    # Try to extract owner/repo from this URL
                    match = github_pattern.search(u)
                    if match:
                        owner, repo = match.group(1), match.group(2)
                        constructed = f"https://github.com/{owner}/{repo}"
                        if constructed not in _seen_github:
                            _seen_github.add(constructed)
                            urls.append(constructed)
                        is_github = True
                if not is_github:
                    filtered_non_github.append(u)

            non_github_urls = filtered_non_github
            _seen_github.clear()
            _seen_links.clear()
            unique_count = len(urls) + len(non_github_urls)
            duplicates_removed = max(0, raw_link_count - unique_count)
            print(f"[worker] ✅ Found {len(urls)} GitHub URLs + {len(non_github_urls)} non-GitHub "
                  f"in bot queue (raw={raw_link_count}, duplicates removed={duplicates_removed})",
                  file=sys.stderr, flush=True)

            # Mark messages as read if requested
            if mark_read:
                try:
                    await client.send_read_acknowledge(bot_entity)
                    print(f"[worker] ✅ Marked all bot messages as read", file=sys.stderr, flush=True)
                except Exception as e:
                    print(f"[worker] ⚠️ Failed to mark as read: {e}", file=sys.stderr, flush=True)

            # Get the max message ID (for selective read marking + min_id
            # tracking on the next "Process New" run)
            max_msg_id = max((m.id for m in messages), default=0)
            # If we used min_id and got no new messages, keep the previous
            # max_msg_id so the next "Process New" run still skips the same
            # already-processed range.
            if min_id > 0 and max_msg_id == 0:
                max_msg_id = min_id

            return {
                "success": True,
                "urls": urls,
                "non_github_urls": non_github_urls,
                "total_messages": len(messages),
                "bot_queue": True,
                "max_message_id": max_msg_id,
                # v25 pre-flight: duplicate tracking for the GUI display
                # and final report.
                "raw_url_count": raw_link_count,
                "duplicates_removed": duplicates_removed,
                "min_id_used": min_id,
            }

        if config.get('preview_only'):
            from_id = int(config['from_id'])
            to_id = int(config['to_id'])
            if from_id > to_id:
                from_id, to_id = to_id, from_id
            messages = await client.get_messages(
                dialog, min_id=from_id, max_id=to_id, reverse=True
            )
            total = len(messages)
            preview_count = int(config.get('preview_count', 2))
            first_msgs = messages[:min(preview_count, total)]
            last_msgs = messages[max(0, total - preview_count):]

            def to_dict(m):
                return {
                    "id": m.id,
                    "date": m.date.isoformat() if m.date else None,
                    "text": (m.text[:200] + "..." if len(m.text or "") > 200
                             else (m.text or "")),
                    "has_links": bool(re.search(r'github\.com', m.text or '')),
                }

            return {
                "success": True,
                "preview": {
                    "first": [to_dict(m) for m in first_msgs],
                    "last": [to_dict(m) for m in last_msgs],
                    "total_count": total,
                },
            }
        else:
            if config.get('offset_start') is not None:
                messages = await client.get_messages(
                    dialog,
                    offset_id=int(config['offset_start']),
                    add_offset=0,
                    limit=int(config['count']),
                )
                messages.reverse()
            else:
                from_id = int(config['from_id'])
                to_id = int(config['to_id'])
                if from_id > to_id:
                    from_id, to_id = to_id, from_id
                messages = await client.get_messages(
                    dialog, min_id=from_id, max_id=to_id, reverse=True
                )

            urls = []
            non_github_urls = []  # capture ALL links, then separate
            # v0.06 — Perf: set-backed dedup (same O(n²)→O(n) fix as the
            # bot-queue block above; output is byte-identical).
            _seen_github = set()
            _seen_links = set()
            # v30 — delegated to links.py (single source of truth)
            github_pattern = _links.GITHUB_URL_PATTERN
            all_links_pattern = _links.ALL_LINKS_PATTERN
            for msg in messages:
                if msg.text:
                    # Find ALL links first
                    for link in all_links_pattern.findall(msg.text):
                        link = link.rstrip('.,);')  # clean trailing punctuation
                        if link not in _seen_links and link not in _seen_github:
                            _seen_links.add(link)
                            non_github_urls.append(link)
                    # Separate GitHub links
                    for owner, repo in github_pattern.findall(msg.text):
                        url = f"https://github.com/{owner}/{repo}"
                        if url not in _seen_github:
                            _seen_github.add(url)
                            urls.append(url)

            # Remove GitHub URLs from the non-github list (with partial match support)
            filtered_non_github = []
            for u in non_github_urls:
                is_github = u in _seen_github
                if not is_github and 'github.com' in u:
                    match = github_pattern.search(u)
                    if match:
                        owner, repo = match.group(1), match.group(2)
                        constructed = f"https://github.com/{owner}/{repo}"
                        if constructed not in _seen_github:
                            _seen_github.add(constructed)
                            urls.append(constructed)
                        is_github = True
                if not is_github:
                    filtered_non_github.append(u)
            non_github_urls = filtered_non_github
            _seen_github.clear()
            _seen_links.clear()

            return {
                "success": True,
                "urls": urls,
                "non_github_urls": non_github_urls,
                "total_messages": len(messages),
            }
    finally:
        await client.disconnect()
        print("[worker] Disconnected.", file=sys.stderr, flush=True)


if __name__ == '__main__':
    # Read JSON config from stdin
    try:
        # IMPORTANT: use readline(), NOT read(). The GUI keeps stdin open so it
        # can send the login code later, so read() would block forever waiting
        # for EOF. readline() returns as soon as it sees a newline, which the
        # GUI sends after the JSON config.
        config_line = sys.stdin.readline()
        if not config_line:
            print(json.dumps({"success": False, "error": "Empty stdin (no config received)"}))
            sys.stdout.flush()
            sys.exit(1)
        config = json.loads(config_line)
        print(f"[worker] Config received: mode={'preview' if config.get('preview_only') else 'fetch'}",
              file=sys.stderr, flush=True)
    except Exception as e:
        print(json.dumps({"success": False, "error": f"Config parse error: {e}"}))
        sys.stdout.flush()
        sys.exit(1)

    try:
        result = asyncio.run(main(config))
        sys.stdout.write(json.dumps(result, default=str))
        sys.stdout.flush()
    except Exception as e:
        # v0.07.1 — translate cryptic network errors into actionable text
        # for the GUI log (the GUI shows this string verbatim).
        err = f"{type(e).__name__}: {e}"
        ename = type(e).__name__
        if 'IncompleteRead' in ename:
            err += (" — Telegram closed the connection through your proxy "
                    "(exit node blocked / DPI). Try a different v2ray node.")
        sys.stdout.write(json.dumps({
            "success": False,
            "error": err,
        }))
        sys.stdout.flush()
        sys.exit(1)
