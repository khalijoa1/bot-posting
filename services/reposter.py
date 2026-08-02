from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any

from aiogram import Bot
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputMediaVideo,
    LinkPreviewOptions,
)
from sqlalchemy import select
from sqlalchemy import or_ as _sa_or  # noqa: F401 - kept for compatibility, unused after the identifier-matching fix below

from db import session
from models import Channel, ContentType, Post, PostMediaItem, PostStatus, PostTarget, RepostRule, SourceChannel

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
# A bare Telegram username on its own (used to recognize a fallback/default
# link value like "mychannel" typed without "@" or a full t.me/ URL).
_BARE_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,32}$")


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


def _strip_masked_links(text: str | None, entities, fallback: str | None) -> str | None:
    """Catch "text formatted as a link" - Telegram's own "Create a link"
    formatting option, which lets a source show arbitrary anchor text (e.g.
    "Join here" or "Click for more") while an entirely different URL is
    hidden underneath it. Telethon reports this as a MessageEntityTextUrl
    entity on the message; critically, the hidden URL never appears
    anywhere in the message's plain text, so _scrub_remaining_links's
    regex - which can only see literal characters - has nothing to match
    and lets the anchor text straight through, still fully clickable and
    still pointing at the source's own link.

    This walks the message's real entities (independent of what the
    visible text says) and swaps every masked link's anchor text for the
    same fallback used for plain links elsewhere - so the hidden URL is
    gone either way, exactly like a bare https://... link would be
    treated.

    Entity offsets/lengths are defined in UTF-16 code units per the
    Telegram Bot API / MTProto spec, not Python string indices (which are
    per code point) - a message containing any character outside the
    Basic Multilingual Plane (many emoji) before the link would otherwise
    make the slice land on the wrong characters. Encoding to UTF-16 first
    keeps the offsets valid.
    """
    if not text or not entities:
        return text

    masked = [e for e in entities if type(e).__name__ == "MessageEntityTextUrl"]
    if not masked:
        return text

    replacement = fallback if fallback else ""
    buf = text.encode("utf-16-le")
    replacement_bytes = replacement.encode("utf-16-le")
    # Rightmost-first so replacing one span doesn't shift the byte offsets
    # of spans still waiting to be processed.
    for e in sorted(masked, key=lambda e: e.offset, reverse=True):
        start = e.offset * 2
        end = start + e.length * 2
        if start < 0 or end > len(buf) or start > end:
            continue
        buf = buf[:start] + replacement_bytes + buf[end:]
    return buf.decode("utf-16-le", errors="ignore")


def _rule_replacement_config(rule: RepostRule, dest: Channel) -> tuple[dict[str, str], str | None]:
    """Extract this rule's {old: new} replacement mapping and default/
    fallback link from replacements_json, scoped the same way this always
    has been (a rule-wide "default" mapping, or an older per-destination-
    keyed one for rules created before that existed). Shared by the
    message-body replacement pass (_apply_replacements) and the inline
    source-button URL translator (_build_source_buttons) below, so a
    mapping or default link set once in the UI applies identically to
    both - a source's own link gets replaced the same way whether it
    showed up as plain text, a masked "text formatted as a link", or a
    button.
    """
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
    return mapping, fallback


def _apply_replacements(text: str | None, entities, rule: RepostRule, dest: Channel) -> str | None:
    """Swap the source channel's links/usernames for the operator's own.

    Three layers, in order:
    1. Masked-link stripping (_strip_masked_links) - has to run FIRST and
       against the untouched original text, since it relies on entity
       offsets that are only valid before any other substitution shifts
       the text around.
    2. Explicit "old -> new" pairs configured on the rule (via the bot's
       Forwarding UI, or hand-edited on an older rule) - exact substring
       match.
    3. A mandatory scrub pass (_scrub_remaining_links) that catches every
       plain link or @mention still left afterward and replaces it with
       the rule's configured fallback link/username, or strips it if no
       fallback is set. This always runs, even if the rule has no
       replacements configured at all - the source's own link or username
       must never be posted as-is, one way or another.

    IMPORTANT: step 3's scrub pass has to run over the WHOLE text (that's
    what guarantees a link nobody explicitly listed still gets caught), but
    that means it would also catch and re-replace whatever step 2 just
    inserted whenever the "new" value is itself a link or @mention - which
    it almost always is (an operator's own channel link/username). Left
    unguarded, a specific "old -> new" replacement would get immediately
    undone by the scrub pass right after being applied. To prevent that,
    each replacement is swapped in as an inert placeholder token first (one
    that can't match a link/mention regex), the scrub pass runs against
    that safely-placeholdered text, and the real replacement values are
    substituted back in only afterward.
    """
    if not text:
        return text

    mapping, fallback = _rule_replacement_config(rule, dest)

    text = _strip_masked_links(text, entities, fallback)

    placeholders: dict[str, str] = {}
    for i, (old, new) in enumerate(mapping.items()):
        if not old:
            continue
        token = f"\x00REPL{i}\x00"
        placeholders[token] = new
        text = text.replace(old, token)

    text = _scrub_remaining_links(text, fallback)

    for token, new in placeholders.items():
        text = text.replace(token, new)

    return text


