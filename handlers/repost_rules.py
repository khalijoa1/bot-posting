"""Manage rules that repost messages from a watched source channel into a
destination channel the bot posts into, including per-rule link
replacement (swap the source channel's links for your own).

Requires a source added via handlers/sources.py and the Telethon userbot
configured (see services/telethon_client.py) to actually detect new source
posts. The original /add_rule /list_rules /remove_rule commands are kept
for backward compatibility; the inline flow below (reachable from
📡 Forwarding -> a source -> ➕ Add Rule) is the easy path - pick the
destination from your registered channels instead of typing a chat id, and
set link replacements by just sending "old -> new" lines.
"""
import json

from aiogram import Router, types, F
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from db import session
from handlers.common import main_menu_kb
from models import Channel, RepostRule, SourceChannel

router = Router()


class RuleUIState(StatesGroup):
    replacements = State()


def _cancel_kb():
    return types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text="❌ Cancel")]],
        resize_keyboard=True,
    )


def _parse_replacements(text: str) -> dict[str, str]:
    """Parse lines like 'https://t.me/source -> https://t.me/mine' (also
    accepts '=' or '|' as the separator) into an {old: new} dict. Blank
    lines and lines without a separator are skipped rather than raising,
    so one typo doesn't nuke every other line the user typed."""
    mapping: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        for sep in ("->", "=>", "|", "="):
            if sep in line:
                old, new = line.split(sep, 1)
                old, new = old.strip(), new.strip()
                if old and new:
                    mapping[old] = new
                break
    return mapping


async def _rule_detail_view(rule_id: int) -> tuple[str, types.InlineKeyboardMarkup] | None:
    async with session() as s:
        rule = await s.get(
            RepostRule, rule_id,
            options=[selectinload(RepostRule.source), selectinload(RepostRule.destination)],
        )
        if not rule:
            return None
        source_id = rule.source_channel_id
        source_label = rule.source.title or rule.source.identifier if rule.source else "?"
        dest_label = rule.destination.title if rule.destination else "(channel removed)"
        try:
            repls = json.loads(rule.replacements_json) if rule.replacements_json else {}
        except Exception:
            repls = {}
        mapping = repls.get("default", {}) if isinstance(repls, dict) else {}

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"➡️ {source_label}  →  {dest_label}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n",
    ]
    if mapping:
        lines.append("Link replacements:")
        for old, new in mapping.items():
            lines.append(f"  {old}\n  → {new}")
    else:
        lines.append(
            "No link replacements set - posts forward with their original "
            "links untouched. Tap ✏️ Edit Links to add some."
        )

    rows = [
        [types.InlineKeyboardButton(text="✏️ Edit Link Replacements", callback_data=f"fwd:editrepl:{rule_id}")],
        [types.InlineKeyboardButton(text="🗑️ Remove Rule", callback_data=f"fwd:delrule:{rule_id}")],
        [types.InlineKeyboardButton(text="🔙 Back", callback_data=f"fwd:src:{source_id}")],
    ]
    return "\n".join(lines), types.InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("fwd:rule:"))
async def cb_rule_detail(query: types.CallbackQuery):
    rule_id = int(query.data.split(":")[2])
    result = await _rule_detail_view(rule_id)
    if not result:
        await query.answer("Rule not found", show_alert=True)
        return
    text, kb = result
    await query.message.edit_text(text, reply_markup=kb)
    await query.answer()


