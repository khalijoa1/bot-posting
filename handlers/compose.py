"""Handler for composing and posting messages to multiple channels."""
from aiogram import F, Router, types
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select

from db import session
from models import Channel, Post, PostStatus, ContentType, PostTarget

router = Router()


class ComposeState(StatesGroup):
    waiting_for_content = State()
    waiting_for_channel_selection = State()
    confirming_post = State()


@router.message(Command("compose"))
async def compose_start(message: types.Message, state: FSMContext):
    """Start composing a message"""
    await message.reply(
        "📝 Compose Mode\n\n"
        "Send the message you want to post (text, photo, or video):",
        parse_mode=None
    )
    await state.set_state(ComposeState.waiting_for_content)


@router.message(ComposeState.waiting_for_content)
async def process_content(message: types.Message, state: FSMContext):
    """Process the message content"""
    if message.text:
        content_type = ContentType.TEXT
        content_data = message.text
    elif message.photo:
        content_type = ContentType.PHOTO
        content_data = message.photo[-1].file_id
    else:
        await message.reply("Only text and photos are supported.", parse_mode=None)
        return

    await state.update_data(
        content_type=content_type,
        content_data=content_data,
        text=message.text if message.text else None,
        photo_file_id=message.photo[-1].file_id if message.photo else None
    )

    # Show channels to select
    async with session() as s:
        q = select(Channel)
        res = await s.execute(q)
        channels = res.scalars().all()

    if not channels:
        await message.reply("❌ No channels configured. Add channels first with /add_channel", parse_mode=None)
        await state.clear()
        return

    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text=ch.title, callback_data=f"ch_{ch.id}")]
            for ch in channels
        ] + [[types.InlineKeyboardButton(text="✓ Done", callback_data="ch_done")]]
    )
    await message.reply("Select channels to post to (tap to toggle):", reply_markup=kb, parse_mode=None)
    await state.update_data(selected_channels=[])
    await state.set_state(ComposeState.waiting_for_channel_selection)


@router.callback_query(ComposeState.waiting_for_channel_selection, F.data.startswith("ch_"))
async def handle_channel_selection(query: types.CallbackQuery, state: FSMContext):
    """Handle channel selection"""
    if query.data == "ch_done":
        data = await state.get_data()
        if not data.get("selected_channels"):
            await query.answer("Select at least one channel!", show_alert=True)
            return

        # Show confirmation
        async with session() as s:
            q = select(Channel).where(Channel.id.in_(data.get("selected_channels")))
            res = await s.execute(q)
            selected_ch = res.scalars().all()

        ch_names = ", ".join([ch.title for ch in selected_ch])
        preview = (
            f"📝 Preview:\n\n{data.get('text', '[Photo]')}\n\n"
            f"📤 Will post to: {ch_names}"
        )
        kb = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text="✓ Post", callback_data="confirm_post")],
                [types.InlineKeyboardButton(text="✗ Cancel", callback_data="cancel_post")]
            ]
        )
        await query.message.reply(preview, reply_markup=kb, parse_mode=None)
        await state.set_state(ComposeState.confirming_post)
        await query.answer()
        return

    # Toggle channel selection
    ch_id = int(query.data.replace("ch_", ""))
    data = await state.get_data()
    selected = data.get("selected_channels", [])

    if ch_id in selected:
        selected.remove(ch_id)
    else:
        selected.append(ch_id)

    await state.update_data(selected_channels=selected)

    # Rebuild keyboard
    async with session() as s:
        q = select(Channel)
        res = await s.execute(q)
        channels = res.scalars().all()

    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=f"{'✓ ' if ch.id in selected else ''}{ch.title}",
                    callback_data=f"ch_{ch.id}"
                )
            ]
            for ch in channels
        ] + [[types.InlineKeyboardButton(text="✓ Done", callback_data="ch_done")]]
    )
    await query.message.edit_reply_markup(reply_markup=kb)
    await query.answer()


@router.callback_query(ComposeState.confirming_post)
async def confirm_post(query: types.CallbackQuery, state: FSMContext):
    """Confirm and post the message"""
    if query.data == "cancel_post":
        await query.message.reply("Cancelled.", parse_mode=None)
        await state.clear()
        await query.answer()
        return

    if query.data != "confirm_post":
        await query.answer()
        return

    data = await state.get_data()
    bot = query.bot

    async with session() as s:
        # Create post record
        post = Post(
            owner_user_id=query.from_user.id,
            content_type=data.get("content_type"),
            text=data.get("text"),
            photo_file_id=data.get("photo_file_id"),
            status=PostStatus.SENT
        )
        s.add(post)
        await s.flush()

        # Post to all selected channels
        channel_ids = data.get("selected_channels", [])
        for ch_id in channel_ids:
            ch = await s.get(Channel, ch_id)
            if not ch:
                continue

            try:
                if data.get("content_type") == ContentType.TEXT:
                    sent_msg = await bot.send_message(
                        chat_id=ch.chat_id,
                        text=data.get("text")
                    )
                elif data.get("content_type") == ContentType.PHOTO:
                    sent_msg = await bot.send_photo(
                        chat_id=ch.chat_id,
                        photo=data.get("photo_file_id")
                    )

                # Record the sent message
                target = PostTarget(
                    post_id=post.id,
                    channel_id=ch_id,
                    message_id=sent_msg.message_id
                )
                s.add(target)
            except Exception as e:
                await query.message.reply(f"❌ Failed to post to {ch.title}: {str(e)}", parse_mode=None)

        await s.commit()

    await query.message.reply(f"✓ Posted to {len(channel_ids)} channel(s)!", parse_mode=None)
    await state.clear()
    await query.answer()

