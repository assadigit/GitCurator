#!/usr/bin/env python3
"""
Telegram Code Request Diagnostic
================================
Standalone script that ONLY tests send_code_request() and prints the FULL
Telegram response. This isolates the code-delivery problem from everything
else (GUI, threading, proxy, session).

Run this in a terminal:
    python diagnose_code.py

It will:
  1. Connect through your proxy
  2. Call send_code_request(phone)
  3. Print the FULL Telegram response (type, phone_code_hash, timeout, etc.)
  4. Print any error (especially FloodWaitError with the wait time)

If this script shows FloodWaitError, you've been rate-limited by Telegram
from too many code requests. You must WAIT (could be minutes to hours).

If this script succeeds but the GUI doesn't, the problem is in our GUI code.

If this script fails with a different error, we'll know exactly what's wrong.
"""

import asyncio
import sys
import os
import json

# Load config from config.json if it exists, otherwise use test.py's credentials
CONFIG_FILE = "config.json"
if os.path.exists(CONFIG_FILE):
    with open(CONFIG_FILE, 'r') as f:
        cfg = json.load(f)
    API_ID = int(cfg.get('telegram_api_id', 0))
    API_HASH = cfg.get('telegram_api_hash', '')
    PHONE = cfg.get('telegram_phone', '')
    PROXY_CFG = cfg.get('proxy', {})
else:
    # Fall back to test.py's hardcoded values
    API_ID = 39788344
    API_HASH = 'REMOVED-API-HASH'
    PHONE = '+98XXXXXXXXXX'
    PROXY_CFG = {
        'enabled': True,
        'type': 'socks5',
        'host': '127.0.0.1',
        'port': 10808,
    }

print("=" * 60)
print("TELEGRAM CODE REQUEST DIAGNOSTIC")
print("=" * 60)
print(f"API ID:   {API_ID}")
print(f"API Hash: {API_HASH[:8]}...")
print(f"Phone:    {PHONE}")
print(f"Proxy:    {PROXY_CFG}")
print("=" * 60)
print()

from telethon import TelegramClient, errors
import socks


def build_proxy():
    if not PROXY_CFG or not PROXY_CFG.get('enabled'):
        return None
    ptype_str = str(PROXY_CFG.get('type', 'socks5')).lower()
    ptype = {
        'socks5': socks.SOCKS5,
        'socks4': socks.SOCKS4,
        'http': socks.HTTP,
    }.get(ptype_str, socks.SOCKS5)
    host = PROXY_CFG.get('host', '127.0.0.1')
    port = int(PROXY_CFG.get('port', 10808))
    return (ptype, host, port, '', '')


async def diagnose():
    proxy = build_proxy()
    print(f"[1/4] Building proxy tuple: {proxy}")
    print()

    # Use a SEPARATE session file so we don't interfere with the main one
    session_file = 'diagnose_session'
    print(f"[2/4] Creating TelegramClient (session: {session_file})...")
    client = TelegramClient(session_file, API_ID, API_HASH, proxy=proxy)

    print("[3/4] Connecting to Telegram...")
    try:
        await client.connect()
        print("      ✅ Connected!")
    except Exception as e:
        print(f"      ❌ Connection failed: {type(e).__name__}: {e}")
        print()
        print("This means your proxy or network is the problem, NOT Telegram.")
        print("Check that v2rayN is running and port 10808 is listening.")
        return

    print()
    print("[4/4] Calling send_code_request(phone)...")
    print(f"      Phone: {PHONE}")
    print()

    try:
        result = await client.send_code_request(PHONE)
        print("=" * 60)
        print("✅ SUCCESS! Telegram accepted the code request.")
        print("=" * 60)
        print(f"Full response object: {result}")
        print()
        print(f"  type:            {result.type}")
        print(f"  phone_code_hash: {result.phone_code_hash}")
        if hasattr(result, 'next_type'):
            print(f"  next_type:       {result.next_type}")
        if hasattr(result, 'timeout'):
            print(f"  timeout:         {result.timeout}")
        print()
        print("Telegram SHOULD have sent a code to your device now.")
        print("Check your Telegram app (Login Code notification at top of chat list)")
        print("or your SMS.")
        print()
        print("If you don't receive a code despite this success, it's a Telegram")
        print("delivery issue, not a code issue.")

    except errors.FloodWaitError as e:
        print("=" * 60)
        print("❌ FLOOD WAIT — Telegram has rate-limited you!")
        print("=" * 60)
        print(f"You must wait {e.seconds} seconds ({e.seconds/60:.1f} minutes) before")
        print("Telegram will allow another code request.")
        print()
        print("This happens when you request too many codes in a short period.")
        print("You've been testing repeatedly, which triggered Telegram's")
        print("anti-abuse system.")
        print()
        print(f"Wait until: {e.seconds}s from now, then try again.")
        print("Do NOT keep retrying — that will extend the wait time!")

    except errors.PhoneNumberBannedError:
        print("=" * 60)
        print("❌ PHONE NUMBER BANNED")
        print("=" * 60)
        print("This phone number is banned from Telegram.")

    except errors.ApiIdInvalidError:
        print("=" * 60)
        print("❌ API ID INVALID")
        print("=" * 60)
        print("Your api_id/api_hash are invalid. Get new ones from")
        print("https://my.telegram.org")

    except errors.AuthKeyError as e:
        print("=" * 60)
        print("❌ AUTH KEY ERROR")
        print("=" * 60)
        print(f"{e}")
        print()
        print("Delete the session file and retry:")
        print(f"  del {session_file}.session")

    except Exception as e:
        print("=" * 60)
        print(f"❌ UNEXPECTED ERROR: {type(e).__name__}")
        print("=" * 60)
        print(f"{e}")
        print()
        print(f"Full error details:")
        import traceback
        traceback.print_exc()

    finally:
        await client.disconnect()
        print()
        print("[done] Disconnected.")


if __name__ == '__main__':
    asyncio.run(diagnose())
    # Clean up the diagnostic session file
    try:
        os.remove('diagnose_session.session')
        print("[cleanup] Removed diagnose_session.session")
    except OSError:
        pass
