from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from aiogram import Bot
from aiogram.types import BufferedInputFile
from sqlalchemy import or_, select

from db import session
from models import Channel, ContentType, Post, PostStatus, PostTarget, RepostRule, SourceChannel

logger = logging.getLogger(__name__)


def _render_template(template: str, context: dict[str, Any]) -> str:
    class SafeDict(dict):
        def __missing__(self, key):
            return ""

    try:
        return template.format_map(SafeDict(context))
    except Exception:
        return template


async def handle_incoming_message(bot: Bot, event) -> None:
    """Process a Telethon NewMessage event and repost it to matching destinations.

    `bot` is the aiogram Bot, used to post into destination channels (where it's
    an admin). `event` is a telethon events.NewMessage.Event - the userbot
    connection is only used to *read* the source channel; posting is always
    done through the regular Bot API so destination behaviour (permissions,
    formatting) matches the rest of the app.
    """
    message = event.message
    chat = await event.get_chat()
    if chat is None:
        return

    identifier_str = str(event.chat_id)
    username = getattr(chat, "username", None) or ""

    async with session() as s:
        q = select(SourceChannel).where(
            or_(SourceChannel.identifier == identifier_str, SourceChannel.identifier == username)
        )
        res = await s.execute(q)
        source = res.scalars().first()
        if not source:
            return

        q2 = select(RepostRule).where(RepostRule.source_channel_id == source.id)
        res2 = await s.execute(q2)
        rules = res2.scalars().all()
        if not rules:
            return

        text = message.message or None
        photo_bytes: bytes | None = None
        if message.photo:
            downloaded = await message.download_media(bytes)
            photo_bytes = downloaded if isinstance(downloaded, (bytes, bytearray)) else None

        post = Post(
            owner_user_id=0,  # system-owned: created by the userbot, not a specific operator chat
            content_type=ContentType.PHOTO if photo_bytes else ContentType.TEXT,
            text=text,
            status=PostStatus.SENT,
            created_at=datetime.utcnow(),
        )
        s.add(post)
        await s.flush()

        for rule in rules:
            qch = select(Channel).where(Channel.id == rule.destination_channel_id)
            rch = await s.execute(qch)
            dest = rch.scalars().first()
            if not dest:
                continue

            context = {
                "original_text": text or "",
                "source_title": source.title or "",
                "source_username": source.identifier,
            }
            caption = _render_template(rule.caption_template, context) if rule.caption_template else text

            if rule.replacements_json:
                try:
                    repls = json.loads(rule.replacements_json)
                except Exception:
                    repls = {}
                per_dest = repls.get(str(dest.chat_id)) or repls.get(str(dest.id)) or repls.get("default") or {}
                if isinstance(per_dest, dict) and caption:
                    for k, v in per_dest.items():
                        caption = caption.replace(k, v)

            try:
                if photo_bytes:
                    sent = await bot.send_photo(
                        chat_id=dest.chat_id,
                        photo=BufferedInputFile(photo_bytes, filename="repost.jpg"),
                        caption=caption,
                    )
                else:
                    sent = await bot.send_message(chat_id=dest.chat_id, text=caption or "")

                pt = PostTarget(post_id=post.id, channel_id=dest.id, message_id=sent.message_id, sent_at=datetime.utcnow())
                s.add(pt)
                await s.commit()
            except Exception:
                logger.exception("Failed to repost into channel %s", dest.title)
                pt = PostTarget(post_id=post.id, channel_id=dest.id, message_id=None, sent_at=None)
                s.add(pt)
                await s.commit()
                continue

        auto_candidates = [r.auto_delete_seconds for r in rules if r.auto_delete_seconds]
        auto_seconds = min(auto_candidates) if auto_candidates else None
        if auto_seconds:
            post.auto_delete_seconds = auto_seconds
            post.delete_at = datetime.utcnow() + timedelta(seconds=auto_seconds)
            await s.commit()
