"""Settings, auto-approve, and per-channel pre-approval/welcome messages.

Auto-approve, the pre-approval message, and the welcome message are
separate settings on purpose: auto-approve controls whether join requests
get approved automatically; the pre-approval message is DMed the instant
a join request comes in, before approval happens; the welcome message is
DMed once the request is actually approved (see
handlers/join_requests.py). A channel can have auto-approve on with
neither message set (silent approval), just one of the two, or both -
and either can be set before auto-approve is even turned on.
"""
import asyncio
import logging

from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select

from db import session
from handlers.common import main_menu_kb
from models import Channel

logger = logging.getLogger(__name__)
router = Router()


class WelcomeMsgState(StatesGroup):
    text = State()


class PreApprovalMsgState(StatesGroup):
    text = State()


class AutoCommentState(StatesGroup):
    text = State()


AUTO_APPROVE_PAGE_SIZE = 6


def _auto_approve_kb(channels, page: int = 0) -> types.InlineKeyboardMarkup:
    total_pages = max(1, (len(channels) + AUTO_APPROVE_PAGE_SIZE - 1) // AUTO_APPROVE_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * AUTO_APPROVE_PAGE_SIZE
    page_channels = channels[start:start + AUTO_APPROVE_PAGE_SIZE]

    rows = []
    for ch in page_channels:
        rows.append([
            types.InlineKeyboardButton(
                text=f"{'✅ ON' if ch.auto_approve_members else '❌ OFF'} - {ch.title}",
                callback_data=f"app_{ch.id}_{page}"
            ),
        ])
        rows.append([
            types.InlineKeyboardButton(
                text=f"📨 {'Edit' if ch.pre_approval_message else 'Set'} Before-msg",
                callback_data=f"setpreapproval_{ch.id}"
            ),
            types.InlineKeyboardButton(
                text=f"💬 {'Edit' if ch.welcome_message else 'Set'} After-msg",
                callback_data=f"setwelcome_{ch.id}"
            ),
        ])
        rows.append([
            types.InlineKeyboardButton(
                text="⏳ Approve Backlog Requests",
                callback_data=f"appbacklog_{ch.id}"
            ),
        ])

    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(types.InlineKeyboardButton(text="⬅️ Prev", callback_data=f"apppage_{page - 1}"))
        nav.append(types.InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="appnoop"))
        if page < total_pages - 1:
            nav.append(types.InlineKeyboardButton(text="Next ➡️", callback_data=f"apppage_{page + 1}"))
        rows.append(nav)

    return types.InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("autoapprove"))
async def auto_approve(message: types.Message):
    """Show auto-approve settings, with buttons per channel to also set the
    pre-approval message (DMed the instant a join request arrives) and the
    welcome message (DMed once it's approved)."""
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

    await message.answer(
        "━━━━━━━━━━━━━━━━━━\n"
        "🔐 AUTO-APPROVE\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Tap a channel to toggle auto-approval of subscriber join "
        "requests.\n\n"
        "📨 Before-msg: DMed the instant someone's join request comes "
        "in, before they're approved.\n"
        "💬 After-msg: DMed once their request is actually approved.\n"
        "⏳ Approve Backlog: approves everyone whose join request is "
        "still sitting pending from before the bot was made admin here - "
        "a one-time catch-up, not something you need to run again "
        "unless requests pile up while auto-approve is off.\n\n"
        "Note: the channel needs \"Approve new members\" turned on in "
        "Telegram. Telegram normally only lets a bot DM someone who's "
        "interacted with it before, but a join request itself counts as "
        "that interaction - so both messages above reach the requester "
        "even if they've never pressed /start on this bot.",
        reply_markup=_auto_approve_kb(channels)
    )


@router.callback_query(F.data.startswith("app_"))
async def toggle_approve(query: types.CallbackQuery):
    """Toggle auto-approve for a channel."""
    parts = query.data.split("_")
    ch_id = int(parts[1])
    page = int(parts[2]) if len(parts) > 2 else 0

    async with session() as s:
        ch = await s.get(Channel, ch_id)
        if not ch:
            await query.answer("Not found", show_alert=True)
            return

        ch.auto_approve_members = not ch.auto_approve_members
        s.add(ch)
        await s.commit()
        status = "✅ ENABLED" if ch.auto_approve_members else "❌ DISABLED"
        title = ch.title

        q = select(Channel)
        res = await s.execute(q)
        channels = res.scalars().all()

    await query.message.edit_reply_markup(reply_markup=_auto_approve_kb(channels, page))
    await query.answer(f"{title}: {status}", show_alert=True)


