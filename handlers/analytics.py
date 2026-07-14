"""Analytics and statistics."""
from aiogram import Router, types
from aiogram.filters import Command
from sqlalchemy import func, select

from db import session
from models import Post, PostTarget, PostStatus, Channel

router = Router()


@router.message(Command("analytics"))
async def show_analytics(message: types.Message):
    """Show analytics dashboard."""
    async with session() as s:
        # Total posts
        total_q = select(func.count(Post.id)).where(Post.owner_user_id == message.from_user.id)
        total_posts = (await s.execute(total_q)).scalar() or 0
        
        # Sent posts
        sent_q = select(func.count(Post.id)).where(
            (Post.owner_user_id == message.from_user.id) &
            (Post.status == PostStatus.SENT)
        )
        sent_posts = (await s.execute(sent_q)).scalar() or 0
        
        # Scheduled posts
        sched_q = select(func.count(Post.id)).where(
            (Post.owner_user_id == message.from_user.id) &
            (Post.status == PostStatus.SCHEDULED)
        )
        scheduled_posts = (await s.execute(sched_q)).scalar() or 0
        
        # Total messages delivered
        msg_q = select(func.count(PostTarget.id)).join(Post).where(
            Post.owner_user_id == message.from_user.id
        )
        total_messages = (await s.execute(msg_q)).scalar() or 0
        
        # Total channels
        ch_q = select(func.count(Channel.id))
        total_channels = (await s.execute(ch_q)).scalar() or 0
    
    await message.answer(
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📊 ANALYTICS DASHBOARD\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📝 Total Posts: {total_posts}\n"
        f"   ✅ Sent: {sent_posts}\n"
        f"   ⏰ Scheduled: {scheduled_posts}\n\n"
        f"📤 Messages Delivered: {total_messages}\n"
        f"📍 Channels Added: {total_channels}\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

