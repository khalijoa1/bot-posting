"""Handler for managing (editing/deleting) posted messages."""
from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select

from db import session
from models import Post, PostTarget

router = Router()


class ManageState(StatesGroup):
    waiting_for_post_id = State()
    choosing_action = State()
    waiting_for_edit_text = State()


@router.message(Command("myposts"))
async def list_posts(message: types.Message):
    """List user's posts"""
    async with session() as s:
        q = select(Post).where(Post.owner_user_id == message.from_user.id)
        res = await s.execute(q)
        posts = res.scalars().all()

    if not posts:
        await message.reply("📭 You have no posts yet\n\nUse /compose to create one", parse_mode=None)
        return

    text = "📋 YOUR POSTS:\n\n"
    for p in posts:
        preview = (p.text or "[Photo]")[:50]
        async with session() as s:
            target_q = select(PostTarget).where(PostTarget.post_id == p.id)
            target_res = await s.execute(target_q)
            targets = target_res.scalars().all()
            ch_count = len(targets)

        text += f"ID: {p.id}\nText: {preview}...\nChannels: {ch_count}\n\n"

    await message.reply(text.strip(), parse_mode=None)


@router.message(Command("edit"))
async def edit_start(message: types.Message, state: FSMContext):
    """Start editing a post"""
    await message.reply(
        "✏️ EDIT MESSAGE\n\n"
        "Send the Post ID to edit:\n\n"
        "(Use /myposts to see IDs)",
        parse_mode=None
    )
    await state.set_state(ManageState.waiting_for_post_id)


@router.message(ManageState.waiting_for_post_id)
async def process_post_id(message: types.Message, state: FSMContext):
    """Process post ID and show action menu"""
    if not message.text:
        await message.reply("Send a post ID", parse_mode=None)
        return

    try:
        post_id = int(message.text.strip())
    except ValueError:
        await message.reply("❌ Invalid Post ID", parse_mode=None)
        return

    async with session() as s:
        post = await s.get(Post, post_id)
        if not post or post.owner_user_id != message.from_user.id:
            await message.reply("❌ Post not found or you don't own it", parse_mode=None)
            await state.clear()
            return

        await state.update_data(post_id=post_id)

        target_q = select(PostTarget).where(PostTarget.post_id == post_id)
        target_res = await s.execute(target_q)
        targets = target_res.scalars().all()

    if not targets:
        await message.reply("❌ No active messages for this post", parse_mode=None)
        await state.clear()
        return

    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text="✏️ Edit Text", callback_data="action_edit")],
            [types.InlineKeyboardButton(text="🗑️ Delete All", callback_data="action_delete")],
            [types.InlineKeyboardButton(text="❌ Cancel", callback_data="action_cancel")]
        ]
    )
    preview = (post.text or "[Photo]")[:50]
    await message.reply(
        f"📄 Post Preview:\n{preview}...\n\n"
        f"📍 In {len(targets)} channel(s)\n\n"
        f"Choose action:",
        reply_markup=kb,
        parse_mode=None
    )
    await state.set_state(ManageState.choosing_action)


@router.callback_query(ManageState.choosing_action)
async def process_action(query: types.CallbackQuery, state: FSMContext):
    """Process edit/delete action"""
    action = query.data.replace("action_", "")

    if action == "cancel":
        await query.message.reply("❌ Cancelled", parse_mode=None)
        await state.clear()
        await query.answer()
        return

    if action == "delete":
        data = await state.get_data()
        post_id = data.get("post_id")
        bot = query.bot

        async with session() as s:
            post = await s.get(Post, post_id)
            target_q = select(PostTarget).where(PostTarget.post_id == post_id)
            target_res = await s.execute(target_q)
            targets = target_res.scalars().all()

            deleted_count = 0
            failed = []
            for target in targets:
                try:
                    await bot.delete_message(
                        chat_id=target.channel.chat_id,
                        message_id=target.message_id
                    )
                    deleted_count += 1
                    await s.delete(target)
                except Exception as e:
                    failed.append(target.channel.title)

            await s.commit()

        result = f"✅ Deleted from {deleted_count} channel(s)"
        if failed:
            result += f"\n❌ Failed: {', '.join(failed)}"
        await query.message.reply(result, parse_mode=None)
        await state.clear()
        await query.answer()
        return

    if action == "edit":
        await query.message.reply(
            "✏️ EDIT\n\n"
            "Send the new message text:",
            parse_mode=None
        )
        await state.set_state(ManageState.waiting_for_edit_text)
        await query.answer()
        return

    await query.answer()


@router.message(ManageState.waiting_for_edit_text)
async def process_edit_text(message: types.Message, state: FSMContext):
    """Edit post in all channels"""
    if not message.text:
        await message.reply("Send text to edit", parse_mode=None)
        return

    data = await state.get_data()
    post_id = data.get("post_id")
    new_text = message.text.strip()
    bot = message.bot

    async with session() as s:
        post = await s.get(Post, post_id)
        post.text = new_text
        s.add(post)

        target_q = select(PostTarget).where(PostTarget.post_id == post_id)
        target_res = await s.execute(target_q)
        targets = target_res.scalars().all()

        edited_count = 0
        failed = []
        for target in targets:
            try:
                await bot.edit_message_text(
                    chat_id=target.channel.chat_id,
                    message_id=target.message_id,
                    text=new_text
                )
                edited_count += 1
            except Exception as e:
                failed.append(target.channel.title)

        await s.commit()

    result = f"✅ Edited in {edited_count} channel(s)"
    if failed:
        result += f"\n❌ Failed: {', '.join(failed)}"
    await message.reply(result, parse_mode=None)
    await state.clear()


@router.message(Command("delete"))
async def delete_start(message: types.Message, state: FSMContext):
    """Start deleting a post"""
    await message.reply(
        "🗑️ DELETE MESSAGE\n\n"
        "Send the Post ID to delete:\n\n"
        "(Use /myposts to see IDs)",
        parse_mode=None
    )
    await state.set_state(ManageState.waiting_for_post_id)

