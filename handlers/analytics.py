"""Analytics and statistics, including per-channel subscriber growth.

Growth needs its own timeline (see services/stats.py, which snapshots every
channel's member count every 6 hours) since Telegram's Bot API only
exposes a single current count with no history. If the bot has been
running less than that, growth just won't have data yet - this shows the
current count either way and adds a growth line only once an earlier
snapshot exists to compare against.
"""
from datetime import datetime, timedelta

from aiogram import types
from aiogram.filters import Command
from aiogram import Router
from sqlalchemy import func, select

from db import session
from models import Channel, ChannelStatSnapshot, Post, PostStatus, PostTarget, SourceChannel

router = Router()


async def _closest_snapshot_before(s, channel_id: int, cutoff: datetime) -> ChannelStatSnapshot | None:
    q = (
        select(ChannelStatSnapshot)
        .where(ChannelStatSnapshot.channel_id == channel_id, ChannelStatSnapshot.taken_at <= cutoff)
        .order_by(ChannelStatSnapshot.taken_at.desc())
        .limit(1)
    )
    res = await s.execute(q)
    return res.scalars().first()


async def _channel_growth_lines(bot, channels: list[Channel]) -> list[str]:
    now = datetime.utcnow()
    lines = []
    async with session() as s:
        for ch in channels:
            try:
                current = await bot.get_chat_member_count(chat_id=ch.chat_id)
            except Exception:
                lines.append(f"  {ch.title}: (couldn't read member count)")
                continue

            snap_7d = await _closest_snapshot_before(s, ch.id, now - timedelta(days=7))
            snap_1d = await _closest_snapshot_before(s, ch.id, now - timedelta(days=1))

            growth_bits = []
            if snap_1d:
                delta = current - snap_1d.member_count
                growth_bits.append(f"{'+' if delta >= 0 else ''}{delta} /24h")
            if snap_7d:
                delta = current - snap_7d.member_count
                growth_bits.append(f"{'+' if delta >= 0 else ''}{delta} /7d")

            growth_str = f" ({', '.join(growth_bits)})" if growth_bits else " (still collecting history)"
            lines.append(f"  {ch.title}: {current} members{growth_str}")
    return lines


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

        # Channels + sources (all aspects the operator is running)
        ch_q = select(Channel)
        channels = (await s.execute(ch_q)).scalars().all()
        total_channels = len(channels)

        src_q = select(func.count(SourceChannel.id))
        total_sources = (await s.execute(src_q)).scalar() or 0

    growth_lines = await _channel_growth_lines(message.bot, channels) if channels else []

    text = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📊 ANALYTICS DASHBOARD\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📝 Total Posts: {total_posts}\n"
        f"   ✅ Sent: {sent_posts}\n"
        f"   ⏰ Scheduled: {scheduled_posts}\n\n"
        f"📤 Messages Delivered: {total_messages}\n"
        f"📍 Channels Added: {total_channels}\n"
        f"📡 Sources Watched: {total_sources}\n"
    )

    if growth_lines:
        text += (
            "\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📈 CHANNEL GROWTH\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            + "\n".join(growth_lines) + "\n"
        )

    text += "\n━━━━━━━━━━━━━━━━━━━━━━━━━━"

    await message.answer(text)
