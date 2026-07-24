"""Manage source channels watched by the Telethon userbot for reposting,
plus the inline-keyboard "Forwarding" UI (source list -> rules per source).

The original /add_source /list_sources /remove_source commands are kept
for backward compatibility, but the buttons below are the easy path: no
need to remember chat ids or command syntax, just tap through.
"""
from aiogram import Router, types, F
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from db import session
from handlers.common import main_menu_kb
from models import RepostRule, SourceChannel

router = Router()


class SourceUIState(StatesGroup):
    identifier = State()
    title = State()


def _cancel_kb():
    return types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text="❌ Cancel")]],
        resize_keyboard=True,
    )


async def _forwarding_view() -> tuple[str, types.InlineKeyboardMarkup]:
    """Build the (text, keyboard) pair for the Forwarding root screen: every
    watched source as a button, plus Add Source."""
    async with session() as s:
        q = select(SourceChannel)
        res = await s.execute(q)
        sources = res.scalars().all()

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📡 FORWARDING (repost from a channel)\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n",
        "Watch public channels you don't own and auto-repost their posts "
        "into your own channels, with links swapped for your own.\n",
    ]
    if not sources:
        lines.append("No sources yet - tap ➕ Add Source to watch your first channel.")
    else:
        lines.append(f"Sources watched: {len(sources)}\nTap one to manage its forwarding rules:")

    rows = [
        [types.InlineKeyboardButton(
            text=f"📡 {s.title or s.identifier}", callback_data=f"fwd:src:{s.id}"
        )]
        for s in sources
    ]
    rows.append([types.InlineKeyboardButton(text="➕ Add Source", callback_data="fwd:addsrc")])
    rows.append([types.InlineKeyboardButton(text="🔙 Back", callback_data="menu:main")])

    return "\n".join(lines), types.InlineKeyboardMarkup(inline_keyboard=rows)


async def show_forwarding_root(target) -> None:
    """Render the Forwarding root screen. `target` is either a Message
    (answer/edit_text both exist on the right object depending on caller) -
    callers pass query.message so edit_text works for in-place navigation.
    """
    text, kb = await _forwarding_view()
    try:
        await target.edit_text(text, reply_markup=kb)
    except Exception:
        await target.answer(text, reply_markup=kb)


async def _source_detail_view(source_id: int) -> tuple[str, types.InlineKeyboardMarkup] | None:
    async with session() as s:
        source = await s.get(SourceChannel, source_id)
        if not source:
            return None
        # Load rules with their destination channel eagerly (selectinload) -
        # touching r.destination on a lazily-loaded relationship outside this
        # `async with` block would raise sqlalchemy.exc.MissingGreenlet.
        q = select(RepostRule).where(RepostRule.source_channel_id == source_id).options(
            selectinload(RepostRule.destination)
        )
        res = await s.execute(q)
        rules = res.scalars().all()

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📡 {source.title or source.identifier}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n",
        f"Identifier: {source.identifier}\n",
    ]
    if not rules:
        lines.append("No forwarding rules yet - tap ➕ Add Rule to send this source's posts into one of your channels.")
    else:
        lines.append(f"Forwarding into {len(rules)} channel(s) - tap one to edit its link replacements:")

    rows = [
        [types.InlineKeyboardButton(
            text=f"➡️ {r.destination.title if r.destination else '(channel removed)'}",
            callback_data=f"fwd:rule:{r.id}",
        )]
        for r in rules
    ]
    rows.append([types.InlineKeyboardButton(text="➕ Add Rule", callback_data=f"fwd:addrule:{source_id}")])
    rows.append([types.InlineKeyboardButton(text="🗑️ Remove Source", callback_data=f"fwd:delsrc:{source_id}")])
    rows.append([types.InlineKeyboardButton(text="🔙 Back", callback_data="fwd:root")])

    return "\n".join(lines), types.InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "fwd:root")
