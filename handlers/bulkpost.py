"""Bulk-post: forward several separate messages to the bot, then push every
one of them to chosen channels in a single pass.

Different from /compose, which authors ONE new post (optionally an album of
photos/videos that were all sent together as one Telegram media group) -
this is for re-sharing several already-existing, UNRELATED messages you
forward in one sitting (e.g. a batch of announcements) without repeating the
whole compose flow for each one.
"""
import asyncio
from datetime import datetime

from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select

from db import session
from handlers.common import main_menu_kb
from models import Channel, ContentType, Post, PostStatus, PostTarget

router = Router()


class BulkPostState(StatesGroup):
    collecting = State()
    select_channels = State()


def _done_kb() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text="✅ Done - choose channels", callback_data="bp_done")],
            [types.InlineKeyboardButton(text="❌ Cancel", callback_data="bp_cancel")],
        ]
    )


@router.message(Command("bulkpost"))
async def bulkpost_start(message: types.Message, state: FSMContext):
    """Start the bulk-post workflow."""
    await state.clear()
    await state.update_data(items=[])
    await message.answer(
        "━━━━━━━━━━━━━━━━━━\n"
        "📤 BULK POST\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Forward me the messages you want to post - one at a time or several "
        "in a row (text, photos, or videos). I'll post every one of them to "
        "the channels you pick, once you're done.\n\n"
        "Tap ✅ Done when you've forwarded everything:",
        reply_markup=_done_kb()
    )
    await state.set_state(BulkPostState.collecting)


@router.message(BulkPostState.collecting, F.text | F.photo | F.video)
async def bulkpost_collect(message: types.Message, state: FSMContext):
    """Buffer one forwarded (or typed) message as a pending post item."""
    if message.photo:
        item = {"type": "photo", "file_id": message.photo[-1].file_id, "text": (message.caption or "").strip()}
    elif message.video:
        item = {"type": "video", "file_id": message.video.file_id, "text": (message.caption or "").strip()}
    else:
        item = {"type": "text", "file_id": None, "text": (message.text or "").strip()}

    if not item["text"] and not item["file_id"]:
        return

    data = await state.get_data()
    items = data.get("items", [])
    items.append(item)
    await state.update_data(items=items)

    await message.answer(f"➕ Added ({len(items)} so far)", reply_markup=_done_kb())


@router.callback_query(BulkPostState.collecting, F.data == "bp_cancel")
async def bulkpost_cancel(query: types.CallbackQuery, state: FSMContext):
    """Cancel while still collecting."""
    await state.clear()
    await query.message.answer("❌ Cancelled", reply_markup=main_menu_kb())
    await query.answer()


@router.callback_query(BulkPostState.collecting, F.data == "bp_done")
async def bulkpost_ask_channels(query: types.CallbackQuery, state: FSMContext):
    """Move from collecting to channel selection."""
    data = await state.get_data()
    items = data.get("items", [])
    if not items:
        await query.answer("Forward at least 1 message first!", show_alert=True)
        return

    async with session() as s:
        q = select(Channel)
        res = await s.execute(q)
        channels = res.scalars().all()

    if not channels:
        await query.message.answer(
            "❌ No channels added\n\n"
            "Add the bot as admin to a channel to register it automatically, "
            "or use /add_channel",
            reply_markup=main_menu_kb()
        )
        await state.clear()
        await query.answer()
        return

    await state.update_data(selected_channels=[])
    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text=f"☐ {ch.title}", callback_data=f"bpch_{ch.id}")]
            for ch in channels
        ] + [
            [types.InlineKeyboardButton(text="📤 Post All Now", callback_data="bpch_next")],
            [types.InlineKeyboardButton(text="❌ Cancel", callback_data="bpch_cancel")],
        ]
    )
    await query.message.answer(
        f"📍 SELECT CHANNELS for {len(items)} post(s):\n\n"
        f"Tap channels (☐=off, ☑=on):",
        reply_markup=kb
    )
    await state.set_state(BulkPostState.select_channels)
    await query.answer()


