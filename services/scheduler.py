import asyncio
import json
from datetime import datetime, timedelta

from aiogram import Bot, types
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from db import session
from models import ContentType, Post, PostMediaItem, PostStatus, PostTarget


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
    """Background scheduler that handles auto-deletion.

    Looks for Posts with status == SENT and delete_at <= now(), then deletes
    the target messages from every channel they were sent to.
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
                    if all_deleted:
                        post.status = PostStatus.DELETED
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
                        except Exception:
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
