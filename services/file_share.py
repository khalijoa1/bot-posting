"""File sharing.

Operator side (allowed users only): upload one or more files, pick the rules, get a
share link (https://t.me/<bot>?start=f_<code>) and/or send the files straight to
people. Rules per share: who can open it (anyone / members of your channels only),
expiry time, download limit, and "protect" (recipients cannot forward or save).

Public side: anyone opening a share link, or typing one of the share's keywords to the bot in
private chat (case-insensitive, exact match), gets the files in chat. The Allowlist
middleware lets only "/start f_<code>" through for non-operators.
Files are stored as Telegram file_ids (nothing is downloaded to the server), so they
only work with the same bot token.
"""
import asyncio
import html
import logging
import secrets
from datetime import datetime, timedelta

from aiogram import Bot, F, Router, types
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select, text

from db import session
from models import BroadcastUser, Channel

logger = logging.getLogger(__name__)
router = Router()

MAX_FILES = 30
EXPIRY = [("Never", None), ("1 hour", 3600), ("24 hours", 86400), ("7 days", 604800), ("30 days", 2592000)]
LIMITS = [("Unlimited", None), ("1 download", 1), ("5 downloads", 5), ("10 downloads", 10),
          ("50 downloads", 50), ("100 downloads", 100)]
KINDS = ("document", "photo", "video", "audio", "voice", "animation", "video_note")
_tables_ready = False


class FS(StatesGroup):
    collecting = State()
    ids = State()
    kw = State()
    kwedit = State()


# --------------------------------------------------------------------------- db
async def _ensure_tables() -> None:
    global _tables_ready
    if _tables_ready:
        return
    async with session() as s:
        await s.execute(text(
            "CREATE TABLE IF NOT EXISTS shared_files ("
            "code TEXT PRIMARY KEY, owner_id INTEGER, created_at TEXT, expires_at TEXT, "
            "max_downloads INTEGER, downloads INTEGER DEFAULT 0, members_only INTEGER DEFAULT 0, "
            "protect INTEGER DEFAULT 0, active INTEGER DEFAULT 1)"))
        await s.execute(text(
            "CREATE TABLE IF NOT EXISTS shared_file_items ("
            "code TEXT, pos INTEGER, kind TEXT, file_id TEXT, caption TEXT, name TEXT, "
            "PRIMARY KEY (code, pos))"))
        await s.execute(text(
            "CREATE TABLE IF NOT EXISTS shared_file_log ("
            "code TEXT, user_id INTEGER, at TEXT, PRIMARY KEY (code, user_id))"))
        await s.commit()
    _tables_ready = True


def _now() -> datetime:
    return datetime.utcnow()


async def _get_share(code: str):
    async with session() as s:
        row = (await s.execute(text(
            "SELECT code, owner_id, created_at, expires_at, max_downloads, downloads, members_only, protect, active "
            "FROM shared_files WHERE code = :c"), {"c": code})).first()
    return row


async def _get_items(code: str) -> list[dict]:
    async with session() as s:
        rows = (await s.execute(text(
            "SELECT kind, file_id, caption, name FROM shared_file_items WHERE code = :c ORDER BY pos"),
            {"c": code})).all()
    return [{"kind": r[0], "file_id": r[1], "caption": r[2], "name": r[3]} for r in rows]