@router.callback_query(BulkPostState.select_channels, F.data.startswith("bpch_"))
async def bulkpost_handle_channels(query: types.CallbackQuery, state: FSMContext):
    """Handle channel toggling and the final 'post all' trigger."""
    if query.data == "bpch_cancel":
        await state.clear()
        await query.message.answer("❌ Cancelled", reply_markup=main_menu_kb())
        await query.answer()
        return

    if query.data == "bpch_next":
        data = await state.get_data()
        selected = data.get("selected_channels", [])
        if not selected:
            await query.answer("Select at least 1 channel!", show_alert=True)
            return
        await query.answer("Posting...")
        await query.message.answer("⏳ Posting everything, one moment...")
        await _post_all(query.message, state, query.from_user.id, query.bot)
        return

    ch_id = int(query.data.replace("bpch_", ""))
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
                callback_data=f"bpch_{ch.id}"
            )]
            for ch in channels
        ] + [
            [types.InlineKeyboardButton(text="📤 Post All Now", callback_data="bpch_next")],
            [types.InlineKeyboardButton(text="❌ Cancel", callback_data="bpch_cancel")],
        ]
    )
    await query.message.edit_reply_markup(reply_markup=kb)
    await query.answer()


async def _post_all(message: types.Message, state: FSMContext, user_id: int, bot) -> None:
    """Create one Post + PostTarget set per buffered item, sending each to
    every selected channel right away. Each forwarded item becomes its own
    Post row (its own /myposts entry, its own /edit and /delete target) -
    they just all get sent in the same batch instead of one at a time.
    """
    data = await state.get_data()
    items = data.get("items", [])
    selected_ids = data.get("selected_channels", [])

    async with session() as s:
        q = select(Channel).where(Channel.id.in_(selected_ids))
        res = await s.execute(q)
        channels = res.scalars().all()

    posted = 0
    failed_items = 0

    for item in items:
        content_type = item["type"]
        text = item["text"] or None
        photo_file_id = item["file_id"] if content_type == "photo" else None
        video_file_id = item["file_id"] if content_type == "video" else None

        async with session() as s:
            post = Post(
                owner_user_id=user_id,
                content_type=ContentType(content_type),
                text=text,
                photo_file_id=photo_file_id,
                video_file_id=video_file_id,
                status=PostStatus.SENT,
            )
            s.add(post)
            await s.flush()

            ok_any = False
            for ch in channels:
                try:
                    if content_type == "photo":
                        msg = await bot.send_photo(chat_id=ch.chat_id, photo=photo_file_id, caption=text)
                    elif content_type == "video":
                        msg = await bot.send_video(chat_id=ch.chat_id, video=video_file_id, caption=text)
                    else:
                        msg = await bot.send_message(chat_id=ch.chat_id, text=text or "")
                    s.add(PostTarget(
                        post_id=post.id, channel_id=ch.id, message_id=msg.message_id, sent_at=datetime.now()
                    ))
                    ok_any = True
                except Exception:
                    continue

            await s.commit()

        if ok_any:
            posted += 1
        else:
            failed_items += 1

        # Small pacing gap so a big batch doesn't trip Telegram's per-chat
        # rate limits when posting to several channels back to back.
        await asyncio.sleep(0.3)

    result = (
        f"✅ BULK POST DONE\n\n"
        f"━━━━━━━━━━\n"
        f"Posted: {posted}/{len(items)}\n"
        f"Channels: {len(channels)}\n"
        f"━━━━━━━━━━"
    )
    if failed_items:
        result += f"\n\n❌ {failed_items} item(s) failed to send to any channel"

    await message.answer(result, reply_markup=main_menu_kb())
    await state.clear()


@router.message(lambda msg: msg.text == "📤 Bulk Post")
async def bulkpost_button(message: types.Message, state: FSMContext):
    """Bulk post from menu."""
    await bulkpost_start(message, state)