async def cb_forwarding_root(query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await show_forwarding_root(query.message)
    await query.answer()


@router.callback_query(F.data.startswith("fwd:src:"))
async def cb_source_detail(query: types.CallbackQuery):
    source_id = int(query.data.split(":")[2])
    result = await _source_detail_view(source_id)
    if not result:
        await query.answer("Source not found", show_alert=True)
        return
    text, kb = result
    await query.message.edit_text(text, reply_markup=kb)
    await query.answer()


@router.callback_query(F.data.startswith("fwd:delsrc:"))
async def cb_delete_source(query: types.CallbackQuery):
    source_id = int(query.data.split(":")[2])
    async with session() as s:
        source = await s.get(SourceChannel, source_id, options=[selectinload(SourceChannel.rules)])
        if source:
            await s.delete(source)  # cascade="all, delete-orphan" removes its rules too
            await s.commit()
    await query.answer("Source removed")
    await show_forwarding_root(query.message)


@router.callback_query(F.data == "fwd:addsrc")
async def cb_add_source_start(query: types.CallbackQuery, state: FSMContext):
    await query.message.answer(
        "Send the channel to watch: a public @username, or its numeric chat id "
        "(e.g. -1001234567890, findable via a utility bot like @RawDataBot):",
        reply_markup=_cancel_kb(),
    )
    await state.set_state(SourceUIState.identifier)
    await query.answer()


@router.message(SourceUIState.identifier, F.text == "❌ Cancel")
async def cancel_add_source_identifier(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Cancelled", reply_markup=main_menu_kb())


@router.message(SourceUIState.identifier, F.text)
async def get_source_identifier(message: types.Message, state: FSMContext):
    identifier = message.text.strip()
    if not identifier:
        await message.answer("❌ Send a @username or numeric chat id")
        return

    async with session() as s:
        q = select(SourceChannel).where(SourceChannel.identifier == identifier)
        res = await s.execute(q)
        if res.scalars().first():
            await message.answer("❌ That source is already being watched")
            await state.clear()
            return

    await state.update_data(identifier=identifier)
    await message.answer(
        "Optional title to show in menus (or send 'skip'):",
        reply_markup=_cancel_kb(),
    )
    await state.set_state(SourceUIState.title)


@router.message(SourceUIState.title, F.text == "❌ Cancel")
async def cancel_add_source_title(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Cancelled", reply_markup=main_menu_kb())


@router.message(SourceUIState.title, F.text)
async def get_source_title(message: types.Message, state: FSMContext):
    raw = message.text.strip()
    title = None if raw.lower() in ("skip", "no", "") else raw
    data = await state.get_data()
    identifier = data.get("identifier")

    async with session() as s:
        sc = SourceChannel(
            owner_user_id=message.from_user.id if message.from_user else 0,
            identifier=identifier,
            title=title,
        )
        s.add(sc)
        await s.commit()

    await state.clear()
    text, kb = await _forwarding_view()
    await message.answer(f"✅ Now watching {title or identifier}\n\n" + text, reply_markup=kb)


# ---------------------------------------------------------------------------
# Backward-compatible text commands.
# ---------------------------------------------------------------------------

@router.message(Command("add_source"))
async def add_source(message: types.Message, command: CommandObject):
    """Usage: /add_source <identifier> [title]
    identifier can be @username or numeric chat id"""
    args = command.args
    if not args:
        await message.reply("Usage: /add_source <identifier> [title]\n\nOr use 📡 Forwarding in /start for a guided flow.")
        return
    parts = args.split(None, 1)
    identifier = parts[0].strip()
    title = parts[1].strip() if len(parts) > 1 else None
    async with session() as s:
        q = select(SourceChannel).where(SourceChannel.identifier == identifier)
        res = await s.execute(q)
        if res.scalars().first():
            await message.reply("Source already exists")
            return
        sc = SourceChannel(
            owner_user_id=message.from_user.id if message.from_user else 0,
            identifier=identifier,
            title=title,
        )
        s.add(sc)
        await s.commit()
    await message.reply(f"Added source {identifier} title={title}")


@router.message(Command("list_sources"))
async def list_sources(message: types.Message):
    async with session() as s:
        q = select(SourceChannel)
        res = await s.execute(q)
        rows = res.scalars().all()
    if not rows:
        await message.reply("No sources configured")
        return
    lines = [f"ID={r.id} identifier={r.identifier} title={r.title}" for r in rows]
    await message.reply("\n".join(lines))


@router.message(Command("remove_source"))
async def remove_source(message: types.Message, command: CommandObject):
    args = command.args
    if not args:
        await message.reply("Usage: /remove_source <identifier_or_id>")
        return
    key = args.strip()
    async with session() as s:
        try:
            val = int(key)
            q = select(SourceChannel).where(SourceChannel.id == val)
        except ValueError:
            q = select(SourceChannel).where(SourceChannel.identifier == key)
        res = await s.execute(q)
        row = res.scalars().first()
        if not row:
            await message.reply("Source not found")
            return
        await s.delete(row)
        await s.commit()
    await message.reply("Removed source")
