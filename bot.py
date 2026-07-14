import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from config import get_settings
from db import init_db
from handlers import categories, channels, repost_rules, sources, reposter, start
from middleware import AllowlistMiddleware
from services.scheduler import run_scheduler_loop

logging.basicConfig(level=logging.INFO)


async def main() -> None:
    settings = get_settings()
    await init_db()

    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())

    dp.update.outer_middleware(AllowlistMiddleware())
    dp.include_router(start.router)
    dp.include_router(channels.router)
    dp.include_router(categories.router)
    dp.include_router(repost_rules.router)
    dp.include_router(sources.router)
    dp.include_router(reposter.router)

    scheduler_task = asyncio.create_task(run_scheduler_loop(bot))
    try:
        await dp.start_polling(bot)
    finally:
        scheduler_task.cancel()


if __name__ == "__main__":
    asyncio.run(main())

