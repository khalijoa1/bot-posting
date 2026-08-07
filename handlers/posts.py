"""Post management - view, edit, delete."""
import json

from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from db import session
from handlers.common import format_duration, main_menu_kb
from models import ContentType, Post, PostStatus, PostTarget

router = Router()


def _cancel_kb() -> types.ReplyKeyboardMarkup:
    """Shared Cancel keyboard for this file's edit/delete flows, reattached
    on every retry prompt so a bad input never leaves the user stuck."""
    return types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text="🔙 Cancel")]],
        resize_keyboard=True,
    )


class EditState(StatesGroup):
    post_id = State()
    new_text = State()


class DeleteState(StatesGroup):
    post_id = State()


@router.message(Command("myposts"))
async def list_posts(message: types.Message, user_id: int | None = None):
    """List all user's posts."""
    owner_id = user_id if user_id is not None else message.from_user.id
    async with session() as s:
        q = select(Post).where(Post.owner_user_id == owner_id)
        res = await s.execute(q)
        posts = res.scalars().all()

    if not posts:
        await message.answer(
            "━━━━━━━━━━━━━━━━\n"
            "📋 MY POSTS\n"
            "━━━━━━━━━━━━━━━━\n\n"
            "❌ No posts yet\n\n"
            "Use /compose to create a post"
        )
        return

    text = "━━━━━━━━━━━━━━━━\n📋 MY POSTS\n━━━━━━━━━━━━━━━━━\n\n"

    for p in posts:
        async with session() as s:
            tq = select(PostTarget).where(PostTarget.post_id == p.id)
            tres = await s.execute(tq)
            targets = tres.scalars().all()

        preview = (p.text or "")[:60]
        text += (
            f"━━━━━━━━━━━━━━━━━\n"
            f"ID: {p.id}\n"
            f"Text: {preview}{'...' if len(p.text or '') > 60 else ''}\n"
            f"Channels: {len(targets)}\n"
            f"Status: {p.status.value}\n"
        )
        if p.repeat_interval_seconds:
            text += f"🔁 Repeats every {format_duration(p.repeat_interval_seconds)}\n"
        text += "\n"

    await message.answer(text)


@router.message(Command("edit"))
async def edit_start(message: types.Message, state: FSMContext):
    """Start edit workflow."""
    await state.clear()
    kb = types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text="🔙 Cancel")]],
        resize_keyboard=True
    )
    await message.answer(
        "━━━━━━━━━━━━━━━━━━\n"
        "✎️ EDIT POST\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Send the Post ID to edit\n\n"
        "(Use /myposts to see IDs):",
        reply_markup=kb
    )
    await state.set_state(EditState.post_id)


@router.message(EditState.post_id, F.text == "🔙 Cancel")
async def cancel_edit(message: types.Message, state: FSMContext):
    """Cancel edit."""
    await state.clear()
    await message.answer("❌ Cancelled", reply_markup=main_menu_kb())


@router.message(EditState.post_id, F.text)
async def get_post_id(message: types.Message, state: FSMContext):
    """Get post ID to edit."""
    try:
        post_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Invalid ID. Send a number", reply_markup=_cancel_kb())
        return

    async with session() as s:
        post = await s.get(Post, post_id)
        if not post or post.owner_user_id != message.from_user.id:
            await message.answer("❌ Post not found or not yours", reply_markup=_cancel_kb())
            return

        tq = select(PostTarget).where(PostTarget.post_id == post_id)
        tres = await s.execute(tq)
        targets = tres.scalars().all()

    if not targets:
        await message.answer("❌ No active messages for this post", reply_markup=_cancel_kb())
        return

    await state.update_data(post_id=post_id)
    kb = types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text="🔙 Cancel")]],
        resize_keyboard=True
    )
    await message.answer(
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Post ID: {post_id}\n"
        f"Channels: {len(targets)}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Send the NEW TEXT (or new caption, if this is a photo/video/album post):\n"
        f"(Will update in all {len(targets)} channels. For an album, only the "
        f"first item's caption is changed, since that's the only one Telegram "
        f"shows.)",
        reply_markup=kb
    )
    await state.set_state(EditState.new_text)


@router.message(EditState.new_text, F.text == "🔙 Cancel")
async def cancel_edit_text(message: types.Message, state: FSMContext):
    """Cancel edit text."""
    await state.clear()
    await message.answer("❌ Cancelled", reply_markup=main_menu_kb())


