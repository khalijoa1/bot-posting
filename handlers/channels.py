"""Channel management with categories and welcome messages."""
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select

from db import session
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
        "Send chat ID:\n\n"
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
    await message.answer("❌ Cancelled", reply_markup=types.ReplyKeyboardRemove())


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
            await message.answer("⚠️ Already added")
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
    await message.answer("❌ Cancelled", reply_markup=types.ReplyKeyboardRemove())


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
    await message.answer("❌ Cancelled", reply_markup=types.ReplyKeyboardRemove())


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
        reply_markup=types.ReplyKeyboardRemove()
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
            "Use /add_channel"
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
        await message.answer("❌ Cancelled", reply_markup=types.ReplyKeyboardRemove())
        return
    
    try:
        ch_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Invalid ID")
        return
    
    async with session() as s:
        ch = await s.get(Channel, ch_id)
        if not ch:
            await message.answer("❌ Not found")
            await state.clear()
            return
        
        title = ch.title
        await s.delete(ch)
        await s.commit()
    
    await message.answer(
        f"✅ DELETED!\n\n"
        f"{title}",
        reply_markup=types.ReplyKeyboardRemove()
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

