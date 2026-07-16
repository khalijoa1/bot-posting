"""Group moderation: link filtering and anti-spam, configurable per group.

Setup (operator only, in private chat with the bot):
  1. Add the bot to your group as an admin with "Delete messages" and
     "Ban users" permissions.
  2. /add_group <chat_id> [title] to register it.
  3. /moderation to choose link and spam-handling rules for that group.

Once registered, every member's plain messages in that group are checked
against the group's rules and acted on automatically.
"""
from __future__ import annotations

import re
import time
from collections import defaultdict, deque

from aiogram import Router, types, F
from aiogram.filters import Command, CommandObject
from sqlalchemy import select

from db import session
from models import LinkPolicy, ModeratedGroup, SpamAction

router = Router()


# ---------------------------------------------------------------------------
# Setup & settings (operator only, private chat)
# ---------------------------------------------------------------------------

@router.message(Command("add_group"))
async def add_group(message: types.Message, command: CommandObject):
    """Usage: /add_group <chat_id> [title]"""
    args = command.args
    if not args:
        await message.reply(
            "Usage: /add_group <chat_id> [title]\n\n"
            "chat_id looks like -1001234567890. Add the bot to the group as "
            "admin first (Delete messages + Ban users permissions)."
        )
        return
    parts = args.split(None, 1)
    try:
        chat_id = int(parts[0].strip())
    except ValueError:
        await message.reply("chat_id must be a number, e.g. -1001234567890")
        return
    title = parts[1].strip() if len(parts) > 1 else str(chat_id)

    async with session() as s:
        q = select(ModeratedGroup).where(ModeratedGroup.chat_id == chat_id)
        res = await s.execute(q)
        if res.scalars().first():
            await message.reply("This group is already registered.")
            return
        g = ModeratedGroup(owner_user_id=message.from_user.id, chat_id=chat_id, title=title)
        s.add(g)
        await s.commit()

    await message.reply(
        f"✅ Registered \"{title}\" for moderation.\n\n"
        f"Default: delete invite links/ad links, warn then mute repeat spammers.\n"
        f"Use /moderation to change settings."
    )


@router.message(Command("list_groups"))
async def list_groups(message: types.Message):
    async with session() as s:
        q = select(ModeratedGroup)
        res = await s.execute(q)
        groups = res.scalars().all()

    if not groups:
        await message.answer(
            "━━━━━━━━━━━━━━━━━━\n"
            "🛡️ MODERATED GROUPS\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "❌ No groups yet\n\n"
            "Use /add_group <chat_id> [title] to add one"
        )
        return

    text = "━━━━━━━━━━━━━━━━━━\n🛡️ MODERATED GROUPS\n━━━━━━━━━━━━━━━━━━\n\n"
    for g in groups:
        text += (
            f"ID: {g.id}\n"
            f"Title: {g.title}\n"
            f"Moderation: {'✅ ON' if g.moderation_enabled else '❌ OFF'}\n"
            f"Links: {g.link_policy.value}\n"
            f"Spam: {g.spam_action.value}\n\n"
        )
    await message.answer(text)


@router.message(Command("remove_group"))
async def remove_group(message: types.Message, command: CommandObject):
    args = command.args
    if not args:
        await message.reply("Usage: /remove_group <id>\n\n(use /list_groups to find the id)")
        return
    try:
        gid = int(args.strip())
    except ValueError:
        await message.reply("id must be numeric - use /list_groups to find it")
        return
    async with session() as s:
        g = await s.get(ModeratedGroup, gid)
        if not g:
            await message.reply("Group not found")
            return
        await s.delete(g)
        await s.commit()
    await message.reply("✅ Removed. Moderation stopped for that group.")


def _mark(current, value) -> str:
    return "🔘" if current == value else "⚪"


def _group_settings_kb(g: ModeratedGroup) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(
                text=f"{'✅ Moderation ON' if g.moderation_enabled else '❌ Moderation OFF'} (tap to toggle)",
                callback_data=f"modtoggle_{g.id}"
            )],
            [types.InlineKeyboardButton(text="— 🔗 Links —", callback_data="modnoop")],
            [types.InlineKeyboardButton(
                text=f"{_mark(g.link_policy, LinkPolicy.DELETE_ALL)} Delete all links",
                callback_data=f"modlink_{g.id}_delete_all"
            )],
            [types.InlineKeyboardButton(
                text=f"{_mark(g.link_policy, LinkPolicy.DELETE_INVITES_ADS)} Delete invite links/ads only",
                callback_data=f"modlink_{g.id}_delete_invites_ads"
            )],
            [types.InlineKeyboardButton(
                text=f"{_mark(g.link_policy, LinkPolicy.ADMINS_ONLY)} Allow admins, delete for others",
                callback_data=f"modlink_{g.id}_admins_only"
            )],
            [types.InlineKeyboardButton(text="— 🐢 Spam —", callback_data="modnoop")],
            [types.InlineKeyboardButton(
                text=f"{_mark(g.spam_action, SpamAction.DELETE_ONLY)} Delete only",
                callback_data=f"modspam_{g.id}_delete_only"
            )],
            [types.InlineKeyboardButton(
                text=f"{_mark(g.spam_action, SpamAction.WARN_MUTE)} Warn, then mute repeat offenders",
                callback_data=f"modspam_{g.id}_warn_mute"
            )],
            [types.InlineKeyboardButton(
                text=f"{_mark(g.spam_action, SpamAction.DELETE_KICK)} Delete + kick immediately",
                callback_data=f"modspam_{g.id}_delete_kick"
            )],
            [types.InlineKeyboardButton(text="✅ Done", callback_data="moddone")],
        ]
    )


