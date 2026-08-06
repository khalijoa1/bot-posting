"""Channel management with categories and welcome messages.

Channels can be registered two ways:
  1. Automatically - add the bot as admin to the channel and it registers
     itself (see channel_admin_added below). This is the recommended way;
     no need to look up or type the numeric chat_id. Works no matter which
     Telegram account performs the promotion - see the note on
     channel_admin_added for why.
  2. Manually - /add_channel, for cases where auto-detection isn't possible
     (e.g. you want to set everything - title, categories, welcome message -
     in one guided flow up front).
"""
import json
import logging

from aiogram import Router, types, F
from aiogram.enums import ChatMemberStatus
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from config import get_settings
from db import session
from handlers.common import main_menu_kb
from models import Channel, Category

router = Router()
logger = logging.getLogger(__name__)


def _cancel_kb() -> types.ReplyKeyboardMarkup:
    """Shared Cancel keyboard, reattached on every retry prompt so a bad
    input never leaves the user stuck without a visible way out."""
    return types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text="❌ Cancel")]],
        resize_keyboard=True,
    )


class ChannelState(StatesGroup):
    chat_id = State()
    title = State()
    select_categories = State()
    welcome_msg = State()
    delete_id = State()


class ForceJoinState(StatesGroup):
    add_identifier = State()
    add_link = State()


@router.message(Command("add_channel"))
async def add_channel_start(message: types.Message, state: FSMContext):
    """Start adding channel."""
    await state.clear()
    await message.answer(
        "━━━━━━━━━━━━━━━━━━\n"
        "➕ ADD CHANNEL\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Tip: you don't have to do this manually - just add the bot as "
        "admin to the channel and it registers itself automatically.\n\n"
        "To add manually instead, send chat ID:\n\n"
        "Format: -1001234567890",
        reply_markup=types.ReplyKeyboardMarkup(
            keyboard=[[types.KeyboardButton(text="❌ Cancel")]],
            resize_keyboard=True
        )
    )
    await state.set_state(ChannelState.chat_id)


@router.message(ChannelState.chat_id, F.text == "❌ Cancel")
async def cancel_add(message: types.Message, state: FSMContext):
    """Cancel add."""
    await state.clear()
    await message.answer("❌ Cancelled", reply_markup=main_menu_kb())


@router.message(ChannelState.chat_id, F.text)
async def get_chat_id(message: types.Message, state: FSMContext):
    """Get chat ID."""
    try:
        chat_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Invalid ID. Send number like -1001234567890", reply_markup=_cancel_kb())
        return

    async with session() as s:
        q = select(Channel).where(Channel.chat_id == chat_id)
        res = await s.execute(q)
        if res.scalars().first():
            await message.answer("⚠️ Already added", reply_markup=main_menu_kb())
            await state.clear()
            return

    await state.update_data(chat_id=chat_id)
    await message.answer(
        f"Chat ID: {chat_id} ✅\n\n"
        f"Now send TITLE:",
        reply_markup=types.ReplyKeyboardMarkup(
            keyboard=[[types.KeyboardButton(text="❌ Cancel")]],
            resize_keyboard=True
        )
    )
    await state.set_state(ChannelState.title)


@router.message(ChannelState.title, F.text == "❌ Cancel")
async def cancel_title(message: types.Message, state: FSMContext):
    """Cancel title."""
    await state.clear()
    await message.answer("❌ Cancelled", reply_markup=main_menu_kb())


@router.message(ChannelState.title, F.text)
async def get_title(message: types.Message, state: FSMContext):
    """Get title and show categories."""
    title = message.text.strip()
    await state.update_data(title=title)

    async with session() as s:
        q = select(Category)
        res = await s.execute(q)
        categories = res.scalars().all()

    if not categories:
        # Skip categories - go to welcome message
        await state.update_data(selected_categories=[])
        await ask_welcome_message(message, state)
        return

    # Show categories
    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(
                text=f"☐ {cat.name}",
                callback_data=f"cat_{cat.id}"
            )]
            for cat in categories
        ] + [
            [types.InlineKeyboardButton(text="✅ Next", callback_data="cat_next")],
            [types.InlineKeyboardButton(text="⏭️ Skip", callback_data="cat_skip")]
        ]
    )

    await message.answer(
        f"Title: {title} ✅\n\n"
        f"📁 SELECT CATEGORIES:\n\n"
        f"(Optional - tap to select)",
        reply_markup=kb
    )
    await state.update_data(selected_categories=[])
    await state.set_state(ChannelState.select_categories)


