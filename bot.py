import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from config import get_settings
from db import init_db
from handlers import menu, compose, posts, channels, categories, analytics, settings, category_post
from middleware import AllowlistMiddleware

logging.basicConfig(level=logging.INFO)


async def main() -> None:
    settings_obj = get_settings()
    await init_db()

    bot = Bot(token=settings_obj.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())

    dp.update.outer_middleware(AllowlistMiddleware())
    
    # Include all handlers in proper order
    dp.include_router(menu.router)
    dp.include_router(compose.router)
    dp.include_router(category_post.router)
    dp.include_router(posts.router)
    dp.include_router(channels.router)
    dp.include_router(categories.router)
    dp.include_router(analytics.router)
    dp.include_router(settings.router)

    try:
        await dp.start_polling(bot)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    asyncio.run(main())

