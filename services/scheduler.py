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
                _debug_recurring = await s.execute(
                    select(Post).where(Post.repeat_interval_seconds != None)
                )
                _recurring_rows = _debug_recurring.scalars().all()
                if _recurring_rows:
                    logger.info(
                        "run_scheduler_loop: ALL recurring posts (any status) now=%s: %s",
                        now,
                        [
                            (p.id, p.status.value, p.repeat_interval_seconds, p.delete_at, p.scheduled_time)
                            for p in _recurring_rows
                        ],
                    )
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
                if posts:
                    logger.info(
                        "run_scheduler_loop: delete-branch now=%s matched %d due post(s): %s",
                        now, len(posts), [(p.id, p.repeat_interval_seconds, p.delete_at) for p in posts],
                    )
                for post in posts:
                    all_deleted = True
                    # Base the next occurrence on when this post was actually
                    # last sent, not on whichever moment the 30s poll happens to
                    # notice delete_at has passed - otherwise the visible gap
                    # between posts silently becomes
                    # auto_delete_seconds + repeat_interval_seconds instead of
                    # just repeat_interval_seconds (target.sent_at gets cleared
                    # below before recycling, so it must be captured first).
                    _sent_times = [t.sent_at for t in post.targets if t.sent_at]
                    posted_at = max(_sent_times) if _sent_times else now
                    for target in post.targets:
                        ids = _target_message_ids(target)
                        if not ids:
                            continue
                        for mid in ids:
                            try:
                                await bot.delete_message(chat_id=target.channel.chat_id, message_id=mid)
                            except Exception:
                                all_deleted = False
                                logger.exception(
                                    "run_scheduler_loop: failed to delete message_id=%s from chat_id=%s for post_id=%s",
                                    mid, target.channel.chat_id, post.id,
                                )
                        target.message_id = None
                        target.extra_message_ids = None
                        target.sent_at = None
                    if post.repeat_interval_seconds:
                        post.status = PostStatus.SCHEDULED
                        next_time = posted_at + timedelta(seconds=post.repeat_interval_seconds)
                        post.scheduled_time = next_time if next_time > now else now
                        post.delete_at = None
                        logger.info(
                            "run_scheduler_loop: recycled post_id=%s back to SCHEDULED for %s (all_deleted=%s)",
                            post.id, post.scheduled_time, all_deleted,
                        )
                    elif all_deleted:
                        post.status = PostStatus.DELETED

                    # Commit after each post instead of batching every due
                    # post into one long-held transaction - the loop body
                    # above does a bot.delete_message() Telegram API call
                    # per message, which can take a while under a burst of
                    # many due posts at once, and this repo's single SQLite
                    # file only tolerates one write lock at a time (see
                    # db.py's WAL/busy_timeout comment). Committing per-post
                    # bounds how long each write lock is held to roughly one
                    # post's worth of network calls instead of the whole
                    # batch, cutting the odds of a collision with
                    # run_post_send_loop's own commits during a simultaneous
                    # burst - this is what caused the multi-hour "database
                    # is locked" storm seen in production on 2026-09-13.
                    await s.commit()

                # Repeating posts with no auto-delete never get a delete_at,
                # so they'd never be touched above - recycle them here based
                # on time-since-last-sent instead.
                q2 = select(Post).where(
                    Post.status == PostStatus.SENT,
                    Post.auto_delete_seconds == None,
                    Post.repeat_interval_seconds != None,
                ).options(selectinload(Post.targets))
                res2 = await s.execute(q2)
                _q2_posts = res2.scalars().all()
                if _q2_posts:
                    logger.info(
                        "run_scheduler_loop: no-auto-delete recycle branch now=%s matched %d repeat-eligible post(s): %s",
                        now, len(_q2_posts), [p.id for p in _q2_posts],
                    )
                for post in _q2_posts:
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
                        logger.info(
                            "run_scheduler_loop: recycled post_id=%s (no-auto-delete branch) back to SCHEDULED, last posted_at=%s",
                            post.id, posted_at,
                        )

                await s.commit()
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("run_scheduler_loop iteration failed")
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
    """Build the single-button InlineKeyboardMarkup for a post, or None if
    no button was configured. Mirrors handlers/compose.py:_build_button -
    kept as a separate copy here (rather than importing) to avoid coupling
    this background loop to the compose handler module. Both fields must
    be set - see models.Post.button_text/button_url.

    Every send in this loop is a resend: the very first send happens via
    handlers/compose.py's post_now/_send_to_channel, which already builds
    and attaches this markup - but every SCHEDULED send and every
    recurring-post recycle (see run_scheduler_loop above) goes through
    THIS loop instead, and until now this loop never attached the button
    at all. That's why a post created with a button would show it once
    (on an immediate "Post Now") but lose it on every scheduled/repeat
    send - the button config was saved on the Post row correctly, this
    loop just wasn't reading it.
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

                # The diagnostic this replaced logged a full dump (including
                # the recompiled SQL text) every 30 seconds forever, whether
                # or not anything was actually due - it served its purpose
                # confirming the original "nothing was sent" mismatch and is
                # now scoped down to only fire when there's something to
                # report, so idle cycles stop generating constant log noise.
                if posts:
                    logger.info(
                        "run_post_send_loop: now=%s matched %d post(s): %s",
                        now, len(posts), [(p.id, p.scheduled_time) for p in posts],
                    )

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

                    # Built once per post rather than per-target - same
                    # button on every channel it's sent to. None for ALBUM
                    # posts is correct and expected: Telegram's
                    # send_media_group has no reply_markup parameter at all
                    # (see _build_media_group's caller below), and
                    # handlers/compose.py's ask_button step already skips
                    # collecting a button for albums for this same reason,
                    # so button_text/button_url are already None on any
                    # ALBUM post row.
                    reply_markup = _build_button(post.button_text, post.button_url)

                    sent_count = 0
                    for target in targets:
                        if target.message_id is not None:
                            continue
                        if target.channel is None:
                            # Stale PostTarget: the channel row it pointed at
                            # was removed, so there's nowhere to send this to.
                            # Previously this fell through to the bare
                            # `except Exception: continue` below via an
                            # AttributeError on target.channel.chat_id,
                            # silently no-op'ing forever with zero log trace.
                            # Log it explicitly so a dangling target is
                            # visible instead of invisible.
                            logger.warning(
                                "post_id=%s target channel_id=%s no longer exists - skipping send",
                                post.id, target.channel_id,
                            )
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
                            sent_count += 1
                        except Exception:
                            # Previously a bare `continue` here swallowed
                            # every send failure with zero trace - Telegram
                            # errors (bot kicked from channel, chat not
                            # found, message too long, etc.) looked
                            # identical to success in the logs. Now logged
                            # so real failures are diagnosable.
                            logger.exception(
                                "Failed to send post_id=%s to chat_id=%s",
                                post.id,
                                target.channel.chat_id if target.channel else target.channel_id,
                            )
                            continue

                    if sent_count:
                        logger.warning(
                            "post_id=%s sent to %d/%d target(s)", post.id, sent_count, len(targets)
                        )
                    elif targets:
                        logger.warning(
                            "post_id=%s scheduled_time reached but sent to 0/%d target(s)",
                            post.id, len(targets),
                        )

                    post.status = PostStatus.SENT
                    if post.auto_delete_seconds:
                        post.delete_at = now + timedelta(seconds=post.auto_delete_seconds)

                    # Commit after each post rather than batching every due
                    # post's sends into one long-held transaction - this loop
                    # can make several sequential Telegram API calls per post
                    # (one per target/channel), and a burst of many due posts
                    # at once (e.g. several recurring posts firing in the
                    # same 30s window) previously held a single write
                    # transaction open for the whole batch. With one SQLite
                    # file shared by this loop, run_scheduler_loop, and the
                    # repost handler, that turned into multi-hour "database
                    # is locked" storms during bursts (seen in production on
                    # 2026-09-13) even with WAL mode + busy_timeout already
                    # enabled (db.py) - the lock was simply held too long for
                    # busy_timeout to help. Committing per-post bounds each
                    # transaction to roughly one post's worth of network
                    # calls instead of the whole batch.
                    await s.commit()
                await s.commit()
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("run_post_send_loop iteration failed")
            await asyncio.sleep(5)
