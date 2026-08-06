import asyncio
import json
import logging
from datetime import datetime, timedelta

from aiogram import Bot, types
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from db import session
from models import ContentType, Post, PostMediaItem, PostStatus, PostTarget

logger = logging.getLogger(__name__)


def _target_message_ids(target: PostTarget) -> list[int]:
    """All message ids a PostTarget covers - the primary one plus, for an
    ALBUM post, every other item's message id stashed in extra_message_ids."""
    ids = [target.message_id] if target.message_id is not None else []
    if target.extra_message_ids:
        try:
            ids.extend(json.loads(target.extra_message_ids))
        except Exception:
            pass
    return ids


async def run_scheduler_loop(bot: Bot) -> None:
    """Background scheduler that handles auto-deletion and recurring posts.

    Two things happen here, both keyed off the SAME Post row so a repeating
    post keeps its original id/edit-history instead of spawning new rows
    every cycle:

    1. Posts with status == SENT and delete_at <= now() get their messages
       deleted from every channel. If the post has repeat_interval_seconds
       set, it is then recycled back to SCHEDULED (targets cleared, a fresh
       scheduled_time set) instead of being left DELETED - see
       services/scheduler.py:run_post_send_loop, which will pick it back up
       and re-send it like any other scheduled post.
    2. Repeating posts that have NO auto-delete (so branch 1 never touches
       them, since delete_at stays None forever) are recycled the same way
       once repeat_interval_seconds has elapsed since they were last sent.
    """
    while True:
        try:
            now = datetime.utcnow()
            async with session() as s:
                # selectinload both hops here - post.targets and each
                # target.channel are touched below as plain attribute access,
                # which (without eager loading) triggers an implicit lazy
                # SELECT that raises sqlalchemy.exc.MissingGreenlet. That
                # would have been silently swallowed by this loop's own
                # broad except-and-retry below, quietly breaking
                # auto-delete for every SENT post every single cycle.
                q = select(Post).where(
                    Post.status == PostStatus.SENT, Post.delete_at != None, Post.delete_at <= now
                ).options(selectinload(Post.targets).selectinload(PostTarget.channel))
                res = await s.execute(q)
                posts = res.scalars().all()
                for post in posts:
                    all_deleted = True
                    for target in post.targets:
                        ids = _target_message_ids(target)
                        if not ids:
                            continue
                        for mid in ids:
                            try:
                                await bot.delete_message(chat_id=target.channel.chat_id, message_id=mid)
                            except Exception:
                                all_deleted = False
                        target.message_id = None
                        target.extra_message_ids = None
                        target.sent_at = None
                    if post.repeat_interval_seconds:
                        post.status = PostStatus.SCHEDULED
                        post.scheduled_time = now + timedelta(seconds=post.repeat_interval_seconds)
                        post.delete_at = None
                    elif all_deleted:
                        post.status = PostStatus.DELETED

                # Repeating posts with no auto-delete never get a delete_at,
                # so they'd never be touched above - recycle them here based
                # on time-since-last-sent instead.
                q2 = select(Post).where(
                    Post.status == PostStatus.SENT,
                    Post.auto_delete_seconds == None,
                    Post.repeat_interval_seconds != None,
                ).options(selectinload(Post.targets))
                res2 = await s.execute(q2)
                for post in res2.scalars().all():
                    sent_times = [t.sent_at for t in post.targets if t.sent_at]
                    if not sent_times:
                        continue
                    posted_at = max(sent_times)
                    if posted_at + timedelta(seconds=post.repeat_interval_seconds) <= now:
                        for target in post.targets:
                            target.message_id = None
                            target.extra_message_ids = None
                            target.sent_at = None
                        post.status = PostStatus.SCHEDULED
                        post.scheduled_time = now

                await s.commit()
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(5)


def _build_media_group(items: list[PostMediaItem], caption: str | None) -> list:
    media = []
    for i, it in enumerate(items):
        cap = caption if i == 0 else None
        if it.media_type == "video":
            media.append(types.InputMediaVideo(media=it.file_id, caption=cap))
        else:
            media.append(types.InputMediaPhoto(media=it.file_id, caption=cap))
    return media


