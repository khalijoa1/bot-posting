import asyncio
from datetime import datetime

from aiogram import Bot
from sqlalchemy import select

from db import session
from models import Post, PostStatus


async def run_scheduler_loop(bot: Bot):
    """Background scheduler that handles post deletions.

    Looks for Posts with status == SENT and delete_at <= now(), then deletes target messages.
    """
    while True:
        try:
            now = datetime.utcnow()
            async with session() as s:
                q = select(Post).where(Post.status == PostStatus.SENT, Post.delete_at != None, Post.delete_at <= now)
                res = await s.execute(q)
                posts = res.scalars().all()
                for post in posts:
                    # iterate through post.targets relationship
                    all_deleted = True
                    for target in post.targets:
                        if target.message_id is None:
                            continue
                        try:
                            await bot.delete_message(chat_id=target.channel.chat_id, message_id=target.message_id)
                        except Exception:
                            # couldn't delete; mark as not all deleted
                            all_deleted = False
                            continue
                    if all_deleted:
                        post.status = PostStatus.DELETED
                        await s.commit()
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            raise
        except Exception:
            # on any error, sleep a bit and continue
            await asyncio.sleep(5)