@router.callback_query(F.data.startswith("apppage_"))
async def paginate_auto_approve(query: types.CallbackQuery):
    """Redraw the auto-approve keyboard on a different page."""
    page = int(query.data.replace("apppage_", ""))

    async with session() as s:
        q = select(Channel)
        res = await s.execute(q)
        channels = res.scalars().all()

    await query.message.edit_reply_markup(reply_markup=_auto_approve_kb(channels, page))
    await query.answer()


@router.callback_query(F.data == "appnoop")
async def noop_auto_approve_page_indicator(query: types.CallbackQuery):
    """The '3/8' page indicator button - not meant to do anything when tapped."""
    await query.answer()


async def _run_backlog_approve_and_notify(bot, chat_id: int, title: str, operator_id: int) -> None:
    """Runs the actual bulk-approval and DMs the operator with the result
    once it's done - see approve_backlog below for why this happens in the
    background instead of inside the callback handler."""
    from services.telethon_client import approve_pending_join_requests
    approved, failed, error = await approve_pending_join_requests(chat_id)

    if error:
        text = f"⚠️ Couldn't approve backlog requests for {title}:\n\n{error}"
    elif approved == 0 and failed == 0:
        text = f"✅ {title}: no pending join requests found - nothing to do."
    else:
        text = f"✅ {title}: approved {approved} pending join request(s)."
        if failed:
            text += f"\n⚠️ {failed} couldn't be approved even after retries - check logs for details."

    try:
        await bot.send_message(operator_id, text)
    except Exception:
        pass


@router.callback_query(F.data.startswith("appbacklog_"))
async def approve_backlog(query: types.CallbackQuery):
    """Bulk-approve every join request to this channel that's been sitting
    pending since before the bot was made admin (or before auto-approve was
    turned on) - see services/telethon_client.approve_pending_join_requests
    for why this needs the Telethon userbot rather than the regular Bot API.

    Runs in the background rather than blocking on the callback: Telegram
    rate-limits bulk join-request approval hard, so a channel with more
    than a handful of pending requests can take several minutes (or longer,
    once flood waits kick in) to work through. Blocking here left the
    operator staring at "Working on it..." with no way to tell whether it
    was still running or had died - this kicks the whole thing off as a
    background task instead and DMs the operator with the final
    approved/failed counts once it's actually finished.
    """
    ch_id = int(query.data.replace("appbacklog_", ""))

    async with session() as s:
        ch = await s.get(Channel, ch_id)
        if not ch:
            await query.answer("Not found", show_alert=True)
            return
        title = ch.title
        chat_id = ch.chat_id

    await query.answer(
        "Started in the background - a large backlog can take a while. "
        "I'll DM you when it's done.",
        show_alert=True,
    )
    asyncio.create_task(
        _run_backlog_approve_and_notify(query.bot, chat_id, title, query.from_user.id)
    )


@router.callback_query(F.data.startswith("setwelcome_"))
async def start_set_welcome(query: types.CallbackQuery, state: FSMContext):
    """Prompt for the after-approval welcome-message text for one channel."""
    ch_id = int(query.data.replace("setwelcome_", ""))

    async with session() as s:
        ch = await s.get(Channel, ch_id)
        if not ch:
            await query.answer("Not found", show_alert=True)
            return
        title = ch.title
        current = ch.welcome_message

    await state.update_data(welcome_channel_id=ch_id)
    await state.set_state(WelcomeMsgState.text)

    current_block = f"\n\nCurrent message:\n{current}" if current else ""
    await query.message.answer(
        f"💬 AFTER-APPROVAL MESSAGE - {title}\n\n"
        f"Send the message to DM new subscribers once they're "
        f"auto-approved into this channel.{current_block}",
        reply_markup=types.ReplyKeyboardMarkup(
            keyboard=[
                [types.KeyboardButton(text="🗑️ Clear Message")],
                [types.KeyboardButton(text="❌ Cancel")]
            ],
            resize_keyboard=True
        )
    )
    await query.answer()


