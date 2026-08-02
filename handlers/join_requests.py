"""Auto-approve channel join requests, based on each channel's setting, and
DM the new subscriber the channel's configured welcome message.

For approval to fire, the channel must have "Approve new members" (join
requests) turned on in Telegram, and the bot must be an admin there with
permission to add/approve members - toggle the per-channel setting with
/autoapprove. Set the welcome message when adding a channel (/add_channel).

Note: Telegram only lets a bot DM a user who has interacted with it before
(e.g. pressed /start on the bot at some point). If the subscriber never has,
the welcome DM will silently fail to send - this is a Telegram-side
restriction, not something the bot can work around.

Force-join: a channel can also require the requester to already belong to
one or more OTHER channels/groups (configured via 🔒 Force-Join in the
Channels menu) before their request gets auto-approved. If they're missing
any, approval is held and they're DMed the list of channels to join plus a
button to recheck - see handle_join_request and cb_recheck below.
"""
import json

from aiogram import Router, types, F
from aiogram.enums import ChatMemberStatus
from sqlalchemy import select

from db import session
from models import Channel

router = Router()


def _parse_required(raw: str | None) -> list[dict]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception:
        return []


async def _missing_required(bot, user_id: int, required: list[dict]) -> list[dict]:
    """Return the subset of `required` entries the user is NOT currently a
    member of. If a membership check itself fails (e.g. the bot isn't an
    admin/member of that chat, so it can't see membership), that entry is
    treated as missing too - fail closed rather than silently approving
    someone we couldn't actually verify.
    """
    missing = []
    for r in required:
        identifier = r.get("identifier")
        if not identifier:
            continue
        try:
            member = await bot.get_chat_member(chat_id=identifier, user_id=user_id)
            if member.status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED):
                missing.append(r)
        except Exception:
            missing.append(r)
    return missing


def _force_join_kb(channel_id: int, missing: list[dict]) -> types.InlineKeyboardMarkup:
    rows = [
        [types.InlineKeyboardButton(text=f"➕ Join {r.get('title') or r.get('identifier')}", url=r.get("link"))]
        for r in missing
        if r.get("link")
    ]
    rows.append([types.InlineKeyboardButton(text="✅ I've joined - Check again", callback_data=f"fj_recheck:{channel_id}")])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


@router.chat_join_request()
async def handle_join_request(update: types.ChatJoinRequest) -> None:
    async with session() as s:
        q = select(Channel).where(Channel.chat_id == update.chat.id)
        res = await s.execute(q)
        channel = res.scalars().first()

    if not channel or not channel.auto_approve_members:
        return

    required = _parse_required(channel.required_join_json)
    if required:
        missing = await _missing_required(update.bot, update.from_user.id, required)
        if missing:
            text = (
                f"🔒 One more step before you're approved into {channel.title}!\n\n"
                "You need to join the following first:\n\n"
                + "\n".join(f"• {r.get('title') or r.get('identifier')}" for r in missing)
                + "\n\nOnce you've joined all of them, tap the check-again button below."
            )
            try:
                await update.bot.send_message(
                    update.from_user.id, text, reply_markup=_force_join_kb(channel.id, missing)
                )
            except Exception:
                # Most common cause: the user has never started a chat with
                # the bot, so Telegram won't let it initiate a DM. Their
                # join request just stays pending until they do (or an
                # operator approves it manually in Telegram).
                pass
            return

    try:
        await update.approve()
    except Exception:
        return

    if channel.welcome_message:
        try:
            await update.bot.send_message(update.from_user.id, channel.welcome_message)
        except Exception:
            pass


@router.callback_query(F.data.startswith("fj_recheck:"))
async def cb_recheck_force_join(query: types.CallbackQuery) -> None:
    """User tapped 'I've joined - Check again' after being held by a
    force-join gate. Re-checks membership and, if satisfied now, approves
    the still-pending join request directly via the Bot API."""
    channel_id = int(query.data.split(":")[1])

    async with session() as s:
        channel = await s.get(Channel, channel_id)

    if not channel:
        await query.answer("Channel not found", show_alert=True)
        return

    required = _parse_required(channel.required_join_json)
    missing = await _missing_required(query.bot, query.from_user.id, required)
    if missing:
        await query.answer(
            "Still missing: " + ", ".join(r.get("title") or r.get("identifier") for r in missing),
            show_alert=True,
        )
        return

    try:
        await query.bot.approve_chat_join_request(chat_id=channel.chat_id, user_id=query.from_user.id)
    except Exception:
        await query.answer(
            "Couldn't approve automatically - your request may have expired, already been handled, "
            "or you may not have a pending request for this channel.",
            show_alert=True,
        )
        return

    await query.answer("✅ Approved! Welcome.")
    try:
        await query.message.edit_text("✅ You're all set - approved and added!")
    except Exception:
        pass

    if channel.welcome_message:
        try:
            await query.bot.send_message(query.from_user.id, channel.welcome_message)
        except Exception:
            pass
