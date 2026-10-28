"""Storage-channel mirror + backup for posts.

Every post the bot saves is copied into a private STORAGE channel. That gives:
  * a visible copy of every post you can open/forward from,
  * edit sync: edit the message text/caption in the channel and the saved post
    in the database is updated (future sends use the new text),
  * automatic rebuild: if the database is empty on startup, posts (and the
    channels they were sent to) are restored from the storage channel.

Nothing here can break normal posting: every step is wrapped so a failure is
logged and skipped. Set STORAGE_CHANNEL_ID (e.g. -1001234567890) to enable; the
bot must be an admin of that channel and the userbot account a member.
"""
import asyncio
import hashlib
import html as html_lib
import json
import logging
import re
from datetime import datetime, timedelta

from aiogram import Bot
from sqlalchemy import select, text, func

from config import get_settings
from db import session
from models import (
    Channel, ContentType, Post, PostMediaItem, PostStatus, PostTarget,
)

logger = logging.getLogger(__name__)

POLL_SECONDS = 10
SEND_GAP_SECONDS = 3.0          # stay well under Telegram's channel flood limits
SENT_HISTORY_DAYS = 14          # how far back to mirror already-sent posts
CAPTION_LIMIT = 1000
TEXT_LIMIT = 4000
_HEADER_RE = re.compile(r"^#P(\d+)\b")
_MARK = "⚙"                # gear: starts the hidden metadata footer line


# --------------------------------------------------------------------------
# rendering / parsing (pure functions)
# --------------------------------------------------------------------------
def _iso(dt):
    return dt.isoformat() if dt else None


def _u16(s):
    return len(s.encode("utf-16-le")) // 2


def build_message(post, meta, limit):
    """Return (message_html, truncated)."""
    status = getattr(post.status, "value", str(post.status))
    ctype = getattr(post.content_type, "value", str(post.content_type))
    when = post.scheduled_time.strftime("%Y-%m-%d %H:%M") if post.scheduled_time else "-"
    header = f"#P{post.id} · {status.upper()} · {ctype}\n\U0001F552 {when}"

    def _footer():
        return f"{_MARK} <code>{html_lib.escape(json.dumps(meta, separators=(',', ':')), quote=False)}</code>"

    footer = _footer()
    # Many target channels can make the settings line alone too long: keep only their ids.
    if _u16(header) + _u16(footer) + 200 > limit and meta.get("ch"):
        meta["ch"] = [[c, ""] for c, _t in meta["ch"]]
        footer = _footer()
    body = post.text or ""
    truncated = False
    # Telegram counts UTF-16 units (an emoji is 2), so measure that way and shrink until it fits
    while body and _u16(header) + _u16(body) + _u16(footer) + 16 > limit:
        body = body[: int(len(body) * 0.9) - 1]
        truncated = True
    if truncated:
        body = body + " …[cut]"
    meta["trunc"] = 1 if truncated else 0
    if truncated:
        footer = f"{_MARK} <code>{html_lib.escape(json.dumps(meta, separators=(',', ':')), quote=False)}</code>"
    return f"{header}\n\n{body}\n\n{footer}", truncated


def parse_message(raw_text, html_text):
    """Parse a storage-channel message. Returns (post_id, body_html, meta) or None."""
    m = _HEADER_RE.match(raw_text or "")
    if not m:
        return None
    post_id = int(m.group(1))
    meta = {}
    for line in reversed((raw_text or "").split("\n")):
        if line.startswith(_MARK):
            try:
                meta = json.loads(line[len(_MARK):].strip())
            except Exception:
                meta = {}
            break
    start = html_text.find("\n\n")
    end = html_text.rfind(_MARK)
    if start == -1:
        body = ""
    else:
        start += 2
        body = html_text[start:end] if end != -1 and end >= start else (
            "" if end != -1 else html_text[start:])
    return post_id, body.strip("\n").strip(), meta


# --------------------------------------------------------------------------
# DB helpers
# --------------------------------------------------------------------------
async def _ensure_table():
    async with session() as s:
        await s.execute(text(
            "CREATE TABLE IF NOT EXISTS storage_map ("
            "post_id INTEGER PRIMARY KEY, message_id INTEGER, fp TEXT)"))
        await s.commit()


