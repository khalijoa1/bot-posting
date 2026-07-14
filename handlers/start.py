from aiogram import Router, types
from aiogram.filters import Command

router = Router()


@router.message(Command("start"))
async def start_handler(message: types.Message):
    """Handler for /start command"""
    user_name = message.from_user.first_name if message.from_user else "User"
    await message.reply(
        f"👋 Welcome {user_name}!\n\n"
        f"I'm an ADMIN HELPBOT for crossposting to multiple channels.\n\n"
        f"📋 CHANNEL MANAGEMENT:\n"
        f"/add_channel - Add a channel to the system\n"
        f"/list_channels - View all channels\n"
        f"/delete_channel ID - Remove a channel\n"
        f"/add_category NAME - Create a category\n"
        f"/list_categories - View categories\n\n"
        f"📝 MESSAGE MANAGEMENT:\n"
        f"/compose - Compose and post to multiple channels\n"
        f"/myposts - View your posted messages\n"
        f"/edit - Edit a posted message (edits in all channels)\n"
        f"/delete - Delete a posted message (from all channels)\n\n"
        f"🔄 ADVANCED:\n"
        f"/add_source IDENTIFIER [TITLE] - Add channel to watch\n"
        f"/list_sources - View source channels\n"
        f"/add_repost_rule SRC_ID DEST_ID - Create reposting rule\n"
        f"/list_repost_rules - View reposting rules\n\n"
        f"💡 Tip: Use /compose to post the same message to multiple channels at once!",
        parse_mode=None
    )


@router.message(Command("help"))
async def help_handler(message: types.Message):
    """Handler for /help command"""
    await start_handler(message)