def _build_button(button_text: str | None, button_url: str | None) -> types.InlineKeyboardMarkup | None:
    """Same construction as handlers/compose.py:_build_button - kept as a
    separate copy here (rather than imported) so this background loop has
    no dependency on the handlers package, matching the rest of this
    module's self-contained style. Both fields must be set for a button to
    show - see models.Post.button_text/button_url.
    """
    if not button_text or not button_url:
        return None
    return types.InlineKeyboardMarkup(
        inline_keyboard=[[types.InlineKeyboardButton(text=button_text, url=button_url)]]
    )


async def run_post_send_loop(bot: Bot) -> None:
    """Background scheduler that sends Posts once their scheduled_time arrives.

    Scheduled posts are created with PostTarget rows already attached
    (message_id=None) recording which channels they should go to - see
    handlers/compose.py. This loop sends to each of those targets and marks
    the post SENT, starting its auto-delete timer (if any) from send time.
    """
    while True:
        try:
            now = datetime.utcnow()
            async with session() as s:
                q = select(Post).where(Post.status == PostStatus.SCHEDULED, Post.scheduled_time <= now)
                res = await s.execute(q)
                posts = res.scalars().all()

                for post in posts:
                    target_q = select(PostTarget).where(PostTarget.post_id == post.id).options(
                        selectinload(PostTarget.channel)
                    )
                    target_res = await s.execute(target_q)
                    targets = target_res.scalars().all()

                    media_items: list[PostMediaItem] = []
                    if post.content_type == ContentType.ALBUM:
                        mi_q = select(PostMediaItem).where(
                            PostMediaItem.post_id == post.id
                        ).order_by(PostMediaItem.position)
                        mi_res = await s.execute(mi_q)
                        media_items = mi_res.scalars().all()

                    # Built once per post, reused for every target below.
                    # None for ALBUM posts regardless of what's stored on
                    # the row - Telegram's send_media_group has no
                    # reply_markup parameter at all (same hard platform
                    # limitation already documented in services/reposter.py
                    # for reposts and in handlers/compose.py's ask_button,
                    # which never lets an album post collect a button in
                    # the first place).
                    reply_markup = (
                        None if post.content_type == ContentType.ALBUM
                        else _build_button(post.button_text, post.button_url)
                    )

                    any_sent = False
                    for target in targets:
                        if target.message_id is not None:
                            continue
                        try:
                            if post.content_type == ContentType.ALBUM and media_items:
                                media = _build_media_group(media_items, post.text or None)
                                sent_list = await bot.send_media_group(
                                    chat_id=target.channel.chat_id, media=media
                                )
                                target.message_id = sent_list[0].message_id
                                if len(sent_list) > 1:
                                    target.extra_message_ids = json.dumps(
                                        [m.message_id for m in sent_list[1:]]
                                    )
                            elif post.content_type == ContentType.PHOTO and post.photo_file_id:
                                msg = await bot.send_photo(
                                    chat_id=target.channel.chat_id,
                                    photo=post.photo_file_id,
                                    caption=post.text or None,
                                    reply_markup=reply_markup,
                                )
                                target.message_id = msg.message_id
                            elif post.content_type == ContentType.VIDEO and post.video_file_id:
                                msg = await bot.send_video(
                                    chat_id=target.channel.chat_id,
                                    video=post.video_file_id,
                                    caption=post.text or None,
                                    reply_markup=reply_markup,
                                )
                                target.message_id = msg.message_id
                            else:
                                msg = await bot.send_message(
                                    chat_id=target.channel.chat_id,
                                    text=post.text or "",
                                    reply_markup=reply_markup,
                                )
                                target.message_id = msg.message_id
                            target.sent_at = now
                            any_sent = True
                        except Exception:
                            logger.exception(
                                "Failed to send post_id=%s to chat_id=%s",
                                post.id, target.channel.chat_id,
                            )
                            continue

                    if not any_sent and post.repeat_interval_seconds:
                        # Nothing actually sent this cycle for a repeating
                        # post - leave it SCHEDULED so this loop's next pass
                        # (30s later) retries automatically. Marking it SENT
                        # here with no sent_at anywhere would permanently
                        # kill the repeat cycle for posts with no
                        # auto-delete: run_scheduler_loop only recycles those
                        # based on target.sent_at, and if that stays empty
                        # forever, the post never becomes due again - which
                        # is exactly the "repeat stops after one send hiccup"
                        # bug this guards against.
                        continue

                    post.status = PostStatus.SENT
                    if post.auto_delete_seconds:
                        post.delete_at = now + timedelta(seconds=post.auto_delete_seconds)
                await s.commit()
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(5)
