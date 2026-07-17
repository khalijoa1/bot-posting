"""Post to all channels in a category, with optional auto-delete."""
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from datetime import datetime, timedelta
from sqlalchemy import select

from db import session
from handlers.common import auto_delete_kb, main_menu_kb, parse_duration
from models import Category, ContentType, Post, PostStatus, PostTarget

router = Router()


class CategoryPostState(StatesGroup):
    select_category = State()
    message_text = State()
    auto_delete = State()


@router.message(Command("post_category"))
async def post_category_start(message: types.Message, state: FSMContext):
    """Start posting to category."""
    await state.clear()

    async with session() as s:
        q = select(Category)
        res = await s.execute(q)
        categories = res.scalars().all()

    if not categories:
        await message.answer(
            "❌ No categories yet\n\n"
            "Create categories first"
        )
        return

    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text=cat.name, callback_data=f"postcat_{cat.id}")]
            for cat in categories
        ] + [
            [types.InlineKeyboardButton(text="❌ Cancel", callback_data="postcat_cancel")]
        ]
    )

    await message.answer(
        "━━━━━━━━━━━━━━━━━━\n"
        "📁 POST TO CATEGORY\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Select category:",
        reply_markup=kb
    )
    await state.set_state(CategoryPostState.select_category)


@router.callback_query(CategoryPostState.select_category)
async def handle_category_select(query: types.CallbackQuery, state: FSMContext):
    """Handle category selection."""
    if query.data == "postcat_cancel":
        await state.clear()
        await query.message.answer("❌ Cancelled", reply_markup=main_menu_kb())
        await query.answer()
        return

    cat_id = int(query.data.replace("postcat_", ""))

    async with session() as s:
        cat = await s.get(Category, cat_id)
        if not cat or not cat.channels:
            await query.answer("No channels in this category", show_alert=True)
            return
        cat_name = cat.name
        cat_channel_count = len(cat.channels)

    await state.update_data(category_id=cat_id)
    await query.message.answer(
        f"📁 {cat_name}\n\n"
        f"Channels: {cat_channel_count}\n\n"
        f"Send message text, or a photo/video (with an optional caption):",
        reply_markup=types.ReplyKeyboardMarkup(
            keyboard=[[types.KeyboardButton(text="❌ Cancel")]],
            resize_keyboard=True
        )
    )
    await state.set_state(CategoryPostState.message_text)
    await query.answer()


async def _ask_category_auto_delete(message: types.Message, state: FSMContext) -> None:
    await message.answer(
        "🗑️ AUTO-DELETE?\n\n"
        "Delete this post automatically after a delay?\n"
        "(Timer starts as soon as it's posted)",
        reply_markup=auto_delete_kb("cad")
    )
    await state.set_state(CategoryPostState.auto_delete)


