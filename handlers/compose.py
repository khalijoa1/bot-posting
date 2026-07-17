"""Compose and post messages with scheduling and auto-delete."""
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from datetime import datetime, timedelta
from sqlalchemy import select

from db import session
from handlers.common import auto_delete_kb, main_menu_kb, parse_duration
from models import Channel, ContentType, Post, PostStatus, PostTarget

router = Router()


class ComposeState(StatesGroup):
    text = State()
    select_channels = State()
    schedule_choice = State()
    schedule_time = State()
    auto_delete = State()


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
        "Send the message text, or a photo/video (with an optional caption):",
        reply_markup=cancel_kb()
    )
    await state.set_state(ComposeState.text)


@router.message(ComposeState.text, F.text == "❌ Cancel")
async def cancel_compose(message: types.Message, state: FSMContext):
    """Cancel at text stage."""
    await state.clear()
    await message.answer("❌ Cancelled", reply_markup=main_menu_kb())


async def _ask_channels(message: types.Message, state: FSMContext, preview: str) -> None:
    """Show the channel-selection step. Shared by the text/photo/video entry
    points so all three content types go through the same rest of the flow.
    """
    async with session() as s:
        q = select(Channel)
        res = await s.execute(q)
        channels = res.scalars().all()

    if not channels:
        await message.answer(
            "❌ No channels added\n\n"
            "Add the bot as admin to a channel to register it automatically, "
            "or use /add_channel",
            reply_markup=main_menu_kb()
        )
        await state.clear()
        return

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
        f"{preview}\n\n"
        f"Tap channels (☐=off, ☑=on):",
        reply_markup=kb
    )
    await state.update_data(selected_channels=[])
    await state.set_state(ComposeState.select_channels)


@router.message(ComposeState.text, F.photo)
async def get_message_photo(message: types.Message, state: FSMContext):
    """Capture a photo (largest size) plus its optional caption."""
    file_id = message.photo[-1].file_id
    caption = (message.caption or "").strip()
    await state.update_data(content_type="photo", photo_file_id=file_id, video_file_id=None, text=caption)
    preview = f"Photo{': ' + caption[:80] if caption else ' (no caption)'}"
    await _ask_channels(message, state, preview)


@router.message(ComposeState.text, F.video)
async def get_message_video(message: types.Message, state: FSMContext):
    """Capture a video plus its optional caption."""
    file_id = message.video.file_id
    caption = (message.caption or "").strip()
    await state.update_data(content_type="video", video_file_id=file_id, photo_file_id=None, text=caption)
    preview = f"Video{': ' + caption[:80] if caption else ' (no caption)'}"
    await _ask_channels(message, state, preview)


@router.message(ComposeState.text, F.text)
async def get_message_text(message: types.Message, state: FSMContext):
    """Get message text."""
    text = message.text.strip()
    if len(text) < 1:
        await message.answer("❌ Message empty. Try again:")
        return

    await state.update_data(content_type="text", text=text, photo_file_id=None, video_file_id=None)
    await _ask_channels(message, state, f"Message: {text[:80]}...")


@router.callback_query(ComposeState.select_channels, F.data.startswith("ch_"))
async def handle_channels(query: types.CallbackQuery, state: FSMContext):
    """Handle channel toggling."""
    if query.data == "ch_cancel":
        await state.clear()
        await query.message.answer("❌ Cancelled", reply_markup=main_menu_kb())
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
        await query.message.answer("❌ Cancelled", reply_markup=main_menu_kb())
        await query.answer()
        return

    if query.data == "sched_now":
        await state.update_data(scheduled_time=None)
        await ask_auto_delete(query.message, state)
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
    """Handle schedule time selection from the preset buttons."""
    if query.data == "time_cancel":
        await state.clear()
        await query.message.answer("❌ Cancelled", reply_markup=main_menu_kb())
        await query.answer()
        return

    if query.data == "time_custom":
        await query.message.answer(
            "Send minutes to delay:\n\n"
            "Example: 30 (for 30 minutes)",
            reply_markup=cancel_kb()
        )
        # Stays in ComposeState.schedule_time; the free-text handler below
        # picks up the reply. (Previously nothing handled this text at all,
        # so "Custom" schedule silently did nothing.)
        await query.answer()
        return

    # Parse minutes from a preset button
    minutes = int(query.data.replace("time_", ""))
    scheduled_time = datetime.now() + timedelta(minutes=minutes)
    await state.update_data(scheduled_time=scheduled_time)
    await ask_auto_delete(query.message, state)
    await query.answer()


