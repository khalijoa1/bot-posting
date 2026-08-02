"""Background loop that sends due recurring group messages (see
models.RecurringMessage and handlers/group_messages.py).

Mirrors the polling pattern used by services/scheduler.py: every 30s, look
for enabled RecurringMessage rows whose interval has elapsed since they
were last sent (or that have never been sent), send each one to its
group, and stamp last_sent_at so it isn't sent again until the interval
passes again. A single missed cycle (e.g. the bot was briefly down) just
means the next check catches it slightly late - there's no drift
accumulation since last_sent_at is always the actual send time, not a
scheduled time.
"""
import asyncio
from datetime import datetime, timedelta

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from db import session
from handlers.group_messages import send_rich_message
from models import RecurringMessage


async def run_recurring_messages_loop(bot: Bot) -> None:
    while True:
        try:
            now = datetime.utcnow()
            async with session() as s:
                q = select(RecurringMessage).where(RecurringMessage.enabled == True).options(  # noqa: E712
                    selectinload(RecurringMessage.group)
                )
                res = await s.execute(q)
                due = [
                    rm for rm in res.scalars().all()
                    if rm.last_sent_at is None or rm.last_sent_at + timedelta(seconds=rm.interval_seconds) <= now
                ]

                for rm in due:
                    group = rm.group
                    if not group:
                        continue
                    await send_rich_message(
                        bot, group.chat_id, rm.text, rm.media_type, rm.media_file_id, rm.buttons_json
                    )
                    rm.last_sent_at = now

                if due:
                    await s.commit()
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(5)
