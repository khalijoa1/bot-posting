"""Link replacement for posts."""
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select

from db import session
from models import Post

router = Router()


class ReplacerState(StatesGroup):
    select_mode = State()
    post_range = State()
    old_link = State()
    new_link = State()


@router.message(Command("replacer"))
async def replacer_start(message: types.Message, state: FSMContext):
    """Start link replacer."""
    await state.clear()
    
    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text="🔄 All Posts", callback_data="repl_all")],
            [types.InlineKeyboardButton(text="📋 Range (e.g. 1-5)", callback_data="repl_range")],
            [types.InlineKeyboardButton(text="📌 Single Post", callback_data="repl_single")],
            [types.InlineKeyboardButton(text="❌ Cancel", callback_data="repl_cancel")]
        ]
    )
    
    await message.answer(
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🔗 LINK REPLACER\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Replace links in:\n\n"
        "🔄 All Posts - All your posts\n"
        "📋 Range - Posts 5 to 10 (etc)\n"
        "📌 Single - Just one post",
        reply_markup=kb
    )
    await state.set_state(ReplacerState.select_mode)


@router.callback_query(ReplacerState.select_mode)
async def handle_mode(query: types.CallbackQuery, state: FSMContext):
    """Handle replacement mode."""
    if query.data == "repl_cancel":
        await state.clear()
        await query.message.answer("❌ Cancelled")
        await query.answer()
        return
    
    mode = query.data.replace("repl_", "")
    await state.update_data(mode=mode)
    
    if mode == "all":
        await state.update_data(post_ids="all")
        await ask_old_link(query.message, state)
    elif mode == "range":
        await query.message.answer(
            "📋 RANGE MODE\n\n"
            "Send range:\n\n"
            "Format: 1-5 (posts 1 to 5)\n"
            "Or: 10-15",
            reply_markup=types.ReplyKeyboardMarkup(
                keyboard=[[types.KeyboardButton(text="❌ Cancel")]],
                resize_keyboard=True
            )
        )
        await state.set_state(ReplacerState.post_range)
    elif mode == "single":
        await query.message.answer(
            "📌 SINGLE POST\n\n"
            "Send Post ID:\n\n"
            "Use /myposts to find ID",
            reply_markup=types.ReplyKeyboardMarkup(
                keyboard=[[types.KeyboardButton(text="❌ Cancel")]],
                resize_keyboard=True
            )
        )
        await state.set_state(ReplacerState.post_range)
    
    await query.answer()


@router.message(ReplacerState.post_range)
async def handle_range(message: types.Message, state: FSMContext):
    """Handle post range input."""
    if message.text == "❌ Cancel":
        await state.clear()
        await message.answer("❌ Cancelled", reply_markup=types.ReplyKeyboardRemove())
        return
    
    data = await state.get_data()
    mode = data.get("mode")
    
    if mode == "range":
        try:
            parts = message.text.split("-")
            start = int(parts[0].strip())
            end = int(parts[1].strip())
            await state.update_data(post_range=(start, end))
        except:
            await message.answer("❌ Invalid format. Use: 1-5")
            return
    else:  # single
        try:
            post_id = int(message.text.strip())
            await state.update_data(post_ids=[post_id])
        except:
            await message.answer("❌ Invalid ID")
            return
    
    await ask_old_link(message, state)


async def ask_old_link(message: types.Message, state: FSMContext):
    """Ask for old link."""
    await message.answer(
        "Send the OLD LINK to replace:\n\n"
        "Example: https://old-url.com",
        reply_markup=types.ReplyKeyboardMarkup(
            keyboard=[[types.KeyboardButton(text="❌ Cancel")]],
            resize_keyboard=True
        )
    )
    await state.set_state(ReplacerState.old_link)


@router.message(ReplacerState.old_link)
async def handle_old_link(message: types.Message, state: FSMContext):
    """Handle old link."""
    if message.text == "❌ Cancel":
        await state.clear()
        await message.answer("❌ Cancelled", reply_markup=types.ReplyKeyboardRemove())
        return
    
    old_link = message.text.strip()
    await state.update_data(old_link=old_link)
    
    await message.answer(
        "Send the NEW LINK:\n\n"
        "Example: https://new-url.com",
        reply_markup=types.ReplyKeyboardMarkup(
            keyboard=[[types.KeyboardButton(text="❌ Cancel")]],
            resize_keyboard=True
        )
    )
    await state.set_state(ReplacerState.new_link)


@router.message(ReplacerState.new_link)
async def handle_new_link(message: types.Message, state: FSMContext):
    """Process replacement."""
    if message.text == "❌ Cancel":
        await state.clear()
        await message.answer("❌ Cancelled", reply_markup=types.ReplyKeyboardRemove())
        return
    
    data = await state.get_data()
    mode = data.get("mode")
    old_link = data.get("old_link")
    new_link = message.text.strip()
    
    async with session() as s:
        if mode == "all":
            q = select(Post).where(Post.owner_user_id == message.from_user.id)
            res = await s.execute(q)
            posts = res.scalars().all()
        elif mode == "range":
            start, end = data.get("post_range")
            q = select(Post).where(
                (Post.owner_user_id == message.from_user.id) &
                (Post.id >= start) &
                (Post.id <= end)
            )
            res = await s.execute(q)
            posts = res.scalars().all()
        else:  # single
            post_ids = data.get("post_ids")
            q = select(Post).where(
                (Post.owner_user_id == message.from_user.id) &
                (Post.id.in_(post_ids))
            )
            res = await s.execute(q)
            posts = res.scalars().all()
        
        updated = 0
        for post in posts:
            if old_link in (post.text or ""):
                post.text = (post.text or "").replace(old_link, new_link)
                s.add(post)
                updated += 1
        
        await s.commit()
    
    result = (
        f"✅ REPLACEMENT COMPLETE!\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"Posts Updated: {updated}\n"
        f"Old: {old_link[:30]}...\n"
        f"New: {new_link[:30]}...\n"
        f"━━━━━━━━━━━━━━━━━"
    )
    
    await message.answer(result, reply_markup=types.ReplyKeyboardRemove())
    await state.clear()


@router.message(lambda msg: msg.text == "🔗 Link Replacer")
async def replacer_button(message: types.Message, state: FSMContext):
    """Link replacer from menu."""
    await replacer_start(message, state)

