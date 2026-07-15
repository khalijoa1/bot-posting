"""Compose and post messages with scheduling."""
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from datetime import datetime, timedelta
from sqlalchemy import select

from db import session
from models import Channel, Post, PostStatus, ContentType, PostTarget, Category

router = Router()


class ComposeState(StatesGroup):
    text = State()
    select_channels = State()
    schedule_choice = State()
    schedule_time = State()


def cancel_kb():
    """Get cancel keyboard."""
    return types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text="❌ Cancel")]],
        resize_keyboard=True
    )


@router.message(Command("compose"))
async def compose_start(message: types.Message, state: FSMContext):
    """Start compose workflow."""
    await state.clear()
    await message.answer(
        "━━━━━━━━━━━━━━━━━━\n"
        "✏️ COMPOSE MESSAGE\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Send the message text:",
        reply_markup=cancel_kb()
    )
    await state.set_state(ComposeState.text)


@router.message(ComposeState.text, F.text == "❌ Cancel")
async def cancel_compose(message: types.Message, state: FSMContext):
    """Cancel at text stage."""
    await state.clear()
    await message.answer("❌ Cancelled", reply_markup=types.ReplyKeyboardRemove())
    kb = types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="📨 MESSAGING")],
            [types.KeyboardButton(text="🔙 Back")],
        ],
        resize_keyboard=True
    )
    await message.answer("Choose action:", reply_markup=kb)


@router.message(ComposeState.text, F.text)
async def get_message_text(message: types.Message, state: FSMContext):
    """Get message text."""
    text = message.text.strip()
    if len(text) < 1:
        await message.answer("❌ Message empty. Try again:")
        return
    
    await state.update_data(text=text)
    
    # Get channels
    async with session() as s:
        q = select(Channel)
        res = await s.execute(q)
        channels = res.scalars().all()
    
    if not channels:
        await message.answer(
            "❌ No channels added\n\n"
            "Use /add_channel first",
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
            [types.InlineKeyboardButton(text="✅ Next", callback_data="ch_next")],
            [types.InlineKeyboardButton(text="❌ Cancel", callback_data="ch_cancel")]
        ]
    )
    
    await message.answer(
        f"📍 SELECT CHANNELS:\n\n"
        f"Message: {text[:80]}...\n\n"
        f"Tap channels (☐=off, ☑=on):",
        reply_markup=kb
    )
    await state.update_data(selected_channels=[])
    await state.set_state(ComposeState.select_channels)


@router.callback_query(ComposeState.select_channels, F.data.startswith("ch_"))
async def handle_channels(query: types.CallbackQuery, state: FSMContext):
    """Handle channel toggling."""
    if query.data == "ch_cancel":
        await state.clear()
        await query.message.answer("❌ Cancelled")
        await query.answer()
        return
    
    if query.data == "ch_next":
        data = await state.get_data()
        if not data.get("selected_channels"):
            await query.answer("Select at least 1 channel!", show_alert=True)
            return
        
        # Ask about scheduling
        kb = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text="📤 Post Now", callback_data="sched_now")],
                [types.InlineKeyboardButton(text="⏰ Schedule Later", callback_data="sched_later")],
                [types.InlineKeyboardButton(text="❌ Cancel", callback_data="sched_cancel")]
            ]
        )
        
        await query.message.answer(
            "⏰ WHEN TO POST?",
            reply_markup=kb
        )
        await state.set_state(ComposeState.schedule_choice)
        await query.answer()
        return
    
    # Toggle channel
    ch_id = int(query.data.replace("ch_", ""))
    data = await state.get_data()
    selected = data.get("selected_channels", [])
    
    if ch_id in selected:
        selected.remove(ch_id)
    else:
        selected.append(ch_id)
    
    await state.update_data(selected_channels=selected)
    
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
            [types.InlineKeyboardButton(text="✅ Next", callback_data="ch_next")],
            [types.InlineKeyboardButton(text="❌ Cancel", callback_data="ch_cancel")]
        ]
    )
    
    await query.message.edit_reply_markup(reply_markup=kb)
    await query.answer()


