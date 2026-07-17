"""Main menu and navigation."""
from aiogram import Router, types
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext

from handlers.common import main_menu_kb

router = Router()


@router.message(CommandStart())
async def main_menu(message: types.Message, state: FSMContext):
    """Main menu with organized navigation.

    Also clears any in-progress flow's FSM state. Previously /start didn't
    touch state, so if someone was stuck mid-flow (e.g. composing a post)
    and typed /start to escape, the bot still thought they were mid-flow
    and their next tap could get swallowed by the old flow's handler.
    """
    await state.clear()
    kb = main_menu_kb()

    await message.answer(
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "👋 HELPBOT - CROSSPOST ADMIN\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Welcome! Choose what to do:\n\n"
        "📨 MESSAGING - Post/Edit/Delete\n"
        "📍 CHANNELS - Manage channels\n"
        "📁 CATEGORIES - Organize channels\n"
        "🛡️ MODERATION - Keep groups clean\n"
        "⚙️ SETTINGS - Auto-approve\n"
        "📊 ANALYTICS - View stats\n"
        "❓ HELP - All commands\n\n"
        "💡 Stuck in the middle of something? Send /cancel any time to "
        "back out and return here.",
        reply_markup=kb
    )


@router.message(lambda msg: msg.text == "📨 MESSAGING")
async def messaging_menu(message: types.Message):
    """Messaging submenu."""
    kb = types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="✏️ Compose & Post")],
            [types.KeyboardButton(text="📨 Post to Category")],
            [types.KeyboardButton(text="📋 View My Posts")],
            [types.KeyboardButton(text="✎️ Edit Post")],
            [types.KeyboardButton(text="🗑️ Delete Post")],
            [types.KeyboardButton(text="🔗 Link Replacer")],
            [types.KeyboardButton(text="🔙 Back")],
        ],
        resize_keyboard=True
    )

    await message.answer(
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "📨 MESSAGING\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "✏️ Compose - Send to channels\n"
        "📨 Category - Post to all in category\n"
        "📋 View - See your posts\n"
        "✎️ Edit - Change post text\n"
        "🗑️ Delete - Remove post\n"
        "🔗 Replacer - Replace links",
        reply_markup=kb
    )


@router.message(lambda msg: msg.text == "📍 CHANNELS")
async def channels_menu(message: types.Message):
    """Channels submenu."""
    kb = types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="➕ Add Channel")],
            [types.KeyboardButton(text="📋 List Channels")],
            [types.KeyboardButton(text="🗑️ Delete Channel")],
            [types.KeyboardButton(text="🔙 Back")],
        ],
        resize_keyboard=True
    )

    await message.answer(
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "📍 CHANNELS\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "➕ Add - Add channel\n"
        "📋 List - View all\n"
        "🗑️ Delete - Remove",
        reply_markup=kb
    )


@router.message(lambda msg: msg.text == "📁 CATEGORIES")
async def categories_menu(message: types.Message):
    """Categories submenu."""
    kb = types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="➕ Add Category")],
            [types.KeyboardButton(text="📋 List Categories")],
            [types.KeyboardButton(text="🔙 Back")],
        ],
        resize_keyboard=True
    )

    await message.answer(
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "📁 CATEGORIES\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Categories help organize channels.\n\n"
        "➕ Add - Create category\n"
        "📋 List - View categories\n\n"
        "Then assign channels to categories\n"
        "when adding them.",
        reply_markup=kb
    )


@router.message(lambda msg: msg.text == "🛡️ MODERATION")
async def moderation_menu(message: types.Message):
    """Moderation submenu - keep groups clean and friendly."""
    kb = types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="🛡️ Moderation")],
            [types.KeyboardButton(text="🔙 Back")],
        ],
        resize_keyboard=True
    )

    await message.answer(
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "🛡️ MODERATION\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Auto-delete spam links and keep\n"
        "your groups friendly.\n\n"
        "Setup (one-time, per group):\n"
        "1. Add this bot as admin in the\n"
        "   group with Delete messages +\n"
        "   Ban users permissions.\n"
        "2. /add_group CHAT_ID [title]\n"
        "3. Tap 🛡️ Moderation below to\n"
        "   choose link & spam rules.\n\n"
        "Other commands:\n"
        "/list_groups - see registered groups\n"
        "/remove_group ID - stop moderating",
        reply_markup=kb
    )


@router.message(lambda msg: msg.text == "⚙️ SETTINGS")
async def settings_menu(message: types.Message):
    """Settings submenu."""
    kb = types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="🔐 Auto-Approve Members")],
            [types.KeyboardButton(text="🔙 Back")],
        ],
        resize_keyboard=True
    )

    await message.answer(
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "⚙️ SETTINGS\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🔐 Auto-Approve - Approve join requests",
        reply_markup=kb
    )


@router.message(lambda msg: msg.text == "📊 ANALYTICS")
async def analytics_view(message: types.Message):
    """Analytics view."""
    from handlers.analytics import show_analytics
    await show_analytics(message)


@router.message(Command("help"))
async def help_cmd(message: types.Message):
    """Help command."""
    kb = types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="🔙 Back")],
        ],
        resize_keyboard=True
    )

    help_text = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📖 ALL COMMANDS\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📨 MESSAGING:\n"
        "/compose - Post to channels\n"
        "/post_category - Post to category\n"
        "/myposts - View your posts\n"
        "/edit - Edit post\n"
        "/delete - Delete post\n"
        "/replacer - Replace links\n\n"
        "📍 CHANNELS:\n"
        "/add_channel - Add channel\n"
        "/list_channels - View channels\n"
        "/delete_channel - Remove\n\n"
        "📁 CATEGORIES:\n"
        "/add_category - Create\n"
        "/list_categories - View\n\n"
        "🛡️ MODERATION:\n"
        "/add_group - Register a group\n"
        "/moderation - Configure rules\n"
        "/list_groups - View groups\n"
        "/remove_group - Stop moderating\n\n"
        "⚙️ SETTINGS:\n"
        "/autoapprove - Auto-approve\n\n"
        "📊 ANALYTICS:\n"
        "/analytics - Stats\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    await message.answer(help_text, reply_markup=kb)


@router.message(lambda msg: msg.text == "❓ HELP")
async def help_menu(message: types.Message):
    """Help from menu."""
    await help_cmd(message)


@router.message(lambda msg: msg.text == "🔙 Back")
async def go_back(message: types.Message, state: FSMContext):
    """Go back to main menu."""
    await main_menu(message, state)


@router.message(Command("cancel"))
async def cancel_any(message: types.Message, state: FSMContext):
    """Universal escape hatch.

    Works no matter what flow (or sub-menu) the user is currently in -
    clears any in-progress FSM state and drops them back at the main menu.
    This is the fix for "some areas I can't go back, I must press /start
    again": now /cancel (or /start) always works from anywhere.
    """
    await state.clear()
    await message.answer("↩️ Cancelled. Back to the main menu:")
    await main_menu(message, state)