def _coerce_button_url(value: str) -> str | None:
    """Turn whatever a rule's specific replacement value or default/
    fallback link is stored as (a full https:// URL, a tg:// deep link, an
    "@username", a bare "t.me/..." link, or a bare username typed with
    neither) into a URL actually usable on a Telegram inline button - the
    Bot API rejects a button whose url isn't a real URL, so a bare
    "@mychannel" (perfectly valid as inline *text*, which is all the other
    replacement paths ever needed until now) would otherwise make the
    whole send fail. Returns None if the value can't be turned into
    anything URL-shaped, so the caller can drop that button instead of
    crashing the repost over it.
    """
    value = value.strip()
    if not value:
        return None
    if value.startswith(("http://", "https://", "tg://")):
        return value
    if value.startswith("@"):
        value = value[1:]
        return f"https://t.me/{value}" if value else None
    if value.lower().startswith("t.me/") or value.lower().startswith("www.t.me/"):
        return f"https://{value.split('://')[-1]}"
    if _BARE_USERNAME_RE.match(value):
        return f"https://t.me/{value}"
    return None


def _translate_button_url(url: str, mapping: dict[str, str], fallback: str | None) -> str | None:
    """Resolve what a single source inline button's URL should become:
    the rule's specific replacement for that exact URL if one is
    configured, otherwise the rule's default/fallback link, otherwise
    None (meaning: drop the button rather than forward the source's own
    link) - the same precedence _apply_replacements uses for text."""
    candidate = mapping.get(url) or fallback
    if not candidate:
        return None
    return _coerce_button_url(candidate)


def _build_source_buttons(message, mapping: dict[str, str], fallback: str | None) -> list[list[InlineKeyboardButton]]:
    """Forward the SOURCE post's own inline URL button(s) - e.g. a "Join
    our channel" button a source attaches to its posts - with each
    button's underlying URL swapped through the exact same replacement
    mapping / default-link fallback used for the message body, instead of
    either leaking the source's own link untouched or (the previous
    behavior) silently dropping the button entirely regardless of what it
    pointed to. A button whose URL isn't covered by a specific mapping and
    has no default link configured is dropped - same "no fallback -> just
    remove it" behavior _scrub_remaining_links already applies to a plain
    link in text - rather than posted with the source's own link intact.

    Non-URL buttons (callback/switch-inline/game/login/etc.) only make
    sense wired to the SOURCE's own bot and chat, so they're skipped
    rather than forwarded - reposted onto a different bot/channel they'd
    be either non-functional or actively misleading.
    """
    markup = getattr(message, "reply_markup", None)
    rows = getattr(markup, "rows", None)
    if not rows:
        return []

    built: list[list[InlineKeyboardButton]] = []
    for row in rows:
        out_row: list[InlineKeyboardButton] = []
        for btn in getattr(row, "buttons", None) or []:
            src_url = getattr(btn, "url", None)
            if not src_url:
                continue
            new_url = _translate_button_url(src_url, mapping, fallback)
            if not new_url:
                continue
            label = getattr(btn, "text", None) or "🔗 Link"
            out_row.append(InlineKeyboardButton(text=label, url=new_url))
        if out_row:
            built.append(out_row)
    return built


