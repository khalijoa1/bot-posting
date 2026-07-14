"""Handler for managing channel subscribers and approvals."""
from aiogram import F, Router, types
from aiogram.filters import Command
from sqlalchemy import select

from db import session
from models import Channel

router = Router()


@router.message(Command("autoapprove"))
async def auto_approve_toggle(message: types.Message):
    """Toggle auto-approval for channels"""
    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text="View Channels", callback_data="auto_view")],
        ]
    )
    await message.reply(
        "🔐 AUTO-APPROVE SUBSCRIBERS\n\n"
        "Tap channel to toggle auto-approval:",
        reply_markup=kb,
        parse_mode=None
    )


@router.callback_query(F.data == "auto_view")
async def show_channels_for_approval(query: types.CallbackQuery):
    """Show channels for approval toggle"""
    async with session() as s:
        ch_q = select(Channel)
        res = await s.execute(ch_q)
        channels = res.scalars().all()

    if not channels:
        await query.answer("No channels", show_alert=True)
        return

    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(
                text=f"{'✅' if ch.auto_approve_members else '❌'} {ch.title}",
                callback_data=f"auto_toggle_{ch.id}"
            )]
            for ch in channels
        ]
    )

    await query.message.edit_reply_markup(reply_markup=kb)
    await query.answer()


@router.callback_query(F.data.startswith("auto_toggle_"))
async def toggle_auto_approve(query: types.CallbackQuery):
    """Toggle auto-approve for a channel"""
    ch_id = int(query.data.replace("auto_toggle_", ""))

    async with session() as s:
        ch = await s.get(Channel, ch_id)
        if ch:
            ch.auto_approve_members = not ch.auto_approve_members
            s.add(ch)
            await s.commit()

            status = "✅ ENABLED" if ch.auto_approve_members else "❌ DISABLED"
            await query.answer(f"{ch.title}: {status}", show_alert=True)

            # Refresh
            ch_q = select(Channel)
            res = await s.execute(ch_q)
            channels = res.scalars().all()

    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(
                text=f"{'✅' if ch.auto_approve_members else '❌'} {ch.title}",
                callback_data=f"auto_toggle_{ch.id}"
            )]
            for ch in channels
        ]
    )

    await query.message.edit_reply_markup(reply_markup=kb)

