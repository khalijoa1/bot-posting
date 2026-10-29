"""Link replacement for posts."""
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select

from db import session
from handlers.common import main_menu_kb
from models import Post

router = Router()


class ReplacerState(StatesGroup):
    select_mode = State()
    post_range = State()
    old_link = State()
    new_link = State()


@router.message(Command("replacer"))
async def replacer_start(message: types.Message, state: FSMContext):
    """Start link replacer."""
    await state.clear()

    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text="🔄 All Posts", callback_data="repl_all")],
            [types.InlineKeyboardButton(text="📋 Range (e.g. 1-5)", callback_data="repl_range")],
            [types.InlineKeyboardButton(text="📌 Single Post", callback_data="repl_single")],
            [types.InlineKeyboardButton(text="❌ Cancel", callback_data="repl_cancel")]
        ]
    )

    await message.answer(
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🔗 LINK REPLACER\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Replace links in:\n\n"
        "🔄 All Posts - All your posts\n"
        "📋 Range - Posts 5 to 10 (etc)\n"
        "📌 Single - Just one post",
        reply_markup=kb
    )
    await state.set_state(ReplacerState.select_mode)


@router.callback_query(ReplacerState.select_mode)
async def handle_mode(query: types.CallbackQuery, state: FSMContext):
    """Handle replacement mode."""
    if query.data == "repl_cancel":
        await state.clear()
        await query.message.answer("❌ Cancelled", reply_markup=main_menu_kb())
        await query.answer()
        return

    mode = query.data.replace("repl_", "")
    await state.update_data(mode=mode)

    if mode == "all":
        await state.update_data(post_ids="all")
        await ask_old_link(query.message, state)
    elif mode == "range":
        await query.message.answer(
            "📋 RANGE MODE\n\n"
            "Send range:\n\n"
            "Format: 1-5 (posts 1 to 5)\n"
            "Or: 10-15",
            reply_markup=types.ReplyKeyboardMarkup(
                keyboard=[[types.KeyboardButton(text="❌ Cancel")]],
                resize_keyboard=True
            )
        )
        await state.set_state(ReplacerState.post_range)
    elif mode == "single":
        await query.message.answer(
            "📌 SINGLE POST\n\n"
            "Send Post ID:\n\n"
            "Use /myposts to find ID",
            reply_markup=types.ReplyKeyboardMarkup(
                keyboard=[[types.KeyboardButton(text="❌ Cancel")]],
                resize_keyboard=True
            )
        )
        await state.set_state(ReplacerState.post_range)

    await query.answer()


@router.message(ReplacerState.post_range)
async def handle_range(message: types.Message, state: FSMContext):
    """Handle post range input."""
    if message.text == "❌ Cancel":
        await state.clear()
        await message.answer("❌ Cancelled", reply_markup=main_menu_kb())
        return

    data = await state.get_data()
    mode = data.get("mode")

    if mode == "range":
        try:
            parts = message.text.split("-")
            start = int(parts[0].strip())
            end = int(parts[1].strip())
            await state.update_data(post_range=(start, end))
        except Exception:
            await message.answer("❌ Invalid format. Use: 1-5")
            return
    else:  # single
        try:
            post_id = int(message.text.strip())
            await state.update_data(post_ids=[post_id])
        except Exception:
            await message.answer("❌ Invalid ID")
            return

    await ask_old_link(message, state)


async def ask_old_link(message: types.Message, state: FSMContext):
    """Ask for old link."""
    await message.answer(
        "Send the OLD LINK to replace:\n\n"
        "Example: https://old-url.com",
        reply_markup=types.ReplyKeyboardMarkup(
            keyboard=[[types.KeyboardButton(text="❌ Cancel")]],
            resize_keyboard=True
        )
    )
    await state.set_state(ReplacerState.old_link)


@router.message(ReplacerState.old_link)
async def handle_old_link(message: types.Message, state: FSMContext):
    """Handle old link."""
    if message.text == "❌ Cancel":
        await state.clear()
        await message.answer("❌ Cancelled", reply_markup=main_menu_kb())
        return

    old_link = message.text.strip()
    await state.update_data(old_link=old_link)

    await message.answer(
        "Send the NEW LINK:\n\n"
        "Example: https://new-url.com",
        reply_markup=types.ReplyKeyboardMarkup(
            keyboard=[[types.KeyboardButton(text="❌ Cancel")]],
            resize_keyboard=True
        )
    )
    await state.set_state(ReplacerState.new_link)


