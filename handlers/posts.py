"""Post management - view, edit, delete."""
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select

from db import session
from models import Post, PostTarget

router = Router()


class EditState(StatesGroup):
    post_id = State()
    new_text = State()


@router.message(Command("myposts"))
async def list_posts(message: types.Message):
    """List all user's posts."""
    async with session() as s:
        q = select(Post).where(Post.owner_user_id == message.from_user.id)
        res = await s.execute(q)
        posts = res.scalars().all()
    
    if not posts:
        await message.answer(
            "━━━━━━━━━━━━━━━━━\n"
            "📋 MY POSTS\n"
            "━━━━━━━━━━━━━━━━━\n\n"
            "❌ No posts yet\n\n"
            "Use /compose to create a post"
        )
        return
    
    text = "━━━━━━━━━━━━━━━━━\n📋 MY POSTS\n━━━━━━━━━━━━━━━━━\n\n"
    
    for p in posts:
        async with session() as s:
            tq = select(PostTarget).where(PostTarget.post_id == p.id)
            tres = await s.execute(tq)
            targets = tres.scalars().all()
        
        preview = (p.text or "")[:60]
        text += (
            f"━━━━━━━━━━━━━━━━━\n"
            f"ID: {p.id}\n"
            f"Text: {preview}{'...' if len(p.text or '') > 60 else ''}\n"
            f"Channels: {len(targets)}\n"
            f"Status: {p.status.value}\n\n"
        )
    
    await message.answer(text)


@router.message(Command("edit"))
async def edit_start(message: types.Message, state: FSMContext):
    """Start edit workflow."""
    await state.clear()
    kb = types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text="🔙 Cancel")]],
        resize_keyboard=True
    )
    await message.answer(
        "━━━━━━━━━━━━━━━━━━\n"
        "✎️ EDIT POST\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Send the Post ID to edit\n\n"
        "(Use /myposts to see IDs):",
        reply_markup=kb
    )
    await state.set_state(EditState.post_id)


@router.message(EditState.post_id, F.text == "🔙 Cancel")
async def cancel_edit(message: types.Message, state: FSMContext):
    """Cancel edit."""
    await state.clear()
    await message.answer("❌ Cancelled", reply_markup=types.ReplyKeyboardRemove())


@router.message(EditState.post_id, F.text)
async def get_post_id(message: types.Message, state: FSMContext):
    """Get post ID to edit."""
    try:
        post_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Invalid ID. Send a number")
        return
    
    async with session() as s:
        post = await s.get(Post, post_id)
        if not post or post.owner_user_id != message.from_user.id:
            await message.answer("❌ Post not found or not yours")
            return
        
        tq = select(PostTarget).where(PostTarget.post_id == post_id)
        tres = await s.execute(tq)
        targets = tres.scalars().all()
    
    if not targets:
        await message.answer("❌ No active messages for this post")
        return
    
    await state.update_data(post_id=post_id)
    kb = types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text="🔙 Cancel")]],
        resize_keyboard=True
    )
    await message.answer(
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Post ID: {post_id}\n"
        f"Channels: {len(targets)}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Send the NEW TEXT:\n"
        f"(Will update in all {len(targets)} channels)",
        reply_markup=kb
    )
    await state.set_state(EditState.new_text)


@router.message(EditState.new_text, F.text == "🔙 Cancel")
async def cancel_edit_text(message: types.Message, state: FSMContext):
    """Cancel edit text."""
    await state.clear()
    await message.answer("❌ Cancelled", reply_markup=types.ReplyKeyboardRemove())


@router.message(EditState.new_text, F.text)
async def apply_edit(message: types.Message, state: FSMContext):
    """Apply edit to all channels."""
    data = await state.get_data()
    post_id = data.get("post_id")
    new_text = message.text.strip()
    bot = message.bot
    
    async with session() as s:
        post = await s.get(Post, post_id)
        post.text = new_text
        s.add(post)
        
        tq = select(PostTarget).where(PostTarget.post_id == post_id)
        tres = await s.execute(tq)
        targets = tres.scalars().all()
        
        success = 0
        failed = []
        for target in targets:
            try:
                await bot.edit_message_text(
                    chat_id=target.channel.chat_id,
                    message_id=target.message_id,
                    text=new_text
                )
                success += 1
            except Exception as e:
                failed.append(target.channel.title)
        
        await s.commit()
    
    result = (
        f"✅ EDITED!\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Channels: {success}/{len(targets)}\n"
        f"━━━━━━━━━━━━━━━━━━"
    )
    
    if failed:
        result += f"\n\n❌ Failed:\n" + "\n".join([f"  • {c}" for c in failed])
    
    await message.answer(result, reply_markup=types.ReplyKeyboardRemove())
    await state.clear()


@router.message(Command("delete"))
async def delete_start(message: types.Message, state: FSMContext):
    """Start delete workflow."""
    await state.clear()
    kb = types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text="🔙 Cancel")]],
        resize_keyboard=True
    )
    await message.answer(
        "━━━━━━━━━━━━━━━━━━\n"
        "🗑️ DELETE POST\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Send the Post ID to delete\n\n"
        "(Use /myposts to see IDs):",
        reply_markup=kb
    )
    await state.set_state(EditState.post_id)


@router.message(lambda msg: msg.text == "📋 View My Posts")
async def view_posts_button(message: types.Message):
    """View posts from menu."""
    await list_posts(message)


@router.message(lambda msg: msg.text == "✎️ Edit Post")
async def edit_button(message: types.Message, state: FSMContext):
    """Edit from menu."""
    await edit_start(message, state)


@router.message(lambda msg: msg.text == "🗑️ Delete Post")
async def delete_button(message: types.Message, state: FSMContext):
    """Delete from menu."""
    await delete_start(message, state)