@router.message(ComposeState.schedule_time, F.text == "❌ Cancel")
async def cancel_custom_schedule(message: types.Message, state: FSMContext):
    """Cancel while typing a custom delay."""
    await state.clear()
    await message.answer("❌ Cancelled", reply_markup=main_menu_kb())


@router.message(ComposeState.schedule_time, F.text)
async def handle_custom_minutes(message: types.Message, state: FSMContext):
    """Parse the custom delay (in minutes) typed after tapping 'Custom'."""
    try:
        minutes = int(message.text.strip())
        if minutes <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Send a positive number of minutes, e.g. 45")
        return

    scheduled_time = datetime.now() + timedelta(minutes=minutes)
    await state.update_data(scheduled_time=scheduled_time)
    await ask_auto_delete(message, state)


async def ask_auto_delete(target: types.Message, state: FSMContext):
    """Ask whether to auto-delete the post after some time."""
    await target.answer(
        "🗑️ AUTO-DELETE?\n\n"
        "Delete this post automatically after a delay?\n"
        "(Timer starts when the post is actually sent)",
        reply_markup=auto_delete_kb("ad")
    )
    await state.set_state(ComposeState.auto_delete)


@router.callback_query(ComposeState.auto_delete, F.data.startswith("ad_"))
async def handle_auto_delete(query: types.CallbackQuery, state: FSMContext):
    """Handle a preset auto-delete choice, then finalize the post."""
    choice = query.data.replace("ad_", "")

    if choice == "cancel":
        await state.clear()
        await query.message.answer("❌ Cancelled", reply_markup=main_menu_kb())
        await query.answer()
        return

    if choice == "custom":
        await query.message.answer(
            "Send auto-delete duration, e.g. 30m, 2h, 1d, or 'no':",
            reply_markup=cancel_kb()
        )
        await query.answer()
        return

    auto_delete_seconds = None if choice == "no" else int(choice)
    await state.update_data(auto_delete_seconds=auto_delete_seconds)
    await query.answer()

    data = await state.get_data()
    if data.get("scheduled_time"):
        await post_scheduled(state, query.from_user.id, query.message.answer)
    else:
        await post_now(state, query.bot, query.from_user.id, query.message.answer)


@router.message(ComposeState.auto_delete, F.text == "❌ Cancel")
async def cancel_auto_delete(message: types.Message, state: FSMContext):
    """Cancel while typing a custom auto-delete duration."""
    await state.clear()
    await message.answer("❌ Cancelled", reply_markup=main_menu_kb())


@router.message(ComposeState.auto_delete, F.text)
async def handle_custom_auto_delete(message: types.Message, state: FSMContext):
    """Parse a custom duration like 30m, 2h, 1d, or 'no', then finalize."""
    try:
        seconds = parse_duration(message.text)
    except ValueError:
        await message.answer("❌ Format like 30m, 2h, 1d, or 'no'")
        return

    await state.update_data(auto_delete_seconds=seconds)
    await message.answer("✅ Got it...")

    data = await state.get_data()
    if data.get("scheduled_time"):
        await post_scheduled(state, message.from_user.id, message.answer)
    else:
        await post_now(state, message.bot, message.from_user.id, message.answer)


async def _send_to_channel(bot, chat_id: int, content_type: str, text: str | None,
                            photo_file_id: str | None, video_file_id: str | None):
    """Send a post to one channel, using the right Bot API method for its
    content type. Shared by the immediate-send and scheduled-send paths.
    """
    if content_type == "photo" and photo_file_id:
        return await bot.send_photo(chat_id=chat_id, photo=photo_file_id, caption=text or None)
    if content_type == "video" and video_file_id:
        return await bot.send_video(chat_id=chat_id, video=video_file_id, caption=text or None)
    return await bot.send_message(chat_id=chat_id, text=text or "")