async def _create_share(owner_id: int, items: list[dict], members_only: bool, exp_idx: int, lim_idx: int,
                        protect: bool) -> str:
    code = secrets.token_urlsafe(6)
    expires = None
    if EXPIRY[exp_idx][1]:
        expires = (_now() + timedelta(seconds=EXPIRY[exp_idx][1])).isoformat()
    async with session() as s:
        await s.execute(text(
            "INSERT INTO shared_files (code, owner_id, created_at, expires_at, max_downloads, downloads, "
            "members_only, protect, active) VALUES (:c, :o, :t, :e, :m, 0, :mo, :p, 1)"),
            {"c": code, "o": owner_id, "t": _now().isoformat(), "e": expires, "m": LIMITS[lim_idx][1],
             "mo": 1 if members_only else 0, "p": 1 if protect else 0})
        for i, it in enumerate(items):
            await s.execute(text(
                "INSERT INTO shared_file_items (code, pos, kind, file_id, caption, name) "
                "VALUES (:c, :p, :k, :f, :cap, :n)"),
                {"c": code, "p": i, "k": it["kind"], "f": it["file_id"], "cap": it.get("caption"),
                 "n": it.get("name")})
        await s.commit()
    return code


# --------------------------------------------------------------------------- helpers
def _extract(message: types.Message) -> dict | None:
    cap = message.caption
    if message.document:
        return {"kind": "document", "file_id": message.document.file_id, "caption": cap,
                "name": message.document.file_name or "document"}
    if message.photo:
        return {"kind": "photo", "file_id": message.photo[-1].file_id, "caption": cap, "name": "photo"}
    if message.video:
        return {"kind": "video", "file_id": message.video.file_id, "caption": cap,
                "name": message.video.file_name or "video"}
    if message.audio:
        return {"kind": "audio", "file_id": message.audio.file_id, "caption": cap,
                "name": message.audio.file_name or message.audio.title or "audio"}
    if message.voice:
        return {"kind": "voice", "file_id": message.voice.file_id, "caption": cap, "name": "voice"}
    if message.animation:
        return {"kind": "animation", "file_id": message.animation.file_id, "caption": cap, "name": "gif"}
    if message.video_note:
        return {"kind": "video_note", "file_id": message.video_note.file_id, "caption": None, "name": "video note"}
    return None


async def _send_items(bot: Bot, chat_id: int, items: list[dict], protect: bool) -> None:
    for it in items:
        kind = it["kind"]
        kwargs = {"chat_id": chat_id, kind: it["file_id"], "protect_content": protect}
        if kind != "video_note" and it.get("caption"):
            kwargs["caption"] = it["caption"]
        await getattr(bot, "send_" + kind)(**kwargs)
        await asyncio.sleep(0.05)


async def _link(bot: Bot, code: str) -> str:
    me = await bot.get_me()
    return f"https://t.me/{me.username}?start=f_{code}"


async def _is_member_somewhere(bot: Bot, user_id: int) -> bool:
    async with session() as s:
        chat_ids = [r[0] for r in (await s.execute(select(Channel.chat_id))).all()]
    if not chat_ids:
        return True  # nothing registered to check against
    for chat_id in chat_ids:
        try:
            m = await bot.get_chat_member(chat_id, user_id)
        except Exception:
            continue
        if m.status in ("member", "administrator", "creator") or (m.status == "restricted" and getattr(m, "is_member", False)):
            return True
    return False


def _status_line(row) -> str:
    _, _, _, expires, max_dl, downloads, members_only, protect, active = row
    parts = []
    if not active:
        parts.append("⏸ disabled")
    if expires:
        try:
            left = datetime.fromisoformat(expires) - _now()
            parts.append("⌛ expired" if left.total_seconds() <= 0 else f"⌛ {int(left.total_seconds() // 3600)}h left")
        except Exception:
            pass
    parts.append(f"⬇ {downloads or 0}/{max_dl}" if max_dl else f"⬇ {downloads or 0}")
    if members_only:
        parts.append("👥 members only")
    if protect:
        parts.append("🔒 protected")
    return " · ".join(parts)



def _norm(t: str) -> str:
    return " ".join((t or "").lower().split())


def _parse_keywords(raw: str) -> list[str]:
    out = []
    for part in raw.replace("\n", ",").split(","):
        k = _norm(part)
        if 2 <= len(k) <= 64 and not k.startswith("/") and k not in out:
            out.append(k)
    return out