@router.message(ReplacerState.new_link)
async def handle_new_link(message: types.Message, state: FSMContext):
    """Process replacement."""
    if message.text == "❌ Cancel":
        await state.clear()
        await message.answer("❌ Cancelled", reply_markup=main_menu_kb())
        return

    data = await state.get_data()
    mode = data.get("mode")
    old_link = data.get("old_link")
    new_link = message.text.strip()

    async with session() as s:
        if mode == "all":
            q = select(Post).where(Post.owner_user_id == message.from_user.id)
            res = await s.execute(q)
            posts = res.scalars().all()
        elif mode == "range":
            start, end = data.get("post_range")
            q = select(Post).where(
                (Post.owner_user_id == message.from_user.id) &
                (Post.id >= start) &
                (Post.id <= end)
            )
            res = await s.execute(q)
            posts = res.scalars().all()
        else:  # single
            post_ids = data.get("post_ids")
            q = select(Post).where(
                (Post.owner_user_id == message.from_user.id) &
                (Post.id.in_(post_ids))
            )
            res = await s.execute(q)
            posts = res.scalars().all()

        updated = 0
        for post in posts:
            if old_link in (post.text or ""):
                post.text = (post.text or "").replace(old_link, new_link)
                s.add(post)
                updated += 1

        await s.commit()

    result = (
        f"✅ REPLACEMENT COMPLETE!\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"Posts Updated: {updated}\n"
        f"Old: {old_link[:30]}...\n"
        f"New: {new_link[:30]}...\n"
        f"━━━━━━━━━━━━━━━━━"
    )

    await message.answer(result, reply_markup=main_menu_kb())
    await state.clear()


@router.message(lambda msg: msg.text == "🔗 Link Replacer")
async def replacer_button(message: types.Message, state: FSMContext):
    """Link replacer from menu."""
    await replacer_start(message, state)



# ---------------------------------------------------------------------------
# /replace_link OLD NEW [yes] - swap a link inside every post, including the
# messages already live in your channels (the bot must be an admin there).
# ---------------------------------------------------------------------------
import asyncio as _asyncio
import logging as _logging
import re as _re

from aiogram.filters import Command as _Command
from sqlalchemy import or_ as _or
from sqlalchemy.orm import selectinload as _selectinload

_rl_log = _logging.getLogger("replace-link")


async def _rl_load(s, old):
    from models import PostTarget
    q = (
        select(Post)
        .where(_or(Post.text.contains(old, autoescape=True), Post.button_url.contains(old, autoescape=True)))
        .options(_selectinload(Post.targets).selectinload(PostTarget.channel))
    )
    return (await s.execute(q)).scalars().all()


@router.message(F.text.regexp(r"(?is)^\W{0,3}/?replace_link\b"))
async def replace_link_cmd(message: types.Message):
    from models import PostStatus
    parts = (message.text or "").split()[1:]
    if len(parts) not in (2, 3):
        await message.answer(
            "Usage:\n/replace_link OLD_LINK NEW_LINK\n\nIt first shows what it would change. "
            "To apply it, add yes at the end:\n/replace_link OLD_LINK NEW_LINK yes"
        )
        return
    old, new = parts[0], parts[1]
    apply = len(parts) == 3 and parts[2].lower() == "yes"
    if old.lower() == new.lower():
        await message.answer("The old and new links are the same.")
        return
    if not apply:
        async with session() as s:
            posts = await _rl_load(s, old)
            live = sum(1 for p in posts if p.status == PostStatus.SENT for t in p.targets if t.message_id is not None)
            nobtn = sum(1 for p in posts if p.status == PostStatus.SENT and not p.button_text
                        for t in p.targets if t.message_id is not None)
        await message.answer(
            f"Found {len(posts)} post(s) containing that link.\n"
            f"{live} live message(s) would be edited in your channels.\n"
            f"{nobtn} of them have no inline button saved - if one currently shows a button, editing may remove it.\n\n"
            f"To apply, send:\n/replace_link {old} {new} yes"
        )
        return
    try:
        from services.reposter import swap_rules_load, swap_rules_save
        _rules = dict(swap_rules_load())
        _rules[old] = new
        swap_rules_save(_rules)
    except Exception:
        _rl_log.exception("could not save the link rule")
    await message.answer("Started. Editing posts in your channels - I will message you when it is finished.")
    _asyncio.create_task(_rl_apply(message.bot, message.chat.id, old, new))