@router.callback_query(ChannelState.select_categories)
async def handle_categories(query: types.CallbackQuery, state: FSMContext):
    """Handle category selection."""
    if query.data == "cat_skip" or query.data == "cat_next":
        await ask_welcome_message(query.message, state)
        await query.answer()
        return

    # Toggle category
    cat_id = int(query.data.replace("cat_", ""))
    data = await state.get_data()
    selected = data.get("selected_categories", [])

    if cat_id in selected:
        selected.remove(cat_id)
    else:
        selected.append(cat_id)

    await state.update_data(selected_categories=selected)

    async with session() as s:
        q = select(Category)
        res = await s.execute(q)
        categories = res.scalars().all()

    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(
                text=f"{'☑' if cat.id in selected else '☐'} {cat.name}",
                callback_data=f"cat_{cat.id}"
            )]
            for cat in categories
        ] + [
            [types.InlineKeyboardButton(text="✅ Next", callback_data="cat_next")],
            [types.InlineKeyboardButton(text="⏭️ Skip", callback_data="cat_skip")]
        ]
    )

    await query.message.edit_reply_markup(reply_markup=kb)
    await query.answer()


async def ask_welcome_message(message: types.Message, state: FSMContext):
    """Ask for welcome message."""
    kb = types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="⏭️ Skip")],
            [types.KeyboardButton(text="❌ Cancel")]
        ],
        resize_keyboard=True
    )

    await message.answer(
        "💬 WELCOME MESSAGE:\n\n"
        "Send a message for new subscribers:\n\n"
        "(or tap Skip)",
        reply_markup=kb
    )
    await state.set_state(ChannelState.welcome_msg)


@router.message(ChannelState.welcome_msg, F.text == "⏭️ Skip")
async def skip_welcome(message: types.Message, state: FSMContext):
    """Skip welcome message."""
    await state.update_data(welcome_message=None)
    await finalize_channel(message, state)


@router.message(ChannelState.welcome_msg, F.text == "❌ Cancel")
async def cancel_welcome(message: types.Message, state: FSMContext):
    """Cancel adding channel."""
    await state.clear()
    await message.answer("❌ Cancelled", reply_markup=main_menu_kb())


@router.message(ChannelState.welcome_msg, F.text)
async def get_welcome_msg(message: types.Message, state: FSMContext):
    """Get welcome message."""
    welcome_msg = message.text.strip()
    await state.update_data(welcome_message=welcome_msg)
    await finalize_channel(message, state)


async def finalize_channel(message: types.Message, state: FSMContext):
    """Add channel to database."""
    data = await state.get_data()
    chat_id = data.get("chat_id")
    title = data.get("title")
    selected_cats = data.get("selected_categories", [])
    welcome_msg = data.get("welcome_message")

    async with session() as s:
        ch = Channel(
            owner_user_id=message.from_user.id,
            chat_id=chat_id,
            title=title,
            welcome_message=welcome_msg
        )
        s.add(ch)
        await s.flush()

        # Link categories - avoid lazy loading
        if selected_cats:
            q = select(Category).where(Category.id.in_(selected_cats))
            res = await s.execute(q)
            cats = res.scalars().all()
            for cat in cats:
                ch.categories.append(cat)

        await s.commit()
        ch_id = ch.id

    cat_count = len(selected_cats)
    cat_text = f"📁 Categories: {cat_count}" if cat_count > 0 else "❌ No categories"

    await message.answer(
        f"✅ CHANNEL ADDED!\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"ID: {ch_id}\n"
        f"Title: {title}\n"
        f"{cat_text}\n"
        f"Welcome Msg: {'✅' if welcome_msg else '❌'}\n"
        f"━━━━━━━━━━━━━━━━━━",
        reply_markup=main_menu_kb()
    )
    await state.clear()