async def keyword_code(text_: str) -> str | None:
    """Share code for a typed keyword, or None. Used by the middleware and the chat handler."""
    await _ensure_tables()
    k = _norm(text_)
    if not k or len(k) > 64:
        return None
    async with session() as s:
        row = (await s.execute(text("SELECT code FROM shared_keywords WHERE keyword = :k"), {"k": k})).first()
    return row[0] if row else None


async def _get_keywords(code: str) -> list[str]:
    async with session() as s:
        rows = (await s.execute(text("SELECT keyword FROM shared_keywords WHERE code = :c ORDER BY keyword"),
                                {"c": code})).all()
    return [r[0] for r in rows]


async def _set_keywords(code: str, keywords: list[str]) -> list[str]:
    """Replace the share's keywords. Returns keywords skipped because another share already uses them."""
    taken = []
    async with session() as s:
        await s.execute(text("DELETE FROM shared_keywords WHERE code = :c"), {"c": code})
        for k in keywords:
            row = (await s.execute(text("SELECT code FROM shared_keywords WHERE keyword = :k"), {"k": k})).first()
            if row:
                taken.append(k)
                continue
            await s.execute(text("INSERT INTO shared_keywords (keyword, code) VALUES (:k, :c)"), {"k": k, "c": code})
        await s.commit()
    return taken


# --------------------------------------------------------------------------- public: open a link
@router.message(CommandStart(deep_link=True, magic=F.args.startswith("f_")))
async def open_share(message: types.Message, command, state: FSMContext, bot: Bot):
    await _ensure_tables()
    await state.clear()
    await _serve(message, (command.args or "")[2:], bot)


async def _keyword_filter(message: types.Message):
    code = await keyword_code(message.text or "")
    return {"kw_code": code} if code else False


@router.message(StateFilter(None), F.chat.type == "private", F.text, ~F.text.startswith("/"), _keyword_filter)
async def keyword_message(message: types.Message, bot: Bot, kw_code: str):
    """Typing a share's keyword in private chat delivers its files."""
    await _serve(message, kw_code, bot)


async def _serve(message: types.Message, code: str, bot: Bot):
    row = await _get_share(code)
    if not row:
        await message.answer("❌ This link is not valid.")
        return
    _, owner_id, _, expires, max_dl, downloads, members_only, protect, active = row
    uid = message.from_user.id
    try:
        from handlers.broadcast import record_broadcast_user
        await record_broadcast_user(message.from_user)
    except Exception:
        logger.exception("file-share: could not record user")
    if not active:
        await message.answer("⏸ This file is not available right now.")
        return
    if expires and datetime.fromisoformat(expires) <= _now():
        await message.answer("⌛ This link has expired.")
        return
    async with session() as s:
        already = (await s.execute(text("SELECT 1 FROM shared_file_log WHERE code = :c AND user_id = :u"),
                                   {"c": code, "u": uid})).first()
    if members_only and not await _is_member_somewhere(bot, uid):
        await message.answer("👥 These files are for members of our channels only. Join one, then open the link again.")
        return
    if not already:
        async with session() as s:
            res = await s.execute(text(
                "UPDATE shared_files SET downloads = downloads + 1 WHERE code = :c AND active = 1 "
                "AND (max_downloads IS NULL OR downloads < max_downloads)"), {"c": code})
            await s.commit()
        if res.rowcount == 0:
            await message.answer("🚫 This link has reached its download limit.")
            return
        async with session() as s:
            await s.execute(text("INSERT INTO shared_file_log (code, user_id, at) VALUES (:c, :u, :t)"),
                            {"c": code, "u": uid, "t": _now().isoformat()})
            await s.commit()
    items = await _get_items(code)
    try:
        await _send_items(bot, message.chat.id, items, bool(protect))
    except Exception:
        logger.exception("file-share: send failed")
        await message.answer("⚠️ Could not send the files. Please try again later.")


