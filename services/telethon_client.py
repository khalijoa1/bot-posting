"""Userbot client (Telethon) that watches source channels and reposts per rule.

Runs alongside the aiogram Bot as a background task. The aiogram Bot posts into
destination channels (where it's an admin); this userbot only needs to *see*
the source channels, which it can do by following public channels with a real
Telegram account, even when the aiogram Bot itself isn't a member there.

Requires TELETHON_API_ID / TELETHON_API_HASH (from https://my.telegram.org)
and a logged-in session. Run `python scripts/telethon_login.py` once locally
to generate TELETHON_SESSION_STRING. If those aren't configured, this feature
is simply skipped - the rest of the bot works fine without it.
"""
import logging

from aiogram import Bot

from config import get_settings
from services.reposter import handle_incoming_message

logger = logging.getLogger(__name__)


def build_client():
    """Build a Telethon client, or return None if it isn't configured."""
    settings = get_settings()
    if not settings.telethon_api_id or not settings.telethon_api_hash:
        logger.info("Telethon API credentials not set - repost-from-source feature disabled.")
        return None

    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
    except ImportError:
        logger.warning("telethon is not installed - repost-from-source feature disabled.")
        return None

    session = StringSession(settings.telethon_session_string) if settings.telethon_session_string else settings.telethon_session_name
    return TelegramClient(session, settings.telethon_api_id, settings.telethon_api_hash)


async def run_userbot(bot: Bot) -> None:
    """Connect the userbot and listen for new messages in any chat it can see.

    handle_incoming_message() checks whether the chat is a registered
    SourceChannel and no-ops otherwise, so it's safe to listen broadly rather
    than needing to know the channel list up front (channels can be
    added/removed at runtime via /add_source without restarting).
    """
    client = build_client()
    if client is None:
        return

    from telethon import events

    @client.on(events.NewMessage())
    async def _on_new_message(event: events.NewMessage.Event) -> None:
        try:
            await handle_incoming_message(bot, event)
        except Exception:
            logger.exception("Error handling incoming message for repost")

    try:
        await client.start()
    except Exception:
        logger.exception(
            "Telethon userbot failed to start - check TELETHON_API_ID/HASH and that "
            "TELETHON_SESSION_STRING (from scripts/telethon_login.py) is set."
        )
        return

    logger.info("Telethon userbot connected - watching for source channel posts.")
    await client.run_until_disconnected()