@router.callback_query(F.data.startswith("fwd:addrule:"))
async def cb_add_rule_pick_dest(query: types.CallbackQuery):
    """Show every registered channel as a destination pick for this source."""
    source_id = int(query.data.split(":")[2])

    async with session() as s:
        source = await s.get(SourceChannel, source_id)
        if not source:
            await query.answer("Source not found", show_alert=True)
            return
        q = select(Channel)
        res = await s.execute(q)
        channels = res.scalars().all()
        existing_q = select(RepostRule.destination_channel_id).where(RepostRule.source_channel_id == source_id)
        existing_res = await s.execute(existing_q)
        already = {row[0] for row in existing_res.all()}

    if not channels:
        await query.answer("Add a destination channel first (📍 Channels)", show_alert=True)
        return

    available = [ch for ch in channels if ch.id not in already]
    if not available:
        await query.answer("Already forwarding into every registered channel", show_alert=True)
        return

    rows = [
        [types.InlineKeyboardButton(text=f"📍 {ch.title}", callback_data=f"fwd:pickdest:{source_id}:{ch.id}")]
        for ch in available
    ]
    rows.append([types.InlineKeyboardButton(text="🔙 Back", callback_data=f"fwd:src:{source_id}")])

    await query.message.edit_text(
        f"➡️ Pick a channel for {source.title or source.identifier} to forward into:",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await query.answer()


@router.callback_query(F.data.startswith("fwd:pickdest:"))
async def cb_add_rule_create(query: types.CallbackQuery):
    _, _, source_id_s, dest_id_s = query.data.split(":")
    source_id, dest_id = int(source_id_s), int(dest_id_s)

    async with session() as s:
        existing_q = select(RepostRule).where(
            RepostRule.source_channel_id == source_id, RepostRule.destination_channel_id == dest_id
        )
        existing_res = await s.execute(existing_q)
        if existing_res.scalars().first():
            await query.answer("That rule already exists", show_alert=True)
            return
        rule = RepostRule(
            source_channel_id=source_id,
            destination_channel_id=dest_id,
            replacements_json=json.dumps({"default": {}}),
        )
        s.add(rule)
        await s.commit()
        rule_id = rule.id

    await query.answer("Rule added")
    text, kb = await _rule_detail_view(rule_id)
    await query.message.edit_text(text, reply_markup=kb)


@router.callback_query(F.data.startswith("fwd:delrule:"))
async def cb_delete_rule(query: types.CallbackQuery):
    rule_id = int(query.data.split(":")[2])
    async with session() as s:
        rule = await s.get(RepostRule, rule_id)
        source_id = rule.source_channel_id if rule else None
        if rule:
            await s.delete(rule)
            await s.commit()
    await query.answer("Rule removed")
    if source_id is not None:
        from handlers.sources import _source_detail_view
        result = await _source_detail_view(source_id)
        if result:
            text, kb = result
            await query.message.edit_text(text, reply_markup=kb)
            return
    from handlers.sources import show_forwarding_root
    await show_forwarding_root(query.message)


@router.callback_query(F.data.startswith("fwd:editrepl:"))
async def cb_edit_replacements_start(query: types.CallbackQuery, state: FSMContext):
    rule_id = int(query.data.split(":")[2])
    await state.update_data(rule_id=rule_id)
    await state.set_state(RuleUIState.replacements)
    await query.message.answer(
        "Send the link replacements, one per line, as:\n\n"
        "old_link -> your_link\n\n"
        "Example:\n"
        "https://t.me/sourcechannel -> https://t.me/mychannel\n"
        "https://t.me/sourcechannel/bot -> https://t.me/mychannel/bot\n\n"
        "This replaces every occurrence of the left side with the right side "
        "in each forwarded post before it's sent. Send 'clear' to remove all "
        "replacements for this rule.",
        reply_markup=_cancel_kb(),
    )
    await query.answer()


@router.message(RuleUIState.replacements, F.text == "❌ Cancel")
async def cancel_edit_replacements(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Cancelled", reply_markup=main_menu_kb())


@router.message(RuleUIState.replacements, F.text)
async def apply_replacements(message: types.Message, state: FSMContext):
    data = await state.get_data()
    rule_id = data.get("rule_id")
    raw = message.text.strip()

    mapping = {} if raw.lower() == "clear" else _parse_replacements(raw)
    if raw.lower() != "clear" and not mapping:
        await message.answer(
            "❌ Couldn't find any 'old -> new' pairs in that. Try again, one per line, "
            "or send 'clear' to remove all replacements."
        )
        return

    async with session() as s:
        rule = await s.get(RepostRule, rule_id)
        if not rule:
            await message.answer("❌ Rule not found (it may have been removed)", reply_markup=main_menu_kb())
            await state.clear()
            return
        try:
            repls = json.loads(rule.replacements_json) if rule.replacements_json else {}
            if not isinstance(repls, dict):
                repls = {}
        except Exception:
            repls = {}
        repls["default"] = mapping
        rule.replacements_json = json.dumps(repls)
        await s.commit()

    await state.clear()
    await message.answer(
        f"✅ Saved {len(mapping)} link replacement(s) for this rule." if mapping
        else "✅ Cleared link replacements for this rule."
    )
    text, kb = await _rule_detail_view(rule_id)
    await message.answer(text, reply_markup=kb)


# ---------------------------------------------------------------------------
# Backward-compatible text commands.
# ---------------------------------------------------------------------------

@router.message(Command("add_rule"))
async def add_rule(message: types.Message, command: CommandObject):
    """Usage: /add_rule <source_identifier_or_id> <destination_chat_id_or_channel_id> [auto_delete_seconds] [caption_template]
    Example: /add_rule @source -1001234567890 3600 From {source_title}: {original_text}

    Or use 📡 Forwarding in /start for a guided flow with a destination
    picker and a simple link-replacement editor instead of typing all this."""
    args = command.args
    if not args:
        await message.reply(
            "Usage: /add_rule SOURCE_ID_OR_IDENTIFIER DEST_CHAT_ID_OR_CHANNEL_ID "
            "[auto_delete_seconds] [caption_template]\n\n"
            "Or use 📡 Forwarding in /start for a guided flow."
        )
        return
    parts = args.split(None, 3)
    if len(parts) < 2:
        await message.reply("Need at least source and destination")
        return
    source_key = parts[0]
    dest_key = parts[1]
    auto_seconds = None
    caption_template = None
    if len(parts) >= 3:
        try:
            auto_seconds = int(parts[2])
        except ValueError:
            auto_seconds = None
    if len(parts) == 4:
        caption_template = parts[3]

    async with session() as s:
        try:
            sid = int(source_key)
            q = select(SourceChannel).where(SourceChannel.id == sid)
        except ValueError:
            q = select(SourceChannel).where(SourceChannel.identifier == source_key)
        res = await s.execute(q)
        source = res.scalars().first()
        if not source:
            await message.reply("Source not found; add it with /add_source first")
            return

        try:
            dval = int(dest_key)
            q2 = select(Channel).where((Channel.chat_id == dval) | (Channel.id == dval))
        except ValueError:
            await message.reply("destination must be numeric chat_id or channel id")
            return
        res2 = await s.execute(q2)
        dest = res2.scalars().first()
        if not dest:
            await message.reply("Destination channel not found; add it with /add_channel first")
            return

        rr = RepostRule(
            source_channel_id=source.id,
            destination_channel_id=dest.id,
            caption_template=caption_template,
            auto_delete_seconds=auto_seconds,
            replacements_json=json.dumps({"default": {}}),
        )
        s.add(rr)
        await s.commit()
    await message.reply(f"Added repost rule id={rr.id} source={source.identifier} -> dest={dest.chat_id}")


@router.message(Command("list_rules"))
async def list_rules(message: types.Message):
    async with session() as s:
        q = select(RepostRule)
        res = await s.execute(q)
        rows = res.scalars().all()
    if not rows:
        await message.reply("No repost rules")
        return
    lines = [
        f"ID={r.id} source_id={r.source_channel_id} dest_id={r.destination_channel_id} "
        f"auto_delete={r.auto_delete_seconds} template={r.caption_template}"
        for r in rows
    ]
    await message.reply("\n".join(lines))


@router.message(Command("remove_rule"))
async def remove_rule(message: types.Message, command: CommandObject):
    args = command.args
    if not args:
        await message.reply("Usage: /remove_rule RULE_ID")
        return
    try:
        rid = int(args.strip())
    except ValueError:
        await message.reply("rule_id must be numeric")
        return
    async with session() as s:
        q = select(RepostRule).where(RepostRule.id == rid)
        res = await s.execute(q)
        row = res.scalars().first()
        if not row:
            await message.reply("Rule not found")
            return
        await s.delete(row)
        await s.commit()
    await message.reply("Removed rule")