# --------------------------------------------------------------------------- operator: menus
def _root_kb() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="➕ New share", callback_data="fs:new")],
        [types.InlineKeyboardButton(text="📋 My shares", callback_data="fs:list")],
        [types.InlineKeyboardButton(text="🔙 Back", callback_data="menu:main")],
    ])


ROOT_TEXT = ("━━━━━━━━━━━━━━━━━━━━━\n📁 FILE SHARING\n━━━━━━━━━━━━━━━━━━━━━\n\n"
             "Upload files, get a share link, or send them straight to people.\n"
             "Per share you can limit who opens it, set an expiry, a download limit, "
             "and stop forwarding/saving.")


@router.message(Command("files"))
async def files_cmd(message: types.Message, state: FSMContext):
    await _ensure_tables()
    await state.clear()
    await message.answer(ROOT_TEXT, reply_markup=_root_kb())


@router.callback_query(F.data == "fs:root")
async def files_root(query: types.CallbackQuery, state: FSMContext):
    await _ensure_tables()
    await state.clear()
    await query.message.edit_text(ROOT_TEXT, reply_markup=_root_kb())
    await query.answer()


@router.callback_query(F.data == "fs:new")
async def new_share(query: types.CallbackQuery, state: FSMContext):
    await state.set_state(FS.collecting)
    await state.update_data(items=[], members=False, exp=0, lim=0, protect=False)
    await query.message.edit_text(
        "📤 Send me the files now (documents, photos, videos, audio, voice). "
        f"Up to {MAX_FILES} per share. Captions are kept.\n\nTap Done when finished.",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="✅ Done", callback_data="fs:done")],
            [types.InlineKeyboardButton(text="❌ Cancel", callback_data="fs:root")],
        ]))
    await query.answer()


@router.message(FS.collecting, F.document | F.photo | F.video | F.audio | F.voice | F.animation | F.video_note)
async def collect(message: types.Message, state: FSMContext):
    data = await state.get_data()
    items = data.get("items", [])
    if len(items) >= MAX_FILES:
        await message.answer(f"Limit of {MAX_FILES} files reached - tap Done.")
        return
    item = _extract(message)
    if item:
        items.append(item)
        await state.update_data(items=items)
        await message.answer(f"➕ Added ({len(items)}): {html.escape(item['name'])}\nSend more, or tap ✅ Done.",
                             reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                                 [types.InlineKeyboardButton(text="✅ Done", callback_data="fs:done")]]))


def _settings_view(data: dict):
    items = data.get("items", [])
    txt = (f"📁 {len(items)} file(s) ready.\n\nChoose the rules, then tap Create link.\n"
           "(Tap a rule to change it.)")
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(
            text="👥 Members of my channels only" if data.get("members") else "🌍 Anyone with the link",
            callback_data="fs:acc")],
        [types.InlineKeyboardButton(text=f"⌛ Expires: {EXPIRY[data.get('exp', 0)][0]}", callback_data="fs:exp")],
        [types.InlineKeyboardButton(text=f"⬇ Limit: {LIMITS[data.get('lim', 0)][0]}", callback_data="fs:lim")],
        [types.InlineKeyboardButton(
            text="🔒 Protected (no forward/save)" if data.get("protect") else "🔓 Forwarding allowed",
            callback_data="fs:prot")],
        [types.InlineKeyboardButton(
            text="🔑 Keywords: " + (", ".join(data["keywords"])[:40] if data.get("keywords") else "none (tap to add)"),
            callback_data="fs:kw")],
        [types.InlineKeyboardButton(text="✅ Create link", callback_data="fs:make")],
        [types.InlineKeyboardButton(text="❌ Cancel", callback_data="fs:root")],
    ])
    return txt, kb


