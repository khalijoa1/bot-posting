"""Settings, auto-approve, and per-channel welcome messages.

Auto-approve and the welcome message are separate settings on purpose:
auto-approve controls whether join requests get approved automatically,
while the welcome message is what gets DMed to a subscriber once they're
approved (see handlers/join_requests.py). A channel can have auto-approve
on with no welcome message (silent approval) or a welcome message set
before auto-approve is even turned on.
"""
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select

from db import session
from handlers.common import main_menu_kb
from models import Channel

router = Router()


class WelcomeMsgState(StatesGroup):
    text = State()


def _auto_approve_kb(channels) -> types.InlineKeyboardMarkup:
    rows = []
    for ch in channels:
        rows.append([
            types.InlineKeyboardButton(
                text=f"{'✅ ON' if ch.auto_approve_members else '❌ OFF'} - {ch.title}",
                callback_data=f"app_{ch.id}"
            ),
            types.InlineKeyboardButton(
                text=f"💬 {'Edit' if ch.welcome_message else 'Set'} Message",
                callback_data=f"setwelcome_{ch.id}"
            ),
        ])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("autoapprove"))
async def auto_approve(message: types.Message):
    """Show auto-approve settings, with a button per channel to also set
    the welcome message it DMs a subscriber once approved."""
    async with session() as s:
        q = select(Channel)
        res = await s.execute(q)
        channels = res.scalars().all()

    if not channels:
        await message.answer(
            "━━━━━━━━━━━━━━━━━━\n"
            "🔐 AUTO-APPROVE\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "❌ No channels added yet"
        )
        return

    await message.answer(
        "━━━━━━━━━━━━━━━━━━\n"
        "🔐 AUTO-APPROVE\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Tap a channel to toggle auto-approval of subscriber join "
        "requests, or 💬 to set/edit the message it DMs them once "
        "approved.\n\n"
        "Note: the channel needs \"Approve new members\" turned on in "
        "Telegram, and Telegram only lets the bot DM someone who has "
        "opened a chat with it before (e.g. pressed /start).",
        reply_markup=_auto_approve_kb(channels)
    )


@router.callback_query(F.data.startswith("app_"))
async def toggle_approve(query: types.CallbackQuery):
    """Toggle auto-approve for a channel."""
    ch_id = int(query.data.replace("app_", ""))

    async with session() as s:
        ch = await s.get(Channel, ch_id)
        if not ch:
            await query.answer("Not found", show_alert=True)
            return

        ch.auto_approve_members = not ch.auto_approve_members
        s.add(ch)
        await s.commit()
        status = "✅ ENABLED" if ch.auto_approve_members else "❌ DISABLED"
        title = ch.title

        q = select(Channel)
        res = await s.execute(q)
        channels = res.scalars().all()

    await query.message.edit_reply_markup(reply_markup=_auto_approve_kb(channels))
    await query.answer(f"{title}: {status}", show_alert=True)


@router.callback_query(F.data.startswith("setwelcome_"))
async def start_set_welcome(query: types.CallbackQuery, state: FSMContext):
    """Prompt for the welcome-message text for one channel."""
    ch_id = int(query.data.replace("setwelcome_", ""))

    async with session() as s:
        ch = await s.get(Channel, ch_id)
        if not ch:
            await query.answer("Not found", show_alert=True)
            return
        title = ch.title
        current = ch.welcome_message

    await state.update_data(welcome_channel_id=ch_id)
    await state.set_state(WelcomeMsgState.text)

    current_block = f"\n\nCurrent message:\n{current}" if current else ""
    await query.message.answer(
        f"💬 WELCOME MESSAGE - {title}\n\n"
        f"Send the message to DM new subscribers once they're "
        f"auto-approved into this channel.{current_block}",
        reply_markup=types.ReplyKeyboardMarkup(
            keyboard=[
                [types.KeyboardButton(text="🗑️ Clear Message")],
                [types.KeyboardButton(text="❌ Cancel")]
            ],
            resize_keyboard=True
        )
    )
    await query.answer()


@router.message(WelcomeMsgState.text, F.text == "❌ Cancel")
async def cancel_set_welcome(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Cancelled", reply_markup=main_menu_kb())


@router.message(WelcomeMsgState.text, F.text == "🗑️ Clear Message")
async def clear_welcome(message: types.Message, state: FSMContext):
    data = await state.get_data()
    ch_id = data.get("welcome_channel_id")

    async with session() as s:
        ch = await s.get(Channel, ch_id)
        if ch:
            ch.welcome_message = None
            s.add(ch)
            await s.commit()

    await message.answer("✅ Welcome message cleared", reply_markup=main_menu_kb())
    await state.clear()


@router.message(WelcomeMsgState.text, F.text)
async def save_welcome(message: types.Message, state: FSMContext):
    data = await state.get_data()
    ch_id = data.get("welcome_channel_id")
    text = message.text.strip()

    async with session() as s:
        ch = await s.get(Channel, ch_id)
        if not ch:
            await message.answer("❌ Channel not found", reply_markup=main_menu_kb())
            await state.clear()
            return
        ch.welcome_message = text
        s.add(ch)
        await s.commit()
        title = ch.title

    await message.answer(
        f"✅ WELCOME MESSAGE SAVED for {title}\n\n"
        f"It will be sent as a DM the moment someone's join request to "
        f"this channel is auto-approved.",
        reply_markup=main_menu_kb()
    )
    await state.clear()


@router.message(lambda msg: msg.text == "🔐 Auto-Approve Members")
async def approve_button(message: types.Message):
    """Auto-approve from menu."""
    await auto_approve(message)
