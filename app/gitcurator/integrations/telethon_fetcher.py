#!/usr/bin/env python3
"""
Telethon Fetcher Module
Version: 3.3 - adds VERSION stamp printed at import time so you can verify
                 the correct file is actually being loaded (not a stale cache).

============================================================================
WHY v3.0 STILL FAILED (and test.py works)
============================================================================
v3.0 differed from test.py in THREE ways, each of which can independently
trigger "OSError: [WinError 121] The semaphore timeout period has expired":

1. PROXY FORMAT
   test.py  ->  proxy = (socks.SOCKS5, '127.0.0.1', 10808, '', '')  # tuple
   v3.0     ->  proxy = {'proxy_type':..., 'addr':..., 'rdns':False, ...}  # dict

   Telethon accepts both, but forwards them to PySocks differently. The
   tuple path is the one test.py exercises, so it's the one we know works
   with v2rayN's local SOCKS5 inbound.

2. EXTRA TelegramClient KWARGS
   test.py  ->  TelegramClient('session', api_id, api_hash, proxy=proxy)
   v3.0     ->  TelegramClient(..., connection_retries=5, retry_delay=2, timeout=60)

   Passing timeout=60 changes Telethon's socket timeout behaviour and can
   interact badly with proxied first-hop auth on slow links.

3. EVENT LOOP LIFECYCLE  (the most likely culprit)
   test.py  ->  asyncio.run(main())          # FRESH loop, cancels all tasks on exit
   v3.0     ->  loop.run_until_complete(...)  # REUSED loop, leaves tasks dangling

   Telethon spawns background asyncio tasks (ping, keep-alive, update handler).
   When you reuse a loop across calls, those tasks from the PREVIOUS call are
   still alive and can corrupt the NEXT connection attempt -> WinError 121.
   asyncio.run() guarantees a clean slate every time.

v3.1 FIX: make the connection path identical to test.py.
   - proxy is a 5-tuple with empty strings (exactly like test.py)
   - TelegramClient gets NO extra kwargs (exactly like test.py)
   - each call uses asyncio.run() for a fresh clean loop (exactly like test.py)

v3.2 FIX: added session-file diagnostics + generous timeout + clear error
          if session is missing (so it doesn't hang on input() in a QThread).

v3.3 FIX: prints VERSION at import so you can verify the correct file is
          loaded (defensive against stale __pycache__).
============================================================================
"""

# === VERSION STAMP - printed at import so you can verify the right file loads ===
__VERSION__ = "3.4"
import sys as _sys
print(f"[telethon_fetcher] LOADED version {__VERSION__} from {__file__}", file=_sys.stderr, flush=True)
# === END VERSION STAMP ===

import os
import re
import asyncio
import sys
import logging
from typing import List, Dict, Optional, Any, Callable

# v30 — Fix (Standardize link parsing): the canonical GitHub URL pattern now
# lives in links.py. The old local copy (no-www, no-dots) silently dropped
# valid repos like github.com/john.doe/my.project — divergent from the fetch
# worker's pattern, so the same message yielded different link sets.
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

from telethon import TelegramClient, errors
from telethon.tl.types import Message
import socks  # required for proxy support

# ============================================================================
# Configuration
# ============================================================================

# IMPORTANT: must match test.py so the already-authenticated session is reused.
SESSION_FILE = "session"
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds


class TelegramFetcherError(Exception):
    pass


# ============================================================================
# Main Fetcher Class
# ============================================================================