@router.callback_query(FS.collecting, F.data == "fs:done")
async def done(query: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("items"):
        await query.answer("Send at least one file first", show_alert=True)
        return
    txt, kb = _settings_view(data)
    await query.message.answer(txt, reply_markup=kb)
    await query.answer()


@router.callback_query(FS.collecting, F.data.in_({"fs:acc", "fs:exp", "fs:lim", "fs:prot"}))
async def toggle(query: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    k = query.data
    if k == "fs:acc":
        data["members"] = not data.get("members")
    elif k == "fs:exp":
        data["exp"] = (data.get("exp", 0) + 1) % len(EXPIRY)
    elif k == "fs:lim":
        data["lim"] = (data.get("lim", 0) + 1) % len(LIMITS)
    else:
        data["protect"] = not data.get("protect")
    await state.update_data(**{x: data[x] for x in ("members", "exp", "lim", "protect") if x in data})
    txt, kb = _settings_view(data)
    await query.message.edit_text(txt, reply_markup=kb)
    await query.answer()


@router.callback_query(FS.collecting, F.data == "fs:kw")
async def kw_start(query: types.CallbackQuery, state: FSMContext):
    await state.set_state(FS.kw)
    await query.message.answer("🔑 Type the keywords, separated by commas (e.g. <code>course, notes, free pdf</code>).\n"
                               "Anyone who types one of them to the bot gets these files. Send <code>-</code> for none.")
    await query.answer()


@router.message(FS.kw, F.text, ~F.text.startswith("/"))
async def kw_save(message: types.Message, state: FSMContext):
    kws = [] if message.text.strip() == "-" else _parse_keywords(message.text)
    await state.update_data(keywords=kws)
    await state.set_state(FS.collecting)
    txt, kb = _settings_view(await state.get_data())
    await message.answer(txt, reply_markup=kb)



def _share_kb(code: str) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="📤 Send to people", callback_data=f"fs:send:{code}")],
        [types.InlineKeyboardButton(text="📋 My shares", callback_data="fs:list"),
         types.InlineKeyboardButton(text="🔙 Back", callback_data="fs:root")],
    ])


@router.callback_query(FS.collecting, F.data == "fs:make")
async def make(query: types.CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    items = data.get("items", [])
    if not items:
        await query.answer("No files", show_alert=True)
        return
    code = await _create_share(query.from_user.id, items, bool(data.get("members")), data.get("exp", 0),
                               data.get("lim", 0), bool(data.get("protect")))
    await state.clear()
    taken = await _set_keywords(code, data.get("keywords", []))
    kws = await _get_keywords(code)
    link = await _link(bot, code)
    row = await _get_share(code)
    extra = ("\n🔑 Keywords: " + ", ".join(kws)) if kws else ""
    if taken:
        extra += "\n⚠️ Already used by another share (skipped): " + ", ".join(taken)
    await query.message.edit_text(
        f"✅ Share created ({len(items)} file(s))\n\n🔗 {link}{extra}\n\n{_status_line(row)}\n\n"
        "Anyone with the link, or who types a keyword to the bot, gets the files.",
        reply_markup=_share_kb(code), disable_web_page_preview=True)
    await query.answer()


# --------------------------------------------------------------------------- operator: list / manage
@router.callback_query(F.data == "fs:list")
async def list_shares(query: types.CallbackQuery, state: FSMContext):
    await _ensure_tables()
    await state.clear()
    async with session() as s:
        rows = (await s.execute(text(
            "SELECT f.code, f.downloads, f.active, (SELECT COUNT(*) FROM shared_file_items i WHERE i.code = f.code) "
            "FROM shared_files f ORDER BY f.created_at DESC LIMIT 15"))).all()
    if not rows:
        await query.message.edit_text("No shares yet.", reply_markup=_root_kb())
        await query.answer()
        return
    kb = [[types.InlineKeyboardButton(
        text=f"{'⏸ ' if not r[2] else ''}{r[0]} · {r[3]} file(s) · ⬇{r[1] or 0}", callback_data=f"fs:v:{r[0]}")]
        for r in rows]
    kb.append([types.InlineKeyboardButton(text="🔙 Back", callback_data="fs:root")])
    await query.message.edit_text("📋 Your latest shares:", reply_markup=types.InlineKeyboardMarkup(inline_keyboard=kb))
    await query.answer()


@router.callback_query(F.data.startswith("fs:v:"))
async def view_share(query: types.CallbackQuery, bot: Bot):
    code = query.data[5:]
    row = await _get_share(code)
    if not row:
        await query.answer("Not found", show_alert=True)
        return
    items = await _get_items(code)
    names = "\n".join(f"• {html.escape(i['name'] or i['kind'])}" for i in items[:10])
    more = f"\n… +{len(items) - 10} more" if len(items) > 10 else ""
    link = await _link(bot, code)
    kws = await _get_keywords(code)
    kwline = ("\n🔑 " + ", ".join(kws)) if kws else "\n🔑 no keywords"
    toggle_txt = "▶️ Enable" if not row[8] else "⏸ Disable"
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="📤 Send to people", callback_data=f"fs:send:{code}")],
        [types.InlineKeyboardButton(text="🔑 Edit keywords", callback_data=f"fs:ke:{code}")],
        [types.InlineKeyboardButton(text=toggle_txt, callback_data=f"fs:tg:{code}"),
         types.InlineKeyboardButton(text="🗑 Delete", callback_data=f"fs:del:{code}")],
        [types.InlineKeyboardButton(text="🔙 Back", callback_data="fs:list")],
    ])
    await query.message.edit_text(f"📁 Share {code}\n🔗 {link}{kwline}\n{_status_line(row)}\n\n{names}{more}",
                                  reply_markup=kb, disable_web_page_preview=True)
    await query.answer()


