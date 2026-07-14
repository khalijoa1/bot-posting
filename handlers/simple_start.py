"""Main menu handler."""
from aiogram import Router, types
from aiogram.filters import Command, CommandStart

router = Router()


@router.message(CommandStart())
async def start_cmd(message: types.Message):
    """Start command with main menu."""
    kb = types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="📝 Compose"), types.KeyboardButton(text="📋 My Posts")],
            [types.KeyboardButton(text="➕ Add Channel"), types.KeyboardButton(text="📍 View Channels")],
            [types.KeyboardButton(text="📁 Categories"), types.KeyboardButton(text="📊 Analytics")],
            [types.KeyboardButton(text="🔐 Auto-Approve"), types.KeyboardButton(text="ℹ️ Help")],
        ],
        resize_keyboard=True
    )

    await message.answer(
        "👋 Welcome to @helpingkhalibot!\n\n"
        "🤖 Telegram Crosspost Admin Bot\n\n"
        "Features:\n"
        "✅ Post to multiple channels instantly\n"
        "✅ Edit/Delete across all channels\n"
        "✅ Schedule posts\n"
        "✅ Replace links automatically\n"
        "✅ Auto-approve subscribers\n"
        "✅ View analytics\n\n"
        "Use the menu or type /help",
        reply_markup=kb
    )


@router.message(Command("help"))
async def help_cmd(message: types.Message):
    """Help command."""
    await message.answer(
        "📖 COMMANDS:\n\n"
        "/compose - Post to channels\n"
        "/myposts - View your posts\n"
        "/edit - Edit a post\n"
        "/delete - Delete a post\n"
        "/add_channel - Add channel\n"
        "/list_channels - View channels\n"
        "/add_category - Create category\n"
        "/list_categories - View categories\n"
        "/analytics - View stats\n"
        "/autoapprove - Toggle auto-approval\n"
        "/start - Main menu"
    )

