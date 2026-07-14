"""Simple compose handler without complex FSM."""
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from datetime import datetime, timedelta
from sqlalchemy import select

from db import session
from models import Channel, Post, PostStatus, ContentType, PostTarget

router = Router()


class ComposeStates(StatesGroup):
    text = State()
    channels = State()
    confirm = State()


@router.message(Command("compose"))
async def start_compose(message: types.Message, state: FSMContext):
    """Start composing a post."""
    await message.answer("✏️ Send the message you want to post:")
    await state.set_state(ComposeStates.text)


@router.message(ComposeStates.text, F.text)
async def get_text(message: types.Message, state: FSMContext):
    """Get message text."""
    await state.update_data(text=message.text)
    
    async with session() as s:
        q = select(Channel)
        res = await s.execute(q)
        channels = res.scalars().all()
    
    if not channels:
        await message.answer("❌ No channels added yet. Use /add_channel first.")
        await state.clear()
        return
    
    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text=f"☐ {ch.title}", callback_data=f"sel_{ch.id}")]
            for ch in channels
        ] + [[types.InlineKeyboardButton(text="✅ Post", callback_data="post_now")]]
    )
    
    await message.answer("📍 Select channels to post to:", reply_markup=kb)
    await state.update_data(selected=[])
    await state.set_state(ComposeStates.channels)


@router.callback_query(ComposeStates.channels, F.data.startswith("sel_"))
async def toggle_channel(query: types.CallbackQuery, state: FSMContext):
    """Toggle channel selection."""
    ch_id = int(query.data.replace("sel_", ""))
    data = await state.get_data()
    selected = data.get("selected", [])
    
    if ch_id in selected:
        selected.remove(ch_id)
    else:
        selected.append(ch_id)
    
    await state.update_data(selected=selected)
    
    async with session() as s:
        q = select(Channel)
        res = await s.execute(q)
        channels = res.scalars().all()
    
    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(
                text=f"{'☑' if ch.id in selected else '☐'} {ch.title}",
                callback_data=f"sel_{ch.id}"
            )]
            for ch in channels
        ] + [[types.InlineKeyboardButton(text="✅ Post", callback_data="post_now")]]
    )
    
    await query.message.edit_reply_markup(reply_markup=kb)
    await query.answer()


@router.callback_query(ComposeStates.channels, F.data == "post_now")
async def post_message(query: types.CallbackQuery, state: FSMContext):
    """Post to selected channels."""
    data = await state.get_data()
    text = data.get("text")
    selected = data.get("selected", [])
    
    if not selected:
        await query.answer("Select at least one channel!", show_alert=True)
        return
    
    bot = query.bot
    success = 0
    failed = []
    
    async with session() as s:
        # Create post
        post = Post(
            owner_user_id=query.from_user.id,
            content_type=ContentType.TEXT,
            text=text,
            status=PostStatus.SENT
        )
        s.add(post)
        await s.flush()
        
        # Post to each channel
        for ch_id in selected:
            ch = await s.get(Channel, ch_id)
            if not ch:
                continue
            
            try:
                msg = await bot.send_message(chat_id=ch.chat_id, text=text)
                target = PostTarget(
                    post_id=post.id,
                    channel_id=ch_id,
                    message_id=msg.message_id
                )
                s.add(target)
                success += 1
            except Exception as e:
                failed.append(ch.title)
        
        await s.commit()
    
    result = f"✅ Posted to {success} channel(s)\nPost ID: {post.id}"
    if failed:
        result += f"\n❌ Failed: {', '.join(failed)}"
    
    await query.message.answer(result)
    await state.clear()
    await query.answer()

