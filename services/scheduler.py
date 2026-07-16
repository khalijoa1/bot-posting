import asyncio
from datetime import datetime, timedelta

from aiogram import Bot
from sqlalchemy import select

from db import session
from models import ContentType, Post, PostStatus, PostTarget


async def run_scheduler_loop(bot: Bot) -> None:
    """Background scheduler that handles auto-deletion.

    Looks for Posts with status == SENT and delete_at <= now(), then deletes
    the target messages from every channel they were sent to.
    """
    while True:
        try:
            now = datetime.utcnow()
            async with session() as s:
                q = select(Post).where(Post.status == PostStatus.SENT, Post.delete_at != None, Post.delete_at <= now)
                res = await s.execute(q)
                posts = res.scalars().all()
                for post in posts:
                    all_deleted = True
                    for target in post.targets:
                        if target.message_id is None:
                            continue
                        try:
                            await bot.delete_message(chat_id=target.channel.chat_id, message_id=target.message_id)
                        except Exception:
                            all_deleted = False
                            continue
                    if all_deleted:
                        post.status = PostStatus.DELETED
                        await s.commit()
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(5)


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
                    target_q = select(PostTarget).where(PostTarget.post_id == post.id)
                    target_res = await s.execute(target_q)
                    targets = target_res.scalars().all()

                    for target in targets:
                        if target.message_id is not None:
                            continue
                        try:
                            if post.content_type == ContentType.PHOTO and post.photo_file_id:
                                msg = await bot.send_photo(
                                    chat_id=target.channel.chat_id,
                                    photo=post.photo_file_id,
                                    caption=post.text,
                                )
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
