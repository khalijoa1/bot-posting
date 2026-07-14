"""Simple post management."""
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select

from db import session
from models import Post, PostTarget

router = Router()


class ManageStates(StatesGroup):
    post_id = State()
    new_text = State()


@router.message(Command("myposts"))
async def my_posts(message: types.Message):
    """Show user's posts."""
    async with session() as s:
        q = select(Post).where(Post.owner_user_id == message.from_user.id)
        res = await s.execute(q)
        posts = res.scalars().all()
    
    if not posts:
        await message.answer("📭 You have no posts yet.")
        return
    
    text = "📋 YOUR POSTS:\n\n"
    for p in posts:
        async with session() as s:
            tq = select(PostTarget).where(PostTarget.post_id == p.id)
            tres = await s.execute(tq)
            targets = tres.scalars().all()
        
        preview = (p.text or "")[:50]
        text += f"ID: {p.id}\nText: {preview}...\nChannels: {len(targets)}\n\n"
    
    await message.answer(text.strip())


@router.message(Command("edit"))
async def edit_post(message: types.Message, state: FSMContext):
    """Edit a post."""
    await message.answer("Send Post ID to edit (use /myposts to see IDs):")
    await state.set_state(ManageStates.post_id)


@router.message(ManageStates.post_id, F.text)
async def get_post_id(message: types.Message, state: FSMContext):
    """Get post ID."""
    try:
        post_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Invalid ID")
        return
    
    async with session() as s:
        post = await s.get(Post, post_id)
        if not post or post.owner_user_id != message.from_user.id:
            await message.answer("❌ Post not found or not yours")
            await state.clear()
            return
        
        tq = select(PostTarget).where(PostTarget.post_id == post_id)
        tres = await s.execute(tq)
        targets = tres.scalars().all()
    
    if not targets:
        await message.answer("❌ No messages for this post")
        await state.clear()
        return
    
    await state.update_data(post_id=post_id)
    await message.answer(f"Send new text (will edit in {len(targets)} channels):")
    await state.set_state(ManageStates.new_text)


@router.message(ManageStates.new_text, F.text)
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
        
        edited = 0
        for target in targets:
            try:
                await bot.edit_message_text(
                    chat_id=target.channel.chat_id,
                    message_id=target.message_id,
                    text=new_text
                )
                edited += 1
            except Exception as e:
                await message.answer(f"❌ {target.channel.title}: {str(e)[:40]}")
        
        await s.commit()
    
    await message.answer(f"✅ Edited in {edited} channel(s)")
    await state.clear()


@router.message(Command("delete"))
async def delete_post(message: types.Message, state: FSMContext):
    """Delete a post."""
    await message.answer("Send Post ID to delete (use /myposts to see IDs):")
    await state.set_state(ManageStates.post_id)


@router.message(ManageStates.post_id)
async def delete_posts(message: types.Message, state: FSMContext):
    """Delete post from all channels."""
    try:
        post_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Invalid ID")
        return
    
    async with session() as s:
        post = await s.get(Post, post_id)
        if not post or post.owner_user_id != message.from_user.id:
            await message.answer("❌ Post not found")
            await state.clear()
            return
        
        tq = select(PostTarget).where(PostTarget.post_id == post_id)
        tres = await s.execute(tq)
        targets = tres.scalars().all()
        
        bot = message.bot
        deleted = 0
        for target in targets:
            try:
                await bot.delete_message(
                    chat_id=target.channel.chat_id,
                    message_id=target.message_id
                )
                await s.delete(target)
                deleted += 1
            except Exception as e:
                await message.answer(f"❌ {target.channel.title}: {str(e)[:40]}")
        
        await s.commit()
    
    await message.answer(f"✅ Deleted from {deleted} channel(s)")
    await state.clear()