async def _rl_apply(bot, chat_id, old, new):
    from aiogram.exceptions import TelegramRetryAfter
    from models import ContentType, PostStatus
    from handlers.compose import _build_button
    pat = _re.compile(_re.escape(old), _re.IGNORECASE)
    stats = {"posts": 0, "edited": 0, "same": 0, "gone": 0, "failed": 0}
    try:
        async with session() as s:
            posts = await _rl_load(s, old)
            for post in posts:
                if post.text:
                    post.text = pat.sub(lambda m: new, post.text)
                if post.button_url:
                    post.button_url = pat.sub(lambda m: new, post.button_url)
                stats["posts"] += 1
                if post.status == PostStatus.SENT:
                    markup = _build_button(post.button_text, post.button_url)
                    for t in post.targets:
                        if t.message_id is None or t.channel is None:
                            continue
                        for _attempt in range(3):
                            try:
                                if post.content_type == ContentType.TEXT:
                                    await bot.edit_message_text(
                                        chat_id=t.channel.chat_id, message_id=t.message_id,
                                        text=post.text or "", reply_markup=markup)
                                else:
                                    await bot.edit_message_caption(
                                        chat_id=t.channel.chat_id, message_id=t.message_id,
                                        caption=post.text or None, reply_markup=markup)
                                stats["edited"] += 1
                                break
                            except TelegramRetryAfter as exc:
                                await _asyncio.sleep(min(exc.retry_after + 1, 60))
                            except Exception as exc:
                                m = str(exc).lower()
                                if "not modified" in m:
                                    stats["same"] += 1
                                elif "not found" in m or "can't be edited" in m:
                                    stats["gone"] += 1
                                else:
                                    stats["failed"] += 1
                                    _rl_log.warning("replace_link: post %s channel %s: %s", post.id, t.channel.chat_id, exc)
                                break
                        await _asyncio.sleep(0.6)
                await s.commit()
    except Exception:
        _rl_log.exception("replace_link failed")
        await bot.send_message(chat_id, "replace_link stopped because of an error - see the logs. Send the same command again; finished posts are already done.")
        return
    await bot.send_message(
        chat_id,
        f"Done. {stats['posts']} post(s) updated, {stats['edited']} channel message(s) edited, "
        f"{stats['same']} already up to date, {stats['gone']} no longer exist, {stats['failed']} failed.",
    )



# ---------------------------------------------------------------------------
# /check_channels - find channels that were deleted or that the bot can no
# longer post in (removed, or no longer an admin).
# ---------------------------------------------------------------------------
@router.message(F.text.regexp(r"(?is)^\W{0,3}/?check_channels\b"))
async def check_channels_cmd(message: types.Message):
    await message.answer("Checking every registered channel - this can take a minute...")
    _asyncio.create_task(_cc_run(message.bot, message.chat.id))


async def _cc_run(bot, chat_id):
    import html as _html
    from aiogram.exceptions import TelegramRetryAfter
    from models import Channel
    try:
        async with session() as s:
            chans = (await s.execute(select(Channel).order_by(Channel.id))).scalars().all()
        me = await bot.get_me()
        gone, noadmin, removed, ok = [], [], [], 0
        for ch in chans:
            for _attempt in range(3):
                try:
                    m = await bot.get_chat_member(ch.chat_id, me.id)
                    if getattr(m.status, "value", str(m.status)) in ("administrator", "creator"):
                        ok += 1
                    else:
                        noadmin.append((ch, getattr(m.status, "value", str(m.status))))
                    break
                except TelegramRetryAfter as exc:
                    await _asyncio.sleep(min(exc.retry_after + 1, 60))
                except Exception as exc:
                    _full = str(exc).lower()
                    _why = str(exc).split(":")[-1].strip()[:70]
                    if "chat not found" in _full or "deactivated" in _full:
                        removed.append((ch, _why))
                    else:
                        gone.append((ch, _why))
                    break
            await _asyncio.sleep(0.25)

        if removed:
            from sqlalchemy import delete as _sqldelete
            from models import PostTarget
            async with session() as s:
                for ch, _w in removed:
                    await s.execute(_sqldelete(PostTarget).where(PostTarget.channel_id == ch.id))
                    obj = await s.get(Channel, ch.id)
                    if obj is not None:
                        await s.delete(obj)
                await s.commit()

        def _line(ch, why):
            return f"ID {ch.id} - {_html.escape(ch.title or '?')} ({ch.chat_id}): {_html.escape(why)}"

        lines = [f"Checked {len(chans)} channel(s): {ok} fine, {len(removed)} deleted and removed from your list, {len(gone)} unreachable, {len(noadmin)} not admin."]
        if removed:
            lines.append("")
            lines.append("Deleted channels, now removed from the list:")
            lines += [_line(c, w) for c, w in removed]
        if gone:
            lines.append("")
            lines.append("DELETED or the bot was removed:")
            lines += [_line(c, w) for c, w in gone]
        if noadmin:
            lines.append("")
            lines.append("Bot is no longer an admin:")
            lines += [_line(c, w) for c, w in noadmin]
        if gone or noadmin:
            lines.append("")
            lines.append("To stop posting there, remove them with /delete_channel.")
        chunk = ""
        for ln in lines:
            if len(chunk) + len(ln) + 1 > 3500:
                await bot.send_message(chat_id, chunk)
                chunk = ""
            chunk += ln + "\n"
        if chunk:
            await bot.send_message(chat_id, chunk)
    except Exception:
        _rl_log.exception("check_channels failed")
        await bot.send_message(chat_id, "check_channels failed - see the logs.")