@router.message(Command("list_channels"))
async def list_channels(message: types.Message):
    """List all channels."""
    async with session() as s:
        q = select(Channel)
        res = await s.execute(q)
        channels = res.scalars().all()

    if not channels:
        await message.answer(
            "━━━━━━━━━━━━━━━━━━\n"
            "📍 CHANNELS\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "❌ No channels\n\n"
            "Add the bot as admin to a channel to register it automatically, "
            "or use /add_channel"
        )
        return

    header = "━━━━━━━━━━━━━━━━━━\n📍 CHANNELS\n━━━━━━━━━━━━━━━━━━\n\n"
    chunks = [header]

    try:
        for ch in channels:
            # Get fresh data, eagerly loading categories in the same query so
            # touching fresh_ch.categories below does not trigger an implicit
            # lazy-load SELECT outside of a greenlet context (which raises
            # sqlalchemy.exc.MissingGreenlet and silently kills this handler -
            # one cause of "the button just does not respond" reports, since
            # the crash happens before Telegram gets any reply back).
            async with session() as s:
                fresh_ch = await s.get(
                    Channel, ch.id, options=[selectinload(Channel.categories)]
                )
                cat_names = [c.name for c in fresh_ch.categories] if fresh_ch and fresh_ch.categories else []

            cats = ", ".join(cat_names) if cat_names else "None"
            entry = (
                f"ID: {ch.id}\n"
                f"Title: {ch.title}\n"
                f"Chat ID: {ch.chat_id}\n"
                f"Categories: {cats}\n"
                f"Auto-Approve: {'✅' if ch.auto_approve_members else '❌'}\n"
                f"Welcome: {'✅' if ch.welcome_message else '❌'}\n\n"
            )

            # Telegram caps a single message at 4096 chars - start a new
            # chunk instead of letting a long channel list silently fail to
            # send. That is the other cause of "list channels does not
            # respond": message.answer() raising on oversized text with
            # nothing ever surfaced back to the user, since this handler had
            # no error handling at all.
            if len(chunks[-1]) + len(entry) > 3800:
                chunks.append("")
            chunks[-1] += entry

        for chunk in chunks:
            if chunk.strip():
                await message.answer(chunk)
    except Exception:
        logger.exception("Failed to list channels")
        await message.answer(
            "⚠️ Something went wrong building the channel list. Try again, "
            "or check the logs if it keeps happening."
        )


@router.message(Command("delete_channel"))
async def delete_channel_start(message: types.Message, state: FSMContext):
    """Start delete."""
    await state.clear()
    await message.answer(
        "🗑️ DELETE CHANNEL\n\n"
        "Send Channel ID:",
        reply_markup=types.ReplyKeyboardMarkup(
            keyboard=[[types.KeyboardButton(text="❌ Cancel")]],
            resize_keyboard=True
        )
    )
    await state.set_state(ChannelState.delete_id)


@router.message(ChannelState.delete_id)
async def delete_confirm(message: types.Message, state: FSMContext):
    """Delete channel."""
    if message.text == "❌ Cancel":
        await state.clear()
        await message.answer("❌ Cancelled", reply_markup=main_menu_kb())
        return

    try:
        ch_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Invalid ID", reply_markup=_cancel_kb())
        return

    async with session() as s:
        ch = await s.get(Channel, ch_id)
        if not ch:
            await message.answer("❌ Not found", reply_markup=main_menu_kb())
            await state.clear()
            return

        title = ch.title
        await s.delete(ch)
        await s.commit()

    await message.answer(
        f"✅ DELETED!\n\n"
        f"{title}",
        reply_markup=main_menu_kb()
    )
    await state.clear()


@router.message(lambda msg: msg.text == "➕ Add Channel")
async def add_button(message: types.Message, state: FSMContext):
    """Add from menu."""
    await add_channel_start(message, state)


@router.message(lambda msg: msg.text == "📋 List Channels")
async def list_button(message: types.Message):
    """List from menu."""
    await list_channels(message)


@router.message(lambda msg: msg.text == "🗑️ Delete Channel")
async def delete_button(message: types.Message, state: FSMContext):
    """Delete from menu."""
    await delete_channel_start(message, state)


# ---------------------------------------------------------------------------
# Force-join (subscription gate): require members to already belong to
# specific other channels/groups before their join request to one of YOUR
# channels gets auto-approved. Only takes effect on channels that already
# have Auto-Approve turned on (/autoapprove) - see handlers/join_requests.py
# for the actual gating logic at approval time.
# ---------------------------------------------------------------------------

