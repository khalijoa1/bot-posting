from aiogram import F, Router, types
from aiogram.filters import Command

router = Router()


@router.message(Command("start"))
async def start_handler(message: types.Message):
    """Main menu with keyboard"""
    user_name = message.from_user.first_name if message.from_user else "User"
    
    kb = types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="✏️ Compose"), types.KeyboardButton(text="📋 My Posts")],
            [types.KeyboardButton(text="➕ Add Channel"), types.KeyboardButton(text="📍 Channels")],
            [types.KeyboardButton(text="📁 Categories"), types.KeyboardButton(text="ℹ️ Help")],
        ],
        resize_keyboard=True
    )

    await message.reply(
        f"👋 Welcome {user_name}!\n\n"
        f"🤖 I'm your Telegram Crosspost Bot\n\n"
        f"I can:\n"
        f"  ✅ Post to multiple channels at once\n"
        f"  ✏️ Edit messages across all channels\n"
        f"  🗑️ Delete messages from all channels\n"
        f"  📁 Organize channels in categories\n\n"
        f"🎮 Use the menu below or type commands:",
        reply_markup=kb,
        parse_mode=None
    )


@router.message(F.text == "ℹ️ Help")
async def help_handler(message: types.Message):
    """Help menu"""
    kb = types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="🏠 Back to Menu")],
        ],
        resize_keyboard=True
    )

    await message.reply(
        "📖 COMMANDS:\n\n"
        "📝 MESSAGING:\n"
        "/compose - Post to multiple channels\n"
        "/myposts - View your posts\n"
        "/edit - Edit a post (all channels)\n"
        "/delete - Delete a post (all channels)\n\n"
        "📍 CHANNELS:\n"
        "/add_channel - Add a channel\n"
        "/list_channels - View all channels\n"
        "/delete_channel ID - Remove a channel\n\n"
        "📁 CATEGORIES:\n"
        "/add_category - Create a category\n"
        "/list_categories - View categories\n\n"
        "💡 TIP: Use /compose to post to multiple channels instantly!",
        reply_markup=kb,
        parse_mode=None
    )


@router.message(F.text == "✏️ Compose")
async def compose_button(message: types.Message):
    """Redirect to compose command"""
    await message.bot.send_message(
        chat_id=message.chat.id,
        text="✏️ Starting compose mode...",
        parse_mode=None
    )
    # Trigger compose command
    from handlers.compose import compose_start
    from aiogram.fsm.storage.memory import MemoryStorage
    from aiogram.fsm.context import FSMContext
    storage = MemoryStorage()
    state = FSMContext(storage=storage, key=f"user:{message.from_user.id}:chat:{message.chat.id}")
    await compose_start(message, state)


@router.message(F.text == "📋 My Posts")
async def myposts_button(message: types.Message):
    """Show my posts"""
    from handlers.manage import list_posts
    await list_posts(message)


@router.message(F.text == "➕ Add Channel")
async def addchannel_button(message: types.Message):
    """Start add channel"""
    from handlers.channels import add_channel_start
    from aiogram.fsm.storage.memory import MemoryStorage
    from aiogram.fsm.context import FSMContext
    storage = MemoryStorage()
    state = FSMContext(storage=storage, key=f"user:{message.from_user.id}:chat:{message.chat.id}")
    await add_channel_start(message, state)


@router.message(F.text == "📍 Channels")
async def channels_button(message: types.Message):
    """Show channels"""
    from handlers.channels import list_channels
    await list_channels(message)


@router.message(F.text == "📁 Categories")
async def categories_button(message: types.Message):
    """Show categories"""
    from handlers.categories import list_categories
    await list_categories(message)


@router.message(F.text == "🏠 Back to Menu")
async def back_to_menu(message: types.Message):
    """Back to main menu"""
    kb = types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="✏️ Compose"), types.KeyboardButton(text="📋 My Posts")],
            [types.KeyboardButton(text="➕ Add Channel"), types.KeyboardButton(text="📍 Channels")],
            [types.KeyboardButton(text="📁 Categories"), types.KeyboardButton(text="ℹ️ Help")],
        ],
        resize_keyboard=True
    )
    await message.reply("🏠 Main Menu", reply_markup=kb, parse_mode=None)


@router.message(Command("help"))
async def help_command(message: types.Message):
    """Help command"""
    await help_handler(message)

