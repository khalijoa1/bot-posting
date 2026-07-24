"""Periodically snapshots each registered channel's member count so
/analytics can show growth over time instead of just a single live number.

Telegram's Bot API only exposes a point-in-time member count
(getChatMemberCount) - there's no historical/growth endpoint - so tracking
"growth" at all requires the bot to keep its own timeline by polling and
storing snapshots itself.
"""
import asyncio
import logging
from datetime import datetime

from aiogram import Bot
from sqlalchemy import select

from db import session
from models import Channel, ChannelStatSnapshot

logger = logging.getLogger(__name__)

# How often to take a fresh snapshot of every channel's member count.
SNAPSHOT_INTERVAL_SECONDS = 6 * 3600  # every 6 hours


async def _snapshot_all_channels(bot: Bot) -> None:
    async with session() as s:
        q = select(Channel)
        res = await s.execute(q)
        channels = res.scalars().all()

        for ch in channels:
            try:
                count = await bot.get_chat_member_count(chat_id=ch.chat_id)
            except Exception:
                logger.warning("Could not read member count for channel %s (%s)", ch.title, ch.chat_id)
                continue
            s.add(ChannelStatSnapshot(channel_id=ch.id, member_count=count, taken_at=datetime.utcnow()))

        await s.commit()


async def run_channel_stats_loop(bot: Bot) -> None:
    """Background loop: snapshot every channel's member count on a fixed
    interval, starting shortly after boot so /analytics has at least one
    data point without waiting a full interval."""
    await asyncio.sleep(30)  # let the bot finish starting up first
    while True:
        try:
            await _snapshot_all_channels(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Error taking channel stat snapshots")
        await asyncio.sleep(SNAPSHOT_INTERVAL_SECONDS)
