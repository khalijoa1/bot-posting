"""Half-time reply on composed posts.

For posts you compose (owner is a real user, not a repost) that have an auto-delete
time, the bot replies "YOU WERE MENTIONED" to the sent post in every target channel
when half of the auto-delete time has passed. One reply per post per delete cycle.
Failures are logged and skipped; normal posting is unaffected.
"""
import asyncio
import logging
from datetime import datetime

from aiogram import Bot, Router, types
from aiogram.filters import Command
from sqlalchemy import select, text

from db import session
from models import Channel, Post, PostStatus, PostTarget

logger = logging.getLogger(__name__)

REPLY_TEXT = "YOU WERE MENTIONED"
POLL_SECONDS = 15

router = Router()


async def _ensure_table():
    async with session() as s:
        await s.execute(text(
            "CREATE TABLE IF NOT EXISTS half_reply ("
            "target_id INTEGER, cycle TEXT, PRIMARY KEY (target_id, cycle))"))
        await s.execute(text(
            "CREATE TABLE IF NOT EXISTS half_reply_state (k TEXT PRIMARY KEY, v TEXT)"))
        await s.commit()
    for ddl in ("ALTER TABLE half_reply ADD COLUMN chat_id INTEGER",
                "ALTER TABLE half_reply ADD COLUMN reply_id INTEGER",
                "ALTER TABLE half_reply ADD COLUMN done INTEGER DEFAULT 0"):
        try:
            async with session() as s2:
                await s2.execute(text(ddl))
                await s2.commit()
        except Exception:
            pass  # column already exists


async def is_paused() -> bool:
    async with session() as s:
        row = (await s.execute(text("SELECT v FROM half_reply_state WHERE k = 'paused'"))).first()
    return bool(row and row[0] == "1")


async def set_paused(value: bool) -> None:
    async with session() as s:
        await s.execute(text("DELETE FROM half_reply_state WHERE k = 'paused'"))
        await s.execute(text("INSERT INTO half_reply_state (k, v) VALUES ('paused', :v)"),
                        {"v": "1" if value else "0"})
        await s.commit()


async def _claim(target_id, cycle) -> bool:
    """Record that this target's reply for this cycle is being sent (False if already done)."""
    async with session() as s:
        row = (await s.execute(
            text("SELECT 1 FROM half_reply WHERE target_id = :t AND cycle = :c"),
            {"t": target_id, "c": cycle})).first()
        if row:
            return False
        await s.execute(text("INSERT INTO half_reply (target_id, cycle) VALUES (:t, :c)"),
                        {"t": target_id, "c": cycle})
        await s.commit()
    return True


async def _set_reply(target_id, cycle, chat_id, reply_id):
    async with session() as s:
        await s.execute(
            text("UPDATE half_reply SET chat_id = :c, reply_id = :r, done = 0 WHERE target_id = :t AND cycle = :y"),
            {"c": chat_id, "r": reply_id, "t": target_id, "y": cycle})
        await s.commit()


async def _cleanup(bot: Bot):
    """Delete the reply once its post is deleted (auto-delete time reached, or removed by hand)."""
    now = datetime.utcnow()
    async with session() as s:
        rows = (await s.execute(text(
            "SELECT h.target_id, h.cycle, h.chat_id, h.reply_id, p.status "
            "FROM half_reply h LEFT JOIN post_targets t ON t.id = h.target_id "
            "LEFT JOIN posts p ON p.id = t.post_id "
            "WHERE h.reply_id IS NOT NULL AND COALESCE(h.done, 0) = 0"))).all()
    for target_id, cycle, chat_id, reply_id, status in rows:
        try:
            due = now >= datetime.fromisoformat(cycle)
        except Exception:
            due = True
        gone = status is None or str(status).upper() in ("DELETED", "CANCELED")
        if not (due or gone):
            continue
        try:
            await bot.delete_message(chat_id=chat_id, message_id=reply_id)
            logger.info("half-reply: deleted reply %s in %s", reply_id, chat_id)
        except Exception:
            logger.info("half-reply: reply %s in %s was already gone or could not be deleted", reply_id, chat_id)
        async with session() as s:
            await s.execute(text("UPDATE half_reply SET done = 1 WHERE target_id = :t AND cycle = :y"),
                            {"t": target_id, "y": cycle})
            await s.commit()
        await asyncio.sleep(1)


async def _pass(bot: Bot):
    await _cleanup(bot)
    if await is_paused():
        return
    now = datetime.utcnow()
    due = []
    async with session() as s:
        posts = (await s.execute(
            select(Post).where(
                Post.status == PostStatus.SENT,
                Post.delete_at.is_not(None),
                Post.auto_delete_seconds.is_not(None),
                Post.owner_user_id != 0,
            ))).scalars().all()
        for post in posts:
            delete_at = post.delete_at.replace(tzinfo=None) if post.delete_at.tzinfo else post.delete_at
            half_at = delete_at.timestamp() - (post.auto_delete_seconds or 0) / 2
            if not (half_at <= now.timestamp() < delete_at.timestamp()):
                continue
            rows = (await s.execute(
                select(PostTarget.id, PostTarget.message_id, Channel.chat_id)
                .join(Channel, Channel.id == PostTarget.channel_id)
                .where(PostTarget.post_id == post.id))).all()
            for target_id, message_id, chat_id in rows:
                if message_id:
                    due.append((target_id, message_id, chat_id, delete_at.isoformat()))
    for target_id, message_id, chat_id, cycle in due:
        try:
            if not await _claim(target_id, cycle):
                continue
            sent = await bot.send_message(
                chat_id=chat_id, text=REPLY_TEXT,
                reply_to_message_id=message_id, allow_sending_without_reply=True)
            await _set_reply(target_id, cycle, chat_id, sent.message_id)
            logger.info("half-reply: replied in %s to message %s", chat_id, message_id)
        except Exception:
            logger.exception("half-reply: could not reply in %s", chat_id)
        await asyncio.sleep(1)


async def run_half_reply_loop(bot: Bot) -> None:
    try:
        await _ensure_table()
    except Exception:
        logger.exception("half-reply: cannot create table - disabled")
        return
    while True:
        try:
            await _pass(bot)
        except Exception:
            logger.exception("half-reply: pass failed")
        await asyncio.sleep(POLL_SECONDS)


@router.message(Command("reply_pause"))
async def cmd_pause(message: types.Message):
    await set_paused(True)
    await message.answer("\u23F8 Half-time replies paused. Send /reply_resume to turn them back on.")


@router.message(Command("reply_resume"))
async def cmd_resume(message: types.Message):
    await set_paused(False)
    await message.answer("\u25B6\uFE0F Half-time replies resumed.")


@router.message(Command("reply_status"))
async def cmd_status(message: types.Message):
    state = "PAUSED \u23F8" if await is_paused() else "ON \u25B6\uFE0F"
    await message.answer(f"Half-time reply (\"{REPLY_TEXT}\"): {state}\n/reply_pause \u00b7 /reply_resume")
