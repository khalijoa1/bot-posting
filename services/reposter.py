from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from aiogram import Bot, types
from sqlalchemy import select, update, and_, or_

from db import session
from models import SourceChannel, RepostRule, Channel, Post, PostTarget, PostStatus


def _render_template(template: str, context: dict[str, Any]) -> str:
    class SafeDict(dict):
        def __missing__(self, key):
            return ""

    try:
        return template.format_map(SafeDict(context))
    except Exception:
        # Fallback: return template as-is if formatting fails
        return template


async def handle_incoming_message(bot: Bot, message: types.Message) -> None:
    """Process an incoming message from a source channel and repost per rules.

    - Detect SourceChannel by numeric id or username.
    - Create a Post record and PostTarget rows.
    - Copy message to destination channels using copy_message, then apply caption templates.
    """
    chat = message.chat
    if chat is None:
        return

    identifier_str = str(chat.id)
    username = (chat.username or "")

    async with session() as s:
        # Find matching source channel record
        q = select(SourceChannel).where(
            or_(SourceChannel.identifier == identifier_str, SourceChannel.identifier == username)
        )
        res = await s.execute(q)
        source = res.scalars().first()
        if not source:
            return

        # Find repost rules for that source
        q2 = select(RepostRule).where(RepostRule.source_channel_id == source.id)
        res2 = await s.execute(q2)
        rules = res2.scalars().all()
        if not rules:
            return

        # Build Post object
        content_type = Post.content_type
        text = None
        photo_file_id = None
        if message.photo:
            content_type_val = Post.content_type.type.__args__[0] if hasattr(Post.content_type, 'type') else 'photo'
            # prefer caption for photos
            text = message.caption
            # pick largest photo
            if message.photo:
                photo_file_id = message.photo[-1].file_id
        else:
            # Use text or caption
            text = message.text or message.caption

        # Create a single Post that will represent this repost event
        post = Post(
            owner_user_id=0,  # system-owned; you may set to operator id when creating via admin
            content_type=("photo" if photo_file_id else "text"),
            text=text,
            photo_file_id=photo_file_id,
            status=PostStatus.SENT,
            created_at=datetime.utcnow(),
        )
        s.add(post)
        await s.flush()

        # For each rule, copy the message to the destination channel
        for rule in rules:
            # resolve destination channel
            qch = select(Channel).where(Channel.id == rule.destination_channel_id)
            rch = await s.execute(qch)
            dest = rch.scalars().first()
            if not dest:
                continue

            try:
                sent = await bot.copy_message(chat_id=dest.chat_id, from_chat_id=chat.id, message_id=message.message_id)
                # record PostTarget
                pt = PostTarget(post_id=post.id, channel_id=dest.id, message_id=sent.message_id, sent_at=datetime.utcnow())
                s.add(pt)

                # Apply caption template / replacements if present
                caption = None
                # Build render context
                context = {
                    "original_text": text or "",
                    "source_title": source.title or "",
                    "source_username": source.identifier,
                    "post_url": f"https://t.me/c/{str(chat.id).lstrip('-100')}/{message.message_id}" if str(chat.id).startswith("-100") else None,
                }
                if rule.caption_template:
                    caption = _render_template(rule.caption_template, context)
                # apply replacements if any
                if rule.replacements_json:
                    try:
                        repls = json.loads(rule.replacements_json)
                    except Exception:
                        repls = {}
                    # Per-destination mapping or default
                    per_dest = repls.get(str(dest.chat_id)) or repls.get(str(dest.id)) or repls.get("default") or {}
                    if isinstance(per_dest, dict):
                        for k, v in per_dest.items():
                            if caption:
                                caption = caption.replace(k, v)
                            else:
                                text = (text or "").replace(k, v)

                # Edit message to apply caption/text change
                if caption is not None:
                    # If the message is media with caption, edit caption
                    try:
                        await bot.edit_message_caption(chat_id=dest.chat_id, message_id=sent.message_id, caption=caption)
                    except Exception:
                        # Fallback to edit text if caption edit fails
                        try:
                            await bot.edit_message_text(chat_id=dest.chat_id, message_id=sent.message_id, text=caption)
                        except Exception:
                            pass
                s.add(post)
                await s.commit()

            except Exception as exc:
                # log and persist failure as PostTarget with message_id=None
                pt = PostTarget(post_id=post.id, channel_id=dest.id, message_id=None, sent_at=None)
                s.add(pt)
                await s.commit()
                # Note: in production you'd want more robust retry/backoff
                continue

        # If any rule asked for auto-delete, set post.delete_at accordingly (take smallest auto_delete_seconds)
        auto_seconds = None
        for r in rules:
            if r.auto_delete_seconds:
                if auto_seconds is None or r.auto_delete_seconds < auto_seconds:
                    auto_seconds = r.auto_delete_seconds
        if auto_seconds:
            post.auto_delete_seconds = auto_seconds
            post.delete_at = datetime.utcnow() + timedelta(seconds=auto_seconds)
            await s.commit()


