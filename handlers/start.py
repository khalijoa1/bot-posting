from aiogram import Router, types
from aiogram.filters import Command

router = Router()


@router.message(Command("start"))
async def start_handler(message: types.Message):
    """Handler for /start command"""
    user_name = message.from_user.first_name if message.from_user else "User"
    await message.reply(
        f"👋 Welcome {user_name}!\n\n"
        "I'm an ADMIN HELPBOT for managing channels and posts.\n\n"
        "Available commands:\n"
        "/add_channel ID TITLE - Add a channel\n"
        "/list_channels - List all channels\n"
        "/delete_channel ID - Delete a channel\n"
        "/add_category NAME - Add a category\n"
        "/list_categories - List categories\n"
        "/add_source IDENTIFIER [TITLE] - Add source channel to watch\n"
        "/list_sources - List source channels\n"
        "/add_repost_rule SRC_ID DEST_ID - Create reposting rule\n"
        "/list_repost_rules - List reposting rules",
        parse_mode=None
    )


@router.message(Command("help"))
async def help_handler(message: types.Message):
    """Handler for /help command"""
    await start_handler(message)

