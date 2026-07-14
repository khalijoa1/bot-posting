"""Channel management."""
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select

from db import session
from models import Channel

router = Router()


class ChannelState(StatesGroup):
    chat_id = State()
    title = State()
    delete_id = State()


@router.message(Command("add_channel"))
async def add_channel_start(message: types.Message, state: FSMContext):
    """Start adding channel."""
    await state.clear()
    kb = types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text="🔙 Cancel")]],
        resize_keyboard=True
    )
    await message.answer(
        "━━━━━━━━━━━━━━━━━━\n"
        "➕ ADD CHANNEL\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Send the channel chat ID\n\n"
        "Format: -1001234567890",
        reply_markup=kb
    )
    await state.set_state(ChannelState.chat_id)


@router.message(ChannelState.chat_id, F.text == "🔙 Cancel")
async def cancel_add(message: types.Message, state: FSMContext):
    """Cancel add channel."""
    await state.clear()
    await message.answer("❌ Cancelled", reply_markup=types.ReplyKeyboardRemove())


@router.message(ChannelState.chat_id, F.text)
async def get_chat_id(message: types.Message, state: FSMContext):
    """Get chat ID."""
    try:
        chat_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Invalid. Send numeric ID like -1001234567890")
        return
    
    async with session() as s:
        q = select(Channel).where(Channel.chat_id == chat_id)
        res = await s.execute(q)
        if res.scalars().first():
            await message.answer("⚠️ Channel already added")
            await state.clear()
            return
    
    await state.update_data(chat_id=chat_id)
    kb = types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text="🔙 Cancel")]],
        resize_keyboard=True
    )
    await message.answer(
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Chat ID: {chat_id}\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"Now send the CHANNEL TITLE/NAME:",
        reply_markup=kb
    )
    await state.set_state(ChannelState.title)


@router.message(ChannelState.title, F.text == "🔙 Cancel")
async def cancel_title(message: types.Message, state: FSMContext):
    """Cancel at title."""
    await state.clear()
    await message.answer("❌ Cancelled", reply_markup=types.ReplyKeyboardRemove())


@router.message(ChannelState.title, F.text)
async def get_title(message: types.Message, state: FSMContext):
    """Get title and add channel."""
    data = await state.get_data()
    chat_id = data.get("chat_id")
    title = message.text.strip()
    
    async with session() as s:
        ch = Channel(
            owner_user_id=message.from_user.id,
            chat_id=chat_id,
            title=title
        )
        s.add(ch)
        await s.commit()
    
    await message.answer(
        f"✅ CHANNEL ADDED!\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"ID: {ch.id}\n"
        f"Title: {title}\n"
        f"Chat ID: {chat_id}\n"
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
            "❌ No channels added\n\n"
            "Use /add_channel to add one"
        )
        return
    
    text = "━━━━━━━━━━━━━━━━━━\n📍 CHANNELS\n━━━━━━━━━━━━━━━━━━\n\n"
    
    for ch in channels:
        text += (
            f"ID: {ch.id}\n"
            f"Title: {ch.title}\n"
            f"Chat ID: {ch.chat_id}\n"
            f"Auto-Approve: {'✅ ON' if ch.auto_approve_members else '❌ OFF'}\n\n"
        )
    
    await message.answer(text)


@router.message(Command("delete_channel"))
async def delete_channel_start(message: types.Message, state: FSMContext):
    """Start delete channel."""
    await state.clear()
    kb = types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text="🔙 Cancel")]],
        resize_keyboard=True
    )
    await message.answer(
        "━━━━━━━━━━━━━━━━━━\n"
        "🗑️ DELETE CHANNEL\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Send the Channel ID to delete\n\n"
        "(Use /list_channels to see IDs):",
        reply_markup=kb
    )
    await state.set_state(ChannelState.delete_id)


@router.message(ChannelState.delete_id, F.text == "🔙 Cancel")
async def cancel_delete(message: types.Message, state: FSMContext):
    """Cancel delete."""
    await state.clear()
    await message.answer("❌ Cancelled", reply_markup=types.ReplyKeyboardRemove())


@router.message(ChannelState.delete_id, F.text)
async def confirm_delete(message: types.Message, state: FSMContext):
    """Delete channel."""
    try:
        ch_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Invalid ID")
        return
    
    async with session() as s:
        ch = await s.get(Channel, ch_id)
        if not ch:
            await message.answer("❌ Channel not found")
            await state.clear()
            return
        
        title = ch.title
        await s.delete(ch)
        await s.commit()
    
    await message.answer(
        f"✅ DELETED!\n\n"
        f"━━━━━━━━━━━━\n"
        f"Channel: {title}\n"
        f"━━━━━━━━━━━━",
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

