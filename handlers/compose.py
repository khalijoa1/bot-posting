"""Handler for composing and posting messages to multiple channels."""
import asyncio
from datetime import datetime, timedelta
from aiogram import Bot, F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select

from db import session
from models import Channel, Post, PostStatus, ContentType, PostTarget

router = Router()


class ComposeState(StatesGroup):
    waiting_for_content = State()
    waiting_for_link_replacement = State()
    waiting_for_schedule = State()
    waiting_for_duration = State()
    waiting_for_channels = State()
    confirming = State()


def create_channel_kb(channels, selected):
    """Create inline keyboard for channel selection"""
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(
                text=f"{'✅' if ch.id in selected else '⬜'} {ch.title}",
                callback_data=f"comp_ch_{ch.id}"
            )]
            for ch in channels
        ] + [[types.InlineKeyboardButton(text="✅ NEXT", callback_data="comp_next")]]
    )


@router.message(Command("compose"))
async def compose_start(message: types.Message, state: FSMContext):
    """Start composing"""
    kb = types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="Back")],
        ],
        resize_keyboard=True
    )
    await message.reply(
        "✏️ COMPOSE MESSAGE\n\n"
        "Send your message:",
        reply_markup=kb,
        parse_mode=None
    )
    await state.set_state(ComposeState.waiting_for_content)


@router.message(ComposeState.waiting_for_content)
async def handle_content(message: types.Message, state: FSMContext):
    """Handle message content"""
    if message.text == "Back":
        await state.clear()
        await message.reply("Cancelled", parse_mode=None)
        return

    if not message.text:
        await message.reply("Send text", parse_mode=None)
        return

    await state.update_data(content=message.text)
    
    kb = types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="Skip")],
            [types.KeyboardButton(text="Back")],
        ],
        resize_keyboard=True
    )
    await message.reply(
        "🔗 Replace links? (format: old_link|new_link each on new line)\n\n"
        "Or tap Skip:",
        reply_markup=kb,
        parse_mode=None
    )
    await state.set_state(ComposeState.waiting_for_link_replacement)


@router.message(ComposeState.waiting_for_link_replacement)
async def handle_links(message: types.Message, state: FSMContext):
    """Handle link replacement"""
    if message.text == "Back":
        await state.set_state(ComposeState.waiting_for_content)
        await message.reply("Go back", parse_mode=None)
        return

    if message.text != "Skip":
        links = {}
        for line in message.text.split("\n"):
            if "|" in line:
                old, new = line.split("|", 1)
                links[old.strip()] = new.strip()
        await state.update_data(link_replacements=links)
    else:
        await state.update_data(link_replacements={})

    kb = types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="Post Now")],
            [types.KeyboardButton(text="Schedule")],
            [types.KeyboardButton(text="Back")],
        ],
        resize_keyboard=True
    )
    await message.reply(
        "⏰ When to post?",
        reply_markup=kb,
        parse_mode=None
    )
    await state.set_state(ComposeState.waiting_for_schedule)


@router.message(ComposeState.waiting_for_schedule)
async def handle_schedule(message: types.Message, state: FSMContext):
    """Handle scheduling"""
    if message.text == "Back":
        await state.set_state(ComposeState.waiting_for_link_replacement)
        return

    if message.text == "Post Now":
        await state.update_data(schedule_time=None, duration=None)
    elif message.text == "Schedule":
        kb = types.ReplyKeyboardMarkup(
            keyboard=[
                [types.KeyboardButton(text="1 hour")],
                [types.KeyboardButton(text="2 hours")],
                [types.KeyboardButton(text="6 hours")],
                [types.KeyboardButton(text="24 hours")],
                [types.KeyboardButton(text="Custom (minutes)")],
                [types.KeyboardButton(text="Back")],
            ],
            resize_keyboard=True
        )
        await message.reply("How long from now?", reply_markup=kb, parse_mode=None)
        await state.set_state(ComposeState.waiting_for_duration)
        return
    else:
        await message.reply("Choose an option", parse_mode=None)
        return

    # Show channels
    async with session() as s:
        q = select(Channel)
        res = await s.execute(q)
        channels = res.scalars().all()

    if not channels:
        await message.reply("❌ No channels. Add with /add_channel", parse_mode=None)
        await state.clear()
        return

    await state.update_data(selected_channels=[])
    await message.reply(
        "📍 SELECT CHANNELS\n\n"
        "Tap to toggle:",
        reply_markup=create_channel_kb(channels, []),
        parse_mode=None
    )
    await state.set_state(ComposeState.waiting_for_channels)


