"""Settings and auto-approve."""
from aiogram import Router, types, F
from aiogram.filters import Command
from sqlalchemy import select

from db import session
from models import Channel

router = Router()


@router.message(Command("autoapprove"))
async def auto_approve(message: types.Message):
    """Show auto-approve settings."""
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
    
    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(
                text=f"{'✅ ON' if ch.auto_approve_members else '❌ OFF'} - {ch.title}",
                callback_data=f"app_{ch.id}"
            )]
            for ch in channels
        ]
    )
    
    await message.answer(
        "━━━━━━━━━━━━━━━━━━\n"
        "🔐 AUTO-APPROVE\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Tap channel to toggle auto-approval\n"
        "of subscriber join requests:",
        reply_markup=kb
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
        
        # Refresh all channels
        q = select(Channel)
        res = await s.execute(q)
        channels = res.scalars().all()
    
    # Update keyboard
    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(
                text=f"{'✅ ON' if c.auto_approve_members else '❌ OFF'} - {c.title}",
                callback_data=f"app_{c.id}"
            )]
            for c in channels
        ]
    )
    
    await query.message.edit_reply_markup(reply_markup=kb)
    
    status = "✅ ENABLED" if ch.auto_approve_members else "❌ DISABLED"
    await query.answer(f"{ch.title}: {status}", show_alert=True)


@router.message(lambda msg: msg.text == "🔐 Auto-Approve Members")
async def approve_button(message: types.Message):
    """Auto-approve from menu."""
    await auto_approve(message)