def _parse_required(raw: str | None) -> list[dict]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception:
        return []


async def _force_join_channel_list_view() -> tuple[str, types.InlineKeyboardMarkup]:
    async with session() as s:
        q = select(Channel)
        res = await s.execute(q)
        channels = res.scalars().all()

    text = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🔒 FORCE-JOIN\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Require someone to already be a member of specific channels/groups "
        "before their join request to one of your channels gets "
        "auto-approved. Only applies where Auto-Approve is already on.\n\n"
        "Pick a channel to configure:"
    )
    if not channels:
        text += "\n\n❌ No channels registered yet."
    rows = [
        [types.InlineKeyboardButton(text=f"📍 {ch.title}", callback_data=f"fj:pick:{ch.id}")]
        for ch in channels
    ]
    rows.append([types.InlineKeyboardButton(text="🔙 Back", callback_data="menu:channels")])
    return text, types.InlineKeyboardMarkup(inline_keyboard=rows)


async def force_join_start(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    text, kb = await _force_join_channel_list_view()
    await message.answer(text, reply_markup=kb)


async def _force_join_detail_view(channel_id: int) -> tuple[str, types.InlineKeyboardMarkup] | None:
    async with session() as s:
        ch = await s.get(Channel, channel_id)
        if not ch:
            return None
        required = _parse_required(ch.required_join_json)
        title = ch.title

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔒 FORCE-JOIN for {title}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n",
    ]
    if not required:
        lines.append("No required channels/groups set - join requests are approved normally.")
    else:
        lines.append("Must already be a member of ALL of these before approval:")
        for r in required:
            lines.append(f"  • {r.get('title') or r.get('identifier')} ({r.get('identifier')})")
        lines.append(
            "\n⚠️ The bot must also be an admin/member of each of these "
            "itself, otherwise it can't check membership."
        )

    rows = []
    for i, r in enumerate(required):
        rows.append([types.InlineKeyboardButton(
            text=f"🚫 Remove {r.get('title') or r.get('identifier')}",
            callback_data=f"fj:del:{channel_id}:{i}",
        )])
    rows.append([types.InlineKeyboardButton(text="➕ Add Required Channel", callback_data=f"fj:add:{channel_id}")])
    rows.append([types.InlineKeyboardButton(text="🔙 Back", callback_data="fj:root")])
    return "\n".join(lines), types.InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "fj:root")
