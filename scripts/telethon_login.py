#!/usr/bin/env python3
"""One-time interactive login for the Telethon userbot (repost-from-source feature).

Run this LOCALLY on your own machine (not on Railway):

    python scripts/telethon_login.py

It asks for your phone number and the login code Telegram sends you (and your
2FA password, if you have one enabled). At the end it prints a session
string - copy it into TELETHON_SESSION_STRING in your .env (local) or as a
Railway variable (production). Treat this string like a password: whoever
has it is logged into your Telegram account.

Before running this, set TELETHON_API_ID, TELETHON_API_HASH, and
TELETHON_PHONE in your .env - get the API id/hash from https://my.telegram.org
under "API Development Tools".
"""
import asyncio

from telethon import TelegramClient
from telethon.sessions import StringSession

from config import get_settings


async def main() -> None:
    settings = get_settings()
    if not settings.telethon_api_id or not settings.telethon_api_hash:
        print("Set TELETHON_API_ID and TELETHON_API_HASH in your .env first.")
        print("Get them from https://my.telegram.org -> API Development Tools.")
        return

    client = TelegramClient(StringSession(), settings.telethon_api_id, settings.telethon_api_hash)
    await client.start(phone=settings.telethon_phone or None)

    session_string = client.session.save()
    print("\nLogin successful. Add this to your .env / Railway variables as TELETHON_SESSION_STRING:\n")
    print(session_string)
    print()

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