def _apply_prefix(text: str | None, rule: RepostRule) -> str | None:
    """Prepend the rule's configured prefix text (with a blank line after
    it) to a forwarded message/caption. If there's no body text at all,
    the prefix becomes the whole message on its own."""
    if not rule.prefix_text:
        return text
    return f"{rule.prefix_text}\n\n{text}" if text else rule.prefix_text


def _build_button(rule: RepostRule) -> InlineKeyboardMarkup | None:
    """Build the rule's single, operator-configured inline link button, if
    both a label and a URL are set for it - this is separate from (and
    always added in addition to) any of the SOURCE's own buttons handled
    by _build_source_buttons above. Telegram inline buttons only support
    plain text - no custom emoji rendering and no background/color
    styling - so the label is shown exactly as typed (emoji characters in
    the text itself still work fine, e.g. "🔗 Join VIP")."""
    if not rule.inline_button_text or not rule.inline_button_url:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=rule.inline_button_text, url=rule.inline_button_url)]]
    )


def _build_reply_markup(message, rule: RepostRule, dest: Channel) -> InlineKeyboardMarkup | None:
    """Combine the rule's own configured button (if any) with the
    source post's own button(s) - link-translated via
    _build_source_buttons - into a single keyboard. The rule's own button
    always comes first/on top; the source's (translated) buttons follow
    beneath it. Returns None (no keyboard at all) only if neither is
    present, which keeps behavior identical to before this feature existed
    for posts that had no buttons of either kind."""
    mapping, fallback = _rule_replacement_config(rule, dest)

    rows: list[list[InlineKeyboardButton]] = []
    own = _build_button(rule)
    if own:
        rows.extend(own.inline_keyboard)
    rows.extend(_build_source_buttons(message, mapping, fallback))

    if not rows:
        return None
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_link_preview(rule: RepostRule) -> LinkPreviewOptions | None:
    """Translate the rule's link_preview_mode into Telegram's
    link_preview_options for a text message. Only meaningful on
    send_message - photo/video captions never get a link preview at all,
    that's a Telegram platform behavior, not something this app controls."""
    mode = rule.link_preview_mode
    if not mode or mode == "default":
        return None
    if mode == "disabled":
        return LinkPreviewOptions(is_disabled=True)
    if mode == "small":
        return LinkPreviewOptions(is_disabled=False, prefer_small_media=True)
    if mode == "large":
        return LinkPreviewOptions(is_disabled=False, prefer_large_media=True)
    if mode == "above":
        return LinkPreviewOptions(is_disabled=False, show_above_text=True)
    return None


# ---------------------------------------------------------------------------
# Album (media-group) buffering.
#
# Telegram delivers a multi-photo/video source post as a SEPARATE Telethon
# NewMessage event per item, all sharing the same `message.grouped_id` -
# exactly analogous to aiogram's own media_group_id on the composing side
# (see handlers/common.py:collect_album_item, which does the same thing for
# an operator's own uploads). Without this buffering, each item was being
# looked up and reposted independently the instant its own event fired,
# which is exactly the reported bug: "if the source posts an album it
# should do the same on the rule channel" - instead the destination got N
# separate single-item messages instead of one grouped album.
#
# Keyed by (chat_id, grouped_id) since Telethon's grouped_id integer is
# only guaranteed unique within a single chat, not globally.
# ---------------------------------------------------------------------------

# Each buffered entry is (message_id, caption, entities, kind, download_task) -
# the download itself is kicked off and stored as a Task the moment the item
# is registered (see _handle_album_item), NOT awaited to completion first.
# That decoupling is what lets the debounce timer below track "did every
# item arrive in time" independently of "how long did each item's own
# download take" - see the long comment in _handle_album_item for the bug
# this fixes.
_album_buffers: dict[tuple[int, int], list[tuple[int, str, Any, str, "asyncio.Future"]]] = {}
_album_tasks: dict[tuple[int, int], asyncio.Task] = {}
# A little more generous than handlers/common.py's 0.9s debounce: source
# albums can include large videos whose download can itself take a couple
# of seconds, so a slightly wider window reduces how often a genuinely
# fast burst of items still ends up split by the timer alone (downloads
# finishing late no longer cause a split at all now - see above - but a
# wider window still means fewer, larger _finalize batches instead of
# many tiny ones when items trickle in close to the boundary).
_ALBUM_DEBOUNCE_SECONDS = 2.5