async def cb_force_join_root(query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    text, kb = await _force_join_channel_list_view()
    await query.message.edit_text(text, reply_markup=kb)
    await query.answer()


@router.callback_query(F.data.startswith("fj:pick:"))
async def cb_force_join_pick(query: types.CallbackQuery):
    channel_id = int(query.data.split(":")[2])
    result = await _force_join_detail_view(channel_id)
    if not result:
        await query.answer("Channel not found", show_alert=True)
        return
    text, kb = result
    await query.message.edit_text(text, reply_markup=kb)
    await query.answer()


@router.callback_query(F.data.startswith("fj:del:"))
async def cb_force_join_remove(query: types.CallbackQuery):
    _, _, channel_id_s, idx_s = query.data.split(":")
    channel_id, idx = int(channel_id_s), int(idx_s)
    async with session() as s:
        ch = await s.get(Channel, channel_id)
        if ch:
            required = _parse_required(ch.required_join_json)
            if 0 <= idx < len(required):
                required.pop(idx)
            ch.required_join_json = json.dumps(required) if required else None
            await s.commit()
    await query.answer("Removed")
    result = await _force_join_detail_view(channel_id)
    if result:
        text, kb = result
        await query.message.edit_text(text, reply_markup=kb)


@router.callback_query(F.data.startswith("fj:add:"))
async def cb_force_join_add_start(query: types.CallbackQuery, state: FSMContext):
    channel_id = int(query.data.split(":")[2])
    await state.update_data(channel_id=channel_id)
    await state.set_state(ForceJoinState.add_identifier)
    await query.message.answer(
        "Send the channel/group to require, as a public @username or its "
        "numeric chat id (e.g. -1001234567890). The bot must be an "
        "admin/member of it to be able to check membership.",
        reply_markup=types.ReplyKeyboardMarkup(
            keyboard=[[types.KeyboardButton(text="❌ Cancel")]], resize_keyboard=True
        ),
    )
    await query.answer()


@router.message(ForceJoinState.add_identifier, F.text == "❌ Cancel")
async def cancel_force_join_identifier(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Cancelled", reply_markup=main_menu_kb())


@router.message(ForceJoinState.add_identifier, F.text)
async def get_force_join_identifier(message: types.Message, state: FSMContext):
    identifier = message.text.strip()
    if not identifier:
        await message.answer("❌ Send a @username or numeric chat id", reply_markup=_cancel_kb())
        return
    await state.update_data(identifier=identifier)

    if identifier.startswith("@"):
        # Public username - the invite link is just t.me/<username>, no
        # need to ask for one separately.
        link = f"https://t.me/{identifier.lstrip('@')}"
        await state.update_data(link=link)
        await _finalize_force_join_add(message, state)
        return

    await state.set_state(ForceJoinState.add_link)
    await message.answer(
        "Since that's a numeric id (private channel/group), send the "
        "invite link to show users so they can join it:",
        reply_markup=types.ReplyKeyboardMarkup(
            keyboard=[[types.KeyboardButton(text="❌ Cancel")]], resize_keyboard=True
        ),
    )


@router.message(ForceJoinState.add_link, F.text == "❌ Cancel")
async def cancel_force_join_link(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Cancelled", reply_markup=main_menu_kb())


@router.message(ForceJoinState.add_link, F.text)
async def get_force_join_link(message: types.Message, state: FSMContext):
    link = message.text.strip()
    if not (link.startswith("http://") or link.startswith("https://") or link.startswith("t.me/")):
        await message.answer("❌ Send a valid link starting with https://", reply_markup=_cancel_kb())
        return
    await state.update_data(link=link)
    await _finalize_force_join_add(message, state)


async def _finalize_force_join_add(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    channel_id = data.get("channel_id")
    identifier = data.get("identifier")
    link = data.get("link")

    async with session() as s:
        ch = await s.get(Channel, channel_id)
        if not ch:
            await message.answer("❌ Channel not found (it may have been removed)", reply_markup=main_menu_kb())
            await state.clear()
            return
        required = _parse_required(ch.required_join_json)
        if any(r.get("identifier") == identifier for r in required):
            await message.answer("⚠️ That one's already required for this channel.")
        else:
            title = None
            try:
                chat = await message.bot.get_chat(identifier)
                title = chat.title
            except Exception:
                pass
            required.append({"identifier": identifier, "link": link, "title": title})
            ch.required_join_json = json.dumps(required)
            await s.commit()

    await state.clear()
    result = await _force_join_detail_view(channel_id)
    if result:
        text, kb = result
        await message.answer(text, reply_markup=kb)


@router.message(lambda msg: msg.text == "🔒 Force-Join")
async def force_join_button(message: types.Message, state: FSMContext):
    """Force-join from menu."""
    await force_join_start(message, state)


# ---------------------------------------------------------------------------
# Automatic registration - triggers the moment the bot is made an admin
# ---------------------------------------------------------------------------

# Per-channel set of category IDs currently ticked in the post-registration
# "pick categories" prompt. In-memory only (same pattern as moderation.py's
# spam counters) - fine since it's just UI state for a prompt that's meant
# to be answered right away.
_pending_cat_selection: dict[int, set[int]] = {}


@router.my_chat_member(F.chat.type == "channel")
async def channel_admin_added(update: types.ChatMemberUpdated) -> None:
    """Auto-register a channel the moment the bot is promoted to admin in
    it, so you don't have to look up and type its numeric chat_id - just
    add the bot as admin with posting permissions and it's registered.

    This used to require the Telegram account that promoted the bot to be
    one of ALLOWED_USER_IDS (the bot's own operator allowlist), and silently
    skip registration otherwise. That broke the common case of operators
    using a second/alt Telegram account to admin a channel their main
    account isn't an admin of - the bot got promoted just fine, but the
    channel never showed up in /list_channels because the promoting
    account wasn't on the allowlist.

    Fixed: registration itself no longer checks who did the promoting.
    Telegram already gates "can promote this bot to admin" behind that
    account being an admin of the channel - that's the trust boundary that
    actually matters, not whether it's specifically one of the operator's
    allowlisted accounts. The confirmation DM (and the category-tagging
    prompt) is still always sent to the bot's real operator(s) in
    ALLOWED_USER_IDS, never to whoever happened to do the promoting, since
    they're the ones who manage the bot day to day.
    """
    if update.new_chat_member.status != ChatMemberStatus.ADMINISTRATOR:
        return
    if update.old_chat_member.status == ChatMemberStatus.ADMINISTRATOR:
        return  # already was admin (e.g. permissions edited) - not a new add

    chat = update.chat
    async with session() as s:
        q = select(Channel).where(Channel.chat_id == chat.id)
        res = await s.execute(q)
        if res.scalars().first():
            return  # already registered

        operators = sorted(get_settings().allowed_user_id_set)
        actor = update.from_user
        owner_id = operators[0] if operators else (actor.id if actor else 0)

        ch = Channel(owner_user_id=owner_id, chat_id=chat.id, title=chat.title or str(chat.id))
        s.add(ch)
        await s.commit()
        ch_id = ch.id

        q2 = select(Category)
        res2 = await s.execute(q2)
        categories = res2.scalars().all()

    added_by = f"\nAdded by: {actor.full_name}" if actor and actor.full_name else ""
    text = (
        "✅ CHANNEL REGISTERED\n\n"
        f"Title: {chat.title}\n"
        f"ID: {ch_id}"
        f"{added_by}\n\n"
        "I noticed I was made admin here and added it automatically - "
        "no need to run /add_channel.\n\n"
    )

    kb = None
    if categories:
        _pending_cat_selection[ch_id] = set()
        text += "Tap categories to assign this channel to (optional), then Done:"
        kb = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text=f"☐ {c.name}", callback_data=f"achcat_{ch_id}_{c.id}")]
                for c in categories
            ] + [[types.InlineKeyboardButton(text="✅ Done", callback_data=f"achdone_{ch_id}")]]
        )
    else:
        text += "No categories exist yet - create one with ➕ Add Category, then assign it via /list_channels."

    # Notify every approved operator, not just whoever did the promoting -
    # that way the person actually running the bot always finds out, even
    # when a different (alt) account was used to add the bot somewhere.
    for op_id in operators:
        try:
            await update.bot.send_message(op_id, text, reply_markup=kb)
        except Exception:
            # That operator hasn't opened a DM with the bot yet -
            # registration still succeeded, they'll see it in /list_channels.
            pass