# ---------------------------------------------------------------------------
# Saved link rules: /swap_add, /swap_list, /swap_remove. Every saved rule is
# applied automatically to everything the repost/forward feature sends.
# Use /replace_link to also fix posts that are already live.
# ---------------------------------------------------------------------------
@router.message(F.text.regexp(r"(?is)^\W{0,3}/?swap_add\b"))
async def swap_add_cmd(message: types.Message):
    from services.reposter import swap_rules_load, swap_rules_save
    lines = (message.text or "").splitlines()
    rows = [lines[0].split()[1:]] + [ln.split() for ln in lines[1:]] if lines else []
    pairs = [(r[0], r[1]) for r in rows if len(r) >= 2]
    if not pairs:
        await message.answer(
            "Usage - one pair per line, OLD then NEW:\n"
            "/swap_add https://t.me/OldBot?start=start https://t.me/NewBot?start=start\n"
            "https://old.example.com https://new.example.com\n\n"
            "Saved rules apply automatically to everything forwarded from now on. "
            "To fix posts that are already live, also run /replace_link."
        )
        return
    rules = dict(swap_rules_load())
    for old, new in pairs:
        rules[old] = new
    swap_rules_save(rules)
    await message.answer(
        f"Saved {len(pairs)} rule(s). {len(rules)} rule(s) are active. "
        "Everything forwarded from now on uses them. For posts already live, run /replace_link OLD NEW."
    )


@router.message(F.text.regexp(r"(?is)^\W{0,3}/?swap_list\b"))
async def swap_list_cmd(message: types.Message):
    import html as _html
    from services.reposter import swap_rules_load
    rules = swap_rules_load()
    if not rules:
        await message.answer("No saved link rules yet. Add some with /swap_add. (The old Premiumvvip01bot link is always replaced.)")
        return
    out = ["Saved link rules (OLD -> NEW):"]
    for old, new in rules.items():
        out.append(_html.escape(old) + " -> " + _html.escape(new))
    chunk = ""
    for ln in out:
        if len(chunk) + len(ln) + 1 > 3500:
            await message.answer(chunk)
            chunk = ""
        chunk += ln + "\n"
    if chunk:
        await message.answer(chunk)


@router.message(F.text.regexp(r"(?is)^\W{0,3}/?swap_remove\b"))
async def swap_remove_cmd(message: types.Message):
    from services.reposter import swap_rules_load, swap_rules_save
    parts = (message.text or "").split()[1:]
    if not parts:
        await message.answer("Usage: /swap_remove OLD_LINK   (or /swap_remove all)")
        return
    rules = dict(swap_rules_load())
    if parts[0].lower() == "all":
        n = len(rules)
        rules = {}
    else:
        n = 1 if rules.pop(parts[0], None) is not None else 0
    swap_rules_save(rules)
    await message.answer(f"Removed {n} rule(s). {len(rules)} left.")



# ---------------------------------------------------------------------------
# /scan_links OLD NEW [yes] [limit] - find EVERY message in your channels that
# contains OLD (text, caption, hidden link or button) by reading the channel
# history with the userbot, and edit it through the bot (the bot must be admin
# and the message must have been sent by this bot). Real inline buttons are
# kept; their links are replaced too. Without "yes" it only counts.
# ---------------------------------------------------------------------------
@router.message(F.text.regexp(r"(?is)^\W{0,3}/?scan_links\b"))
async def scan_links_cmd(message: types.Message):
    parts = (message.text or "").split()[1:]
    if len(parts) < 2:
        await message.answer(
            "Usage:\n/scan_links OLD_LINK NEW_LINK        (counts only)\n"
            "/scan_links OLD_LINK NEW_LINK yes    (edits)\n"
            "Optional last number = how many recent messages to check per channel (default 500, max 5000)."
        )
        return
    old, new = parts[0], parts[1]
    apply = any(p.lower() == "yes" for p in parts[2:])
    limit = 500
    for p in parts[2:]:
        if p.isdigit():
            limit = max(1, min(int(p), 5000))
    if apply:
        try:
            from services.reposter import swap_rules_load, swap_rules_save
            _rules = dict(swap_rules_load())
            _rules[old] = new
            swap_rules_save(_rules)
        except Exception:
            _rl_log.exception("could not save the link rule")
    await message.answer(
        ("Editing" if apply else "Counting") + f" messages with that link in your channels (last {limit} per channel). "
        "This can take several minutes - I will message you when it is done."
    )
    _asyncio.create_task(_scan_links(message.bot, message.chat.id, old, new, apply, limit))


