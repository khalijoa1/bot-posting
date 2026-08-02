"""Per-group welcome message (sent when someone new joins) and recurring
messages (sent on a repeating interval) for moderated groups.

Both share the same content shape - text, an optional single photo/video,
and an optional list of inline URL buttons - and both are configured from
the same "🛡️ Moderation" -> pick a group -> "🎉 Welcome Message" /
"🔁 Recurring Messages" screens (see handlers/moderation.py's
_group_settings_kb, which links here).

Welcome text supports {name} (or {first_name}) as a placeholder for the
new member's first name, substituted in at send time.

This router is included in bot.py BEFORE moderation.router: a group's
"new member joined" service message would otherwise also match
moderation.py's broad "any group message" moderation check (harmlessly,
since it has no links/spam signal, but there's no reason to let it try) -
aiogram stops walking further routers once a handler here matches, so
welcome_new_members below claims that update before moderation ever sees
it.
"""
from __future__ import annotations

import json
import logging

from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select

from db import session
from handlers.common import parse_duration, format_duration
from models import ModeratedGroup, RecurringMessage

router = Router()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared helpers - buttons JSON <-> keyboard, and sending a rich message
# ---------------------------------------------------------------------------

def _parse_buttons(raw: str | None) -> list[dict]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _buttons_kb(raw: str | None) -> types.InlineKeyboardMarkup | None:
    buttons = _parse_buttons(raw)
    rows = [[types.InlineKeyboardButton(text=b.get("text") or "Link", url=b["url"])]
            for b in buttons if b.get("url")]
    return types.InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


async def send_rich_message(
    bot, chat_id: int, text: str | None, media_type: str | None,
    media_file_id: str | None, buttons_json: str | None,
) -> bool:
    """Sends text/media/buttons to `chat_id`. Returns True on success,
    swallows and logs any failure (blocked bot, kicked from chat, etc.) so
    callers - a join-event handler and a background loop - never crash the
    caller's larger flow over one bad send."""
    kb = _buttons_kb(buttons_json)
    text = text or None
    try:
        if media_type == "photo" and media_file_id:
            await bot.send_photo(chat_id, media_file_id, caption=text, reply_markup=kb)
        elif media_type == "video" and media_file_id:
            await bot.send_video(chat_id, media_file_id, caption=text, reply_markup=kb)
        elif text:
            await bot.send_message(chat_id, text, reply_markup=kb)
        elif kb:
            # Buttons but no text/media - still send something, otherwise
            # the buttons would be silently dropped.
            await bot.send_message(chat_id, "​", reply_markup=kb)
        else:
            return False
        return True
    except Exception:
        logger.exception("Failed to send rich message to chat_id=%s", chat_id)
        return False


def _cancel_kb() -> types.ReplyKeyboardMarkup:
    return types.ReplyKeyboardMarkup(keyboard=[[types.KeyboardButton(text="❌ Cancel")]], resize_keyboard=True)


INTERVAL_PRESETS = [
    ("Every 30 min", 1800),
    ("Every 1 hour", 3600),
    ("Every 6 hours", 21600),
    ("Every 12 hours", 43200),
    ("Every 24 hours", 86400),
]


def _interval_kb(prefix: str) -> types.InlineKeyboardMarkup:
    rows = [[types.InlineKeyboardButton(text=label, callback_data=f"{prefix}_{seconds}")]
            for label, seconds in INTERVAL_PRESETS]
    rows.append([types.InlineKeyboardButton(text="Custom", callback_data=f"{prefix}_custom")])
    rows.append([types.InlineKeyboardButton(text="❌ Cancel", callback_data=f"{prefix}_cancel")])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


# ---------------------------------------------------------------------------
# Welcome message on join
# ---------------------------------------------------------------------------

@router.message(F.new_chat_members)
async def welcome_new_members(message: types.Message) -> None:
    async with session() as s:
        q = select(ModeratedGroup).where(ModeratedGroup.chat_id == message.chat.id)
        group = (await s.execute(q)).scalars().first()

    if not group or not group.welcome_enabled:
        return
    if not (group.welcome_text or group.welcome_media_file_id):
        return

    for member in message.new_chat_members:
        if member.is_bot:
            continue
        name = member.first_name or "there"
        text = (group.welcome_text or "")
        if text:
            text = text.replace("{first_name}", name).replace("{name}", name)
        await send_rich_message(
            message.bot, message.chat.id, text,
            group.welcome_media_type, group.welcome_media_file_id, group.welcome_buttons_json,
        )


# ---------------------------------------------------------------------------
# Welcome message configuration
# ---------------------------------------------------------------------------

class WelcomeState(StatesGroup):
    waiting_text = State()
    waiting_media = State()