class TelegramFetcher:
    def __init__(self, api_id: int, api_hash: str, phone: str,
                 proxy: Optional[Dict[str, Any]] = None,
                 session_file: str = SESSION_FILE,
                 code_callback: Optional[Callable[[], str]] = None,
                 logger: Optional[logging.Logger] = None):
        self.api_id = api_id
        self.api_hash = api_hash
        self.phone = phone
        self.proxy = proxy or {}
        self.session_file = session_file or SESSION_FILE
        self.code_callback = code_callback
        self.logger = logger or logging.getLogger(__name__)
        self.client: Optional[TelegramClient] = None
        self._connected = False

    # ------------------------------------------------------------------ proxy
    def _build_proxy_tuple(self) -> Optional[tuple]:
        """Build a proxy 5-tuple EXACTLY like test.py:
               (socks.SOCKS5, '127.0.0.1', 10808, '', '')
        The 4th element (empty string) becomes `rdns` inside PySocks and is
        falsy -> rdns=False -> DNS resolved locally. This is the exact path
        test.py exercises, so it's the one we know works with v2rayN.
        """
        if not self.proxy or not self.proxy.get('enabled', False):
            return None

        ptype_str = str(self.proxy.get('type', 'socks5')).lower()
        ptype = {
            'socks5': socks.SOCKS5,
            'socks4': socks.SOCKS4,
            'http': socks.HTTP,
        }.get(ptype_str, socks.SOCKS5)

        host = self.proxy.get('host', '127.0.0.1')
        port = int(self.proxy.get('port', 10808))
        # Empty strings (NOT None) for username/password - exactly like test.py.
        # The empty string is what makes rdns falsy in PySocks' positional args.
        username = self.proxy.get('username') or ''
        password = self.proxy.get('password') or ''

        proxy_tuple = (ptype, host, port, username, password)
        self.logger.info(
            f"Proxy tuple (matches test.py): type={ptype_str} host={host} "
            f"port={port} user={username!r}"
        )
        return proxy_tuple

    # --------------------------------------------------------------- connect
    async def connect(self) -> bool:
        """Connect + authenticate using client.start().

        Session handling matches test.py (same session file name 'session').
        We add a generous timeout and connection_retries because proxied
        first-hop connections from a QThread can be slower than from the
        main thread (test.py). We also check the session file exists so we
        can give a clear error instead of a mysterious TimeoutError when
        the session is missing and client.start() tries to do fresh auth
        (which needs a code_callback the GUI doesn't provide).
        """
        proxy_tuple = self._build_proxy_tuple()
        last_err: Optional[Exception] = None

        # ---- DIAGNOSTIC: log session file path + existence ----
        session_abs = os.path.abspath(self.session_file)
        session_file_path = self.session_file + '.session'  # Telethon appends .session
        session_abs_full = os.path.abspath(session_file_path)
        session_exists = os.path.isfile(session_abs_full)
        self.logger.info(
            f"Session file: {session_abs_full} (exists={session_exists})"
        )
        if not session_exists:
            self.logger.warning(
                f"Session file NOT FOUND at {session_abs_full}. "
                f"Run test.py first to authenticate and create it, in the SAME "
                f"directory where you run main.py."
            )

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                # Tear down any previous client before recreating.
                if self.client is not None:
                    try:
                        await self.client.disconnect()
                    except Exception:
                        pass
                    self.client = None

                # TelegramClient with generous timeout for proxied connections.
                # test.py doesn't need this because the main thread's default
                # socket behaviour is faster; QThread + asyncio.run() can need
                # more time for the first proxied hop.
                client_kwargs: Dict[str, Any] = {
                    'proxy': proxy_tuple,
                    'connection_retries': 5,
                    'retry_delay': RETRY_DELAY,
                    'timeout': 120,   # generous for slow proxied first-hop auth
                }
                self.client = TelegramClient(
                    self.session_file,
                    self.api_id,
                    self.api_hash,
                    **client_kwargs,
                )

                start_kwargs: Dict[str, Any] = {'phone': self.phone}
                if self.code_callback:
                    start_kwargs['code_callback'] = self.code_callback
                else:
                    # If no session exists and no code_callback, client.start()
                    # would call input() for the login code, which hangs in a
                    # QThread (no stdin). Raise a clear error instead.
                    if not session_exists:
                        raise TelegramFetcherError(
                            f"Session file not found: {session_abs_full}. "
                            f"Run 'python test.py' first in the same directory "
                            f"to authenticate."
                        )

                # client.start() does: connect -> (auth if needed) -> ready.
                self.logger.info(
                    f"Attempt {attempt}/{MAX_RETRIES}: calling client.start()..."
                )
                await self.client.start(**start_kwargs)
                self._connected = True
                self.logger.info("Connected to Telegram successfully.")
                return True

            except TelegramFetcherError:
                raise
            except errors.rpcerrorlist.PhoneCodeInvalidError:
                raise TelegramFetcherError("Invalid Telegram login code.")
            except errors.rpcerrorlist.PhoneNumberBannedError:
                raise TelegramFetcherError("This phone number is banned by Telegram.")
            except errors.rpcerrorlist.AuthKeyError:
                raise TelegramFetcherError(
                    "Auth key error - delete the session file and retry."
                )
            except Exception as e:
                last_err = e
                self.logger.warning(
                    f"Attempt {attempt}/{MAX_RETRIES} at connecting failed: "
                    f"{type(e).__name__}: {e}"
                )
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_DELAY * attempt)

        raise TelegramFetcherError(
            f"Failed to connect after {MAX_RETRIES} attempts: "
            f"{type(last_err).__name__ if last_err else 'Unknown'}: {last_err}"
        )

    async def disconnect(self):
        if self.client is not None:
            try:
                await self.client.disconnect()
            except Exception:
                pass
        self._connected = False
        self.client = None
        self.logger.info("Disconnected from Telegram.")

    # --------------------------------------------------------------- fetch
    async def get_saved_messages_by_ids(self, from_id: int, to_id: int) -> List[Message]:
        if not self._connected:
            raise TelegramFetcherError("Not connected. Call connect() first.")
        if from_id > to_id:
            from_id, to_id = to_id, from_id
        me = await self.client.get_me()
        dialog = await self.client.get_input_entity(me.id)
        messages = await self.client.get_messages(
            dialog, min_id=from_id, max_id=to_id, reverse=True
        )
        self.logger.info(
            f"Retrieved {len(messages)} messages from IDs {from_id} to {to_id}."
        )
        return messages

    async def get_saved_messages_by_offset(self, start_id: int, count: int) -> List[Message]:
        if not self._connected:
            raise TelegramFetcherError("Not connected. Call connect() first.")
        if count <= 0:
            raise ValueError("Count must be positive.")
        me = await self.client.get_me()
        dialog = await self.client.get_input_entity(me.id)
        messages = await self.client.get_messages(
            dialog, offset_id=start_id, add_offset=0, limit=count
        )
        messages.reverse()
        self.logger.info(
            f"Retrieved {len(messages)} messages from start_id {start_id}."
        )
        return messages

    async def get_message_preview(self, from_id: int, to_id: int,
                                  preview_count: int = 2) -> Dict[str, Any]:
        if not self._connected:
            raise TelegramFetcherError("Not connected. Call connect() first.")
        if from_id > to_id:
            from_id, to_id = to_id, from_id
        me = await self.client.get_me()
        dialog = await self.client.get_input_entity(me.id)
        messages = await self.client.get_messages(
            dialog, min_id=from_id, max_id=to_id, reverse=True
        )
        total = len(messages)
        if total == 0:
            return {"first": [], "last": [], "total_count": 0}
        first_count = min(preview_count, total)
        last_start = max(0, total - preview_count)
        first_msgs = messages[:first_count]
        last_msgs = messages[last_start:]

        def msg_to_dict(msg: Message) -> Dict:
            return {
                "id": msg.id,
                "date": msg.date.isoformat() if msg.date else None,
                "text": (msg.text[:200] + ("..." if len(msg.text or "") > 200 else ""))
                        if msg.text else "",
                "has_links": bool(re.search(r'github\.com', msg.text or '')),
            }
        return {
            "first": [msg_to_dict(m) for m in first_msgs],
            "last": [msg_to_dict(m) for m in last_msgs],
            "total_count": total,
        }

    async def extract_github_urls_from_messages(self, messages: List[Message]) -> List[str]:
        """v30 — delegated to links.extract_github_urls (single source of
        truth: www + dots in owner/repo, dedup, order-preserved)."""
        urls = []
        pattern = _links.GITHUB_URL_PATTERN
        for msg in messages:
            if msg.text:
                matches = pattern.findall(msg.text)
                for owner, repo in matches:
                    url = f"https://github.com/{owner}/{repo}"
                    if url not in urls:
                        urls.append(url)
        self.logger.info(
            f"Extracted {len(urls)} unique GitHub URLs from {len(messages)} messages."
        )
        return urls