@router.message(ComposeState.waiting_for_duration)
async def handle_duration(message: types.Message, state: FSMContext):
    """Handle duration"""
    if message.text == "Back":
        await state.set_state(ComposeState.waiting_for_schedule)
        return

    duration_map = {
        "1 hour": 60,
        "2 hours": 120,
        "6 hours": 360,
        "24 hours": 1440,
    }

    if message.text in duration_map:
        minutes = duration_map[message.text]
        schedule_time = datetime.now() + timedelta(minutes=minutes)
        await state.update_data(schedule_time=schedule_time, duration=minutes)
    elif message.text == "Custom (minutes)":
        await message.reply("Send minutes:", parse_mode=None)
        return
    else:
        try:
            minutes = int(message.text)
            schedule_time = datetime.now() + timedelta(minutes=minutes)
            await state.update_data(schedule_time=schedule_time, duration=minutes)
        except ValueError:
            await message.reply("Invalid. Choose option or send number", parse_mode=None)
            return

    # Show channels
    async with session() as s:
        q = select(Channel)
        res = await s.execute(q)
        channels = res.scalars().all()

    if not channels:
        await message.reply("❌ No channels", parse_mode=None)
        await state.clear()
        return

    await state.update_data(selected_channels=[])
    await message.reply(
        "📍 SELECT CHANNELS:",
        reply_markup=create_channel_kb(channels, []),
        parse_mode=None
    )
    await state.set_state(ComposeState.waiting_for_channels)


@router.callback_query(ComposeState.waiting_for_channels, F.data.startswith("comp_"))
async def handle_channels(query: types.CallbackQuery, state: FSMContext):
    """Handle channel selection"""
    if query.data == "comp_next":
        data = await state.get_data()
        if not data.get("selected_channels"):
            await query.answer("Select channels!", show_alert=True)
            return

        await confirm_and_post(query.message, state, query.bot)
        await state.clear()
        await query.answer()
        return

    # Toggle channel
    ch_id = int(query.data.replace("comp_ch_", ""))
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

    await query.message.edit_reply_markup(reply_markup=create_channel_kb(channels, selected))
    await query.answer()


async def confirm_and_post(message: types.Message, state: FSMContext, bot: Bot):
    """Confirm and send post"""
    data = await state.get_data()
    content = data.get("content")
    channels_ids = data.get("selected_channels", [])
    schedule_time = data.get("schedule_time")
    links = data.get("link_replacements", {})

    # Replace links
    final_content = content
    for old, new in links.items():
        final_content = final_content.replace(old, new)

    async with session() as s:
        q = select(Channel).where(Channel.id.in_(channels_ids))
        res = await s.execute(q)
        channels = res.scalars().all()

        # Create post
        post = Post(
            owner_user_id=message.chat.id,
            content_type=ContentType.TEXT,
            text=final_content,
            status=PostStatus.SENT if not schedule_time else PostStatus.SCHEDULED,
            scheduled_time=schedule_time
        )
        s.add(post)
        await s.flush()

        # Post now or schedule
        if not schedule_time:
            success = 0
            for ch in channels:
                try:
                    sent = await bot.send_message(chat_id=ch.chat_id, text=final_content)
                    target = PostTarget(post_id=post.id, channel_id=ch.id, message_id=sent.message_id)
                    s.add(target)
                    success += 1
                except Exception as e:
                    await message.reply(f"❌ {ch.title}: {str(e)[:50]}", parse_mode=None)

            await s.commit()
            await message.reply(
                f"✅ POSTED!\n\nChannels: {success}/{len(channels)}\nPost ID: {post.id}",
                parse_mode=None
            )
        else:
            await s.commit()
            await message.reply(
                f"✅ SCHEDULED!\n\nTime: {schedule_time.strftime('%Y-%m-%d %H:%M')}\n"
                f"Channels: {len(channels)}\nPost ID: {post.id}",
                parse_mode=None
            )

