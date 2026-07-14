from aiogram import Router, types
from aiogram.filters import Command
from sqlalchemy import select

from db import session
from models import SourceChannel

router = Router()


@router.message(Command("add_source"))
async def add_source(message: types.Message):
    """Usage: /add_source <identifier> [title]
identifier can be @username or numeric chat id"""
    args = message.get_args()
    if not args:
        await message.reply("Usage: /add_source <identifier> [title]")
        return
    parts = args.split(None, 1)
    identifier = parts[0].strip()
    title = parts[1].strip() if len(parts) > 1 else None
    async with session() as s:
        # check exists
        q = select(SourceChannel).where(SourceChannel.identifier == identifier)
        res = await s.execute(q)
        if res.scalars().first():
            await message.reply("Source already exists")
            return
        sc = SourceChannel(owner_user_id=message.from_user.id if message.from_user else 0, identifier=identifier, title=title)
        s.add(sc)
        await s.commit()
        await message.reply(f"Added source {identifier} title={title}")


@router.message(Command("list_sources"))
async def list_sources(message: types.Message):
    async with session() as s:
        q = select(SourceChannel)
        res = await s.execute(q)
        rows = res.scalars().all()
        if not rows:
            await message.reply("No sources configured")
            return
        lines = [f"ID={r.id} identifier={r.identifier} title={r.title}" for r in rows]
        await message.reply("\n".join(lines))


@router.message(Command("remove_source"))
async def remove_source(message: types.Message):
    args = message.get_args()
    if not args:
        await message.reply("Usage: /remove_source <identifier_or_id>")
        return
    key = args.strip()
    async with session() as s:
        # try id
        try:
            val = int(key)
            q = select(SourceChannel).where(SourceChannel.id == val)
        except ValueError:
            q = select(SourceChannel).where(SourceChannel.identifier == key)
        res = await s.execute(q)
        row = res.scalars().first()
        if not row:
            await message.reply("Source not found")
            return
        await s.delete(row)
        await s.commit()
        await message.reply("Removed source")
