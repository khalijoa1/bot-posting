"""Category management."""
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select

from db import session
from handlers.common import main_menu_kb
from models import Category, Channel

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
    await message.answer("❌ Cancelled", reply_markup=main_menu_kb())


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
        cat_id = cat.id

    await message.answer(
        f"✅ CATEGORY CREATED!\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"ID: {cat_id}\n"
        f"Name: {name}\n"
        f"━━━━━━━━━━━━━━━━",
        reply_markup=main_menu_kb()
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


async def _channel_category_kb(ch_id: int):
    """Build the (title, keyboard) pair for toggling one channel's categories.
    Re-fetches everything inside a single session to avoid lazy-load errors
    on relationships accessed after the session closes.
    """
    async with session() as s:
        ch = await s.get(Channel, ch_id)
        if not ch:
            return None
        title = ch.title
        current = {c.id for c in ch.categories}

        q = select(Category)
        res = await s.execute(q)
        categories = res.scalars().all()

    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(
                text=f"{'☑' if c.id in current else '☐'} {c.name}",
                callback_data=f"chcattgl_{ch_id}_{c.id}"
            )]
            for c in categories
        ] + [[types.InlineKeyboardButton(text="✅ Done", callback_data=f"chcatdone_{ch_id}")]]
    )
    return title, kb


@router.message(Command("assign_categories"))
async def assign_categories_start(message: types.Message):
    """Pick a channel, then toggle which categories it belongs to.

    This is the one persistent way to (re)organize a channel into
    categories after the fact - previously categories could only be picked
    once, at the moment a channel was added, with no way to revisit it.
    """
    async with session() as s:
        q = select(Channel)
        res = await s.execute(q)
        channels = res.scalars().all()

    if not channels:
        await message.answer(
            "━━━━━━━━━━━━━━━━━━\n"
            "🏷️ ASSIGN CATEGORIES\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "❌ No channels yet.\n\n"
            "Add the bot as admin to a channel to register it, or use /add_channel."
        )
        return

    async with session() as s:
        q = select(Category)
        res = await s.execute(q)
        if not res.scalars().first():
            await message.answer(
                "━━━━━━━━━━━━━━━━━━\n"
                "🏷️ ASSIGN CATEGORIES\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "❌ No categories yet.\n\n"
                "Create one first with ➕ Add Category."
            )
            return

    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text=f"📍 {ch.title}", callback_data=f"chcatpick_{ch.id}")]
            for ch in channels
        ]
    )
    await message.answer(
        "━━━━━━━━━━━━━━━━━━\n"
        "🏷️ ASSIGN CATEGORIES\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Pick a channel to set its categories:",
        reply_markup=kb
    )


@router.callback_query(F.data.startswith("chcatpick_"))
async def pick_channel_for_categories(query: types.CallbackQuery):
    ch_id = int(query.data.replace("chcatpick_", ""))
    result = await _channel_category_kb(ch_id)
    if not result:
        await query.answer("Channel not found", show_alert=True)
        return
    title, kb = result
    await query.message.edit_text(f"🏷️ {title}\n\nTap to toggle categories, then Done:", reply_markup=kb)
    await query.answer()


@router.callback_query(F.data.startswith("chcattgl_"))
async def toggle_channel_category(query: types.CallbackQuery):
    _, ch_id_s, cat_id_s = query.data.split("_")
    ch_id, cat_id = int(ch_id_s), int(cat_id_s)

    async with session() as s:
        ch = await s.get(Channel, ch_id)
        cat = await s.get(Category, cat_id)
        if not ch or not cat:
            await query.answer("Not found", show_alert=True)
            return
        if cat in ch.categories:
            ch.categories.remove(cat)
        else:
            ch.categories.append(cat)
        await s.commit()

    result = await _channel_category_kb(ch_id)
    if result:
        _, kb = result
        await query.message.edit_reply_markup(reply_markup=kb)
    await query.answer("Updated")


@router.callback_query(F.data.startswith("chcatdone_"))
async def finish_channel_categories(query: types.CallbackQuery):
    ch_id = int(query.data.replace("chcatdone_", ""))
    async with session() as s:
        ch = await s.get(Channel, ch_id)
        title = ch.title if ch else "?"
        cat_names = [c.name for c in ch.categories] if ch else []

    names = ", ".join(cat_names) if cat_names else "None"
    await query.message.edit_text(f"✅ {title}\n\nCategories: {names}")
    await query.answer()


@router.message(lambda msg: msg.text == "➕ Add Category")
async def add_button(message: types.Message, state: FSMContext):
    """Add category from menu."""
    await add_category_start(message, state)


@router.message(lambda msg: msg.text == "📋 List Categories")
async def list_button(message: types.Message):
    """List categories from menu."""
    await list_categories(message)