@router.callback_query(ComposeState.schedule_choice)
async def handle_schedule(query: types.CallbackQuery, state: FSMContext):
    """Handle scheduling choice."""
    if query.data == "sched_cancel":
        await state.clear()
        await query.message.answer("❌ Cancelled")
        await query.answer()
        return
    
    if query.data == "sched_now":
        await state.update_data(scheduled_time=None)
        await post_now(query, state)
        await query.answer()
        return
    
    if query.data == "sched_later":
        kb = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text="1 Hour", callback_data="time_60")],
                [types.InlineKeyboardButton(text="2 Hours", callback_data="time_120")],
                [types.InlineKeyboardButton(text="6 Hours", callback_data="time_360")],
                [types.InlineKeyboardButton(text="24 Hours", callback_data="time_1440")],
                [types.InlineKeyboardButton(text="Custom", callback_data="time_custom")],
                [types.InlineKeyboardButton(text="❌ Cancel", callback_data="time_cancel")]
            ]
        )
        
        await query.message.answer(
            "⏰ SELECT SCHEDULE:",
            reply_markup=kb
        )
        await state.set_state(ComposeState.schedule_time)
        await query.answer()


@router.callback_query(ComposeState.schedule_time)
async def handle_schedule_time(query: types.CallbackQuery, state: FSMContext):
    """Handle schedule time selection."""
    if query.data == "time_cancel":
        await state.clear()
        await query.message.answer("❌ Cancelled")
        await query.answer()
        return
    
    if query.data == "time_custom":
        kb = types.ReplyKeyboardMarkup(
            keyboard=[[types.KeyboardButton(text="❌ Cancel")]],
            resize_keyboard=True
        )
        await query.message.answer(
            "Send minutes to delay:\n\n"
            "Example: 30 (for 30 minutes)",
            reply_markup=kb
        )
        await state.set_state(ComposeState.schedule_time)
        await query.answer()
        return
    
    # Parse minutes
    minutes = int(query.data.replace("time_", ""))
    scheduled_time = datetime.now() + timedelta(minutes=minutes)
    await state.update_data(scheduled_time=scheduled_time)
    
    await post_scheduled(query, state)
    await query.answer()


async def post_now(query: types.CallbackQuery, state: FSMContext):
    """Post immediately."""
    data = await state.get_data()
    text = data.get("text")
    selected_ids = data.get("selected_channels", [])
    
    bot = query.bot
    success = 0
    failed = []
    
    async with session() as s:
        q = select(Channel).where(Channel.id.in_(selected_ids))
        res = await s.execute(q)
        channels = res.scalars().all()
        
        post = Post(
            owner_user_id=query.from_user.id,
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
                failed.append(f"{ch.title}")
        
        await s.commit()
    
    result = f"✅ POSTED!\n\n━━━━━━━━━━\n{success}/{len(channels)}\n━━━━━━━━━━\nID: {post.id}"
    if failed:
        result += f"\n\n❌ Failed:\n" + "\n".join(failed)
    
    await query.message.answer(result)
    await state.clear()


async def post_scheduled(query: types.CallbackQuery, state: FSMContext):
    """Schedule post."""
    data = await state.get_data()
    text = data.get("text")
    selected_ids = data.get("selected_channels", [])
    scheduled_time = data.get("scheduled_time")
    
    async with session() as s:
        q = select(Channel).where(Channel.id.in_(selected_ids))
        res = await s.execute(q)
        channels = res.scalars().all()
        
        post = Post(
            owner_user_id=query.from_user.id,
            content_type=ContentType.TEXT,
            text=text,
            status=PostStatus.SCHEDULED,
            scheduled_time=scheduled_time
        )
        s.add(post)
        await s.commit()
    
    time_str = scheduled_time.strftime("%Y-%m-%d %H:%M")
    result = (
        f"✅ SCHEDULED!\n\n"
        f"━━━━━━━━━━━━━━━\n"
        f"Time: {time_str}\n"
        f"Channels: {len(channels)}\n"
        f"ID: {post.id}\n"
        f"━━━━━━━━━━━━━━━"
    )
    
    await query.message.answer(result)
    await state.clear()


@router.message(lambda msg: msg.text == "✏️ Compose Post")
async def compose_button(message: types.Message, state: FSMContext):
    """Compose from menu."""
    await compose_start(message, state)