@router.message(WelcomeMsgState.text, F.text == "❌ Cancel")
async def cancel_set_welcome(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Cancelled", reply_markup=main_menu_kb())


@router.message(WelcomeMsgState.text, F.text == "🗑️ Clear Message")
async def clear_welcome(message: types.Message, state: FSMContext):
    data = await state.get_data()
    ch_id = data.get("welcome_channel_id")

    async with session() as s:
        ch = await s.get(Channel, ch_id)
        if ch:
            ch.welcome_message = None
            s.add(ch)
            await s.commit()

    await message.answer("✅ After-approval message cleared", reply_markup=main_menu_kb())
    await state.clear()


@router.message(WelcomeMsgState.text, F.text)
async def save_welcome(message: types.Message, state: FSMContext):
    data = await state.get_data()
    ch_id = data.get("welcome_channel_id")
    text = message.text.strip()

    async with session() as s:
        ch = await s.get(Channel, ch_id)
        if not ch:
            await message.answer("❌ Channel not found", reply_markup=main_menu_kb())
            await state.clear()
            return
        ch.welcome_message = text
        s.add(ch)
        await s.commit()
        title = ch.title

    await message.answer(
        f"✅ AFTER-APPROVAL MESSAGE SAVED for {title}\n\n"
        f"It will be sent as a DM the moment someone's join request to "
        f"this channel is auto-approved.",
        reply_markup=main_menu_kb()
    )
    await state.clear()


@router.callback_query(F.data.startswith("setpreapproval_"))
async def start_set_preapproval(query: types.CallbackQuery, state: FSMContext):
    """Prompt for the before-approval message text for one channel - DMed
    the instant a join request comes in, before it's approved."""
    ch_id = int(query.data.replace("setpreapproval_", ""))

    async with session() as s:
        ch = await s.get(Channel, ch_id)
        if not ch:
            await query.answer("Not found", show_alert=True)
            return
        title = ch.title
        current = ch.pre_approval_message

    await state.update_data(preapproval_channel_id=ch_id)
    await state.set_state(PreApprovalMsgState.text)

    current_block = f"\n\nCurrent message:\n{current}" if current else ""
    await query.message.answer(
        f"📨 BEFORE-APPROVAL MESSAGE - {title}\n\n"
        f"Send the message to DM someone the instant their join request "
        f"to this channel comes in - before they're approved (e.g. "
        f"\"Thanks for requesting to join, you'll be in shortly!\" or a "
        f"rules notice). Leave it unset for no before-message.{current_block}",
        reply_markup=types.ReplyKeyboardMarkup(
            keyboard=[
                [types.KeyboardButton(text="🗑️ Clear Message")],
                [types.KeyboardButton(text="❌ Cancel")]
            ],
            resize_keyboard=True
        )
    )
    await query.answer()


@router.message(PreApprovalMsgState.text, F.text == "❌ Cancel")
async def cancel_set_preapproval(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Cancelled", reply_markup=main_menu_kb())


@router.message(PreApprovalMsgState.text, F.text == "🗑️ Clear Message")
async def clear_preapproval(message: types.Message, state: FSMContext):
    data = await state.get_data()
    ch_id = data.get("preapproval_channel_id")

    async with session() as s:
        ch = await s.get(Channel, ch_id)
        if ch:
            ch.pre_approval_message = None
            s.add(ch)
            await s.commit()

    await message.answer("✅ Before-approval message cleared", reply_markup=main_menu_kb())
    await state.clear()


@router.message(PreApprovalMsgState.text, F.text)
async def save_preapproval(message: types.Message, state: FSMContext):
    data = await state.get_data()
    ch_id = data.get("preapproval_channel_id")
    text = message.text.strip()

    async with session() as s:
        ch = await s.get(Channel, ch_id)
        if not ch:
            await message.answer("❌ Channel not found", reply_markup=main_menu_kb())
            await state.clear()
            return
        ch.pre_approval_message = text
        s.add(ch)
        await s.commit()
        title = ch.title

    await message.answer(
        f"✅ BEFORE-APPROVAL MESSAGE SAVED for {title}\n\n"
        f"It will be sent as a DM the instant someone's join request to "
        f"this channel comes in, before they're approved.",
        reply_markup=main_menu_kb()
    )
    await state.clear()


@router.message(lambda msg: msg.text == "🔐 Auto-Approve Members")
async def approve_button(message: types.Message):
    """Auto-approve from menu."""
    await auto_approve(message)


# ---------------------------------------------------------------------------
# Auto-comment: bot replies with a fixed message in a channel's linked
# discussion group under every post, so it shows up as the first comment.
#
# Mechanism: when a channel has a discussion group linked (Telegram
# Channel settings -> Discussion) and the bot is a member/admin of BOTH the
# channel and that group, Telegram automatically copies every channel post
# into the group as a regular message with is_automatic_forward=True and
# its forward origin pointing back at the channel. Replying to that copied
# message in the group (reply_to_message_id) is exactly what makes a
# message show up as a "comment" under the original channel post - this
# isn't a special API, it's just a normal reply that Telegram's UI renders
# as a comment because of the forward link. See
# handle_channel_auto_forward below.
# ---------------------------------------------------------------------------

def _auto_comment_kb(channels) -> types.InlineKeyboardMarkup:
    rows = []
    for ch in channels:
        rows.append([
            types.InlineKeyboardButton(
                text=f"{'✅ ON' if ch.auto_comment_enabled else '❌ OFF'} - {ch.title}",
                callback_data=f"acmt_{ch.id}"
            ),
            types.InlineKeyboardButton(
                text=f"✏️ {'Edit' if ch.auto_comment_text else 'Set'} Comment",
                callback_data=f"acmttext_{ch.id}"
            ),
        ])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("autocomment"))
async def auto_comment_settings(message: types.Message):
    """Show auto-comment settings: per-channel on/off plus the fixed
    message text to post as a comment under every post to that channel."""
    async with session() as s:
        q = select(Channel)
        res = await s.execute(q)
        channels = res.scalars().all()

    if not channels:
        await message.answer(
            "━━━━━━━━━━━━━━━━━━\n"
            "💬 AUTO-COMMENT\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "❌ No channels added yet"
        )
        return

    await message.answer(
        "━━━━━━━━━━━━━━━━━━\n"
        "💬 AUTO-COMMENT\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Posts a fixed message as the first comment under every post sent "
        "to a channel (e.g. \"Join our VIP group 👉\"). Tap a channel to "
        "toggle it, or ✏️ to set/edit its message - each channel can have "
        "its own text.\n\n"
        "Requirements: the channel needs a discussion group already linked "
        "in Telegram (Channel settings -> Discussion), and the bot must be "
        "a member/admin of that discussion group too, not just the "
        "channel itself - otherwise it never sees the post land there to "
        "reply to.",
        reply_markup=_auto_comment_kb(channels)
    )


@router.callback_query(F.data.startswith("acmt_"))
async def toggle_auto_comment(query: types.CallbackQuery):
    """Toggle auto-comment for a channel."""
    ch_id = int(query.data.replace("acmt_", ""))

    async with session() as s:
        ch = await s.get(Channel, ch_id)
        if not ch:
            await query.answer("Not found", show_alert=True)
            return

        ch.auto_comment_enabled = not ch.auto_comment_enabled
        s.add(ch)
        await s.commit()
        status = "✅ ENABLED" if ch.auto_comment_enabled else "❌ DISABLED"
        title = ch.title

        q = select(Channel)
        res = await s.execute(q)
        channels = res.scalars().all()

    await query.message.edit_reply_markup(reply_markup=_auto_comment_kb(channels))
    await query.answer(f"{title}: {status}", show_alert=True)


@router.callback_query(F.data.startswith("acmttext_"))
async def start_set_auto_comment(query: types.CallbackQuery, state: FSMContext):
    """Prompt for the fixed comment text for one channel."""
    ch_id = int(query.data.replace("acmttext_", ""))

    async with session() as s:
        ch = await s.get(Channel, ch_id)
        if not ch:
            await query.answer("Not found", show_alert=True)
            return
        title = ch.title
        current = ch.auto_comment_text

    await state.update_data(auto_comment_channel_id=ch_id)
    await state.set_state(AutoCommentState.text)

    current_block = f"\n\nCurrent message:\n{current}" if current else ""
    await query.message.answer(
        f"✏️ AUTO-COMMENT - {title}\n\n"
        f"Send the message to post as a comment under every future post to "
        f"this channel.{current_block}",
        reply_markup=types.ReplyKeyboardMarkup(
            keyboard=[
                [types.KeyboardButton(text="🗑️ Clear Message")],
                [types.KeyboardButton(text="❌ Cancel")]
            ],
            resize_keyboard=True
        )
    )
    await query.answer()


@router.message(AutoCommentState.text, F.text == "❌ Cancel")
async def cancel_set_auto_comment(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Cancelled", reply_markup=main_menu_kb())


@router.message(AutoCommentState.text, F.text == "🗑️ Clear Message")
async def clear_auto_comment(message: types.Message, state: FSMContext):
    data = await state.get_data()
    ch_id = data.get("auto_comment_channel_id")

    async with session() as s:
        ch = await s.get(Channel, ch_id)
        if ch:
            ch.auto_comment_text = None
            s.add(ch)
            await s.commit()

    await message.answer("✅ Auto-comment message cleared", reply_markup=main_menu_kb())
    await state.clear()


@router.message(AutoCommentState.text, F.text)
async def save_auto_comment(message: types.Message, state: FSMContext):
    data = await state.get_data()
    ch_id = data.get("auto_comment_channel_id")
    text = message.text.strip()

    async with session() as s:
        ch = await s.get(Channel, ch_id)
        if not ch:
            await message.answer("❌ Channel not found", reply_markup=main_menu_kb())
            await state.clear()
            return
        ch.auto_comment_text = text
        s.add(ch)
        await s.commit()
        title = ch.title

    await message.answer(
        f"✅ AUTO-COMMENT SAVED for {title}\n\n"
        f"It'll be posted as a comment under every future post to this "
        f"channel (as long as auto-comment is toggled ON for it and the "
        f"discussion group is linked with the bot in it).",
        reply_markup=main_menu_kb()
    )
    await state.clear()


@router.message(lambda msg: msg.text == "💬 Auto-Comment")
async def auto_comment_button(message: types.Message):
    """Auto-comment from menu."""
    await auto_comment_settings(message)


# Dedup guard for albums: Telegram auto-forwards each item of a multi-photo
# post into the discussion group as its own message, all sharing one
# media_group_id - without this, a 3-photo album post would get 3 separate
# copies of the same auto-comment instead of one. In-memory only (same
# pattern as moderation.py's spam counters); capped so it can't grow
# unbounded on a long-running process.
_recent_album_comments: set[str] = set()
_ALBUM_DEDUP_CAP = 500


def _forward_origin_chat_id(message: types.Message) -> tuple[int, int] | None:
    """Return (origin_chat_id, origin_message_id) for a message that was
    auto-forwarded from a channel into its linked discussion group, or
    None if this message isn't that. Checks the modern `forward_origin`
    field first (Bot API 7.0+) and falls back to the older
    `forward_from_chat`/`forward_from_message_id` pair some aiogram/Bot API
    combinations still populate, so this works regardless of exact version.
    """
    origin = getattr(message, "forward_origin", None)
    if origin is not None:
        chat = getattr(origin, "chat", None)
        msg_id = getattr(origin, "message_id", None)
        if chat is not None and msg_id is not None:
            return chat.id, msg_id

    ffc = getattr(message, "forward_from_chat", None)
    ffmid = getattr(message, "forward_from_message_id", None)
    if ffc is not None and ffmid is not None:
        return ffc.id, ffmid

    return None


@router.message(F.is_automatic_forward, F.chat.type == "supergroup")
async def handle_channel_auto_forward(message: types.Message) -> None:
    """A channel post just landed in its linked discussion group - if that
    channel has auto-comment on with a message set, reply to it here so it
    shows up as the first comment under the post.
    """
    origin = _forward_origin_chat_id(message)
    if not origin:
        return
    origin_chat_id, _origin_message_id = origin

    if message.media_group_id:
        dedup_key = f"{origin_chat_id}:{message.media_group_id}"
        if dedup_key in _recent_album_comments:
            return
        _recent_album_comments.add(dedup_key)
        if len(_recent_album_comments) > _ALBUM_DEDUP_CAP:
            _recent_album_comments.clear()

    async with session() as s:
        q = select(Channel).where(Channel.chat_id == origin_chat_id)
        res = await s.execute(q)
        channel = res.scalars().first()

    if not channel or not channel.auto_comment_enabled or not channel.auto_comment_text:
        return

    try:
        await message.reply(channel.auto_comment_text)
    except Exception:
        logger.exception(
            "Failed to post auto-comment for channel_id=%s in discussion group chat_id=%s",
            channel.id, message.chat.id,
        )
