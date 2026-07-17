"""Channel management with categories and welcome messages.

Channels can be registered two ways:
  1. Automatically - add the bot as admin to the channel and it registers
     itself (see channel_admin_added below). This is the recommended way;
     no need to look up or type the numeric chat_id.
  2. Manually - /add_channel, for cases where auto-detection isn't possible
     (e.g. the bot was added by someone other than an approved operator, or
     you want to set everything - title, categories, welcome message - in
     one guided flow up front).
"""
from aiogram import Router, types, F
from aiogram.enums import ChatMemberStatus
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select

from config import get_settings
from db import session
from handlers.common import main_menu_kb
from models import Channel, Category

router = Router()


class ChannelState(StatesGroup):
    chat_id = State()
    title = State()
    select_categories = State()
    welcome_msg = State()
    delete_id = State()


@router.message(Command("add_channel"))
async def add_channel_start(message: types.Message, state: FSMContext):
    """Start adding channel."""
    await state.clear()
    await message.answer(
        "━━━━━━━━━━━━━━━━━━\n"
        "➕ ADD CHANNEL\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Tip: you don't have to do this manually - just add the bot as "
        "admin to the channel and it registers itself automatically.\n\n"
        "To add manually instead, send chat ID:\n\n"
        "Format: -1001234567890",
        reply_markup=types.ReplyKeyboardMarkup(
            keyboard=[[types.KeyboardButton(text="❌ Cancel")]],
            resize_keyboard=True
        )
    )
    await state.set_state(ChannelState.chat_id)


@router.message(ChannelState.chat_id, F.text == "❌ Cancel")
async def cancel_add(message: types.Message, state: FSMContext):
    """Cancel add."""
    await state.clear()
    await message.answer("❌ Cancelled", reply_markup=main_menu_kb())


@router.message(ChannelState.chat_id, F.text)
async def get_chat_id(message: types.Message, state: FSMContext):
    """Get chat ID."""
    try:
        chat_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Invalid ID. Send number like -1001234567890")
        return

    async with session() as s:
        q = select(Channel).where(Channel.chat_id == chat_id)
        res = await s.execute(q)
        if res.scalars().first():
            await message.answer("⚠️ Already added", reply_markup=main_menu_kb())
            await state.clear()
            return

    await state.update_data(chat_id=chat_id)
    await message.answer(
        f"Chat ID: {chat_id} ✅\n\n"
        f"Now send TITLE:",
        reply_markup=types.ReplyKeyboardMarkup(
            keyboard=[[types.KeyboardButton(text="❌ Cancel")]],
            resize_keyboard=True
        )
    )
    await state.set_state(ChannelState.title)


@router.message(ChannelState.title, F.text == "❌ Cancel")
async def cancel_title(message: types.Message, state: FSMContext):
    """Cancel title."""
    await state.clear()
    await message.answer("❌ Cancelled", reply_markup=main_menu_kb())


@router.message(ChannelState.title, F.text)
async def get_title(message: types.Message, state: FSMContext):
    """Get title and show categories."""
    title = message.text.strip()
    await state.update_data(title=title)

    async with session() as s:
        q = select(Category)
        res = await s.execute(q)
        categories = res.scalars().all()

    if not categories:
        # Skip categories - go to welcome message
        await state.update_data(selected_categories=[])
        await ask_welcome_message(message, state)
        return

    # Show categories
    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(
                text=f"☐ {cat.name}",
                callback_data=f"cat_{cat.id}"
            )]
            for cat in categories
        ] + [
            [types.InlineKeyboardButton(text="✅ Next", callback_data="cat_next")],
            [types.InlineKeyboardButton(text="⏭️ Skip", callback_data="cat_skip")]
        ]
    )

    await message.answer(
        f"Title: {title} ✅\n\n"
        f"📁 SELECT CATEGORIES:\n\n"
        f"(Optional - tap to select)",
        reply_markup=kb
    )
    await state.update_data(selected_categories=[])
    await state.set_state(ChannelState.select_categories)


@router.callback_query(ChannelState.select_categories)
async def handle_categories(query: types.CallbackQuery, state: FSMContext):
    """Handle category selection."""
    if query.data == "cat_skip" or query.data == "cat_next":
        await ask_welcome_message(query.message, state)
        await query.answer()
        return

    # Toggle category
    cat_id = int(query.data.replace("cat_", ""))
    data = await state.get_data()
    selected = data.get("selected_categories", [])

    if cat_id in selected:
        selected.remove(cat_id)
    else:
        selected.append(cat_id)

    await state.update_data(selected_categories=selected)

    async with session() as s:
        q = select(Category)
        res = await s.execute(q)
        categories = res.scalars().all()

    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(
                text=f"{'☑' if cat.id in selected else '☐'} {cat.name}",
                callback_data=f"cat_{cat.id}"
            )]
            for cat in categories
        ] + [
            [types.InlineKeyboardButton(text="✅ Next", callback_data="cat_next")],
            [types.InlineKeyboardButton(text="⏭️ Skip", callback_data="cat_skip")]
        ]
    )

    await query.message.edit_reply_markup(reply_markup=kb)
    await query.answer()


