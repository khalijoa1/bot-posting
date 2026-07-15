"""Post to all channels in a category."""
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select

from db import session
from models import Category, Post, PostStatus, ContentType, PostTarget

router = Router()


class CategoryPostState(StatesGroup):
    select_category = State()
    message_text = State()


@router.message(Command("post_category"))
async def post_category_start(message: types.Message, state: FSMContext):
    """Start posting to category."""
    await state.clear()
    
    async with session() as s:
        q = select(Category)
        res = await s.execute(q)
        categories = res.scalars().all()
    
    if not categories:
        await message.answer(
            "❌ No categories yet\n\n"
            "Create categories first"
        )
        return
    
    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text=cat.name, callback_data=f"postcat_{cat.id}")]
            for cat in categories
        ] + [
            [types.InlineKeyboardButton(text="❌ Cancel", callback_data="postcat_cancel")]
        ]
    )
    
    await message.answer(
        "━━━━━━━━━━━━━━━━━━\n"
        "📁 POST TO CATEGORY\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Select category:",
        reply_markup=kb
    )
    await state.set_state(CategoryPostState.select_category)


@router.callback_query(CategoryPostState.select_category)
async def handle_category_select(query: types.CallbackQuery, state: FSMContext):
    """Handle category selection."""
    if query.data == "postcat_cancel":
        await state.clear()
        await query.message.answer("❌ Cancelled")
        await query.answer()
        return
    
    cat_id = int(query.data.replace("postcat_", ""))
    
    async with session() as s:
        cat = await s.get(Category, cat_id)
        if not cat or not cat.channels:
            await query.answer("No channels in this category", show_alert=True)
            return
    
    await state.update_data(category_id=cat_id)
    await query.message.answer(
        f"📁 {cat.name}\n\n"
        f"Channels: {len(cat.channels)}\n\n"
        f"Send message text:",
        reply_markup=types.ReplyKeyboardMarkup(
            keyboard=[[types.KeyboardButton(text="❌ Cancel")]],
            resize_keyboard=True
        )
    )
    await state.set_state(CategoryPostState.message_text)
    await query.answer()


@router.message(CategoryPostState.message_text)
async def handle_category_post(message: types.Message, state: FSMContext):
    """Post to category."""
    if message.text == "❌ Cancel":
        await state.clear()
        await message.answer("❌ Cancelled", reply_markup=types.ReplyKeyboardRemove())
        return
    
    data = await state.get_data()
    cat_id = data.get("category_id")
    text = message.text.strip()
    
    bot = message.bot
    success = 0
    failed = []
    
    async with session() as s:
        cat = await s.get(Category, cat_id)
        channels = cat.channels if cat else []
        
        post = Post(
            owner_user_id=message.from_user.id,
            content_type=ContentType.TEXT,
            text=text,
            status=PostStatus.SENT
        )
        s.add(post)
        await s.flush()
        
        for ch in channels:
            try:
                msg = await bot.send_message(chat_id=ch.chat_id, text=text)
                target = PostTarget(
                    post_id=post.id,
                    channel_id=ch.id,
                    message_id=msg.message_id
                )
                s.add(target)
                success += 1
            except Exception as e:
                failed.append(ch.title)
        
        await s.commit()
    
    result = (
        f"✅ POSTED TO CATEGORY!\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Channels: {success}/{len(channels)}\n"
        f"Post ID: {post.id}\n"
        f"━━━━━━━━━━━━━━━━━━"
    )
    
    if failed:
        result += f"\n\n❌ Failed:\n" + "\n".join(failed)
    
    await message.answer(result, reply_markup=types.ReplyKeyboardRemove())
    await state.clear()


@router.message(lambda msg: msg.text == "📨 Post to Category")
async def category_post_button(message: types.Message, state: FSMContext):
    """Post to category from menu."""
    await post_category_start(message, state)

