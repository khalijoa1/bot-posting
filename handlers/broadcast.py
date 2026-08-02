"""Broadcast a single message to everyone who has ever submitted a join
request to one of the operator's auto-approve channels.

The audience builds itself automatically over time: every chat_join_request
(see handlers/join_requests.py) records the requester into BroadcastUser,
regardless of whether that particular channel currently has auto-approve
turned on - submitting the join request is itself the qualifying
interaction that lets this bot DM them later (the same Bot API 5.5+
chat_join_request exception documented in join_requests.py). Nothing here
scrapes a channel's member list or messages anyone who hasn't actually
reached out to one of the operator's channels through this bot.
"""
import asyncio
import logging
from datetime import datetime

from aiogram import Router, types, F, Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select, func

from db import session
from handlers.common import main_menu_kb
from models import BroadcastUser

logger = logging.getLogger(__name__)
router = Router()

# Delay between sends - comfortably under Telegram's flood limits (roughly
# 30 messages/second overall to distinct chats) without needing per-
# broadcast tuning. TelegramRetryAfter is still handled below for the rare
# case Telegram asks for more room anyway.
_SEND_DELAY_SECONDS = 0.05


class BroadcastState(StatesGroup):
    content = State()
    confirm = State()


def _cancel_kb() -> types.ReplyKeyboardMarkup:
    return types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text="❌ Cancel")]],
        resize_keyboard=True,
    )


async def record_broadcast_user(user: types.User | None) -> None:
    """Upsert a Telegram user into the broadcast audience. Called from
    handlers/join_requests.py the instant their join request arrives -
    that's the qualifying interaction, not anything that happens here."""
    if user is None:
        return
    now = datetime.utcnow()
    async with session() as s:
        existing = await s.get(BroadcastUser, user.id)
        if existing:
            existing.username = user.username
            existing.full_name = user.full_name
            existing.last_seen = now
        else:
            s.add(BroadcastUser(
                user_id=user.id, username=user.username, full_name=user.full_name,
                first_seen=now, last_seen=now,
            ))
        await s.commit()


async def _audience_count() -> int:
    async with session() as s:
        res = await s.execute(select(func.count()).select_from(BroadcastUser))
        return res.scalar() or 0


async def broadcast_start(message: types.Message, state: FSMContext) -> None:
    count = await _audience_count()
    if count == 0:
        await message.answer(
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "📢 BROADCAST\n"
            "━━━━━━━━━━━━━━━━━━━━━\n\n"
            "No one's in your broadcast list yet. It fills up automatically "
            "as people submit join requests to your channels (turn on "
            "🔐 Auto-Approve under ⚙️ Settings so requests actually come "
            "through). Once at least one person has requested to join "
            "somewhere, you'll be able to message them here.",
            reply_markup=main_menu_kb(),
        )
        return

    await state.set_state(BroadcastState.content)
    await message.answer(
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "📢 BROADCAST\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Current audience: {count} people who've submitted a join request "
        "to one of your channels.\n\n"
        "Send the message to broadcast - text, a single photo, or a single "
        "video (with or without a caption). It'll be copied exactly as-is "
        "to everyone. Albums (multiple photos/videos in one post) aren't "
        "supported here yet - send one item at a time.",
        reply_markup=_cancel_kb(),
    )


@router.message(Command("broadcast"))
async def broadcast_cmd(message: types.Message, state: FSMContext) -> None:
    await broadcast_start(message, state)


@router.message(BroadcastState.content, F.text == "❌ Cancel")
async def cancel_broadcast_content(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("❌ Cancelled", reply_markup=main_menu_kb())


@router.message(BroadcastState.content, F.media_group_id)
async def reject_album_broadcast(message: types.Message) -> None:
    await message.answer(
        "❌ Albums aren't supported for broadcast yet - send a single "
        "message (text, one photo, or one video) instead.",
        reply_markup=_cancel_kb(),
    )


@router.message(BroadcastState.content)
async def get_broadcast_content(message: types.Message, state: FSMContext) -> None:
    count = await _audience_count()
    await state.update_data(from_chat_id=message.chat.id, source_message_id=message.message_id)
    await state.set_state(BroadcastState.confirm)
    await message.answer(
        f"Send this to {count} people now? This can't be undone once started.",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="✅ Send", callback_data="bcast:send")],
            [types.InlineKeyboardButton(text="❌ Cancel", callback_data="bcast:cancel")],
        ]),
    )


@router.callback_query(BroadcastState.confirm, F.data == "bcast:cancel")
async def cancel_broadcast_confirm(query: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await query.message.edit_text("❌ Broadcast cancelled")
    await query.message.answer("Back to menu:", reply_markup=main_menu_kb())
    await query.answer()


@router.callback_query(BroadcastState.confirm, F.data == "bcast:send")
async def confirm_broadcast(query: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    from_chat_id = data.get("from_chat_id")
    source_message_id = data.get("source_message_id")
    await state.clear()

    await query.message.edit_text("📢 Broadcasting in the background - I'll message you here when it's done.")
    await query.answer()

    # Run as a background task rather than awaiting it inline: a broadcast
    # to a large audience can take a while (rate-limited sends, one per
    # person), and this callback handler needs to return quickly so the
    # bot stays responsive to everything else in the meantime.
    asyncio.create_task(_run_broadcast(query.bot, query.from_user.id, from_chat_id, source_message_id))


async def _run_broadcast(bot: Bot, operator_id: int, from_chat_id: int, source_message_id: int) -> None:
    async with session() as s:
        res = await s.execute(select(BroadcastUser.user_id))
        user_ids = [row[0] for row in res.all()]

    sent = 0
    failed = 0
    removed = 0

    for user_id in user_ids:
        try:
            await bot.copy_message(chat_id=user_id, from_chat_id=from_chat_id, message_id=source_message_id)
            sent += 1
        except TelegramRetryAfter as exc:
            # Telegram explicitly asked us to slow down - wait exactly as
            # long as it says, then retry this one person once before
            # moving on, rather than just dropping them.
            await asyncio.sleep(exc.retry_after)
            try:
                await bot.copy_message(chat_id=user_id, from_chat_id=from_chat_id, message_id=source_message_id)
                sent += 1
            except Exception:
                failed += 1
        except TelegramForbiddenError:
            # They've blocked the bot or deactivated their account - this
            # will never succeed again, so remove them from future
            # broadcasts instead of re-trying (and re-failing) every time.
            failed += 1
            removed += 1
            async with session() as s:
                row = await s.get(BroadcastUser, user_id)
                if row:
                    await s.delete(row)
                    await s.commit()
        except TelegramBadRequest:
            failed += 1
        except Exception:
            logger.exception("Broadcast send failed for user_id=%s", user_id)
            failed += 1
        await asyncio.sleep(_SEND_DELAY_SECONDS)

    summary = (
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "📢 BROADCAST COMPLETE\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"✅ Sent: {sent}\n"
        f"❌ Failed: {failed}"
        + (f" ({removed} blocked/deactivated - removed from future broadcasts)" if removed else "")
    )
    try:
        await bot.send_message(operator_id, summary)
    except Exception:
        logger.exception("Failed to deliver broadcast summary to operator_id=%s", operator_id)