async def _scan_links(bot, chat_id, old, new, apply, limit):
    import html as _h
    from aiogram.exceptions import TelegramRetryAfter
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    from models import Channel
    pat = _re.compile(_re.escape(old), _re.IGNORECASE)
    pat_esc = _re.compile(_re.escape(_h.escape(old, quote=False)), _re.IGNORECASE)
    st = {"chans": 0, "unreachable": 0, "found": 0, "edited": 0, "notmine": 0, "skipped": 0, "failed": 0}
    try:
        from services.storage_channel import _get_client
        client = await _get_client()
        if client is None:
            await bot.send_message(chat_id, "The userbot account is not available, so I cannot read channel history. Nothing was changed.")
            return
        from telethon.extensions import html as tl_html
        async with session() as s:
            chans = (await s.execute(select(Channel).order_by(Channel.id))).scalars().all()
        for ch in chans:
            try:
                ent = await client.get_input_entity(ch.chat_id)
            except Exception:
                st["unreachable"] += 1
                continue
            st["chans"] += 1
            try:
                async for msg in client.iter_messages(ent, limit=limit):
                    raw = msg.message or ""
                    html_text = tl_html.unparse(raw, msg.entities or []) if raw else ""
                    rows, btn_hit, nonurl = [], False, False
                    mk = getattr(msg, "reply_markup", None)
                    for r in (getattr(mk, "rows", None) or []):
                        row = []
                        for b in r.buttons:
                            url = getattr(b, "url", None)
                            if url:
                                if pat.search(url):
                                    btn_hit = True
                                row.append((b.text, url))
                            else:
                                nonurl = True
                        rows.append(row)
                    text_hit = bool(html_text) and bool(pat.search(html_text) or pat_esc.search(html_text))
                    if not (text_hit or btn_hit):
                        continue
                    st["found"] += 1
                    if not apply:
                        continue
                    if nonurl:
                        st["skipped"] += 1
                        continue
                    new_html = pat.sub(lambda m: new, pat_esc.sub(lambda m: _h.escape(new, quote=False), html_text))
                    kb = [[InlineKeyboardButton(text=tx, url=pat.sub(lambda m: new, u)) for tx, u in row] for row in rows if row]
                    markup = InlineKeyboardMarkup(inline_keyboard=kb) if kb else None
                    is_media = bool(msg.media) and type(msg.media).__name__ != "MessageMediaWebPage"
                    for _attempt in range(3):
                        try:
                            if is_media:
                                await bot.edit_message_caption(chat_id=ch.chat_id, message_id=msg.id,
                                                               caption=new_html or None, parse_mode="HTML", reply_markup=markup)
                            else:
                                await bot.edit_message_text(chat_id=ch.chat_id, message_id=msg.id,
                                                            text=new_html, parse_mode="HTML", reply_markup=markup)
                            st["edited"] += 1
                            break
                        except TelegramRetryAfter as exc:
                            await _asyncio.sleep(min(exc.retry_after + 1, 60))
                        except Exception as exc:
                            m = str(exc).lower()
                            if "can't be edited" in m or "not found" in m or "not modified" in m:
                                st["notmine"] += 1
                            else:
                                st["failed"] += 1
                                _rl_log.warning("scan_links: channel %s message %s: %s", ch.chat_id, msg.id, exc)
                            break
                    await _asyncio.sleep(0.6)
            except Exception:
                _rl_log.exception("scan_links: channel %s failed", ch.chat_id)
    except Exception:
        _rl_log.exception("scan_links failed")
        await bot.send_message(chat_id, "scan_links stopped because of an error - see the logs.")
        return
    head = f"Checked {st['chans']} channel(s) ({st['unreachable']} could not be read by the userbot). Found {st['found']} message(s) with that link."
    if apply:
        body = (f"\nEdited {st['edited']}. Not editable (not sent by this bot) {st['notmine']}. "
                f"Skipped (has non-link buttons) {st['skipped']}. Failed {st['failed']}.")
    else:
        body = f"\nTo edit them, send:\n/scan_links {old} {new} yes"
    await bot.send_message(chat_id, head + body)