async def _load_map():
    async with session() as s:
        rows = (await s.execute(text("SELECT post_id, message_id, fp FROM storage_map"))).all()
    return {r[0]: (r[1], r[2]) for r in rows}


async def _save_map(post_id, message_id, fp):
    async with session() as s:
        await s.execute(text("DELETE FROM storage_map WHERE post_id = :p"), {"p": post_id})
        await s.execute(text("INSERT INTO storage_map (post_id, message_id, fp) VALUES (:p, :m, :f)"),
                        {"p": post_id, "m": message_id, "f": fp})
        await s.commit()


async def _post_snapshot(s, post):
    targets = (await s.execute(
        select(Channel.chat_id, Channel.title)
        .join(PostTarget, PostTarget.channel_id == Channel.id)
        .where(PostTarget.post_id == post.id))).all()
    media = (await s.execute(
        select(PostMediaItem).where(PostMediaItem.post_id == post.id)
        .order_by(PostMediaItem.position))).scalars().all()
    meta = {
        "o": post.owner_user_id,
        "st": _iso(post.scheduled_time),
        "ad": post.auto_delete_seconds,
        "da": _iso(post.delete_at),
        "rp": post.repeat_interval_seconds,
        "bt": post.button_text,
        "bu": post.button_url,
        "ch": [[c, t] for c, t in targets],
    }
    return meta, media


def _fingerprint(post, meta, media):
    raw = json.dumps([
        getattr(post.status, "value", str(post.status)),
        getattr(post.content_type, "value", str(post.content_type)),
        post.text, meta, [(m.media_type, m.file_id) for m in media],
        post.photo_file_id, post.video_file_id,
    ], sort_keys=True, default=str)
    return hashlib.sha1(raw.encode()).hexdigest()


def _wanted(post):
    if not post.owner_user_id:
        return False  # only posts you compose, never reposts
    status = post.status
    if status in (PostStatus.DRAFT, PostStatus.SCHEDULED):
        return True
    if status == PostStatus.SENT:
        if post.repeat_interval_seconds:
            return True
        created = getattr(post, "created_at", None)
        if created is None:
            return True
        try:
            return created.replace(tzinfo=None) >= datetime.utcnow() - timedelta(days=SENT_HISTORY_DAYS)
        except Exception:
            return True
    return False  # canceled / deleted: only refreshed if already mirrored


# --------------------------------------------------------------------------
# bot -> channel (mirror)
# --------------------------------------------------------------------------
async def _send_mirror(bot: Bot, chat_id, post, meta, media):
    ctype = post.content_type
    limit = TEXT_LIMIT if ctype == ContentType.TEXT else CAPTION_LIMIT
    message, _ = build_message(post, meta, limit)

    async def _try(fn, **kw):
        try:
            return await fn(**kw)
        except Exception as exc:
            if "parse" in str(exc).lower() or "entities" in str(exc).lower():
                kw["parse_mode"] = None
                return await fn(**kw)
            raise

    if ctype == ContentType.PHOTO and post.photo_file_id:
        msg = await _try(bot.send_photo, chat_id=chat_id, photo=post.photo_file_id, caption=message)
        return msg.message_id
    if ctype == ContentType.VIDEO and post.video_file_id:
        msg = await _try(bot.send_video, chat_id=chat_id, video=post.video_file_id, caption=message)
        return msg.message_id
    if ctype == ContentType.ALBUM and media:
        from aiogram.types import InputMediaPhoto, InputMediaVideo
        items = []
        for i, m in enumerate(media[:10]):
            cap = message if i == 0 else None
            cls = InputMediaPhoto if m.media_type == "photo" else InputMediaVideo
            items.append(cls(media=m.file_id, caption=cap))
        msgs = await bot.send_media_group(chat_id=chat_id, media=items)
        meta["mids"] = [x.message_id for x in msgs]
        return msgs[0].message_id
    msg = await _try(bot.send_message, chat_id=chat_id, text=message, disable_web_page_preview=True)
    return msg.message_id