@router.message(CategoryPostState.message_text, F.text == "❌ Cancel")
async def cancel_category_text(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Cancelled", reply_markup=main_menu_kb())


@router.message(CategoryPostState.message_text, F.photo)
async def handle_category_photo(message: types.Message, state: FSMContext):
    """Capture a photo (largest size) plus its optional caption."""
    file_id = message.photo[-1].file_id
    caption = (message.caption or "").strip()
    await state.update_data(content_type="photo", photo_file_id=file_id, video_file_id=None, text=caption)
    await _ask_category_auto_delete(message, state)


@router.message(CategoryPostState.message_text, F.video)
async def handle_category_video(message: types.Message, state: FSMContext):
    """Capture a video plus its optional caption."""
    file_id = message.video.file_id
    caption = (message.caption or "").strip()
    await state.update_data(content_type="video", video_file_id=file_id, photo_file_id=None, text=caption)
    await _ask_category_auto_delete(message, state)


@router.message(CategoryPostState.message_text, F.text)
async def handle_category_text(message: types.Message, state: FSMContext):
    """Capture the message text, then ask about auto-delete."""
    await state.update_data(content_type="text", text=message.text.strip(), photo_file_id=None, video_file_id=None)
    await _ask_category_auto_delete(message, state)


@router.callback_query(CategoryPostState.auto_delete, F.data.startswith("cad_"))
async def handle_category_auto_delete(query: types.CallbackQuery, state: FSMContext):
    """Handle a preset auto-delete choice, then post to the category."""
    choice = query.data.replace("cad_", "")

    if choice == "cancel":
        await state.clear()
        await query.message.answer("❌ Cancelled", reply_markup=main_menu_kb())
        await query.answer()
        return

    if choice == "custom":
        await query.message.answer(
            "Send auto-delete duration, e.g. 30m, 2h, 1d, or 'no':",
            reply_markup=types.ReplyKeyboardMarkup(
                keyboard=[[types.KeyboardButton(text="❌ Cancel")]],
                resize_keyboard=True
            )
        )
        await query.answer()
        return

    auto_delete_seconds = None if choice == "no" else int(choice)
    await query.answer()
    await do_category_post(state, query.from_user.id, query.message.answer, auto_delete_seconds)


@router.message(CategoryPostState.auto_delete, F.text == "❌ Cancel")
async def cancel_category_auto_delete(message: types.Message, state: FSMContext):
    """Cancel while typing a custom auto-delete duration."""
    await state.clear()
    await message.answer("❌ Cancelled", reply_markup=main_menu_kb())


@router.message(CategoryPostState.auto_delete, F.text)
async def handle_category_custom_auto_delete(message: types.Message, state: FSMContext):
    """Parse a custom duration like 30m, 2h, 1d, or 'no', then post."""
    try:
        seconds = parse_duration(message.text)
    except ValueError:
        await message.answer("❌ Format like 30m, 2h, 1d, or 'no'")
        return

    await do_category_post(state, message.from_user.id, message.answer, seconds)


async def do_category_post(state: FSMContext, user_id: int, answer, auto_delete_seconds: int | None) -> None:
    """Send the composed text/photo/video to every channel in the chosen category."""
    data = await state.get_data()
    cat_id = data.get("category_id")
    text = data.get("text")
    content_type = data.get("content_type", "text")
    photo_file_id = data.get("photo_file_id")
    video_file_id = data.get("video_file_id")

    success = 0
    failed = []

    async with session() as s:
        cat = await s.get(Category, cat_id)
        channels = cat.channels if cat else []

        delete_at = datetime.now() + timedelta(seconds=auto_delete_seconds) if auto_delete_seconds else None

        post = Post(
            owner_user_id=user_id,
            content_type=ContentType(content_type),
            text=text,
            photo_file_id=photo_file_id,
            video_file_id=video_file_id,
            status=PostStatus.SENT,
            auto_delete_seconds=auto_delete_seconds,
            delete_at=delete_at,
        )
        s.add(post)
        await s.flush()

        # `answer` is a bound method on the Message/CallbackQuery that carries
        # its own bot instance, so grab it from there instead of importing
        # a fresh Bot.
        bot = answer.__self__.bot

        for ch in channels:
            try:
                if content_type == "photo" and photo_file_id:
                    msg = await bot.send_photo(chat_id=ch.chat_id, photo=photo_file_id, caption=text or None)
                elif content_type == "video" and video_file_id:
                    msg = await bot.send_video(chat_id=ch.chat_id, video=video_file_id, caption=text or None)
                else:
                    msg = await bot.send_message(chat_id=ch.chat_id, text=text or "")
                target = PostTarget(
                    post_id=post.id,
                    channel_id=ch.id,
                    message_id=msg.message_id,
                    sent_at=datetime.now(),
                )
                s.add(target)
                success += 1
            except Exception:
                failed.append(ch.title)

        await s.commit()
        post_id = post.id

    result = (
        f"✅ POSTED TO CATEGORY!\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Channels: {success}/{len(channels)}\n"
        f"Post ID: {post_id}\n"
        f"━━━━━━━━━━━━━━━━━━"
    )

    if failed:
        result += f"\n\n❌ Failed:\n" + "\n".join(failed)

    await answer(result, reply_markup=main_menu_kb())
    await state.clear()


@router.message(lambda msg: msg.text == "📨 Post to Category")
async def category_post_button(message: types.Message, state: FSMContext):
    """Post to category from menu."""
    await post_category_start(message, state)
