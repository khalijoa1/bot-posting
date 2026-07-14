from aiogram import Router, types
from aiogram.filters import Command
from sqlalchemy import select

from db import session
from models import Category, Channel

router = Router()


@router.message(Command("add_category"))
async def add_category(message: types.Message):
    args = message.get_args()
    if not args:
        await message.reply("Usage: /add_category <name>")
        return
    name = args.strip()
    async with session() as s:
        cat = Category(owner_user_id=message.from_user.id if message.from_user else 0, name=name)
        s.add(cat)
        await s.commit()
        await message.reply(f"Created category id={cat.id} name={name}")


@router.message(Command("list_categories"))
async def list_categories(message: types.Message):
    async with session() as s:
        q = select(Category)
        res = await s.execute(q)
        rows = res.scalars().all()
        if not rows:
            await message.reply("No categories")
            return
        lines = [f"ID={r.id} name={r.name}" for r in rows]
        await message.reply("\n".join(lines))


@router.message(Command("assign_category"))
async def assign_category(message: types.Message):
    """Usage: /assign_category <channel_chat_id_or_id> <category_id>"""
    args = message.get_args()
    if not args:
        await message.reply("Usage: /assign_category <channel_chat_id_or_id> <category_id>")
        return
    parts = args.split()
    if len(parts) < 2:
        await message.reply("Usage: /assign_category <channel_chat_id_or_id> <category_id>")
        return
    ch_key = parts[0]
    try:
        cat_id = int(parts[1])
    except ValueError:
        await message.reply("category_id must be numeric")
        return
    # resolve channel and category
    try:
        ch_val = int(ch_key)
    except ValueError:
        await message.reply("channel id must be numeric")
        return
    async with session() as s:
        ch_q = select(Channel).where((Channel.chat_id == ch_val) | (Channel.id == ch_val))
        cres = await s.execute(ch_q)
        ch = cres.scalars().first()
        if not ch:
            await message.reply("Channel not found")
            return
        cat_q = select(Category).where(Category.id == cat_id)
        cres2 = await s.execute(cat_q)
        cat = cres2.scalars().first()
        if not cat:
            await message.reply("Category not found")
            return
        ch.categories.append(cat)
        s.add(ch)
        await s.commit()
        await message.reply(f"Assigned channel {ch.chat_id} to category {cat.name}")