async def _refresh_mirror(bot: Bot, chat_id, message_id, post, meta):
    ctype = post.content_type
    limit = TEXT_LIMIT if ctype == ContentType.TEXT else CAPTION_LIMIT
    message, _ = build_message(post, meta, limit)
    try:
        if ctype == ContentType.TEXT:
            await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=message,
                                        disable_web_page_preview=True)
        else:
            await bot.edit_message_caption(chat_id=chat_id, message_id=message_id, caption=message)
    except Exception as exc:
        if "not modified" in str(exc).lower():
            return
        if "parse" in str(exc).lower() or "entities" in str(exc).lower():
            if ctype == ContentType.TEXT:
                await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=message,
                                            parse_mode=None)
            else:
                await bot.edit_message_caption(chat_id=chat_id, message_id=message_id, caption=message,
                                               parse_mode=None)
            return
        raise


async def _mirror_pass(bot: Bot, chat_id):
    mapping = await _load_map()
    async with session() as s:
        posts = (await s.execute(select(Post).order_by(Post.id))).scalars().all()
        todo = []
        for post in posts:
            if not post.owner_user_id:
                continue  # reposts (owner 0) are never mirrored - only posts you compose
            meta, media = await _post_snapshot(s, post)
            fp = _fingerprint(post, meta, media)
            entry = mapping.get(post.id)
            if entry is None:
                if _wanted(post):
                    todo.append((post, meta, media, fp, None))
            elif entry[1] != fp:
                todo.append((post, meta, media, fp, entry[0]))
        # detach data we need before the session closes
        jobs = []
        for post, meta, media, fp, mid in todo:
            s.expunge(post)
            for m in media:
                s.expunge(m)
            jobs.append((post, meta, media, fp, mid))
    for post, meta, media, fp, mid in jobs:
        try:
            if mid is None:
                new_id = await _send_mirror(bot, chat_id, post, meta, media)
                await _save_map(post.id, new_id, fp)
                logger.info("storage: mirrored post %s as message %s", post.id, new_id)
            else:
                await _refresh_mirror(bot, chat_id, mid, post, meta)
                await _save_map(post.id, mid, fp)
        except Exception:
            logger.exception("storage: could not mirror post %s", post.id)
        await asyncio.sleep(SEND_GAP_SECONDS)


# --------------------------------------------------------------------------
# channel -> bot (edit sync) and restore, via the Telethon userbot
# --------------------------------------------------------------------------
async def _get_client(timeout=120):
    from services import telethon_client as tc
    waited = 0
    while waited < timeout:
        client = getattr(tc, "_client", None)
        if client is not None and client.is_connected():
            return client
        await asyncio.sleep(2)
        waited += 2
    return None


def _to_html(msg):
    from telethon.extensions import html as tl_html
    return tl_html.unparse(msg.message or "", msg.entities or [])


async def _on_edit(event):
    try:
        msg = event.message
        parsed = parse_message(msg.raw_text or "", _to_html(msg))
        if not parsed:
            return
        post_id, body, meta = parsed
        if meta.get("trunc"):
            return  # mirror was shortened; never write the shortened text back
        async with session() as s:
            post = await s.get(Post, post_id)
            if post is None:
                return
            if (post.text or "") == body:
                return
            post.text = body
            await s.commit()
        logger.info("storage: post %s text updated from the storage channel", post_id)
    except Exception:
        logger.exception("storage: edit sync failed")


