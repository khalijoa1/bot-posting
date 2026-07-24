from __future__ import annotations

import json
import logging
import re
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


# Matches http(s)/www links and bare t.me links (Telegram's own share-link
# domain, which very often shows up without a scheme in forwarded captions).
_LINK_RE = re.compile(r"(?:https?://|www\.)\S+|(?<!\w)t\.me/\S+", re.IGNORECASE)
# Matches @username mentions (Telegram usernames are 5-32 chars; using a
# slightly looser 3-32 to be safe rather than under-match).
_MENTION_RE = re.compile(r"(?<!\w)@[A-Za-z0-9_]{3,32}\b")


def _scrub_remaining_links(text: str | None, fallback: str | None) -> str | None:
    """Final safety pass: after any explicit replacements have run, nothing
    that still looks like a link or an @mention is allowed to survive
    untouched - it's swapped for the operator's own fallback link/username
    if one is configured, otherwise removed outright. This is what
    guarantees the source channel's own link or username can never slip
    into a repost, even for link formats or usernames nobody explicitly
    configured a replacement for.
    """
    if not text:
        return text

    def _sub(_match: re.Match) -> str:
        return fallback if fallback else ""

    text = _LINK_RE.sub(_sub, text)
    text = _MENTION_RE.sub(_sub, text)
    # Collapse whatever whitespace/blank lines stripping a link left behind.
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _apply_replacements(text: str | None, rule: RepostRule, dest: Channel) -> str | None:
    """Swap the source channel's links/usernames for the operator's own.

    Two layers:
    1. Explicit "old -> new" pairs configured on the rule (via the bot's
       Forwarding UI, or hand-edited on an older rule) - applied first,
       exact substring match.
    2. A mandatory scrub pass (_scrub_remaining_links) that catches every
       link or @mention still left afterward and replaces it with the
       rule's configured fallback link/username, or strips it if no
       fallback is set. This always runs, even if the rule has no
       replacements configured at all - the source's own link or username
       must never be posted as-is, one way or another.

    Rules created through the UI store the mapping under the "default" key
    regardless of destination. Older rules created via the original
    /add_rule + hand-edited replacements_json may instead key by
    destination chat_id/channel id - both are honoured here.
    """
    if not text:
        return text

    mapping: dict[str, str] = {}
    fallback: str | None = None
    if rule.replacements_json:
        try:
            repls = json.loads(rule.replacements_json)
        except Exception:
            repls = {}
        if isinstance(repls, dict):
            mapping = repls.get("default") or repls.get(str(dest.chat_id)) or repls.get(str(dest.id)) or {}
            fallback = repls.get("fallback")

    if isinstance(mapping, dict):
        for old, new in mapping.items():
            text = text.replace(old, new)

    return _scrub_remaining_links(text, fallback)


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
        video_bytes: bytes | None = None
        if message.photo:
            downloaded = await message.download_media(bytes)
            photo_bytes = downloaded if isinstance(downloaded, (bytes, bytearray)) else None
        elif message.video:
            # Previously only photos were downloaded here - a video source
            # post fell through to the plain-text branch below with no
            # media at all, silently dropping the video and reposting the
            # caption alone. Videos are handled the same way as photos now.
            downloaded = await message.download_media(bytes)
            video_bytes = downloaded if isinstance(downloaded, (bytes, bytearray)) else None

        if photo_bytes:
            content_type = ContentType.PHOTO
        elif video_bytes:
            content_type = ContentType.VIDEO
        else:
            content_type = ContentType.TEXT

        post = Post(
            owner_user_id=0,  # system-owned: created by the userbot, not a specific operator chat
            content_type=content_type,
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

            # Scrub the source's own text FIRST, before it's dropped into a
            # caption_template - that way an operator-authored template
            # (e.g. their own "Join us: https://t.me/mychannel" footer)
            # never gets clipped by the same pass that's removing the
            # *source's* links, since it never touches that literal
            # template text at all.
            cleaned_text = _apply_replacements(text, rule, dest)

            context = {
                "original_text": cleaned_text or "",
                "source_title": source.title or "",
                "source_username": source.identifier,
            }
            caption = _render_template(rule.caption_template, context) if rule.caption_template else cleaned_text

            try:
                if photo_bytes:
                    sent = await bot.send_photo(
                        chat_id=dest.chat_id,
                        photo=BufferedInputFile(photo_bytes, filename="repost.jpg"),
                        caption=caption,
                    )
                elif video_bytes:
                    sent = await bot.send_video(
                        chat_id=dest.chat_id,
                        video=BufferedInputFile(video_bytes, filename="repost.mp4"),
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
