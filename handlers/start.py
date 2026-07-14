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
        "/add_channel <chat_id> <title> - Add a channel\n"
        "/list_channels - List all channels\n"
        "/delete_channel <id> - Delete a channel\n"
        "/add_category <name> - Add a category\n"
        "/list_categories - List categories\n"
        "/add_source <identifier> [title] - Add source channel to watch\n"
        "/list_sources - List source channels\n"
        "/add_repost_rule <source_id> <dest_id> - Create reposting rule\n"
        "/list_repost_rules - List reposting rules"
    )


@router.message(Command("help"))
async def help_handler(message: types.Message):
    """Handler for /help command"""
    await start_handler(message)

