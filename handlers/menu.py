"""Main menu and navigation.

Navigation is entirely inline-keyboard based: tapping a button edits the
current menu message in place (fast, no chat clutter) rather than sending a
brand-new message with a fresh reply-keyboard every time. Actual data-entry
flows (compose, add channel, etc.) still use a small reply-keyboard "Cancel"
button while they're waiting for free text - that part is unchanged, since
inline buttons don't replace typed input.
"""
from aiogram import Router, types, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext

from handlers.common import main_menu_kb, nav_kb

router = Router()

WELCOME_TEXT = (
    "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "👋 HELPBOT - CROSSPOST ADMIN\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "Welcome! Choose what to do:\n\n"
    "📨 Messaging - Post/Edit/Delete\n"
    "📍 Channels - Manage channels\n"
    "📁 Categories - Organize channels\n"
    "🛡️ Moderation - Keep groups clean\n"
    "📡 Forwarding - Repost from other channels, with your links swapped in\n"
    "⚙️ Settings - Auto-approve\n"
    "📊 Analytics - View stats + channel growth\n"
    "❓ Help - All commands\n\n"
    "💡 Stuck in the middle of something? Send /cancel any time to "
    "back out and return here."
)

MESSAGING_TEXT = (
    "━━━━━━━━━━━━━━━━━━━━━━\n"
    "📨 MESSAGING\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "✏️ Compose - Send to channels\n"
    "📨 Category - Post to all in category\n"
    "📋 View - See your posts\n"
    "✎️ Edit - Change post text\n"
    "🗑️ Delete - Remove post\n"
    "🔗 Replacer - Replace links"
)

CHANNELS_TEXT = (
    "━━━━━━━━━━━━━━━━━━━━━\n"
    "📍 CHANNELS\n"
    "━━━━━━━━━━━━━━━━━━━━━\n\n"
    "Easiest way: add the bot as admin to a channel and it registers "
    "itself automatically - no need to look up the chat id.\n\n"
    "➕ Add - Add channel manually\n"
    "📋 List - View all\n"
    "🗑️ Delete - Remove"
)

CATEGORIES_TEXT = (
    "━━━━━━━━━━━━━━━━━━━━━\n"
    "📁 CATEGORIES\n"
    "━━━━━━━━━━━━━━━━━━━━━\n\n"
    "Categories help organize channels.\n\n"
    "➕ Add - Create category\n"
    "📋 List - View categories\n\n"
    "Then assign channels to categories\n"
    "when adding them."
)

MODERATION_TEXT = (
    "━━━━━━━━━━━━━━━━━━━━━\n"
    "🛡️ MODERATION\n"
    "━━━━━━━━━━━━━━━━━━━━━\n\n"
    "Auto-delete spam links and keep\n"
    "your groups friendly.\n\n"
    "Easiest way: add the bot as admin to a group (with Delete messages + "
    "Ban users permissions) and it starts moderating automatically.\n\n"
    "Tap Configure below to choose link & spam rules, or manage with:\n"
    "/add_group, /list_groups, /remove_group"
)

SETTINGS_TEXT = (
    "━━━━━━━━━━━━━━━━━━━━━\n"
    "⚙️ SETTINGS\n"
    "━━━━━━━━━━━━━━━━━━━━━\n\n"
    "🔐 Auto-Approve - Approve join requests"
)

HELP_TEXT = (
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
    "📡 FORWARDING (use the 📡 Forwarding menu button for a guided flow):\n"
    "/add_source - Watch a channel\n"
    "/list_sources, /remove_source\n"
    "/add_rule - Forward source -> your channel\n"
    "/list_rules, /remove_rule\n\n"
    "⚙️ SETTINGS:\n"
    "/autoapprove - Auto-approve\n\n"
    "📊 ANALYTICS:\n"
    "/analytics - Stats + channel growth\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
)

BACK_ONLY_KB = nav_kb([[("🔙 Back", "menu:main")]])

MESSAGING_KB = nav_kb([
    [("✏️ Compose & Post", "act:compose")],
    [("📨 Post to Category", "act:post_category")],
    [("📋 View My Posts", "act:myposts")],
    [("✎️ Edit Post", "act:edit"), ("🗑️ Delete Post", "act:delete")],
    [("🔗 Link Replacer", "act:replacer")],
    [("🔙 Back", "menu:main")],
])

CHANNELS_KB = nav_kb([
    [("➕ Add Channel", "act:add_channel"), ("📋 List Channels", "act:list_channels")],
    [("🗑️ Delete Channel", "act:delete_channel")],
    [("🔙 Back", "menu:main")],
])

CATEGORIES_KB = nav_kb([
    [("➕ Add Category", "act:add_category"), ("📋 List Categories", "act:list_categories")],
    [("🔙 Back", "menu:main")],
])

MODERATION_KB = nav_kb([
    [("⚙️ Configure Moderation", "act:moderation")],
    [("🔙 Back", "menu:main")],
])

SETTINGS_KB = nav_kb([
    [("🔐 Auto-Approve Members", "act:autoapprove")],
    [("🔙 Back", "menu:main")],
])


@router.message(CommandStart())
async def main_menu(message: types.Message, state: FSMContext):
    """Main menu with organized navigation.

    Also clears any in-progress flow's FSM state. Previously /start didn't
    touch state, so if someone was stuck mid-flow (e.g. composing a post)
    and typed /start to escape, the bot still thought they were mid-flow
    and their next tap could get swallowed by the old flow's handler.
    """
    await state.clear()
    await message.answer(WELCOME_TEXT, reply_markup=main_menu_kb())