@router.message(EditState.new_text, F.text)
async def apply_edit(message: types.Message, state: FSMContext):
    """Apply edit to all channels."""
    data = await state.get_data()
    post_id = data.get("post_id")
    new_text = message.text.strip()
    bot = message.bot

    async with session() as s:
        post = await s.get(Post, post_id)
        post.text = new_text
        s.add(post)

        tq = select(PostTarget).where(PostTarget.post_id == post_id).options(selectinload(PostTarget.channel))
        tres = await s.execute(tq)
        targets = tres.scalars().all()

        success = 0
        failed = []
        for target in targets:
            if target.message_id is None:
                continue
            try:
                if post.content_type == ContentType.TEXT:
                    await bot.edit_message_text(
                        chat_id=target.channel.chat_id,
                        message_id=target.message_id,
                        text=new_text
                    )
                else:
                    # Photo/video/album posts don't have a "text" body - the
                    # typed replacement becomes the new caption instead
                    # (on the first message, for an album).
                    await bot.edit_message_caption(
                        chat_id=target.channel.chat_id,
                        message_id=target.message_id,
                        caption=new_text
                    )
                success += 1
            except Exception:
                failed.append(target.channel.title)

        await s.commit()

    result = (
        f"✅ EDITED!\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Channels: {success}/{len(targets)}\n"
        f"━━━━━━━━━━━━━━━━━━"
    )

    if failed:
        result += f"\n\n❌ Failed:\n" + "\n".join([f" • {c}" for c in failed])

    await message.answer(result, reply_markup=main_menu_kb())
    await state.clear()


@router.message(Command("delete"))
async def delete_start(message: types.Message, state: FSMContext):
    """Start delete workflow. Uses its own state - previously this reused
    EditState.post_id, which meant /delete silently fell into the edit flow
    and asked for replacement text instead of ever deleting anything."""
    await state.clear()
    kb = types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text="🔙 Cancel")]],
        resize_keyboard=True
    )
    await message.answer(
        "━━━━━━━━━━━━━━━━━━\n"
        "🗑️ DELETE POST\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Send the Post ID to delete\n\n"
        "(Use /myposts to see IDs):",
        reply_markup=kb
    )
    await state.set_state(DeleteState.post_id)


@router.message(DeleteState.post_id, F.text == "🔙 Cancel")
async def cancel_delete(message: types.Message, state: FSMContext):
    """Cancel delete."""
    await state.clear()
    await message.answer("❌ Cancelled", reply_markup=main_menu_kb())


@router.message(DeleteState.post_id, F.text)
async def choose_delete_target(message: types.Message, state: FSMContext):
    """After sending a Post ID, choose whether to delete the post from
    every channel, or from just one channel of your choosing - the latter
    is what lets you stop a repeating post from posting into ONE channel
    while it keeps cycling normally everywhere else, instead of having to
    kill the whole post."""
    try:
        post_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Invalid ID. Send a number", reply_markup=_cancel_kb())
        return

    async with session() as s:
        post = await s.get(Post, post_id)
        if not post or post.owner_user_id != message.from_user.id:
            await message.answer("❌ Post not found or not yours", reply_markup=main_menu_kb())
            await state.clear()
            return

        tq = select(PostTarget).where(PostTarget.post_id == post_id).options(selectinload(PostTarget.channel))
        tres = await s.execute(tq)
        targets = tres.scalars().all()
        repeats = post.repeat_interval_seconds is not None

    if not targets:
        await message.answer("❌ No active channels for this post", reply_markup=main_menu_kb())
        await state.clear()
        return

    rows = [
        [types.InlineKeyboardButton(
            text=f"🗑️ Remove from {t.channel.title}",
            callback_data=f"delch_{post_id}_{t.channel_id}"
        )]
        for t in targets
    ]
    rows.append([types.InlineKeyboardButton(text="🗑️ Delete from ALL channels", callback_data=f"delall_{post_id}")])
    rows.append([types.InlineKeyboardButton(text="🔙 Cancel", callback_data="delcancel")])

    repeat_note = (
        "\n🔁 This post repeats - removing just one channel keeps it "
        "cycling normally in the rest."
        if repeats else ""
    )
    await message.answer(
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Post ID: {post_id}\n"
        f"Channels: {len(targets)}{repeat_note}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Choose what to remove:",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows)
    )
    await state.clear()


