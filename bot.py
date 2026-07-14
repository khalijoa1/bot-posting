import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from config import get_settings
from db import init_db
from handlers import analytics, categories, channels, compose, manage, start, subscribers
from middleware import AllowlistMiddleware

logging.basicConfig(level=logging.INFO)


async def main() -> None:
    settings = get_settings()
    await init_db()

    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())

    dp.update.outer_middleware(AllowlistMiddleware())
    
    # Include all handlers
    dp.include_router(start.router)
    dp.include_router(channels.router)
    dp.include_router(categories.router)
    dp.include_router(compose.router)
    dp.include_router(manage.router)
    dp.include_router(analytics.router)
    dp.include_router(subscribers.router)

    try:
        await dp.start_polling(bot)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    asyncio.run(main())