async def _restore(bot: Bot, client, chat_id, merge=False):
    """Rebuild posts/channels from the storage channel when the DB has none.

    merge=True (the /restore_recurring command): only re-create RECURRING posts that are
    in the storage channel but missing from the database, and queue them to resume
    posting (scheduled shortly from now, staggered) with their original repeat interval,
    auto-delete time, button and target channels.
    """
    existing = set()
    async with session() as s:
        count = (await s.execute(select(func.count(Post.id)))).scalar() or 0
        if merge:
            existing = {r[0] for r in (await s.execute(select(Post.id))).all()}
    if count and not merge:
        return 0
    if merge:
        logger.warning("storage: restoring missing recurring posts from the storage channel")
    else:
        logger.warning("storage: database has no posts - restoring from the storage channel")
    settings = get_settings()
    default_owner = int(str(settings.allowed_user_ids).split(",")[0].strip() or 0)
    restored = 0
    seen = {}
    async for msg in client.iter_messages(chat_id, limit=None, reverse=True):
        parsed = parse_message(msg.raw_text or "", _to_html(msg)) if (msg.raw_text or "") else None
        if not parsed:
            continue
        post_id, body, meta = parsed
        if post_id in seen:
            continue
        seen[post_id] = True
        if merge and (post_id in existing or not meta.get("rp")):
            continue
        try:
            header = (msg.raw_text or "").split("\n")[0]
            parts = [p.strip() for p in header.split("·")]
            status = PostStatus(parts[1].lower()) if len(parts) > 1 else PostStatus.DRAFT
            if merge:
                status = PostStatus.SCHEDULED
                meta = dict(meta)
                meta["st"] = (datetime.utcnow() + timedelta(seconds=60 + 20 * restored)).isoformat()
                meta["da"] = None
            ctype = ContentType(parts[2].lower()) if len(parts) > 2 else ContentType.TEXT
            owner = meta.get("o") or default_owner
            photo_id = video_id = None
            album_files = []
            if ctype in (ContentType.PHOTO, ContentType.VIDEO):
                data = await client.download_media(msg, bytes)
                from aiogram.types import BufferedInputFile
                if ctype == ContentType.PHOTO:
                    sent = await bot.send_photo(owner, BufferedInputFile(data, "p.jpg"))
                    photo_id = sent.photo[-1].file_id
                else:
                    sent = await bot.send_video(owner, BufferedInputFile(data, "v.mp4"))
                    video_id = sent.video.file_id
                await bot.delete_message(owner, sent.message_id)
            elif ctype == ContentType.ALBUM:
                from aiogram.types import BufferedInputFile
                for mid in meta.get("mids", [msg.id]):
                    m2 = await client.get_messages(chat_id, ids=mid)
                    if m2 is None or not m2.media:
                        continue
                    data = await client.download_media(m2, bytes)
                    is_video = bool(getattr(m2, "video", None))
                    if is_video:
                        sent = await bot.send_video(owner, BufferedInputFile(data, "v.mp4"))
                        album_files.append(("video", sent.video.file_id))
                    else:
                        sent = await bot.send_photo(owner, BufferedInputFile(data, "p.jpg"))
                        album_files.append(("photo", sent.photo[-1].file_id))
                    await bot.delete_message(owner, sent.message_id)

            def _dt(v):
                return datetime.fromisoformat(v) if v else None

            async with session() as s:
                post = Post(
                    id=post_id, owner_user_id=owner, content_type=ctype, text=body or None,
                    photo_file_id=photo_id, video_file_id=video_id, status=status,
                    scheduled_time=_dt(meta.get("st")), auto_delete_seconds=meta.get("ad"),
                    delete_at=_dt(meta.get("da")), repeat_interval_seconds=meta.get("rp"),
                    button_text=meta.get("bt"), button_url=meta.get("bu"),
                )
                s.add(post)
                await s.flush()
                for pos, (mtype, fid) in enumerate(album_files):
                    s.add(PostMediaItem(post_id=post_id, position=pos, media_type=mtype, file_id=fid))
                for chat, title in meta.get("ch", []):
                    ch = (await s.execute(select(Channel).where(Channel.chat_id == chat))).scalars().first()
                    if ch is None:
                        ch = Channel(owner_user_id=owner, chat_id=chat, title=title or str(chat))
                        s.add(ch)
                        await s.flush()
                    s.add(PostTarget(post_id=post_id, channel_id=ch.id))
                await s.commit()
            restored += 1
        except Exception:
            logger.exception("storage: could not restore post %s", post_id)
    logger.warning("storage: restored %s posts from the storage channel", restored)
    return restored


