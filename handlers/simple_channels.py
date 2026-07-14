"""Simple channel management."""
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select

from db import session
from models import Channel, Category

router = Router()


class ChannelStates(StatesGroup):
    chat_id = State()
    title = State()


@router.message(Command("add_channel"))
async def add_channel(message: types.Message, state: FSMContext):
    """Start adding a channel."""
    await message.answer("📍 Send the channel chat ID (e.g., -1001234567890):")
    await state.set_state(ChannelStates.chat_id)


@router.message(ChannelStates.chat_id, F.text)
async def get_chat_id(message: types.Message, state: FSMContext):
    """Get channel chat ID."""
    try:
        chat_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Invalid ID. Send a number like -1001234567890")
        return
    
    async with session() as s:
        q = select(Channel).where(Channel.chat_id == chat_id)
        res = await s.execute(q)
        if res.scalars().first():
            await message.answer("⚠️ Channel already exists")
            await state.clear()
            return
    
    await state.update_data(chat_id=chat_id)
    await message.answer("📝 Send the channel title/name:")
    await state.set_state(ChannelStates.title)


@router.message(ChannelStates.title, F.text)
async def get_title(message: types.Message, state: FSMContext):
    """Get channel title and add it."""
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
    
    await message.answer(f"✅ Channel added!\n\nTitle: {title}\nID: {ch.id}")
    await state.clear()


@router.message(Command("list_channels"))
async def list_channels(message: types.Message):
    """List all channels."""
    async with session() as s:
        q = select(Channel)
        res = await s.execute(q)
        channels = res.scalars().all()
    
    if not channels:
        await message.answer("📍 No channels added yet.\n\nUse /add_channel to add one.")
        return
    
    text = "📍 CHANNELS:\n\n"
    for ch in channels:
        text += f"ID: {ch.id}\nTitle: {ch.title}\nChat ID: {ch.chat_id}\n\n"
    
    await message.answer(text.strip())


@router.message(Command("delete_channel"))
async def delete_channel(message: types.Message):
    """Delete a channel - requires channel ID."""
    await message.answer("Send channel ID to delete (use /list_channels to see IDs):")


@router.message(F.text)
async def try_delete(message: types.Message):
    """Try to delete if it looks like a delete command."""
    if not message.text.isdigit():
        return
    
    ch_id = int(message.text)
    async with session() as s:
        ch = await s.get(Channel, ch_id)
        if ch:
            await s.delete(ch)
            await s.commit()
            await message.answer(f"✅ Deleted: {ch.title}")
        else:
            await message.answer("❌ Channel not found")

