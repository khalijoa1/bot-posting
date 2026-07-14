"""Simple analytics."""
from aiogram import Router, types
from aiogram.filters import Command
from sqlalchemy import func, select

from db import session
from models import Post, PostTarget, PostStatus, Category

router = Router()


@router.message(Command("analytics"))
async def analytics(message: types.Message):
    """Show analytics."""
    async with session() as s:
        # Total posts
        total_q = select(func.count(Post.id)).where(Post.owner_user_id == message.from_user.id)
        total = (await s.execute(total_q)).scalar() or 0
        
        # Sent
        sent_q = select(func.count(Post.id)).where(
            (Post.owner_user_id == message.from_user.id) &
            (Post.status == PostStatus.SENT)
        )
        sent = (await s.execute(sent_q)).scalar() or 0
        
        # Messages sent
        msg_q = select(func.count(PostTarget.id)).join(Post).where(
            Post.owner_user_id == message.from_user.id
        )
        messages = (await s.execute(msg_q)).scalar() or 0
    
    await message.answer(
        f"📊 ANALYTICS:\n\n"
        f"📝 Total Posts: {total}\n"
        f"✅ Sent: {sent}\n"
        f"📤 Messages Delivered: {messages}"
    )


@router.message(Command("add_category"))
async def add_category(message: types.Message):
    """Add a category - expects name in message."""
    # Get the category name from user input
    args = message.text.replace("/add_category", "").strip()
    
    if not args:
        await message.answer("📁 Send category name:\n\n/add_category MyCategory")
        return
    
    async with session() as s:
        cat = Category(
            owner_user_id=message.from_user.id,
            name=args
        )
        s.add(cat)
        await s.commit()
    
    await message.answer(f"✅ Category created: {args}")


@router.message(Command("list_categories"))
async def list_categories(message: types.Message):
    """List categories."""
    async with session() as s:
        q = select(Category)
        res = await s.execute(q)
        cats = res.scalars().all()
    
    if not cats:
        await message.answer("📁 No categories yet.\n\nUse /add_category to create one.")
        return
    
    text = "📁 CATEGORIES:\n\n"
    for c in cats:
        text += f"ID: {c.id}\nName: {c.name}\n\n"
    
    await message.answer(text.strip())