# ============================================================================
# Synchronous Wrapper
# ----------------------------------------------------------------------------
# Uses asyncio.run() - NOT a reused loop - so every call starts with a fresh
# event loop, exactly like test.py. This is critical: Telethon leaves
# background tasks on the loop, and a reused loop with stale tasks is a
# known cause of WinError 121 on the next connection.
#
# MUST be called from a worker thread (TestWorker / ProcessingWorker), never
# from the GUI thread, so the UI stays responsive.
# ============================================================================

def fetch_github_urls_sync(api_id: int, api_hash: str, phone: str,
                           proxy: Optional[Dict] = None,
                           from_id: Optional[int] = None,
                           to_id: Optional[int] = None,
                           offset_start: Optional[int] = None,
                           count: Optional[int] = None,
                           preview_only: bool = False,
                           preview_count: int = 2,
                           session_file: str = SESSION_FILE,
                           code_callback: Optional[Callable[[], str]] = None
                           ) -> Dict[str, Any]:

    async def _async_fetch():
        fetcher = TelegramFetcher(
            api_id, api_hash, phone,
            proxy=proxy,
            session_file=session_file,
            code_callback=code_callback,
            logger=logging.getLogger(),
        )
        try:
            await fetcher.connect()
            if preview_only:
                if from_id is not None and to_id is not None:
                    preview = await fetcher.get_message_preview(
                        from_id, to_id, preview_count
                    )
                    return {"success": True, "preview": preview}
                else:
                    return {
                        "success": False,
                        "error": "Preview requires from_id and to_id",
                    }
            else:
                if from_id is not None and to_id is not None:
                    messages = await fetcher.get_saved_messages_by_ids(
                        from_id, to_id
                    )
                elif offset_start is not None and count is not None:
                    messages = await fetcher.get_saved_messages_by_offset(
                        offset_start, count
                    )
                else:
                    return {
                        "success": False,
                        "error": "Either (from_id,to_id) or (offset_start,count) must be provided",
                    }
                urls = await fetcher.extract_github_urls_from_messages(messages)
                return {
                    "success": True,
                    "urls": urls,
                    "total_messages": len(messages),
                }
        except TelegramFetcherError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            return {"success": False, "error": f"{type(e).__name__}: {e}"}
        finally:
            await fetcher.disconnect()

    # asyncio.run() creates a FRESH event loop, runs the coroutine, cancels
    # all pending tasks, shuts down async generators, and closes the loop.
    # This is identical to what `asyncio.run(main())` does in test.py.
    #
    # DIAGNOSTIC: print the event loop type so we can verify which policy
    # is active (ProactorEventLoop vs SelectorEventLoop).
    _logger = logging.getLogger()
    _policy = asyncio.get_event_loop_policy()
    _logger.info(f"Event loop policy: {type(_policy).__name__}")
    if sys.platform == 'win32':
        _logger.info(
            "SelectorEventLoopPolicy active (GOOD - works with PySocks in QThread)"
            if isinstance(_policy, asyncio.WindowsSelectorEventLoopPolicy)
            else "ProactorEventLoopPolicy active (BAD - may cause WinError 121 in QThread)"
        )
    return asyncio.run(_async_fetch())