async def post_now(state: FSMContext, bot, user_id: int, answer) -> None:
    """Post immediately."""
    data = await state.get_data()
    text = data.get("text")
    content_type = data.get("content_type", "text")
    photo_file_id = data.get("photo_file_id")
    video_file_id = data.get("video_file_id")
    selected_ids = data.get("selected_channels", [])
    auto_delete_seconds = data.get("auto_delete_seconds")

    success = 0
    failed = []

    async with session() as s:
        q = select(Channel).where(Channel.id.in_(selected_ids))
        res = await s.execute(q)
        channels = res.scalars().all()

        delete_at = datetime.now() + timedelta(seconds=auto_delete_seconds) if auto_delete_seconds else None

        post = Post(
            owner_user_id=user_id,
            content_type=ContentType(content_type),
            text=text,
            photo_file_id=photo_file_id,
            video_file_id=video_file_id,
            status=PostStatus.SENT,
            auto_delete_seconds=auto_delete_seconds,
            delete_at=delete_at,
        )
        s.add(post)
        await s.flush()

        for ch in channels:
            try:
                msg = await _send_to_channel(bot, ch.chat_id, content_type, text, photo_file_id, video_file_id)
                target = PostTarget(
                    post_id=post.id,
                    channel_id=ch.id,
                    message_id=msg.message_id,
                    sent_at=datetime.now(),
                )
                s.add(target)
                success += 1
            except Exception:
                failed.append(f"{ch.title}")

        await s.commit()
        post_id = post.id

    result = f"✅ POSTED!\n\n━━━━━━━━━━\n{success}/{len(channels)}\n━━━━━━━━━━\nID: {post_id}"
    if failed:
        result += f"\n\n❌ Failed:\n" + "\n".join(failed)

    await answer(result, reply_markup=main_menu_kb())
    await state.clear()


async def post_scheduled(state: FSMContext, user_id: int, answer) -> None:
    """Save the post for the background scheduler to send later.

    Creates PostTarget rows (message_id=None) up front so the scheduler knows
    which channels to send to - previously the selected channels were only
    kept in FSM memory and discarded, so scheduled posts had nowhere to go.
    """
    data = await state.get_data()
    text = data.get("text")
    content_type = data.get("content_type", "text")
    photo_file_id = data.get("photo_file_id")
    video_file_id = data.get("video_file_id")
    selected_ids = data.get("selected_channels", [])
    scheduled_time = data.get("scheduled_time")
    auto_delete_seconds = data.get("auto_delete_seconds")

    async with session() as s:
        q = select(Channel).where(Channel.id.in_(selected_ids))
        res = await s.execute(q)
        channels = res.scalars().all()

        post = Post(
            owner_user_id=user_id,
            content_type=ContentType(content_type),
            text=text,
            photo_file_id=photo_file_id,
            video_file_id=video_file_id,
            status=PostStatus.SCHEDULED,
            scheduled_time=scheduled_time,
            auto_delete_seconds=auto_delete_seconds,
        )
        s.add(post)
        await s.flush()

        for ch in channels:
            s.add(PostTarget(post_id=post.id, channel_id=ch.id))

        await s.commit()
        post_id = post.id

    time_str = scheduled_time.strftime("%Y-%m-%d %H:%M")
    result = (
        f"✅ SCHEDULED!\n\n"
        f"━━━━━━━━━━━━━━━\n"
        f"Time: {time_str}\n"
        f"Channels: {len(channels)}\n"
        f"ID: {post_id}\n"
        f"━━━━━━━━━━━━━━━"
    )

    await answer(result, reply_markup=main_menu_kb())
    await state.clear()


@router.message(lambda msg: msg.text == "✏️ Compose & Post")
async def compose_button(message: types.Message, state: FSMContext):
    """Compose from menu."""
    await compose_start(message, state)