@router.callback_query(F.data.startswith("fs:tg:"))
async def toggle_active(query: types.CallbackQuery, bot: Bot):
    code = query.data[6:]
    async with session() as s:
        await s.execute(text("UPDATE shared_files SET active = 1 - COALESCE(active, 1) WHERE code = :c"), {"c": code})
        await s.commit()
    query.data = f"fs:v:{code}"
    await view_share(query, bot)


@router.callback_query(F.data.startswith("fs:ke:"))
async def kw_edit_start(query: types.CallbackQuery, state: FSMContext):
    code = query.data[6:]
    await state.set_state(FS.kwedit)
    await state.update_data(code=code)
    cur = ", ".join(await _get_keywords(code)) or "none"
    await query.message.edit_text(f"🔑 Current keywords: {html.escape(cur)}\n\nType the new list, separated by commas "
                                  "(replaces the old one). Send <code>-</code> to remove all. /cancel to go back.")
    await query.answer()


@router.message(FS.kwedit, F.text, ~F.text.startswith("/"))
async def kw_edit_save(message: types.Message, state: FSMContext):
    code = (await state.get_data()).get("code")
    await state.clear()
    kws = [] if message.text.strip() == "-" else _parse_keywords(message.text)
    taken = await _set_keywords(code, kws)
    now = ", ".join(await _get_keywords(code)) or "none"
    msg = f"✅ Keywords: {html.escape(now)}"
    if taken:
        msg += "\n⚠️ Already used by another share (skipped): " + html.escape(", ".join(taken))
    await message.answer(msg, reply_markup=_root_kb())


@router.callback_query(F.data.startswith("fs:del:"))
async def delete_share(query: types.CallbackQuery):
    code = query.data[7:]
    async with session() as s:
        for t in ("shared_file_items", "shared_file_log", "shared_keywords", "shared_files"):
            await s.execute(text(f"DELETE FROM {t} WHERE code = :c"), {"c": code})
        await s.commit()
    await query.message.edit_text("🗑 Share deleted. Its link no longer works.", reply_markup=_root_kb())
    await query.answer()