async def _ensure_entity(bot: Bot, client, chat_id) -> bool:
    """Make sure the userbot can see the storage channel.

    The userbot account must be a member of the channel. If Telethon does not know the
    channel yet, have the bot (channel admin) make a one-use invite link and let the
    userbot join with it, then refresh the userbot's dialog cache.
    """
    try:
        await client.get_input_entity(chat_id)
        return True
    except Exception:
        pass
    try:
        from telethon.tl.functions.messages import ImportChatInviteRequest
        link = await bot.create_chat_invite_link(chat_id, name="userbot", member_limit=1)
        invite_hash = link.invite_link.rstrip("/").split("/")[-1].lstrip("+")
        await client(ImportChatInviteRequest(invite_hash))
        logger.info("storage: userbot joined the storage channel via invite link")
    except Exception as exc:  # already a member, or the bot cannot create invite links
        logger.warning("storage: could not add the userbot to the storage channel: %r", exc)
    try:
        await client.get_dialogs()
        await client.get_input_entity(chat_id)
        return True
    except Exception:
        logger.exception("storage: userbot still cannot see the storage channel")
        return False


async def _restore_cmd(message, bot: Bot):
    """/restore_recurring - bring back recurring posts saved in the storage channel."""
    chat_id = int(getattr(get_settings(), "storage_channel_id", 0) or 0)
    if not chat_id:
        await message.answer("The storage channel is not set up (STORAGE_CHANNEL_ID).")
        return
    client = await _get_client(timeout=20)
    if client is None:
        await message.answer("The userbot is not connected, so I cannot read the storage channel right now.")
        return
    await message.answer("Looking for recurring posts in the storage channel...")
    if not await _ensure_entity(bot, client, chat_id):
        await message.answer(
            "The userbot account cannot see the storage channel. Add that Telegram account "
            "to the channel as a member (and make sure this bot is an admin), then send "
            "/restore_recurring again.")
        return
    try:
        n = await _restore(bot, client, chat_id, merge=True)
    except Exception:
        logger.exception("storage: /restore_recurring failed")
        await message.answer("Restore failed - see the logs.")
        return
    if n:
        await message.answer(
            f"Restored {n} recurring post(s). They will start posting within a few minutes, "
            "with the same repeat time, auto-delete and channels as before.")
    else:
        await message.answer("No missing recurring posts found in the storage channel.")


_RESTORE_RE = re.compile(r"^\W*/?\s*restore[_ ]?recurring\b", re.IGNORECASE)


def _is_restore_text(message) -> bool:
    """Match /restore_recurring however it was typed (backticks, dots, @botname, 'restore recurring')."""
    return bool(_RESTORE_RE.match(getattr(message, "text", None) or ""))


def _register_commands():
    try:
        from services import half_reply
        half_reply.router.message.register(_restore_cmd, _is_restore_text)
        logger.info("storage: /restore_recurring command registered")
    except Exception:
        logger.exception("storage: could not register /restore_recurring")


_register_commands()


async def run_storage_sync(bot: Bot) -> None:
    settings = get_settings()
    chat_id = int(getattr(settings, "storage_channel_id", 0) or 0)
    if not chat_id:
        logger.info("storage: STORAGE_CHANNEL_ID not set - storage channel disabled")
        return
    try:
        await _ensure_table()
    except Exception:
        logger.exception("storage: cannot create map table - disabled")
        return

    client = await _get_client()
    if client is None:
        logger.warning("storage: userbot not available - edit sync and restore disabled, mirroring only")
    else:
        try:
            await _restore(bot, client, chat_id)
        except Exception:
            logger.exception("storage: restore failed")
        try:
            from telethon import events
            client.add_event_handler(_on_edit, events.MessageEdited(chats=chat_id))
            logger.info("storage: edit sync active for channel %s", chat_id)
        except Exception:
            logger.exception("storage: could not enable edit sync")

    while True:
        try:
            await _mirror_pass(bot, chat_id)
        except Exception:
            logger.exception("storage: mirror pass failed")
        await asyncio.sleep(POLL_SECONDS)
