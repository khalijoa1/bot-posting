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
        "✏️ COMPOSE MESSAGE\n\n"
        "Send the message you want to post (text only for now):\n\n"
        "(You'll select channels next)",
        parse_mode=None
    )
    await state.set_state(ComposeState.waiting_for_content)


@router.message(ComposeState.waiting_for_content)
async def process_content(message: types.Message, state: FSMContext):
    """Process the message content"""
    if not message.text:
        await message.reply("⚠️ Please send text", parse_mode=None)
        return

    content_text = message.text
    await state.update_data(content_text=content_text)

    # Show channels to select
    async with session() as s:
        q = select(Channel)
        res = await s.execute(q)
        channels = res.scalars().all()

    if not channels:
        await message.reply(
            "❌ No channels available\n\n"
            "Add channels first with /add_channel",
            parse_mode=None
        )
        await state.clear()
        return

    # Create inline keyboard for channel selection
    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text=ch.title, callback_data=f"ch_{ch.id}")]
            for ch in channels
        ] + [[types.InlineKeyboardButton(text="✅ POST", callback_data="ch_done")]]
    )

    await message.reply(
        "📍 SELECT CHANNELS\n\n"
        "Tap channels to add to post:\n\n"
        "(Green checkmark = selected)",
        reply_markup=kb,
        parse_mode=None
    )
    await state.update_data(selected_channels=[])
    await state.set_state(ComposeState.waiting_for_channel_selection)


@router.callback_query(ComposeState.waiting_for_channel_selection, F.data.startswith("ch_"))
async def handle_channel_selection(query: types.CallbackQuery, state: FSMContext):
    """Handle channel selection"""
    if query.data == "ch_done":
        data = await state.get_data()
        selected = data.get("selected_channels", [])

        if not selected:
            await query.answer("❌ Select at least one channel!", show_alert=True)
            return

        # Show confirmation
        async with session() as s:
            q = select(Channel).where(Channel.id.in_(selected))
            res = await s.execute(q)
            selected_ch = res.scalars().all()

        ch_names = "\n".join([f"  • {ch.title}" for ch in selected_ch])
        content = data.get("content_text", "")
        preview = content[:100] + ("..." if len(content) > 100 else "")

        kb = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text="✅ POST NOW", callback_data="confirm_post")],
                [types.InlineKeyboardButton(text="❌ CANCEL", callback_data="cancel_post")]
            ]
        )

        await query.message.reply(
            f"📤 CONFIRM POST\n\n"
            f"Message:\n{preview}\n\n"
            f"Will post to:\n{ch_names}",
            reply_markup=kb,
            parse_mode=None
        )
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

    # Rebuild keyboard with checkmarks
    async with session() as s:
        q = select(Channel)
        res = await s.execute(q)
        channels = res.scalars().all()

    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=f"{'✅ ' if ch.id in selected else '⬜ '}{ch.title}",
                    callback_data=f"ch_{ch.id}"
                )
            ]
            for ch in channels
        ] + [[types.InlineKeyboardButton(text="✅ POST", callback_data="ch_done")]]
    )
    await query.message.edit_reply_markup(reply_markup=kb)
    await query.answer()


@router.callback_query(ComposeState.confirming_post)
async def confirm_post(query: types.CallbackQuery, state: FSMContext):
    """Confirm and post the message"""
    if query.data == "cancel_post":
        await query.message.reply("❌ Cancelled", parse_mode=None)
        await state.clear()
        await query.answer()
        return

    if query.data != "confirm_post":
        await query.answer()
        return

    data = await state.get_data()
    bot = query.bot
    content_text = data.get("content_text", "")
    channel_ids = data.get("selected_channels", [])

    # Post to channels
    success_count = 0
    failed_channels = []

    async with session() as s:
        # Create post record
        post = Post(
            owner_user_id=query.from_user.id,
            content_type=ContentType.TEXT,
            text=content_text,
            status=PostStatus.SENT
        )
        s.add(post)
        await s.flush()

        # Post to all selected channels
        for ch_id in channel_ids:
            ch = await s.get(Channel, ch_id)
            if not ch:
                failed_channels.append(f"Channel {ch_id}")
                continue

            try:
                # Send message to channel
                sent_msg = await bot.send_message(
                    chat_id=ch.chat_id,
                    text=content_text
                )

                # Record the sent message
                target = PostTarget(
                    post_id=post.id,
                    channel_id=ch_id,
                    message_id=sent_msg.message_id
                )
                s.add(target)
                success_count += 1
            except Exception as e:
                failed_channels.append(f"{ch.title} ({str(e)[:30]})")

        await s.commit()

    # Send result message
    result_text = f"✅ POSTED!\n\nChannels: {success_count}/{len(channel_ids)}\nPost ID: {post.id}"
    if failed_channels:
        result_text += f"\n\n❌ Failed:\n" + "\n".join([f"  • {c}" for c in failed_channels])

    await query.message.reply(result_text, parse_mode=None)
    await state.clear()
    await query.answer()

