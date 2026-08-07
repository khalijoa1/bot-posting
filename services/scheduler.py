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

                # TEMPORARY DIAGNOSTIC: "nothing was sent" was reported for
                # posts 474/483/489/922/925/991/1230 despite them showing
                # status='scheduled' with a past scheduled_time in the DB -
                # this logs the query's own view of `now` plus every post id
                # + raw scheduled_time it actually matched, every single
                # pass, so a query/type mismatch (vs. some other cause) is
                # directly visible instead of inferred from absence of logs.
                # Safe to remove once the real cause is confirmed.
                logger.warning(
                    "run_post_send_loop: now=%s matched %d post(s): %s | compiled_sql=%s",
                    now, len(posts), [(p.id, p.scheduled_time) for p in posts],
                    str(q.compile(compile_kwargs={"literal_binds": True})).replace("\n", " "),
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
                                )
                                target.message_id = msg.message_id
                            elif post.content_type == ContentType.VIDEO and post.video_file_id:
                                msg = await bot.send_video(
                                    chat_id=target.channel.chat_id,
                                    video=post.video_file_id,
                                    caption=post.text or None,
                                )
                                target.message_id = msg.message_id
                            else:
                                msg = await bot.send_message(chat_id=target.channel.chat_id, text=post.text or "")
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
                await s.commit()
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("run_post_send_loop iteration failed")
            await asyncio.sleep(5)
