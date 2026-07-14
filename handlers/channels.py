"""Handler for managing channels."""
from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select

from db import session
from models import Category, Channel

router = Router()


class ChannelState(StatesGroup):
    waiting_for_chat_id = State()
    waiting_for_title = State()
    waiting_for_categories = State()


@router.message(Command("add_channel"))
async def add_channel_start(message: types.Message, state: FSMContext):
    """Start adding a channel"""
    kb = types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text="Back")]],
        resize_keyboard=True
    )
    await message.reply(
        "📍 ADD CHANNEL\n\n"
        "Send the chat ID:\n"
        "(e.g., -1001234567890)",
        reply_markup=kb,
        parse_mode=None
    )
    await state.set_state(ChannelState.waiting_for_chat_id)


@router.message(ChannelState.waiting_for_chat_id)
async def process_chat_id(message: types.Message, state: FSMContext):
    """Process chat ID"""
    if message.text == "Back":
        await state.clear()
        await message.reply("Cancelled", parse_mode=None)
        return

    try:
        chat_id = int(message.text.strip())
    except ValueError:
        await message.reply("❌ Invalid ID. Send numeric ID", parse_mode=None)
        return

    # Check if exists
    async with session() as s:
        q = select(Channel).where(Channel.chat_id == chat_id)
        res = await s.execute(q)
        if res.scalars().first():
            await message.reply("⚠️ Already added", parse_mode=None)
            await state.clear()
            return

    await state.update_data(chat_id=chat_id)
    await message.reply("Now send channel title:", parse_mode=None)
    await state.set_state(ChannelState.waiting_for_title)


@router.message(ChannelState.waiting_for_title)
async def process_title(message: types.Message, state: FSMContext):
    """Process title"""
    title = message.text.strip()
    await state.update_data(title=title)

    # Get categories
    async with session() as s:
        q = select(Category)
        res = await s.execute(q)
        categories = res.scalars().all()

    if not categories:
        # No categories - add directly
        data = await state.get_data()
        async with session() as s:
            ch = Channel(
                owner_user_id=message.from_user.id,
                chat_id=data["chat_id"],
                title=data["title"]
            )
            s.add(ch)
            await s.commit()

        await message.reply(
            f"✅ Channel Added!\n\n"
            f"Title: {title}\n"
            f"ID: {ch.id}",
            parse_mode=None
        )
        await state.clear()
        return

    # Show categories
    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text=f"📁 {cat.name}", callback_data=f"cat_{cat.id}")]
            for cat in categories
        ] + [[types.InlineKeyboardButton(text="✅ Skip", callback_data="cat_skip")]]
    )

    await message.reply("Select categories (optional):", reply_markup=kb, parse_mode=None)
    await state.update_data(selected_categories=[])
    await state.set_state(ChannelState.waiting_for_categories)


@router.callback_query(ChannelState.waiting_for_categories)
async def process_categories(query: types.CallbackQuery, state: FSMContext):
    """Process category selection"""
    callback_data = query.data

    if callback_data == "cat_skip":
        # Skip and add channel
        data = await state.get_data()
        async with session() as s:
            ch = Channel(
                owner_user_id=query.from_user.id,
                chat_id=data["chat_id"],
                title=data["title"]
            )
            s.add(ch)
            await s.commit()

        await query.message.reply(
            f"✅ Channel Added!\n\n"
            f"Title: {data['title']}\n"
            f"ID: {ch.id}",
            parse_mode=None
        )
        await state.clear()
        await query.answer()
        return

    # Toggle category
    cat_id = int(callback_data.replace("cat_", ""))
    data = await state.get_data()
    selected = data.get("selected_categories", [])

    if cat_id in selected:
        selected.remove(cat_id)
    else:
        selected.append(cat_id)

    await state.update_data(selected_categories=selected)

    # Refresh keyboard
    async with session() as s:
        q = select(Category)
        res = await s.execute(q)
        categories = res.scalars().all()

    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(
                text=f"{'✅' if cat.id in selected else '⬜'} {cat.name}",
                callback_data=f"cat_{cat.id}"
            )]
            for cat in categories
        ] + [[types.InlineKeyboardButton(text="✅ Done", callback_data="cat_done")]]
    )

    await query.message.edit_reply_markup(reply_markup=kb)
    await query.answer()

    # Check if this is the done click
    if callback_data == "cat_done":
        data = await state.get_data()
        cat_ids = data.get("selected_categories", [])

        async with session() as s:
            ch = Channel(
                owner_user_id=query.from_user.id,
                chat_id=data["chat_id"],
                title=data["title"]
            )
            s.add(ch)
            await s.flush()

            if cat_ids:
                cat_q = select(Category).where(Category.id.in_(cat_ids))
                cat_res = await s.execute(cat_q)
                cats = cat_res.scalars().all()
                for cat in cats:
                    ch.categories.append(cat)

            await s.commit()

        await query.message.reply(
            f"✅ Channel Added!\n\n"
            f"Title: {data['title']}\n"
            f"Categories: {len(cat_ids)}",
            parse_mode=None
        )
        await state.clear()


@router.message(Command("list_channels"))
async def list_channels(message: types.Message):
    """List all channels"""
    async with session() as s:
        q = select(Channel)
        res = await s.execute(q)
        channels = res.scalars().all()

    if not channels:
        await message.reply("📍 No channels yet\n\nUse /add_channel", parse_mode=None)
        return

    text = "📍 CHANNELS:\n\n"
    for ch in channels:
        text += f"ID: {ch.id}\nTitle: {ch.title}\nChat ID: {ch.chat_id}\n\n"

    await message.reply(text.strip(), parse_mode=None)


@router.message(Command("delete_channel"))
async def delete_channel_start(message: types.Message, state: FSMContext):
    """Start deleting channel"""
    await message.reply("Send channel ID to delete:", parse_mode=None)


@router.message()
async def delete_channel_process(message: types.Message):
    """Process channel deletion"""
    try:
        ch_id = int(message.text.strip())
    except ValueError:
        await message.reply("Invalid ID", parse_mode=None)
        return

    async with session() as s:
        ch = await s.get(Channel, ch_id)
        if ch:
            await s.delete(ch)
            await s.commit()
            await message.reply("✅ Deleted", parse_mode=None)
        else:
            await message.reply("❌ Not found", parse_mode=None)

