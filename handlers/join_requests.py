"""Auto-approve channel join requests, based on each channel's setting.

For this to fire, the channel must have "Approve new members" (join requests)
turned on in Telegram, and the bot must be an admin there with permission to
add/approve members - toggle the per-channel setting with /autoapprove.
"""
from aiogram import Router, types
from sqlalchemy import select

from db import session
from models import Channel

router = Router()


@router.chat_join_request()
async def handle_join_request(update: types.ChatJoinRequest) -> None:
    async with session() as s:
        q = select(Channel).where(Channel.chat_id == update.chat.id)
        res = await s.execute(q)
        channel = res.scalars().first()

    if channel and channel.auto_approve_members:
        try:
            await update.approve()
        except Exception:
            pass