class ButtonState(StatesGroup):
    """Shared by both welcome and recurring-message button management -
    `kind`/`gid`/`rid` in the FSM data say which one a given add-button
    flow is targeting."""
    waiting_label = State()
    waiting_url = State()


def _welcome_kb(g: ModeratedGroup) -> types.InlineKeyboardMarkup:
    rows = [[types.InlineKeyboardButton(
        text=f"{'✅ ON' if g.welcome_enabled else '❌ OFF'} (tap to toggle)",
        callback_data=f"gwtoggle_{g.id}",
    )]]
    rows.append([types.InlineKeyboardButton(text="📝 Set Text", callback_data=f"gwtext_{g.id}")])
    rows.append([types.InlineKeyboardButton(
        text=("🖼 Change Photo/Video" if g.welcome_media_file_id else "🖼 Set Photo/Video"),
        callback_data=f"gwmedia_{g.id}",
    )])
    if g.welcome_media_file_id:
        rows.append([types.InlineKeyboardButton(text="🚫 Remove Media", callback_data=f"gwmediadel_{g.id}")])
    rows.append([types.InlineKeyboardButton(text="🔘 Manage Buttons", callback_data=f"gwbtns_{g.id}")])
    rows.append([types.InlineKeyboardButton(text="📤 Send Test to Me", callback_data=f"gwtest_{g.id}")])
    rows.append([types.InlineKeyboardButton(text="🔙 Back", callback_data=f"modg_{g.id}")])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


async def _show_welcome_menu(target, g: ModeratedGroup) -> None:
    media = {"photo": "Photo", "video": "Video", None: "None"}.get(g.welcome_media_type, "None")
    btn_count = len(_parse_buttons(g.welcome_buttons_json))
    preview = (g.welcome_text or "(no text set)")[:300]
    text = (
        f"🎉 WELCOME MESSAGE — {g.title}\n\n"
        f"Status: {'✅ ON' if g.welcome_enabled else '❌ OFF'}\n"
        f"Media: {media}\n"
        f"Buttons: {btn_count}\n\n"
        f"Tip: use {{name}} in the text to insert the new member's first name.\n\n"
        f"Text preview:\n{preview}"
    )
    await target.answer(text, reply_markup=_welcome_kb(g))


@router.callback_query(F.data.startswith("gwelcome_"))
async def open_welcome(query: types.CallbackQuery) -> None:
    gid = int(query.data.replace("gwelcome_", ""))
    async with session() as s:
        g = await s.get(ModeratedGroup, gid)
    if not g:
        await query.answer("Not found", show_alert=True)
        return
    await _refresh_welcome_inplace(query.message, g)
    await query.answer()


async def _refresh_welcome_inplace(message: types.Message, g: ModeratedGroup) -> None:
    media = {"photo": "Photo", "video": "Video", None: "None"}.get(g.welcome_media_type, "None")
    btn_count = len(_parse_buttons(g.welcome_buttons_json))
    preview = (g.welcome_text or "(no text set)")[:300]
    text = (
        f"🎉 WELCOME MESSAGE — {g.title}\n\n"
        f"Status: {'✅ ON' if g.welcome_enabled else '❌ OFF'}\n"
        f"Media: {media}\n"
        f"Buttons: {btn_count}\n\n"
        f"Tip: use {{name}} in the text to insert the new member's first name.\n\n"
        f"Text preview:\n{preview}"
    )
    try:
        await message.edit_text(text, reply_markup=_welcome_kb(g))
    except Exception:
        pass


@router.callback_query(F.data.startswith("gwtoggle_"))
async def toggle_welcome(query: types.CallbackQuery) -> None:
    gid = int(query.data.replace("gwtoggle_", ""))
    async with session() as s:
        g = await s.get(ModeratedGroup, gid)
        if not g:
            await query.answer("Not found", show_alert=True)
            return
        g.welcome_enabled = not g.welcome_enabled
        s.add(g)
        await s.commit()
        await _refresh_welcome_inplace(query.message, g)
    await query.answer("Updated")


@router.callback_query(F.data.startswith("gwtext_"))
async def welcome_text_start(query: types.CallbackQuery, state: FSMContext) -> None:
    gid = int(query.data.replace("gwtext_", ""))
    await state.clear()
    await state.update_data(gid=gid)
    await state.set_state(WelcomeState.waiting_text)
    await query.message.answer(
        "📝 Send the welcome message text. You can use {name} to insert the new member's first name.",
        reply_markup=_cancel_kb(),
    )
    await query.answer()


