import asyncio
from telethon import TelegramClient
import socks

# ====== YOUR CREDENTIALS ======
api_id = 39788344
api_hash = 'REMOVED-API-HASH'
phone = '+98XXXXXXXXXX'

# ====== PROXY SETTINGS ======
USE_PROXY = True
PROXY_TYPE = socks.SOCKS5    # or socks.HTTP if your client uses HTTP
PROXY_HOST = '127.0.0.1'

# ⚠️ CHANGE THIS TO THE PORT YOUR CLIENT SHOWS ⚠️
PROXY_PORT = 10808           # v2rayN default SOCKS5. Try 10808, 10809, 7890, 7891, or 1080.

PROXY_USER = ''
PROXY_PASS = ''

# ==============================

if USE_PROXY:
    proxy = (PROXY_TYPE, PROXY_HOST, PROXY_PORT, PROXY_USER, PROXY_PASS)
else:
    proxy = None

async def main():
    client = TelegramClient('session', api_id, api_hash, proxy=proxy)
    await client.start(phone=phone)
    me = await client.get_me()
    print(f"Logged in as: {me.first_name} (@{me.username})")
    async for msg in client.iter_messages(me, limit=1):
        print(f"Latest message ID: {msg.id}")
    await client.disconnect()

asyncio.run(main())