# --------------------------------------------------------------------------- operator: send to people
@router.callback_query(F.data.startswith("fs:send:"))
async def send_menu(query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    code = query.data[8:]
    async with session() as s:
        n = (await s.execute(text("SELECT COUNT(*) FROM broadcast_users"))).scalar() or 0
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text=f"📢 Everyone in my audience ({n})", callback_data=f"fs:sa:{code}")],
        [types.InlineKeyboardButton(text="👤 Specific user IDs", callback_data=f"fs:si:{code}")],
        [types.InlineKeyboardButton(text="🔙 Back", callback_data=f"fs:v:{code}")],
    ])
    await query.message.edit_text(
        "Who should get the files?\n\nThe audience is everyone who requested to join your channels or opened a "
        "share link. (Telegram only lets a bot message people who have talked to it.)", reply_markup=kb)
    await query.answer()


@router.callback_query(F.data.startswith("fs:sa:"))
async def send_all_confirm(query: types.CallbackQuery):
    code = query.data[6:]
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="✅ Send now", callback_data=f"fs:sc:{code}")],
        [types.InlineKeyboardButton(text="❌ Cancel", callback_data=f"fs:v:{code}")],
    ])
    await query.message.edit_text("Send these files to your whole audience now?", reply_markup=kb)
    await query.answer()


@router.callback_query(F.data.startswith("fs:sc:"))
async def send_all(query: types.CallbackQuery, bot: Bot):
    code = query.data[6:]
    async with session() as s:
        ids = [r[0] for r in (await s.execute(select(BroadcastUser.user_id))).all()]
    await query.message.edit_text("📤 Sending in the background - I'll message you when it's done.")
    await query.answer()
    asyncio.create_task(_deliver(bot, query.from_user.id, code, ids))


@router.callback_query(F.data.startswith("fs:si:"))
async def send_ids_start(query: types.CallbackQuery, state: FSMContext):
    code = query.data[6:]
    await state.set_state(FS.ids)
    await state.update_data(code=code)
    await query.message.edit_text("Send the Telegram user IDs, separated by spaces, commas or new lines.\n"
                                  "(They must have started this bot.)\n\n/cancel to go back.")
    await query.answer()


@router.message(FS.ids, F.text, ~F.text.startswith("/"))
async def send_ids(message: types.Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    code = data.get("code")
    ids = []
    for tok in message.text.replace(",", " ").split():
        if tok.lstrip("-").isdigit():
            ids.append(int(tok))
    await state.clear()
    if not ids:
        await message.answer("No valid IDs found.", reply_markup=_root_kb())
        return
    await message.answer(f"📤 Sending to {len(ids)} user(s)…")
    asyncio.create_task(_deliver(bot, message.from_user.id, code, ids))


async def _deliver(bot: Bot, operator_id: int, code: str, user_ids: list[int]) -> None:
    row = await _get_share(code)
    items = await _get_items(code)
    if not row or not items:
        await bot.send_message(operator_id, "❌ That share no longer exists.")
        return
    protect = bool(row[7])
    sent = failed = 0
    for uid in user_ids:
        try:
            await _send_items(bot, uid, items, protect)
            sent += 1
            async with session() as s:
                await s.execute(text("DELETE FROM shared_file_log WHERE code = :c AND user_id = :u"), {"c": code, "u": uid})
                await s.execute(text("INSERT INTO shared_file_log (code, user_id, at) VALUES (:c, :u, :t)"),
                                {"c": code, "u": uid, "t": _now().isoformat()})
                await s.commit()
        except Exception as exc:
            failed += 1
            if exc.__class__.__name__ == "TelegramRetryAfter":
                await asyncio.sleep(getattr(exc, "retry_after", 2))
        await asyncio.sleep(0.1)
    try:
        await bot.send_message(operator_id, f"📤 Done.\n✅ Sent: {sent}\n❌ Failed: {failed} (blocked the bot or never started it)")
    except Exception:
        logger.exception("file-share: could not send summary")
