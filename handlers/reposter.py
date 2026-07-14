from aiogram import Router, types

from services.reposter import handle_incoming_message

router = Router()


@router.message()
async def on_channel_message(message: types.Message):
    # Only act on messages coming from channels/supergroups where bot is present
    chat = message.chat
    if not chat:
        return
    # Delegate to repost service which checks DB for SourceChannel matching
    await handle_incoming_message(message.bot, message)
