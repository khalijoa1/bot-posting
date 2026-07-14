from aiogram import Router, types
from aiogram.filters import Command
from sqlalchemy import select

from db import session
from models import Channel

router = Router()


@router.message(Command("add_channel"))
async def add_channel(message: types.Message):
    """Usage: /add_channel <chat_id> <title>
    Example: /add_channel -1001234567890 "My Channel"""
    args = message.get_args()
    if not args:
        await message.reply("Usage: /add_channel <chat_id> <title>")
        return
    parts = args.split(None, 1)
    if len(parts) < 1:
        await message.reply("Usage: /add_channel <chat_id> <title>")
        return
    chat_id = parts[0].strip()
    title = parts[1].strip() if len(parts) > 1 else ""
    try:
        chat_id_int = int(chat_id)
    except ValueError:
        await message.reply("chat_id must be numeric, e.g. -1001234567890")
        return

    async with session() as s:
        # check if exists
        q = select(Channel).where(Channel.chat_id == chat_id_int)
        res = await s.execute(q)
        existing = res.scalars().first()
        if existing:
            await message.reply("Channel already exists in DB.")
            return
        ch = Channel(owner_user_id=message.from_user.id if message.from_user else 0, chat_id=chat_id_int, title=title)
        s.add(ch)
        await s.commit()
        await message.reply(f"Added channel {chat_id_int} titled: {title}")


@router.message(Command("list_channels"))
async def list_channels(message: types.Message):
    async with session() as s:
        q = select(Channel)
        res = await s.execute(q)
        rows = res.scalars().all()
        if not rows:
            await message.reply("No channels registered")
            return
        lines = [f"ID={r.id} chat_id={r.chat_id} title={r.title}" for r in rows]
        await message.reply("\n".join(lines))


@router.message(Command("delete_channel"))
async def delete_channel(message: types.Message):
    args = message.get_args()
    if not args:
        await message.reply("Usage: /delete_channel <chat_id_or_id>")
        return
    key = args.strip()
    async with session() as s:
        # try by chat_id then id
        try:
            val = int(key)
        except ValueError:
            await message.reply("Invalid id")
            return
        q = select(Channel).where((Channel.chat_id == val) | (Channel.id == val))
        res = await s.execute(q)
        row = res.scalars().first()
        if not row:
            await message.reply("Channel not found")
            return
        await s.delete(row)
        await s.commit()
        await message.reply("Deleted channel")
