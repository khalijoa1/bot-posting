"""Compose and post messages."""
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select

from db import session
from models import Channel, Post, PostStatus, ContentType, PostTarget

router = Router()


class ComposeState(StatesGroup):
    text = State()
    select_channels = State()


@router.message(Command("compose"))
async def compose_start(message: types.Message, state: FSMContext):
    """Start compose workflow."""
    await state.clear()
    kb = types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text="🔙 Cancel")]],
        resize_keyboard=True
    )
    await message.answer(
        "━━━━━━━━━━━━━━━━━━\n"
        "✏️ COMPOSE MESSAGE\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Send the message text you want to post:",
        reply_markup=kb
    )
    await state.set_state(ComposeState.text)


@router.message(ComposeState.text, F.text == "🔙 Cancel")
async def cancel_compose(message: types.Message, state: FSMContext):
    """Cancel composition."""
    await state.clear()
    await message.answer("❌ Cancelled", reply_markup=types.ReplyKeyboardRemove())


@router.message(ComposeState.text, F.text)
async def get_message_text(message: types.Message, state: FSMContext):
    """Get message text and show channel selection."""
    text = message.text.strip()
    if len(text) < 1:
        await message.answer("❌ Message cannot be empty")
        return
    
    await state.update_data(text=text)
    
    # Get all channels
    async with session() as s:
        q = select(Channel)
        res = await s.execute(q)
        channels = res.scalars().all()
    
    if not channels:
        await message.answer(
            "❌ No channels added\n\n"
            "Use /add_channel to add channels first",
            reply_markup=types.ReplyKeyboardRemove()
        )
        await state.clear()
        return
    
    # Show channel selection
    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(
                text=f"☐ {ch.title}",
                callback_data=f"ch_{ch.id}"
            )]
            for ch in channels
        ] + [
            [types.InlineKeyboardButton(text="✅ POST NOW", callback_data="post_confirm")],
            [types.InlineKeyboardButton(text="❌ CANCEL", callback_data="post_cancel")]
        ]
    )
    
    await message.answer(
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📍 SELECT CHANNELS\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"Message preview: {text[:100]}...\n\n"
        f"Tap channels to select (☐ = unselected):\n\n"
        f"Then tap ✅ POST NOW",
        reply_markup=kb
    )
    await state.update_data(selected_channels=[])
    await state.set_state(ComposeState.select_channels)


@router.callback_query(ComposeState.select_channels, F.data.startswith("ch_"))
async def toggle_channel(query: types.CallbackQuery, state: FSMContext):
    """Toggle channel selection."""
    ch_id = int(query.data.replace("ch_", ""))
    data = await state.get_data()
    selected = data.get("selected_channels", [])
    
    if ch_id in selected:
        selected.remove(ch_id)
    else:
        selected.append(ch_id)
    
    await state.update_data(selected_channels=selected)
    
    # Refresh keyboard with updated status
    async with session() as s:
        q = select(Channel)
        res = await s.execute(q)
        channels = res.scalars().all()
    
    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(
                text=f"{'☑' if ch.id in selected else '☐'} {ch.title}",
                callback_data=f"ch_{ch.id}"
            )]
            for ch in channels
        ] + [
            [types.InlineKeyboardButton(text="✅ POST NOW", callback_data="post_confirm")],
            [types.InlineKeyboardButton(text="❌ CANCEL", callback_data="post_cancel")]
        ]
    )
    
    await query.message.edit_reply_markup(reply_markup=kb)
    await query.answer()


@router.callback_query(ComposeState.select_channels, F.data == "post_cancel")
async def cancel_post(query: types.CallbackQuery, state: FSMContext):
    """Cancel post."""
    await state.clear()
    await query.message.answer("❌ Cancelled")
    await query.answer()


@router.callback_query(ComposeState.select_channels, F.data == "post_confirm")
async def confirm_post(query: types.CallbackQuery, state: FSMContext):
    """Confirm and post message."""
    data = await state.get_data()
    text = data.get("text")
    selected_ids = data.get("selected_channels", [])
    
    if not selected_ids:
        await query.answer("Select at least one channel!", show_alert=True)
        return
    
    bot = query.bot
    success_count = 0
    failed_channels = []
    
    async with session() as s:
        # Get selected channels
        q = select(Channel).where(Channel.id.in_(selected_ids))
        res = await s.execute(q)
        channels = res.scalars().all()
        
        # Create post record
        post = Post(
            owner_user_id=query.from_user.id,
            content_type=ContentType.TEXT,
            text=text,
            status=PostStatus.SENT
        )
        s.add(post)
        await s.flush()
        
        # Post to each channel
        for ch in channels:
            try:
                msg = await bot.send_message(chat_id=ch.chat_id, text=text)
                target = PostTarget(
                    post_id=post.id,
                    channel_id=ch.id,
                    message_id=msg.message_id
                )
                s.add(target)
                success_count += 1
            except Exception as e:
                failed_channels.append(f"{ch.title} ({str(e)[:30]})")
        
        await s.commit()
    
    # Show result
    result = (
        f"✅ POSTED!\n\n"
        f"━━━━━━━━━━━━\n"
        f"Channels: {success_count}/{len(channels)}\n"
        f"Post ID: {post.id}\n"
        f"━━━━━━━━━━━━"
    )
    
    if failed_channels:
        result += f"\n\n❌ Failed:\n" + "\n".join([f"  • {c}" for c in failed_channels])
    
    await query.message.answer(result)
    await state.clear()
    await query.answer()


@router.message(lambda msg: msg.text == "✏️ Compose Post")
async def compose_button(message: types.Message, state: FSMContext):
    """Compose from menu button."""
    await compose_start(message, state)

