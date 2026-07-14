"""Category management."""
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select

from db import session
from models import Category

router = Router()


class CategoryState(StatesGroup):
    name = State()


@router.message(Command("add_category"))
async def add_category_start(message: types.Message, state: FSMContext):
    """Start adding category."""
    await state.clear()
    kb = types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text="🔙 Cancel")]],
        resize_keyboard=True
    )
    await message.answer(
        "━━━━━━━━━━━━━━━━━━\n"
        "➕ ADD CATEGORY\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Send the category name:",
        reply_markup=kb
    )
    await state.set_state(CategoryState.name)


@router.message(CategoryState.name, F.text == "🔙 Cancel")
async def cancel_category(message: types.Message, state: FSMContext):
    """Cancel adding category."""
    await state.clear()
    await message.answer("❌ Cancelled", reply_markup=types.ReplyKeyboardRemove())


@router.message(CategoryState.name, F.text)
async def get_category_name(message: types.Message, state: FSMContext):
    """Get category name and add it."""
    name = message.text.strip()
    
    if len(name) < 1:
        await message.answer("❌ Name cannot be empty")
        return
    
    async with session() as s:
        cat = Category(
            owner_user_id=message.from_user.id,
            name=name
        )
        s.add(cat)
        await s.commit()
    
    await message.answer(
        f"✅ CATEGORY CREATED!\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"ID: {cat.id}\n"
        f"Name: {name}\n"
        f"━━━━━━━━━━━━━━━━",
        reply_markup=types.ReplyKeyboardRemove()
    )
    await state.clear()


@router.message(Command("list_categories"))
async def list_categories(message: types.Message):
    """List all categories."""
    async with session() as s:
        q = select(Category)
        res = await s.execute(q)
        categories = res.scalars().all()
    
    if not categories:
        await message.answer(
            "━━━━━━━━━━━━━━━━━━\n"
            "📁 CATEGORIES\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "❌ No categories\n\n"
            "Use /add_category to create one"
        )
        return
    
    text = "━━━━━━━━━━━━━━━━━━\n📁 CATEGORIES\n━━━━━━━━━━━━━━━━━━\n\n"
    
    for cat in categories:
        text += f"ID: {cat.id}\nName: {cat.name}\n\n"
    
    await message.answer(text)


@router.message(lambda msg: msg.text == "➕ Add Category")
async def add_button(message: types.Message, state: FSMContext):
    """Add category from menu."""
    await add_category_start(message, state)


@router.message(lambda msg: msg.text == "📋 List Categories")
async def list_button(message: types.Message):
    """List categories from menu."""
    await list_categories(message)

