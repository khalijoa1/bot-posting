"""Handler for managing categories."""
from aiogram import Router, types
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select

from db import session
from models import Category

router = Router()


class CategoryState(StatesGroup):
    waiting_for_name = State()


@router.message(Command("add_category"))
async def add_category_start(message: types.Message, state: FSMContext):
    """Start adding a category"""
    await message.reply(
        "📁 Add Category\n\n"
        "Send the category name:",
        parse_mode=None
    )
    await state.set_state(CategoryState.waiting_for_name)


@router.message(CategoryState.waiting_for_name)
async def process_category_name(message: types.Message, state: FSMContext):
    """Process category name"""
    if not message.text:
        await message.reply("Please send text for the category name", parse_mode=None)
        return

    name = message.text.strip()
    
    async with session() as s:
        cat = Category(
            owner_user_id=message.from_user.id if message.from_user else 0,
            name=name
        )
        s.add(cat)
        await s.commit()
        await message.reply(f"✅ Category created!\n\nID: {cat.id}\nName: {name}", parse_mode=None)
    
    await state.clear()


@router.message(Command("list_categories"))
async def list_categories(message: types.Message):
    """List all categories"""
    async with session() as s:
        q = select(Category)
        res = await s.execute(q)
        rows = res.scalars().all()

    if not rows:
        await message.reply("📁 No categories yet\n\nUse /add_category to create one", parse_mode=None)
        return

    text = "📁 Categories:\n\n"
    for r in rows:
        text += f"  ID: {r.id}\n  Name: {r.name}\n\n"
    
    await message.reply(text.strip(), parse_mode=None)

