"""Auto-approve channel join requests, based on each channel's setting, and
DM the new subscriber the channel's configured welcome message.

For approval to fire, the channel must have "Approve new members" (join
requests) turned on in Telegram, and the bot must be an admin there with
permission to add/approve members - toggle the per-channel setting with
/autoapprove. Set the welcome message when adding a channel (/add_channel).

Note: Telegram only lets a bot DM a user who has interacted with it before
(e.g. pressed /start on the bot at some point). If the subscriber never has,
the welcome DM will silently fail to send - this is a Telegram-side
restriction, not something the bot can work around.
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

    if not channel or not channel.auto_approve_members:
        return

    try:
        await update.approve()
    except Exception:
        return

    if channel.welcome_message:
        try:
            await update.bot.send_message(update.from_user.id, channel.welcome_message)
        except Exception:
            # Most common cause: the user has never started a chat with the
            # bot, so Telegram won't let it initiate a DM. Nothing to do.
            pass