async def ask_welcome_message(message: types.Message, state: FSMContext):
    """Ask for welcome message."""
    kb = types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="⏭️ Skip")],
            [types.KeyboardButton(text="❌ Cancel")]
        ],
        resize_keyboard=True
    )

    await message.answer(
        "💬 WELCOME MESSAGE:\n\n"
        "Send a message for new subscribers:\n\n"
        "(or tap Skip)",
        reply_markup=kb
    )
    await state.set_state(ChannelState.welcome_msg)


@router.message(ChannelState.welcome_msg, F.text == "⏭️ Skip")
async def skip_welcome(message: types.Message, state: FSMContext):
    """Skip welcome message."""
    await state.update_data(welcome_message=None)
    await finalize_channel(message, state)


@router.message(ChannelState.welcome_msg, F.text == "❌ Cancel")
async def cancel_welcome(message: types.Message, state: FSMContext):
    """Cancel adding channel."""
    await state.clear()
    await message.answer("❌ Cancelled", reply_markup=main_menu_kb())


@router.message(ChannelState.welcome_msg, F.text)
async def get_welcome_msg(message: types.Message, state: FSMContext):
    """Get welcome message."""
    welcome_msg = message.text.strip()
    await state.update_data(welcome_message=welcome_msg)
    await finalize_channel(message, state)


async def finalize_channel(message: types.Message, state: FSMContext):
    """Add channel to database."""
    data = await state.get_data()
    chat_id = data.get("chat_id")
    title = data.get("title")
    selected_cats = data.get("selected_categories", [])
    welcome_msg = data.get("welcome_message")

    async with session() as s:
        ch = Channel(
            owner_user_id=message.from_user.id,
            chat_id=chat_id,
            title=title,
            welcome_message=welcome_msg
        )
        s.add(ch)
        await s.flush()

        # Link categories - avoid lazy loading
        if selected_cats:
            q = select(Category).where(Category.id.in_(selected_cats))
            res = await s.execute(q)
            cats = res.scalars().all()
            for cat in cats:
                ch.categories.append(cat)

        await s.commit()
        ch_id = ch.id

    cat_count = len(selected_cats)
    cat_text = f"📁 Categories: {cat_count}" if cat_count > 0 else "❌ No categories"

    await message.answer(
        f"✅ CHANNEL ADDED!\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"ID: {ch_id}\n"
        f"Title: {title}\n"
        f"{cat_text}\n"
        f"Welcome Msg: {'✅' if welcome_msg else '❌'}\n"
        f"━━━━━━━━━━━━━━━━━━",
        reply_markup=main_menu_kb()
    )
    await state.clear()


@router.message(Command("list_channels"))
async def list_channels(message: types.Message):
    """List all channels."""
    async with session() as s:
        q = select(Channel)
        res = await s.execute(q)
        channels = res.scalars().all()

    if not channels:
        await message.answer(
            "━━━━━━━━━━━━━━━━━━\n"
            "📍 CHANNELS\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "❌ No channels\n\n"
            "Add the bot as admin to a channel to register it automatically, "
            "or use /add_channel"
        )
        return

    text = "━━━━━━━━━━━━━━━━━━\n📍 CHANNELS\n━━━━━━━━━━━━━━━━━━\n\n"

    for ch in channels:
        # Get fresh data to avoid lazy loading
        async with session() as s:
            fresh_ch = await s.get(Channel, ch.id)
            cat_names = [c.name for c in fresh_ch.categories] if fresh_ch.categories else []

        cats = ", ".join(cat_names) if cat_names else "None"
        text += (
            f"ID: {ch.id}\n"
            f"Title: {ch.title}\n"
            f"Chat ID: {ch.chat_id}\n"
            f"Categories: {cats}\n"
            f"Auto-Approve: {'✅' if ch.auto_approve_members else '❌'}\n"
            f"Welcome: {'✅' if ch.welcome_message else '❌'}\n\n"
        )

    await message.answer(text)


@router.message(Command("delete_channel"))
async def delete_channel_start(message: types.Message, state: FSMContext):
    """Start delete."""
    await state.clear()
    await message.answer(
        "🗑️ DELETE CHANNEL\n\n"
        "Send Channel ID:",
        reply_markup=types.ReplyKeyboardMarkup(
            keyboard=[[types.KeyboardButton(text="❌ Cancel")]],
            resize_keyboard=True
        )
    )
    await state.set_state(ChannelState.delete_id)


@router.message(ChannelState.delete_id)
async def delete_confirm(message: types.Message, state: FSMContext):
    """Delete channel."""
    if message.text == "❌ Cancel":
        await state.clear()
        await message.answer("❌ Cancelled", reply_markup=main_menu_kb())
        return

    try:
        ch_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Invalid ID")
        return

    async with session() as s:
        ch = await s.get(Channel, ch_id)
        if not ch:
            await message.answer("❌ Not found", reply_markup=main_menu_kb())
            await state.clear()
            return

        title = ch.title
        await s.delete(ch)
        await s.commit()

    await message.answer(
        f"✅ DELETED!\n\n"
        f"{title}",
        reply_markup=main_menu_kb()
    )
    await state.clear()