@router.message(Command("moderation"))
async def moderation_settings(message: types.Message):
    """Pick a group to configure."""
    async with session() as s:
        q = select(ModeratedGroup)
        res = await s.execute(q)
        groups = res.scalars().all()

    if not groups:
        await message.answer(
            "━━━━━━━━━━━━━━━━━━\n"
            "🛡️ MODERATION\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "❌ No groups registered yet.\n\n"
            "Use /add_group <chat_id> [title] first."
        )
        return

    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text=f"🛡️ {g.title}", callback_data=f"modg_{g.id}")]
            for g in groups
        ]
    )
    await message.answer(
        "━━━━━━━━━━━━━━━━━━\n"
        "🛡️ MODERATION SETTINGS\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Pick a group to configure:",
        reply_markup=kb
    )


@router.message(lambda msg: msg.text == "🛡️ Moderation")
async def moderation_button(message: types.Message):
    """Moderation from menu."""
    await moderation_settings(message)


@router.callback_query(F.data.startswith("modg_"))
async def open_group_settings(query: types.CallbackQuery):
    gid = int(query.data.replace("modg_", ""))
    async with session() as s:
        g = await s.get(ModeratedGroup, gid)
        if not g:
            await query.answer("Not found", show_alert=True)
            return
        await query.message.edit_text(
            f"🛡️ {g.title}\n\nTap to change a setting:",
            reply_markup=_group_settings_kb(g)
        )
    await query.answer()


@router.callback_query(F.data.startswith("modtoggle_"))
async def toggle_moderation(query: types.CallbackQuery):
    gid = int(query.data.replace("modtoggle_", ""))
    async with session() as s:
        g = await s.get(ModeratedGroup, gid)
        if not g:
            await query.answer("Not found", show_alert=True)
            return
        g.moderation_enabled = not g.moderation_enabled
        s.add(g)
        await s.commit()
        await query.message.edit_reply_markup(reply_markup=_group_settings_kb(g))
    await query.answer("Updated")


@router.callback_query(F.data.startswith("modlink_"))
async def set_link_policy(query: types.CallbackQuery):
    _, gid, policy = query.data.split("_", 2)
    async with session() as s:
        g = await s.get(ModeratedGroup, int(gid))
        if not g:
            await query.answer("Not found", show_alert=True)
            return
        g.link_policy = LinkPolicy(policy)
        s.add(g)
        await s.commit()
        await query.message.edit_reply_markup(reply_markup=_group_settings_kb(g))
    await query.answer("Updated")


@router.callback_query(F.data.startswith("modspam_"))
async def set_spam_action(query: types.CallbackQuery):
    _, gid, action = query.data.split("_", 2)
    async with session() as s:
        g = await s.get(ModeratedGroup, int(gid))
        if not g:
            await query.answer("Not found", show_alert=True)
            return
        g.spam_action = SpamAction(action)
        s.add(g)
        await s.commit()
        await query.message.edit_reply_markup(reply_markup=_group_settings_kb(g))
    await query.answer("Updated")


@router.callback_query(F.data == "moddone")
async def close_settings(query: types.CallbackQuery):
    await query.message.edit_text("✅ Moderation settings saved.")
    await query.answer()


@router.callback_query(F.data == "modnoop")
async def noop(query: types.CallbackQuery):
    await query.answer()


# ---------------------------------------------------------------------------
# Passive group monitoring - runs for every member's plain message
# ---------------------------------------------------------------------------

LINK_RE = re.compile(r"(https?://\S+|www\.\S+|t\.me/\S+|telegram\.me/\S+)", re.IGNORECASE)
INVITE_AD_RE = re.compile(
    r"(t\.me/\+\S+|t\.me/joinchat/\S+|telegram\.me/joinchat/\S+|"
    r"bit\.ly/\S+|tinyurl\.com/\S+|is\.gd/\S+|cutt\.ly/\S+)",
    re.IGNORECASE,
)

