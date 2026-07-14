"""Handler for analytics and statistics."""
from aiogram import Router, types
from aiogram.filters import Command
from sqlalchemy import func, select

from db import session
from models import Post, PostTarget, PostStatus

router = Router()


@router.message(Command("analytics"))
async def show_analytics(message: types.Message):
    """Show analytics dashboard"""
    async with session() as s:
        # Total posts
        total_posts_q = select(func.count(Post.id)).where(Post.owner_user_id == message.from_user.id)
        total_posts = (await s.execute(total_posts_q)).scalar() or 0

        # Sent vs scheduled
        sent_q = select(func.count(Post.id)).where(
            (Post.owner_user_id == message.from_user.id) & 
            (Post.status == PostStatus.SENT)
        )
        sent = (await s.execute(sent_q)).scalar() or 0

        scheduled_q = select(func.count(Post.id)).where(
            (Post.owner_user_id == message.from_user.id) & 
            (Post.status == PostStatus.SCHEDULED)
        )
        scheduled = (await s.execute(scheduled_q)).scalar() or 0

        # Total messages sent
        total_targets_q = select(func.count(PostTarget.id)).join(Post).where(
            Post.owner_user_id == message.from_user.id
        )
        total_targets = (await s.execute(total_targets_q)).scalar() or 0

    analytics_text = (
        "📊 ANALYTICS\n\n"
        f"📝 Total Posts: {total_posts}\n"
        f"✅ Sent: {sent}\n"
        f"⏰ Scheduled: {scheduled}\n"
        f"📤 Messages Delivered: {total_targets}\n"
    )

    await message.reply(analytics_text, parse_mode=None)


@router.message(Command("poststats"))
async def post_stats(message: types.Message):
    """Show detailed post statistics"""
    async with session() as s:
        posts_q = select(Post).where(Post.owner_user_id == message.from_user.id)
        posts = (await s.execute(posts_q)).scalars().all()

    if not posts:
        await message.reply("📭 No posts yet", parse_mode=None)
        return

    stats_text = "📊 POST STATISTICS\n\n"
    for p in posts:
        async with session() as s:
            targets_q = select(func.count(PostTarget.id)).where(PostTarget.post_id == p.id)
            target_count = (await s.execute(targets_q)).scalar() or 0

        preview = (p.text or "[Photo]")[:40]
        stats_text += (
            f"ID: {p.id}\n"
            f"Text: {preview}...\n"
            f"Channels: {target_count}\n"
            f"Status: {p.status.value}\n\n"
        )

    await message.reply(stats_text, parse_mode=None)