def _build_media_group(items: list[dict], caption: str | None) -> list:
    """Same idea as handlers/compose.py's own _build_media_group, but built
    from raw downloaded bytes (BufferedInputFile) instead of Bot-API
    file_id strings - a repost has no such file_id to reuse, since the
    media was read from the source channel via Telethon, not uploaded by
    an operator through this bot. Only the first item gets the caption,
    per Telegram's rule that an album shows a single caption for the whole
    group."""
    media = []
    for i, it in enumerate(items):
        cap = caption if i == 0 else None
        filename = "repost.mp4" if it["kind"] == "video" else "repost.jpg"
        buf = BufferedInputFile(it["bytes"], filename=filename)
        if it["kind"] == "video":
            media.append(InputMediaVideo(media=buf, caption=cap))
        else:
            media.append(InputMediaPhoto(media=buf, caption=cap))
    return media


async def _repost_album(bot: Bot, items: list[dict], source: SourceChannel, rules: list[RepostRule]) -> None:
    """Repost a complete, buffered source album as one Telegram album per
    matching rule, via bot.send_media_group - mirrors _repost_single below,
    but for the multi-item case."""
    cap_item = next((it for it in items if it["caption"]), None)
    text = cap_item["caption"] if cap_item else None
    entities = cap_item["entities"] if cap_item else None

    async with session() as s:
        post = Post(
            owner_user_id=0,
            content_type=ContentType.ALBUM,
            text=text,
            status=PostStatus.SENT,
            created_at=datetime.utcnow(),
        )
        s.add(post)
        await s.flush()

        for i, it in enumerate(items):
            # file_id is left empty here - unlike an operator-composed
            # album (handlers/compose.py), a repost's media came in as raw
            # bytes from Telethon, so there's no Bot-API file_id to record
            # that could actually be reused later. This row still exists so
            # /myposts-style bookkeeping reflects "an album with N items".
            s.add(PostMediaItem(post_id=post.id, position=i, media_type=it["kind"], file_id=""))
        await s.flush()

        for rule in rules:
            qch = select(Channel).where(Channel.id == rule.destination_channel_id)
            rch = await s.execute(qch)
            dest = rch.scalars().first()
            if not dest:
                continue

            cleaned_text = _apply_replacements(text, entities, rule, dest)
            context = {
                "original_text": cleaned_text or "",
                "source_title": source.title or "",
                "source_username": source.identifier,
            }
            caption = _render_template(rule.caption_template, context) if rule.caption_template else cleaned_text
            caption = _apply_prefix(caption, rule)
            # Telegram's send_media_group has no reply_markup parameter at
            # all, so NEITHER a rule's own configured button NOR any of the
            # source's own buttons can apply to an album repost - that's a
            # Telegram platform limitation (albums can't carry an inline
            # keyboard at all, on any bot), not something skipped by
            # mistake here.
            media = _build_media_group(items, caption or None)

            try:
                sent = await bot.send_media_group(chat_id=dest.chat_id, media=media)
                first, rest = sent[0], sent[1:]
                pt = PostTarget(
                    post_id=post.id,
                    channel_id=dest.id,
                    message_id=first.message_id,
                    extra_message_ids=json.dumps([m.message_id for m in rest]) if rest else None,
                    sent_at=datetime.utcnow(),
                )
                s.add(pt)
                await s.commit()
                logger.info(
                    "repost-debug: successfully reposted ALBUM (%d items) into dest channel id=%s chat_id=%s title=%r",
                    len(items), dest.id, dest.chat_id, dest.title,
                )
            except Exception:
                logger.exception("Failed to repost album into channel %s", dest.title)
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