FLOOD_WINDOW_SECONDS = 8
FLOOD_MAX_MESSAGES = 5
DUPLICATE_WINDOW_SECONDS = 20
DUPLICATE_THRESHOLD = 3
MUTE_MINUTES = 10
WARNING_COOLDOWN_SECONDS = 30

# In-memory, per-process state. Fine for a single-replica deployment; resets
# on restart, which just means everyone's spam counters start fresh - no
# persistence needed for a sliding rate-limit window.
_recent_messages: dict[tuple[int, int], deque[tuple[float, str]]] = defaultdict(deque)
_last_warned: dict[tuple[int, int], float] = {}
_warn_counts: dict[tuple[int, int], int] = defaultdict(int)


def _check_flood(key: tuple[int, int], now: float, text: str) -> tuple[bool, bool]:
    """Returns (is_flood, is_duplicate) and records this message."""
    q = _recent_messages[key]
    q.append((now, text))
    while q and now - q[0][0] > FLOOD_WINDOW_SECONDS:
        q.popleft()

    is_flood = len(q) > FLOOD_MAX_MESSAGES
    is_duplicate = text != "" and sum(
        1 for t, msg_text in q if msg_text == text and now - t <= DUPLICATE_WINDOW_SECONDS
    ) >= DUPLICATE_THRESHOLD
    return is_flood, is_duplicate


async def _is_admin(bot, chat_id: int, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ("administrator", "creator")
    except Exception:
        return False


async def _warn(bot, chat_id: int, user_id: int, name: str, text: str) -> None:
    """Send a friendly warning, at most once per cooldown window per user."""
    key = (chat_id, user_id)
    now = time.time()
    if now - _last_warned.get(key, 0) < WARNING_COOLDOWN_SECONDS:
        return
    _last_warned[key] = now
    try:
        await bot.send_message(chat_id, f"{name}, {text}")
    except Exception:
        pass


@router.message(F.chat.type.in_({"group", "supergroup"}))
async def moderate_group_message(message: types.Message) -> None:
    """Checks a group message against that group's moderation rules."""
    if not message.from_user or message.from_user.is_bot:
        return

    chat_id = message.chat.id
    user_id = message.from_user.id
    name = message.from_user.first_name or "there"
    text = message.text or message.caption or ""
    bot = message.bot

    async with session() as s:
        q = select(ModeratedGroup).where(ModeratedGroup.chat_id == chat_id)
        res = await s.execute(q)
        group = res.scalars().first()

    if not group or not group.moderation_enabled:
        return

    # --- Link check ---
    has_forbidden_link = False
    if group.link_policy == LinkPolicy.DELETE_ALL:
        has_forbidden_link = bool(LINK_RE.search(text))
    elif group.link_policy == LinkPolicy.DELETE_INVITES_ADS:
        has_forbidden_link = bool(INVITE_AD_RE.search(text))
    elif group.link_policy == LinkPolicy.ADMINS_ONLY:
        if bool(LINK_RE.search(text)) and not await _is_admin(bot, chat_id, user_id):
            has_forbidden_link = True

    if has_forbidden_link:
        try:
            await message.delete()
        except Exception:
            pass
        await _warn(bot, chat_id, user_id, name, "🔗 links aren't allowed here — your message was removed.")
        return

    # --- Spam / flood check ---
    now = time.time()
    key = (chat_id, user_id)
    is_flood, is_duplicate = _check_flood(key, now, text)

    if not (is_flood or is_duplicate):
        return

    try:
        await message.delete()
    except Exception:
        pass

    if group.spam_action == SpamAction.DELETE_ONLY:
        return

    if group.spam_action == SpamAction.DELETE_KICK:
        try:
            await bot.ban_chat_member(chat_id, user_id)
            await bot.unban_chat_member(chat_id, user_id)  # ban+unban = kick, they can rejoin
        except Exception:
            pass
        try:
            await bot.send_message(
                chat_id,
                f"🚫 {name} was removed for spamming. They're welcome back once things calm down!"
            )
        except Exception:
            pass
        return

    # WARN_MUTE
    _warn_counts[key] += 1
    if _warn_counts[key] >= 2:
        try:
            until = int(now) + MUTE_MINUTES * 60
            await bot.restrict_chat_member(
                chat_id, user_id,
                permissions=types.ChatPermissions(can_send_messages=False),
                until_date=until,
            )
            await bot.send_message(
                chat_id,
                f"🔇 {name} has been muted for {MUTE_MINUTES} minutes for repeated spamming. "
                f"Let's keep things friendly! 🙂"
            )
            _warn_counts[key] = 0
        except Exception:
            pass
    else:
        await _warn(bot, chat_id, user_id, name, "🐢 please slow down — sending messages that fast looks like spam.")
