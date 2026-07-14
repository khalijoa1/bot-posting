from aiogram import F, Router, types
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select

from db import session
from models import Category, Channel

router = Router()


class AddChannelState(StatesGroup):
    waiting_for_chat_id = State()
    waiting_for_title = State()
    waiting_for_categories = State()


@router.message(Command("add_channel"))
async def add_channel_start(message: types.Message, state: FSMContext):
    """Start the add channel flow"""
    await message.reply(
        "Send the channel/group chat ID (e.g., -1001234567890)",
        parse_mode=None
    )
    await state.set_state(AddChannelState.waiting_for_chat_id)


@router.message(AddChannelState.waiting_for_chat_id)
async def process_chat_id(message: types.Message, state: FSMContext):
    """Process the chat ID input"""
    chat_id_str = message.text.strip()
    try:
        chat_id_int = int(chat_id_str)
    except ValueError:
        await message.reply("Invalid chat ID. Please send a numeric ID (e.g., -1001234567890)")
        return

    async with session() as s:
        q = select(Channel).where(Channel.chat_id == chat_id_int)
        res = await s.execute(q)
        existing = res.scalars().first()
        if existing:
            await message.reply("This channel is already in the database.")
            await state.clear()
            return

    await state.update_data(chat_id=chat_id_int)
    await message.reply("Now send the channel title/name:", parse_mode=None)
    await state.set_state(AddChannelState.waiting_for_title)


@router.message(AddChannelState.waiting_for_title)
async def process_title(message: types.Message, state: FSMContext):
    """Process the title and move to category selection"""
    title = message.text.strip()
    await state.update_data(title=title)

    async with session() as s:
        q = select(Category)
        res = await s.execute(q)
        categories = res.scalars().all()

    if not categories:
        # No categories exist, skip to final add
        await message.reply(
            f"No categories exist yet. Adding channel '{title}' without categories.",
            parse_mode=None
        )
        data = await state.get_data()
        await add_channel_to_db(message, state, data.get("chat_id"), data.get("title"), [])
        await state.clear()
        return

    # Show category selection keyboard
    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text=cat.name, callback_data=f"cat_{cat.id}")]
            for cat in categories
        ] + [[types.InlineKeyboardButton(text="✓ Done", callback_data="cat_done")]]
    )
    await message.reply("Select categories (tap to toggle):", reply_markup=kb, parse_mode=None)
    await state.update_data(selected_categories=[])
    await state.set_state(AddChannelState.waiting_for_categories)


@router.callback_query(AddChannelState.waiting_for_categories, F.data.startswith("cat_"))
async def handle_category_selection(query: types.CallbackQuery, state: FSMContext):
    """Handle category selection toggling"""
    callback_data = query.data

    if callback_data == "cat_done":
        # Finished selecting categories
        data = await state.get_data()
        await add_channel_to_db(
            query.message,
            state,
            data.get("chat_id"),
            data.get("title"),
            data.get("selected_categories", [])
        )
        await query.answer("Channel added!", show_alert=True)
        await state.clear()
        return

    # Toggle category selection
    cat_id = int(callback_data.replace("cat_", ""))
    data = await state.get_data()
    selected = data.get("selected_categories", [])

    if cat_id in selected:
        selected.remove(cat_id)
    else:
        selected.append(cat_id)

    await state.update_data(selected_categories=selected)

    # Rebuild keyboard with visual feedback
    async with session() as s:
        q = select(Category)
        res = await s.execute(q)
        categories = res.scalars().all()

    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=f"{'✓ ' if cat.id in selected else ''}{cat.name}",
                    callback_data=f"cat_{cat.id}"
                )
            ]
            for cat in categories
        ] + [[types.InlineKeyboardButton(text="✓ Done", callback_data="cat_done")]]
    )
    await query.message.edit_reply_markup(reply_markup=kb)
    await query.answer()


async def add_channel_to_db(
    msg: types.Message,
    state: FSMContext,
    chat_id: int,
    title: str,
    category_ids: list
):
    """Add channel to database with selected categories"""
    async with session() as s:
        owner_id = msg.from_user.id if msg.from_user else 0
        ch = Channel(owner_user_id=owner_id, chat_id=chat_id, title=title)
        s.add(ch)
        await s.flush()

        # Add to categories
        if category_ids:
            q = select(Category).where(Category.id.in_(category_ids))
            res = await s.execute(q)
            categories = res.scalars().all()
            ch.categories = categories

        await s.commit()

    cat_names = ", ".join([f"'{c}'" for c in category_ids]) if category_ids else "none"
    await msg.reply(
        f"✓ Channel '{title}' (ID: {chat_id}) added with categories: {cat_names}",
        parse_mode=None
    )


@router.message(Command("list_channels"))
async def list_channels(message: types.Message):
    """List all channels"""
    async with session() as s:
        q = select(Channel)
        res = await s.execute(q)
        rows = res.scalars().all()
        if not rows:
            await message.reply("No channels registered", parse_mode=None)
            return
        lines = [f"ID={r.id} chat_id={r.chat_id} title={r.title}" for r in rows]
        await message.reply("\n".join(lines), parse_mode=None)


@router.message(Command("delete_channel"))
async def delete_channel(message: types.Message):
    """Delete a channel"""
    args = message.get_args()
    if not args:
        await message.reply("Usage: /delete_channel ID", parse_mode=None)
        return
    key = args.strip()
    async with session() as s:
        try:
            val = int(key)
        except ValueError:
            await message.reply("Invalid ID", parse_mode=None)
            return
        q = select(Channel).where((Channel.chat_id == val) | (Channel.id == val))
        res = await s.execute(q)
        row = res.scalars().first()
        if not row:
            await message.reply("Channel not found", parse_mode=None)
            return
        await s.delete(row)
        await s.commit()
        await message.reply("Channel deleted", parse_mode=None)