async def _handle_album_item(bot: Bot, event, source: SourceChannel, rules: list[RepostRule]) -> None:
    """Buffer one item of a source album and, once no further item has
    arrived for _ALBUM_DEBOUNCE_SECONDS, repost the whole thing as one
    album via _repost_album - instead of this item going out on its own
    the instant its own NewMessage event fires."""
    message = event.message
    key = (event.chat_id, message.grouped_id)

    if not (message.photo or message.video):
        # Some other media type inside the album this app doesn't support
        # reposting (e.g. a document/audio) - drop just this one item
        # rather than losing the whole album over it.
        logger.info(
            "repost-debug: album item chat_id=%s grouped_id=%s message_id=%s has no supported photo/video - skipped",
            event.chat_id, message.grouped_id, message.id,
        )
        return

    # Register this item and (re)start the debounce timer BEFORE awaiting
    # its download - not after, like an earlier version of this function
    # did. Downloads take a highly variable amount of time (a large video
    # can take several seconds longer than a small photo), and awaiting
    # the download first meant a slow item could still be mid-download
    # when a FASTER sibling item's debounce timer fired: that sibling's
    # timer had no way to know the slow item existed yet, so it finalized
    # and sent an incomplete album, and the slow item then landed moments
    # later as its own separate post - "posting separately in some
    # instances" was near-guaranteed to be download-speed-dependent,
    # exactly matching what was reported. Registering the download as its
    # own task immediately, before any await, means every item that
    # actually arrived within the debounce window is counted regardless
    # of how long its own download subsequently takes - _finalize below
    # then waits for every registered download to actually finish before
    # building the album, howeverlong that takes.
    download_task = asyncio.ensure_future(message.download_media(bytes))
    pending = _album_buffers.setdefault(key, [])
    pending.append((message.id, (message.message or "").strip(),
                     getattr(message, "entities", None),
                     "video" if message.video else "photo", download_task))

    existing = _album_tasks.get(key)
    if existing and not existing.done():
        existing.cancel()

    async def _finalize():
        try:
            await asyncio.sleep(_ALBUM_DEBOUNCE_SECONDS)
        except asyncio.CancelledError:
            return
        entries = _album_buffers.pop(key, [])
        _album_tasks.pop(key, None)
        if not entries:
            return

        collected: list[dict] = []
        for msg_id, caption, entities, kind, task in entries:
            try:
                downloaded = await task
            except Exception:
                logger.exception(
                    "repost-debug: album item chat_id=%s grouped_id=%s message_id=%s failed to download - skipped",
                    event.chat_id, message.grouped_id, msg_id,
                )
                continue
            if not isinstance(downloaded, (bytes, bytearray)):
                continue
            collected.append({"kind": kind, "bytes": downloaded, "caption": caption, "entities": entities, "id": msg_id})

        if not collected:
            return
        # Telethon can deliver album items very slightly out of order;
        # message id order matches the order they were actually posted in.
        collected.sort(key=lambda it: it["id"])
        logger.info(
            "repost-debug: album chat_id=%s grouped_id=%s complete with %d item(s) - reposting",
            event.chat_id, message.grouped_id, len(collected),
        )
        try:
            await _repost_album(bot, collected, source, rules)
        except Exception:
            logger.exception("Failed to finalize/repost album chat_id=%s grouped_id=%s", event.chat_id, message.grouped_id)

    _album_tasks[key] = asyncio.create_task(_finalize())


async def _repost_single(bot: Bot, message, source: SourceChannel, rules: list[RepostRule]) -> None:
    """Repost one non-album message - the original per-message send path."""
    text = message.message or None
    entities = getattr(message, "entities", None)
    photo_bytes: bytes | None = None
    video_bytes: bytes | None = None
    if message.photo:
        downloaded = await message.download_media(bytes)
        photo_bytes = downloaded if isinstance(downloaded, (bytes, bytearray)) else None
    elif message.video:
        downloaded = await message.download_media(bytes)
        video_bytes = downloaded if isinstance(downloaded, (bytes, bytearray)) else None

    if photo_bytes:
        content_type = ContentType.PHOTO
    elif video_bytes:
        content_type = ContentType.VIDEO
    else:
        content_type = ContentType.TEXT

    async with session() as s:
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

            cleaned_text = _apply_replacements(text, entities, rule, dest)

            context = {
                "original_text": cleaned_text or "",
                "source_title": source.title or "",
                "source_username": source.identifier,
            }
            caption = _render_template(rule.caption_template, context) if rule.caption_template else cleaned_text
            caption = _apply_prefix(caption, rule)
            # Combines the rule's own configured button (if set) with the
            # SOURCE message's own inline URL button(s), each translated
            # through this rule's replacement mapping / default link -
            # see _build_reply_markup.
            reply_markup = _build_reply_markup(message, rule, dest)

            try:
                if photo_bytes:
                    sent = await bot.send_photo(
                        chat_id=dest.chat_id,
                        photo=BufferedInputFile(photo_bytes, filename="repost.jpg"),
                        caption=caption,
                        reply_markup=reply_markup,
                    )
                elif video_bytes:
                    sent = await bot.send_video(
                        chat_id=dest.chat_id,
                        video=BufferedInputFile(video_bytes, filename="repost.mp4"),
                        caption=caption,
                        reply_markup=reply_markup,
                    )
                else:
                    sent = await bot.send_message(
                        chat_id=dest.chat_id,
                        text=caption or "",
                        reply_markup=reply_markup,
                        link_preview_options=_build_link_preview(rule),
                    )

                pt = PostTarget(post_id=post.id, channel_id=dest.id, message_id=sent.message_id, sent_at=datetime.utcnow())
                s.add(pt)
                await s.commit()
                logger.info("repost-debug: successfully reposted into dest channel id=%s chat_id=%s title=%r", dest.id, dest.chat_id, dest.title)
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