@router.message(WelcomeState.waiting_text, F.text == "❌ Cancel")
async def welcome_text_cancel(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("❌ Cancelled", reply_markup=types.ReplyKeyboardRemove())


@router.message(WelcomeState.waiting_text, F.text)
async def welcome_text_save(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    gid = data["gid"]
    async with session() as s:
        g = await s.get(ModeratedGroup, gid)
        if not g:
            await state.clear()
            await message.answer("❌ Group no longer exists.", reply_markup=types.ReplyKeyboardRemove())
            return
        g.welcome_text = message.text
        await s.commit()
    await state.clear()
    await message.answer("✅ Welcome text saved.", reply_markup=types.ReplyKeyboardRemove())
    async with session() as s:
        g = await s.get(ModeratedGroup, gid)
    await _show_welcome_menu(message, g)


@router.callback_query(F.data.startswith("gwmediadel_"))
async def welcome_media_delete(query: types.CallbackQuery) -> None:
    gid = int(query.data.replace("gwmediadel_", ""))
    async with session() as s:
        g = await s.get(ModeratedGroup, gid)
        if not g:
            await query.answer("Not found", show_alert=True)
            return
        g.welcome_media_type = None
        g.welcome_media_file_id = None
        await s.commit()
        await _refresh_welcome_inplace(query.message, g)
    await query.answer("Removed")


@router.callback_query(F.data.startswith("gwmedia_"))
async def welcome_media_start(query: types.CallbackQuery, state: FSMContext) -> None:
    gid = int(query.data.replace("gwmedia_", ""))
    await state.clear()
    await state.update_data(gid=gid)
    await state.set_state(WelcomeState.waiting_media)
    await query.message.answer(
        "🖼 Send a photo or video for the welcome message, or type \"remove\" to clear it:",
        reply_markup=_cancel_kb(),
    )
    await query.answer()


@router.message(WelcomeState.waiting_media, F.text == "❌ Cancel")
async def welcome_media_cancel(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("❌ Cancelled", reply_markup=types.ReplyKeyboardRemove())


@router.message(WelcomeState.waiting_media, F.text.lower() == "remove")
async def welcome_media_remove(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    gid = data["gid"]
    async with session() as s:
        g = await s.get(ModeratedGroup, gid)
        if g:
            g.welcome_media_type = None
            g.welcome_media_file_id = None
            await s.commit()
    await state.clear()
    await message.answer("✅ Media removed.", reply_markup=types.ReplyKeyboardRemove())
    async with session() as s:
        g = await s.get(ModeratedGroup, gid)
    if g:
        await _show_welcome_menu(message, g)


@router.message(WelcomeState.waiting_media, F.photo)
async def welcome_media_photo(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    gid = data["gid"]
    file_id = message.photo[-1].file_id
    async with session() as s:
        g = await s.get(ModeratedGroup, gid)
        if g:
            g.welcome_media_type = "photo"
            g.welcome_media_file_id = file_id
            await s.commit()
    await state.clear()
    await message.answer("✅ Photo saved.", reply_markup=types.ReplyKeyboardRemove())
    async with session() as s:
        g = await s.get(ModeratedGroup, gid)
    if g:
        await _show_welcome_menu(message, g)


@router.message(WelcomeState.waiting_media, F.video)
async def welcome_media_video(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    gid = data["gid"]
    file_id = message.video.file_id
    async with session() as s:
        g = await s.get(ModeratedGroup, gid)
        if g:
            g.welcome_media_type = "video"
            g.welcome_media_file_id = file_id
            await s.commit()
    await state.clear()
    await message.answer("✅ Video saved.", reply_markup=types.ReplyKeyboardRemove())
    async with session() as s:
        g = await s.get(ModeratedGroup, gid)
    if g:
        await _show_welcome_menu(message, g)


@router.message(WelcomeState.waiting_media)
async def welcome_media_invalid(message: types.Message) -> None:
    await message.answer("❌ Send a photo, a video, or type \"remove\".")


@router.callback_query(F.data.startswith("gwtest_"))
async def welcome_test_send(query: types.CallbackQuery) -> None:
    gid = int(query.data.replace("gwtest_", ""))
    async with session() as s:
        g = await s.get(ModeratedGroup, gid)
    if not g:
        await query.answer("Not found", show_alert=True)
        return
    if not (g.welcome_text or g.welcome_media_file_id):
        await query.answer("Nothing set yet - add text or media first.", show_alert=True)
        return
    text = (g.welcome_text or "").replace("{first_name}", "You").replace("{name}", "You")
    ok = await send_rich_message(
        query.bot, query.from_user.id, text, g.welcome_media_type, g.welcome_media_file_id, g.welcome_buttons_json
    )
    await query.answer("✅ Sent to your DMs" if ok else "❌ Couldn't DM you - open a chat with the bot first", show_alert=True)


# ---------------------------------------------------------------------------
# Buttons management - shared between welcome and recurring messages
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("gwbtns_"))
async def welcome_buttons_menu(query: types.CallbackQuery) -> None:
    gid = int(query.data.replace("gwbtns_", ""))
    async with session() as s:
        g = await s.get(ModeratedGroup, gid)
    if not g:
        await query.answer("Not found", show_alert=True)
        return
    await query.message.edit_text(
        f"🔘 WELCOME MESSAGE BUTTONS — {g.title}\n\nTap 🗑 to remove a button, or add a new one:",
        reply_markup=_buttons_manage_kb("w", gid, g.welcome_buttons_json, back_cb=f"gwelcome_{gid}"),
    )
    await query.answer()


def _buttons_manage_kb(kind: str, target_id: int, buttons_json: str | None, back_cb: str) -> types.InlineKeyboardMarkup:
    buttons = _parse_buttons(buttons_json)
    rows = []
    for i, b in enumerate(buttons):
        rows.append([
            types.InlineKeyboardButton(text=f"{b.get('text')} → {(b.get('url') or '')[:24]}", callback_data="gnoop"),
            types.InlineKeyboardButton(text="🗑", callback_data=f"g{kind}btndel_{target_id}_{i}"),
        ])
    rows.append([types.InlineKeyboardButton(text="➕ Add Button", callback_data=f"g{kind}btnadd_{target_id}")])
    rows.append([types.InlineKeyboardButton(text="🔙 Back", callback_data=back_cb)])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "gnoop")
async def noop(query: types.CallbackQuery) -> None:
    await query.answer()


@router.callback_query(F.data.startswith("gwbtndel_"))
async def welcome_button_delete(query: types.CallbackQuery) -> None:
    parts = query.data.replace("gwbtndel_", "").split("_")
    gid, idx = int(parts[0]), int(parts[1])
    async with session() as s:
        g = await s.get(ModeratedGroup, gid)
        if not g:
            await query.answer("Not found", show_alert=True)
            return
        buttons = _parse_buttons(g.welcome_buttons_json)
        if 0 <= idx < len(buttons):
            buttons.pop(idx)
            g.welcome_buttons_json = json.dumps(buttons)
            await s.commit()
        await query.message.edit_reply_markup(
            reply_markup=_buttons_manage_kb("w", gid, g.welcome_buttons_json, back_cb=f"gwelcome_{gid}")
        )
    await query.answer("Removed")


@router.callback_query(F.data.startswith("gwbtnadd_"))
async def welcome_button_add_start(query: types.CallbackQuery, state: FSMContext) -> None:
    gid = int(query.data.replace("gwbtnadd_", ""))
    await state.clear()
    await state.update_data(kind="welcome", gid=gid)
    await state.set_state(ButtonState.waiting_label)
    await query.message.answer("Send the button's label (short text shown on the button):", reply_markup=_cancel_kb())
    await query.answer()


@router.callback_query(F.data.startswith("grmbtnadd_"))
async def recurring_button_add_start(query: types.CallbackQuery, state: FSMContext) -> None:
    rid = int(query.data.replace("grmbtnadd_", ""))
    await state.clear()
    await state.update_data(kind="recurring", rid=rid)
    await state.set_state(ButtonState.waiting_label)
    await query.message.answer("Send the button's label (short text shown on the button):", reply_markup=_cancel_kb())
    await query.answer()


@router.message(ButtonState.waiting_label, F.text == "❌ Cancel")
async def button_add_cancel_label(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("❌ Cancelled", reply_markup=types.ReplyKeyboardRemove())


@router.message(ButtonState.waiting_label, F.text)
async def button_add_label(message: types.Message, state: FSMContext) -> None:
    label = message.text.strip()[:64]
    await state.update_data(label=label)
    await state.set_state(ButtonState.waiting_url)
    await message.answer(f"Label: \"{label}\"\n\nNow send the URL (must start with http:// or https://):", reply_markup=_cancel_kb())


@router.message(ButtonState.waiting_url, F.text == "❌ Cancel")
async def button_add_cancel_url(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("❌ Cancelled", reply_markup=types.ReplyKeyboardRemove())


@router.message(ButtonState.waiting_url, F.text)
async def button_add_url(message: types.Message, state: FSMContext) -> None:
    url = message.text.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        await message.answer("❌ That doesn't look like a URL - it must start with http:// or https://. Try again:")
        return
    data = await state.get_data()
    label = data["label"]
    kind = data["kind"]

    if kind == "welcome":
        gid = data["gid"]
        async with session() as s:
            g = await s.get(ModeratedGroup, gid)
            if not g:
                await state.clear()
                await message.answer("❌ Group no longer exists.", reply_markup=types.ReplyKeyboardRemove())
                return
            buttons = _parse_buttons(g.welcome_buttons_json)
            buttons.append({"text": label, "url": url})
            g.welcome_buttons_json = json.dumps(buttons)
            await s.commit()
        await state.clear()
        await message.answer("✅ Button added.", reply_markup=types.ReplyKeyboardRemove())
        async with session() as s:
            g = await s.get(ModeratedGroup, gid)
        await message.answer(
            f"🔘 WELCOME MESSAGE BUTTONS — {g.title}",
            reply_markup=_buttons_manage_kb("w", gid, g.welcome_buttons_json, back_cb=f"gwelcome_{gid}"),
        )
        return

    # kind == "recurring"
    rid = data["rid"]
    async with session() as s:
        rm = await s.get(RecurringMessage, rid)
        if not rm:
            await state.clear()
            await message.answer("❌ That recurring message no longer exists.", reply_markup=types.ReplyKeyboardRemove())
            return
        buttons = _parse_buttons(rm.buttons_json)
        buttons.append({"text": label, "url": url})
        rm.buttons_json = json.dumps(buttons)
        await s.commit()
        gid = rm.group_id
    await state.clear()
    await message.answer("✅ Button added.", reply_markup=types.ReplyKeyboardRemove())
    async with session() as s:
        rm = await s.get(RecurringMessage, rid)
    await message.answer(
        "🔘 RECURRING MESSAGE BUTTONS",
        reply_markup=_buttons_manage_kb("rm", rid, rm.buttons_json, back_cb=f"grmopen_{gid}_{rid}"),
    )


@router.callback_query(F.data.startswith("grmbtndel_"))
async def recurring_button_delete(query: types.CallbackQuery) -> None:
    parts = query.data.replace("grmbtndel_", "").split("_")
    rid, idx = int(parts[0]), int(parts[1])
    async with session() as s:
        rm = await s.get(RecurringMessage, rid)
        if not rm:
            await query.answer("Not found", show_alert=True)
            return
        buttons = _parse_buttons(rm.buttons_json)
        if 0 <= idx < len(buttons):
            buttons.pop(idx)
            rm.buttons_json = json.dumps(buttons)
            await s.commit()
        await query.message.edit_reply_markup(
            reply_markup=_buttons_manage_kb("rm", rid, rm.buttons_json, back_cb=f"grmopen_{rm.group_id}_{rid}")
        )
    await query.answer("Removed")


@router.callback_query(F.data.startswith("grmbtns_"))
async def recurring_buttons_menu(query: types.CallbackQuery) -> None:
    rid = int(query.data.replace("grmbtns_", ""))
    async with session() as s:
        rm = await s.get(RecurringMessage, rid)
    if not rm:
        await query.answer("Not found", show_alert=True)
        return
    await query.message.edit_text(
        "🔘 RECURRING MESSAGE BUTTONS\n\nTap 🗑 to remove a button, or add a new one:",
        reply_markup=_buttons_manage_kb("rm", rid, rm.buttons_json, back_cb=f"grmopen_{rm.group_id}_{rid}"),
    )
    await query.answer()


# ---------------------------------------------------------------------------
# Recurring messages - list / detail / create / toggle / delete
# ---------------------------------------------------------------------------

class RecurringState(StatesGroup):
    waiting_content = State()
    waiting_interval_custom = State()


def _recurring_list_kb(gid: int, messages: list[RecurringMessage]) -> types.InlineKeyboardMarkup:
    rows = []
    for rm in messages:
        label = (rm.text or f"{rm.media_type or 'message'}").strip()[:30] or "(empty)"
        status = "✅" if rm.enabled else "❌"
        rows.append([types.InlineKeyboardButton(
            text=f"{status} {label} — every {format_duration(rm.interval_seconds)}",
            callback_data=f"grmopen_{gid}_{rm.id}",
        )])
    rows.append([types.InlineKeyboardButton(text="➕ Add Recurring Message", callback_data=f"grmadd_{gid}")])
    rows.append([types.InlineKeyboardButton(text="🔙 Back", callback_data=f"modg_{gid}")])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("grecur_"))
async def open_recurring_list(query: types.CallbackQuery) -> None:
    gid = int(query.data.replace("grecur_", ""))
    async with session() as s:
        g = await s.get(ModeratedGroup, gid)
        if not g:
            await query.answer("Not found", show_alert=True)
            return
        q = select(RecurringMessage).where(RecurringMessage.group_id == gid).order_by(RecurringMessage.id)
        messages = (await s.execute(q)).scalars().all()

    text = f"🔁 RECURRING MESSAGES — {g.title}\n\n"
    text += "No recurring messages yet." if not messages else "Tap one to edit, or add a new one:"
    await query.message.edit_text(text, reply_markup=_recurring_list_kb(gid, messages))
    await query.answer()


def _recurring_detail_kb(gid: int, rm: RecurringMessage) -> types.InlineKeyboardMarkup:
    rows = [[types.InlineKeyboardButton(
        text=f"{'✅ ON' if rm.enabled else '❌ OFF'} (tap to toggle)", callback_data=f"grmtoggle_{gid}_{rm.id}"
    )]]
    rows.append([types.InlineKeyboardButton(text="📝 Edit Text", callback_data=f"grmtext_{gid}_{rm.id}")])
    rows.append([types.InlineKeyboardButton(
        text=("🖼 Change Photo/Video" if rm.media_file_id else "🖼 Set Photo/Video"),
        callback_data=f"grmmedia_{gid}_{rm.id}",
    )])
    if rm.media_file_id:
        rows.append([types.InlineKeyboardButton(text="🚫 Remove Media", callback_data=f"grmmediadel_{gid}_{rm.id}")])
    rows.append([types.InlineKeyboardButton(text="⏱ Change Interval", callback_data=f"grminterval_{gid}_{rm.id}")])
    rows.append([types.InlineKeyboardButton(text="🔘 Manage Buttons", callback_data=f"grmbtns_{rm.id}")])
    rows.append([types.InlineKeyboardButton(text="🗑️ Delete", callback_data=f"grmdel_{gid}_{rm.id}")])
    rows.append([types.InlineKeyboardButton(text="🔙 Back", callback_data=f"grecur_{gid}")])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


async def _show_recurring_detail(target_message, gid: int, rm: RecurringMessage, edit: bool = True) -> None:
    media = {"photo": "Photo", "video": "Video", None: "None"}.get(rm.media_type, "None")
    btn_count = len(_parse_buttons(rm.buttons_json))
    preview = (rm.text or "(no text set)")[:300]
    last_sent = rm.last_sent_at.strftime("%Y-%m-%d %H:%M UTC") if rm.last_sent_at else "never yet"
    text = (
        f"🔁 RECURRING MESSAGE\n\n"
        f"Status: {'✅ ON' if rm.enabled else '❌ OFF'}\n"
        f"Interval: every {format_duration(rm.interval_seconds)}\n"
        f"Last sent: {last_sent}\n"
        f"Media: {media}\n"
        f"Buttons: {btn_count}\n\n"
        f"Text preview:\n{preview}"
    )
    kb = _recurring_detail_kb(gid, rm)
    if edit:
        try:
            await target_message.edit_text(text, reply_markup=kb)
            return
        except Exception:
            pass
    await target_message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("grmopen_"))
async def open_recurring_detail(query: types.CallbackQuery) -> None:
    gid, rid = (int(x) for x in query.data.replace("grmopen_", "").split("_"))
    async with session() as s:
        rm = await s.get(RecurringMessage, rid)
    if not rm:
        await query.answer("Not found", show_alert=True)
        return
    await _show_recurring_detail(query.message, gid, rm)
    await query.answer()


@router.callback_query(F.data.startswith("grmtoggle_"))
async def toggle_recurring(query: types.CallbackQuery) -> None:
    gid, rid = (int(x) for x in query.data.replace("grmtoggle_", "").split("_"))
    async with session() as s:
        rm = await s.get(RecurringMessage, rid)
        if not rm:
            await query.answer("Not found", show_alert=True)
            return
        rm.enabled = not rm.enabled
        await s.commit()
        await _show_recurring_detail(query.message, gid, rm)
    await query.answer("Updated")


@router.callback_query(F.data.startswith("grmdel_"))
async def delete_recurring(query: types.CallbackQuery) -> None:
    gid, rid = (int(x) for x in query.data.replace("grmdel_", "").split("_"))
    async with session() as s:
        rm = await s.get(RecurringMessage, rid)
        if rm:
            await s.delete(rm)
            await s.commit()
        g = await s.get(ModeratedGroup, gid)
        q = select(RecurringMessage).where(RecurringMessage.group_id == gid).order_by(RecurringMessage.id)
        messages = (await s.execute(q)).scalars().all()
    text = f"🔁 RECURRING MESSAGES — {g.title}\n\n"
    text += "No recurring messages yet." if not messages else "Tap one to edit, or add a new one:"
    await query.message.edit_text(text, reply_markup=_recurring_list_kb(gid, messages))
    await query.answer("Deleted")


@router.callback_query(F.data.startswith("grmmediadel_"))
async def recurring_media_delete(query: types.CallbackQuery) -> None:
    gid, rid = (int(x) for x in query.data.replace("grmmediadel_", "").split("_"))
    async with session() as s:
        rm = await s.get(RecurringMessage, rid)
        if not rm:
            await query.answer("Not found", show_alert=True)
            return
        rm.media_type = None
        rm.media_file_id = None
        await s.commit()
        await _show_recurring_detail(query.message, gid, rm)
    await query.answer("Removed")


@router.callback_query(F.data.startswith("grmtext_"))
async def recurring_text_start(query: types.CallbackQuery, state: FSMContext) -> None:
    gid, rid = (int(x) for x in query.data.replace("grmtext_", "").split("_"))
    await state.clear()
    await state.update_data(mode="edit_text", gid=gid, rid=rid)
    await state.set_state(RecurringState.waiting_content)
    await query.message.answer("📝 Send the new text for this recurring message:", reply_markup=_cancel_kb())
    await query.answer()


@router.callback_query(F.data.startswith("grmmedia_"))
async def recurring_media_start(query: types.CallbackQuery, state: FSMContext) -> None:
    gid, rid = (int(x) for x in query.data.replace("grmmedia_", "").split("_"))
    await state.clear()
    await state.update_data(mode="edit_media", gid=gid, rid=rid)
    await state.set_state(RecurringState.waiting_content)
    await query.message.answer(
        "🖼 Send a photo or video, or type \"remove\" to clear it:", reply_markup=_cancel_kb()
    )
    await query.answer()


@router.callback_query(F.data.startswith("grminterval_"))
async def recurring_interval_start(query: types.CallbackQuery, state: FSMContext) -> None:
    gid, rid = (int(x) for x in query.data.replace("grminterval_", "").split("_"))
    await state.update_data(mode="edit_interval", gid=gid, rid=rid)
    await query.message.edit_text("⏱ Pick a new interval:", reply_markup=_interval_kb("grmeditint"))
    await query.answer()


@router.callback_query(F.data.startswith("grmadd_"))
async def recurring_add_start(query: types.CallbackQuery, state: FSMContext) -> None:
    gid = int(query.data.replace("grmadd_", ""))
    await state.clear()
    await state.update_data(mode="create", gid=gid)
    await state.set_state(RecurringState.waiting_content)
    await query.message.answer(
        "➕ ADD RECURRING MESSAGE\n\n"
        "Send the message content - text, or a photo/video with a caption. "
        "This is what will be posted to the group repeatedly.",
        reply_markup=_cancel_kb(),
    )
    await query.answer()


@router.message(RecurringState.waiting_content, F.text == "❌ Cancel")
async def recurring_content_cancel(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("❌ Cancelled", reply_markup=types.ReplyKeyboardRemove())


@router.message(RecurringState.waiting_content, F.text.lower() == "remove")
async def recurring_media_remove(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("mode") != "edit_media":
        await message.answer("❌ Send a photo, a video, or text.")
        return
    rid = data["rid"]
    gid = data["gid"]
    async with session() as s:
        rm = await s.get(RecurringMessage, rid)
        if rm:
            rm.media_type = None
            rm.media_file_id = None
            await s.commit()
    await state.clear()
    await message.answer("✅ Media removed.", reply_markup=types.ReplyKeyboardRemove())
    async with session() as s:
        rm = await s.get(RecurringMessage, rid)
    if rm:
        await _show_recurring_detail(message, gid, rm, edit=False)


@router.message(RecurringState.waiting_content, F.media_group_id)
async def recurring_content_reject_album(message: types.Message) -> None:
    await message.answer(
        "❌ Albums aren't supported here - send a single photo or video (or plain text) instead.",
        reply_markup=_cancel_kb(),
    )


@router.message(RecurringState.waiting_content)
async def recurring_content_received(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    mode = data["mode"]

    text = message.caption or message.text
    media_type = None
    media_file_id = None
    if message.photo:
        media_type, media_file_id = "photo", message.photo[-1].file_id
    elif message.video:
        media_type, media_file_id = "video", message.video.file_id
    elif not text:
        await message.answer("❌ Send some text, or a photo/video (with or without a caption).")
        return

    if mode == "edit_text":
        rid, gid = data["rid"], data["gid"]
        async with session() as s:
            rm = await s.get(RecurringMessage, rid)
            if not rm:
                await state.clear()
                await message.answer("❌ No longer exists.", reply_markup=types.ReplyKeyboardRemove())
                return
            rm.text = text
            await s.commit()
        await state.clear()
        await message.answer("✅ Text updated.", reply_markup=types.ReplyKeyboardRemove())
        async with session() as s:
            rm = await s.get(RecurringMessage, rid)
        await _show_recurring_detail(message, gid, rm, edit=False)
        return

    if mode == "edit_media":
        if not media_file_id:
            await message.answer("❌ Send a photo, a video, or type \"remove\".")
            return
        rid, gid = data["rid"], data["gid"]
        async with session() as s:
            rm = await s.get(RecurringMessage, rid)
            if not rm:
                await state.clear()
                await message.answer("❌ No longer exists.", reply_markup=types.ReplyKeyboardRemove())
                return
            rm.media_type = media_type
            rm.media_file_id = media_file_id
            await s.commit()
        await state.clear()
        await message.answer("✅ Media updated.", reply_markup=types.ReplyKeyboardRemove())
        async with session() as s:
            rm = await s.get(RecurringMessage, rid)
        await _show_recurring_detail(message, gid, rm, edit=False)
        return

    # mode == "create": content collected, now ask for the interval before
    # actually creating the row.
    await state.update_data(text=text, media_type=media_type, media_file_id=media_file_id)
    await message.answer(
        "⏱ How often should this be posted?", reply_markup=_interval_kb("grmnewint")
    )


@router.callback_query(RecurringState.waiting_content, F.data.startswith("grmnewint_"))
async def recurring_create_interval(query: types.CallbackQuery, state: FSMContext) -> None:
    choice = query.data.replace("grmnewint_", "")
    if choice == "cancel":
        await state.clear()
        await query.message.edit_text("❌ Cancelled")
        await query.answer()
        return
    if choice == "custom":
        await state.set_state(RecurringState.waiting_interval_custom)
        await query.message.edit_text("Send a custom interval, e.g. 45m, 3h, or 2d:")
        await query.answer()
        return

    seconds = int(choice)
    data = await state.get_data()
    gid = data["gid"]
    async with session() as s:
        rm = RecurringMessage(
            group_id=gid, text=data.get("text"), media_type=data.get("media_type"),
            media_file_id=data.get("media_file_id"), buttons_json=None,
            interval_seconds=seconds, enabled=True,
        )
        s.add(rm)
        await s.commit()
        rid = rm.id
    await state.clear()
    await query.message.edit_text(f"✅ Recurring message created - posting every {format_duration(seconds)}.")
    async with session() as s:
        rm = await s.get(RecurringMessage, rid)
    await _show_recurring_detail(query.message, gid, rm, edit=False)
    await query.answer()


@router.message(RecurringState.waiting_interval_custom)
async def recurring_custom_interval(message: types.Message, state: FSMContext) -> None:
    try:
        seconds = parse_duration(message.text)
    except ValueError:
        seconds = None
    if not seconds:
        await message.answer("❌ Couldn't parse that - try e.g. 45m, 3h, or 2d:")
        return

    data = await state.get_data()
    gid = data["gid"]

    if data.get("mode") == "edit_interval":
        rid = data["rid"]
        async with session() as s:
            rm = await s.get(RecurringMessage, rid)
            if not rm:
                await state.clear()
                await message.answer("❌ No longer exists.", reply_markup=types.ReplyKeyboardRemove())
                return
            rm.interval_seconds = seconds
            await s.commit()
        await state.clear()
        await message.answer(
            f"✅ Interval updated - now every {format_duration(seconds)}.",
            reply_markup=types.ReplyKeyboardRemove(),
        )
        async with session() as s:
            rm = await s.get(RecurringMessage, rid)
        await _show_recurring_detail(message, gid, rm, edit=False)
        return

    # mode == "create"
    async with session() as s:
        rm = RecurringMessage(
            group_id=gid, text=data.get("text"), media_type=data.get("media_type"),
            media_file_id=data.get("media_file_id"), buttons_json=None,
            interval_seconds=seconds, enabled=True,
        )
        s.add(rm)
        await s.commit()
        rid = rm.id
    await state.clear()
    await message.answer(
        f"✅ Recurring message created - posting every {format_duration(seconds)}.",
        reply_markup=types.ReplyKeyboardRemove(),
    )
    async with session() as s:
        rm = await s.get(RecurringMessage, rid)
    await _show_recurring_detail(message, gid, rm, edit=False)


@router.callback_query(F.data.startswith("grmeditint_"))
async def recurring_edit_interval(query: types.CallbackQuery, state: FSMContext) -> None:
    choice = query.data.replace("grmeditint_", "")
    data = await state.get_data()
    gid, rid = data.get("gid"), data.get("rid")
    if gid is None or rid is None:
        await query.answer("Session expired - open the recurring message again.", show_alert=True)
        return

    if choice == "cancel":
        async with session() as s:
            rm = await s.get(RecurringMessage, rid)
        if rm:
            await _show_recurring_detail(query.message, gid, rm)
        await query.answer()
        return

    if choice == "custom":
        await state.set_state(RecurringState.waiting_interval_custom)
        await state.update_data(mode="edit_interval")
        await query.message.edit_text("Send a custom interval, e.g. 45m, 3h, or 2d:")
        await query.answer()
        return

    seconds = int(choice)
    async with session() as s:
        rm = await s.get(RecurringMessage, rid)
        if not rm:
            await query.answer("Not found", show_alert=True)
            return
        rm.interval_seconds = seconds
        await s.commit()
        await _show_recurring_detail(query.message, gid, rm)
    await state.clear()
    await query.answer("Updated")