@router.callback_query(F.data.startswith("achcat_"))
async def toggle_auto_channel_category(query: types.CallbackQuery) -> None:
    """Toggle a category on/off for a channel that was just auto-registered."""
    _, ch_id_s, cat_id_s = query.data.split("_")
    ch_id, cat_id = int(ch_id_s), int(cat_id_s)
    selected = _pending_cat_selection.setdefault(ch_id, set())
    if cat_id in selected:
        selected.discard(cat_id)
    else:
        selected.add(cat_id)

    async with session() as s:
        q = select(Category)
        res = await s.execute(q)
        categories = res.scalars().all()

    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(
                text=f"{'☑' if c.id in selected else '☐'} {c.name}",
                callback_data=f"achcat_{ch_id}_{c.id}"
            )]
            for c in categories
        ] + [[types.InlineKeyboardButton(text="✅ Done", callback_data=f"achdone_{ch_id}")]]
    )
    await query.message.edit_reply_markup(reply_markup=kb)
    await query.answer()


@router.callback_query(F.data.startswith("achdone_"))
async def finish_auto_channel_category(query: types.CallbackQuery) -> None:
    """Save the ticked categories for an auto-registered channel."""
    ch_id = int(query.data.replace("achdone_", ""))
    selected = _pending_cat_selection.pop(ch_id, set())

    async with session() as s:
        # Eagerly load categories so appending to the collection below
        # doesn't need an implicit lazy-load (see list_channels comment
        # above for why that crashes this handler under async SQLAlchemy).
        ch = await s.get(Channel, ch_id, options=[selectinload(Channel.categories)])
        if ch and selected:
            q = select(Category).where(Category.id.in_(selected))
            res = await s.execute(q)
            cats = res.scalars().all()
            for c in cats:
                ch.categories.append(c)
            await s.commit()

    await query.message.edit_text(f"✅ Saved. Categories assigned: {len(selected)}")
    await query.answer()