@router.message(lambda msg: msg.text == "➕ Add Channel")
async def add_button(message: types.Message, state: FSMContext):
    """Add from menu."""
    await add_channel_start(message, state)


@router.message(lambda msg: msg.text == "📋 List Channels")
async def list_button(message: types.Message):
    """List from menu."""
    await list_channels(message)


@router.message(lambda msg: msg.text == "🗑️ Delete Channel")
async def delete_button(message: types.Message, state: FSMContext):
    """Delete from menu."""
    await delete_channel_start(message, state)


# ---------------------------------------------------------------------------
# Automatic registration - triggers the moment the bot is made an admin
# ---------------------------------------------------------------------------

# Per-channel set of category IDs currently ticked in the post-registration
# "pick categories" prompt. In-memory only (same pattern as moderation.py's
# spam counters) - fine since it's just UI state for a prompt that's meant
# to be answered right away.
_pending_cat_selection: dict[int, set[int]] = {}


@router.my_chat_member(F.chat.type == "channel")
async def channel_admin_added(update: types.ChatMemberUpdated) -> None:
    """Auto-register a channel the moment the bot is promoted to admin in
    it, so you don't have to look up and type its numeric chat_id - just
    add the bot as admin with posting permissions and it's registered.
    """
    if update.new_chat_member.status != ChatMemberStatus.ADMINISTRATOR:
        return
    if update.old_chat_member.status == ChatMemberStatus.ADMINISTRATOR:
        return  # already was admin (e.g. permissions edited) - not a new add

    actor = update.from_user
    if not actor or actor.id not in get_settings().allowed_user_id_set:
        # Someone who isn't an approved operator added the bot somewhere -
        # ignore silently rather than auto-registering a stranger's channel.
        return

    chat = update.chat
    async with session() as s:
        q = select(Channel).where(Channel.chat_id == chat.id)
        res = await s.execute(q)
        if res.scalars().first():
            return  # already registered

        ch = Channel(owner_user_id=actor.id, chat_id=chat.id, title=chat.title or str(chat.id))
        s.add(ch)
        await s.commit()
        ch_id = ch.id

        q2 = select(Category)
        res2 = await s.execute(q2)
        categories = res2.scalars().all()

    text = (
        "✅ CHANNEL REGISTERED\n\n"
        f"Title: {chat.title}\n"
        f"ID: {ch_id}\n\n"
        "I noticed I was made admin here and added it automatically - "
        "no need to run /add_channel.\n\n"
    )

    kb = None
    if categories:
        _pending_cat_selection[ch_id] = set()
        text += "Tap categories to assign this channel to (optional), then Done:"
        kb = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text=f"☐ {c.name}", callback_data=f"achcat_{ch_id}_{c.id}")]
                for c in categories
            ] + [[types.InlineKeyboardButton(text="✅ Done", callback_data=f"achdone_{ch_id}")]]
        )
    else:
        text += "No categories exist yet - create one with ➕ Add Category, then assign it via /list_channels."

    try:
        await update.bot.send_message(actor.id, text, reply_markup=kb)
    except Exception:
        # Operator hasn't opened a DM with the bot yet - registration still
        # succeeded, they'll see it in /list_channels.
        pass


@router.callback_query(F.data.startswith("achcat_"))
async def toggle_auto_channel_category(query: types.CallbackQuery) -> None:
    """Toggle a category on/off for a channel that was just auto-registered."""
    _, ch_id_s, cat_id_s = query.data.split("_")
    ch_id, cat_id = int(ch_id_s), int(cat_id_s)
    selected = _pending_cat_selection.setdefault(ch_id, set())
    if cat_id in selected:
        selected.discard(cat_id)
    else:
        selected.add(cat_id)

    async with session() as s:
        q = select(Category)
        res = await s.execute(q)
        categories = res.scalars().all()

    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(
                text=f"{'☑' if c.id in selected else '☐'} {c.name}",
                callback_data=f"achcat_{ch_id}_{c.id}"
            )]
            for c in categories
        ] + [[types.InlineKeyboardButton(text="✅ Done", callback_data=f"achdone_{ch_id}")]]
    )
    await query.message.edit_reply_markup(reply_markup=kb)
    await query.answer()


@router.callback_query(F.data.startswith("achdone_"))
async def finish_auto_channel_category(query: types.CallbackQuery) -> None:
    """Save the ticked categories for an auto-registered channel."""
    ch_id = int(query.data.replace("achdone_", ""))
    selected = _pending_cat_selection.pop(ch_id, set())

    async with session() as s:
        ch = await s.get(Channel, ch_id)
        if ch and selected:
            q = select(Category).where(Category.id.in_(selected))
            res = await s.execute(q)
            cats = res.scalars().all()
            for c in cats:
                ch.categories.append(c)
            await s.commit()

    await query.message.edit_text(f"✅ Saved. Categories assigned: {len(selected)}")
    await query.answer()