@router.callback_query(F.data.startswith("delch_"))
async def delete_from_one_channel(query: types.CallbackQuery):
    """Remove this post from exactly one channel. If that was the last
    channel it was still targeting, the whole post is marked DELETED -
    otherwise it stays active (and keeps repeating, if it's a repeating
    post) in every channel that wasn't removed."""
    _, post_id_s, channel_id_s = query.data.split("_")
    post_id, channel_id = int(post_id_s), int(channel_id_s)
    bot = query.bot

    async with session() as s:
        post = await s.get(Post, post_id)
        if not post or post.owner_user_id != query.from_user.id:
            await query.answer("Not found or not yours", show_alert=True)
            return

        tq = select(PostTarget).where(
            PostTarget.post_id == post_id, PostTarget.channel_id == channel_id
        ).options(selectinload(PostTarget.channel))
        tres = await s.execute(tq)
        target = tres.scalars().first()
        if not target:
            await query.answer("Already removed", show_alert=True)
            return

        title = target.channel.title
        ids = [target.message_id] if target.message_id is not None else []
        if target.extra_message_ids:
            try:
                ids.extend(json.loads(target.extra_message_ids))
            except Exception:
                pass

        deleted_ok = True
        for mid in ids:
            try:
                await bot.delete_message(chat_id=target.channel.chat_id, message_id=mid)
            except Exception:
                deleted_ok = False

        await s.delete(target)

        remaining_q = select(PostTarget).where(PostTarget.post_id == post_id)
        remaining_res = await s.execute(remaining_q)
        remaining_count = len(remaining_res.scalars().all())
        fully_deleted = remaining_count == 0
        if fully_deleted:
            post.status = PostStatus.DELETED

        await s.commit()

    note = "" if deleted_ok else "\n⚠️ Message may have already been removed from Telegram."
    await query.answer()
    if fully_deleted:
        await query.message.edit_text(
            f"✅ Removed from {title} - that was the last channel, so the "
            f"post is now fully deleted.{note}"
        )
    else:
        await query.message.edit_text(
            f"✅ Removed from {title}. Still active in {remaining_count} "
            f"other channel(s).{note}"
        )


@router.callback_query(F.data.startswith("delall_"))
async def delete_from_all_channels(query: types.CallbackQuery):
    """Delete the post from every channel it targets."""
    post_id = int(query.data.replace("delall_", ""))
    bot = query.bot

    async with session() as s:
        post = await s.get(Post, post_id)
        if not post or post.owner_user_id != query.from_user.id:
            await query.answer("Not found or not yours", show_alert=True)
            return

        tq = select(PostTarget).where(PostTarget.post_id == post_id).options(selectinload(PostTarget.channel))
        tres = await s.execute(tq)
        targets = tres.scalars().all()

        success = 0
        failed = []
        for target in targets:
            # For an ALBUM post this also deletes the other items stored in
            # extra_message_ids - previously only the single message_id was
            # ever deleted, so a 3-video album post would leave 2 stray
            # videos behind in the channel after "deleting" it.
            ids = [target.message_id] if target.message_id is not None else []
            if target.extra_message_ids:
                try:
                    ids.extend(json.loads(target.extra_message_ids))
                except Exception:
                    pass
            if ids:
                try:
                    for mid in ids:
                        await bot.delete_message(chat_id=target.channel.chat_id, message_id=mid)
                    success += 1
                except Exception:
                    failed.append(target.channel.title)
            await s.delete(target)

        post.status = PostStatus.DELETED
        await s.commit()

    result = f"✅ DELETED from {success} channel(s)"
    if failed:
        result += f"\n\n❌ Failed:\n" + "\n".join([f" • {c}" for c in failed])

    await query.answer()
    await query.message.edit_text(result)


@router.callback_query(F.data == "delcancel")
async def cancel_delete_cb(query: types.CallbackQuery):
    """Cancel from the per-channel delete-choice keyboard."""
    await query.answer()
    await query.message.edit_text("❌ Cancelled")


@router.message(lambda msg: msg.text == "📋 View My Posts")
async def view_posts_button(message: types.Message):
    """View posts from menu."""
    await list_posts(message)


@router.message(lambda msg: msg.text == "✎️ Edit Post")
async def edit_button(message: types.Message, state: FSMContext):
    """Edit from menu."""
    await edit_start(message, state)


@router.message(lambda msg: msg.text == "🗑️ Delete Post")
async def delete_button(message: types.Message, state: FSMContext):
    """Delete from menu."""
    await delete_start(message, state)