async def handle_incoming_message(bot: Bot, event) -> None:
    """Process a Telethon NewMessage event and repost it to matching destinations.

    `bot` is the aiogram Bot, used to post into destination channels (where it's
    an admin). `event` is a telethon events.NewMessage.Event - the userbot
    connection is only used to *read* the source channel; posting is always
    done through the regular Bot API so destination behaviour (permissions,
    formatting) matches the rest of the app.

    If the message is part of a source album (message.grouped_id set), it's
    handed off to the buffering path (_handle_album_item) instead of being
    reposted immediately on its own - see the "Album (media-group)
    buffering" section above for why.
    """
    message = event.message
    chat = await event.get_chat()
    if chat is None:
        logger.info("repost-debug: event.get_chat() returned None for chat_id=%s - skipping", event.chat_id)
        return

    identifier_str = str(event.chat_id)
    username = getattr(chat, "username", None) or ""

    # Match against every form a source's identifier could have been saved
    # in: the numeric chat id, the bare username Telethon reports (no "@"),
    # or an "@"-prefixed username.
    candidates = {identifier_str}
    if username:
        candidates.add(username)
        candidates.add(f"@{username}")

    # TEMPORARY diagnostic logging - trace every incoming message through the
    # matching pipeline so a "still not forwarding" report can be root-caused
    # from the logs instead of guessed at. Safe to remove once forwarding is
    # confirmed working end-to-end.
    logger.info(
        "repost-debug: incoming message chat_id=%s username=%r candidates=%s grouped_id=%s",
        identifier_str, username, sorted(candidates), getattr(message, "grouped_id", None),
    )

    async with session() as s:
        q = select(SourceChannel).where(SourceChannel.identifier.in_(candidates))
        res = await s.execute(q)
        source = res.scalars().first()
        if not source:
            all_sources_q = select(SourceChannel.identifier)
            all_sources_res = await s.execute(all_sources_q)
            known = [row[0] for row in all_sources_res.all()]
            logger.info(
                "repost-debug: no SourceChannel matched candidates=%s - known identifiers in DB=%s",
                sorted(candidates), known,
            )
            return

        logger.info("repost-debug: matched source id=%s identifier=%r title=%r", source.id, source.identifier, source.title)

        q2 = select(RepostRule).where(RepostRule.source_channel_id == source.id)
        res2 = await s.execute(q2)
        rules = res2.scalars().all()
        if not rules:
            logger.info("repost-debug: source id=%s matched but has NO RepostRule rows - nothing to forward to", source.id)
            return

        logger.info("repost-debug: source id=%s has %d rule(s) - proceeding to repost", source.id, len(rules))

    # source/rules are plain ORM objects loaded with expire_on_commit=False
    # (see db.py), so they stay usable after the session above closes -
    # needed here since the album path defers sending until the debounce
    # timer fires, well after this function itself has returned.
    if getattr(message, "grouped_id", None):
        await _handle_album_item(bot, event, source, rules)
        return

    await _repost_single(bot, message, source, rules)