@router.message(Command("cancel"))
async def cancel_any(message: types.Message, state: FSMContext):
    """Universal escape hatch.

    Works no matter what flow (or sub-menu) the user is currently in -
    clears any in-progress FSM state and drops them back at the main menu.
    This is the fix for "some areas I can't go back, I must press /start
    again": now /cancel (or /start) always works from anywhere.
    """
    await state.clear()
    await message.answer("↩️ Cancelled. Back to the main menu:", reply_markup=main_menu_kb())


@router.callback_query(F.data == "menu:main")
async def nav_main(query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await query.message.edit_text(WELCOME_TEXT, reply_markup=main_menu_kb())
    await query.answer()


@router.callback_query(F.data == "menu:messaging")
async def nav_messaging(query: types.CallbackQuery):
    await query.message.edit_text(MESSAGING_TEXT, reply_markup=MESSAGING_KB)
    await query.answer()


@router.callback_query(F.data == "menu:channels")
async def nav_channels(query: types.CallbackQuery):
    await query.message.edit_text(CHANNELS_TEXT, reply_markup=CHANNELS_KB)
    await query.answer()


@router.callback_query(F.data == "menu:categories")
async def nav_categories(query: types.CallbackQuery):
    await query.message.edit_text(CATEGORIES_TEXT, reply_markup=CATEGORIES_KB)
    await query.answer()


@router.callback_query(F.data == "menu:moderation")
async def nav_moderation(query: types.CallbackQuery):
    await query.message.edit_text(MODERATION_TEXT, reply_markup=MODERATION_KB)
    await query.answer()


@router.callback_query(F.data == "menu:settings")
async def nav_settings(query: types.CallbackQuery):
    await query.message.edit_text(SETTINGS_TEXT, reply_markup=SETTINGS_KB)
    await query.answer()


@router.callback_query(F.data == "menu:help")
async def nav_help(query: types.CallbackQuery):
    await query.message.edit_text(HELP_TEXT, reply_markup=BACK_ONLY_KB)
    await query.answer()


@router.message(Command("help"))
async def help_cmd(message: types.Message):
    """Help command (also reachable by typing /help directly)."""
    await message.answer(HELP_TEXT, reply_markup=BACK_ONLY_KB)


@router.callback_query(F.data == "menu:analytics")
async def nav_analytics(query: types.CallbackQuery):
    from handlers.analytics import show_analytics
    await query.answer()
    await show_analytics(query.message)
    await query.message.answer("⬅️ Back to menu:", reply_markup=BACK_ONLY_KB)


# ---------------------------------------------------------------------------
# Action buttons - each opens the same flow the old /command would have,
# just triggered from a submenu tap instead of typed text. The 📡 Forwarding
# button on the main menu (callback_data "fwd:root") is handled directly by
# handlers/sources.py instead of routed through here, since its own screen
# (source list) IS the submenu - no separate static page needed.
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "act:compose")
async def act_compose(query: types.CallbackQuery, state: FSMContext):
    from handlers.compose import compose_start
    await query.answer()
    await compose_start(query.message, state)


@router.callback_query(F.data == "act:post_category")
async def act_post_category(query: types.CallbackQuery, state: FSMContext):
    from handlers.category_post import post_category_start
    await query.answer()
    await post_category_start(query.message, state)


@router.callback_query(F.data == "act:myposts")
async def act_myposts(query: types.CallbackQuery):
    from handlers.posts import list_posts
    await query.answer()
    await list_posts(query.message)
    await query.message.answer("⬅️ Back to menu:", reply_markup=BACK_ONLY_KB)


@router.callback_query(F.data == "act:edit")
async def act_edit(query: types.CallbackQuery, state: FSMContext):
    from handlers.posts import edit_start
    await query.answer()
    await edit_start(query.message, state)


@router.callback_query(F.data == "act:delete")
async def act_delete(query: types.CallbackQuery, state: FSMContext):
    from handlers.posts import delete_start
    await query.answer()
    await delete_start(query.message, state)


@router.callback_query(F.data == "act:replacer")
async def act_replacer(query: types.CallbackQuery, state: FSMContext):
    from handlers.replacer import replacer_start
    await query.answer()
    await replacer_start(query.message, state)


@router.callback_query(F.data == "act:add_channel")
async def act_add_channel(query: types.CallbackQuery, state: FSMContext):
    from handlers.channels import add_channel_start
    await query.answer()
    await add_channel_start(query.message, state)


@router.callback_query(F.data == "act:list_channels")
async def act_list_channels(query: types.CallbackQuery):
    from handlers.channels import list_channels
    await query.answer()
    await list_channels(query.message)
    await query.message.answer("⬅️ Back to menu:", reply_markup=BACK_ONLY_KB)


@router.callback_query(F.data == "act:delete_channel")
async def act_delete_channel(query: types.CallbackQuery, state: FSMContext):
    from handlers.channels import delete_channel_start
    await query.answer()
    await delete_channel_start(query.message, state)


@router.callback_query(F.data == "act:add_category")
async def act_add_category(query: types.CallbackQuery, state: FSMContext):
    from handlers.categories import add_category_start
    await query.answer()
    await add_category_start(query.message, state)


@router.callback_query(F.data == "act:list_categories")
async def act_list_categories(query: types.CallbackQuery):
    from handlers.categories import list_categories
    await query.answer()
    await list_categories(query.message)
    await query.message.answer("⬅️ Back to menu:", reply_markup=BACK_ONLY_KB)


@router.callback_query(F.data == "act:moderation")
async def act_moderation(query: types.CallbackQuery):
    from handlers.moderation import moderation_settings
    await query.answer()
    await moderation_settings(query.message)


@router.callback_query(F.data == "act:autoapprove")
async def act_autoapprove(query: types.CallbackQuery):
    from handlers.settings import auto_approve
    await query.answer()
    await auto_approve(query.message)
