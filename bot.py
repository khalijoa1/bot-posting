import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from config import get_settings
from db import init_db
from handlers import (
    menu,
    compose,
    category_post,
    posts,
    channels,
    categories,
    analytics,
    settings,
    replacer,
    sources,
    repost_rules,
    join_requests,
    moderation,
)
from middleware import AllowlistMiddleware
from services.scheduler import run_scheduler_loop, run_post_send_loop
from services.telethon_client import run_userbot

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
    dp.include_router(replacer.router)
    dp.include_router(analytics.router)
    dp.include_router(settings.router)
    dp.include_router(sources.router)
    dp.include_router(repost_rules.router)
    dp.include_router(join_requests.router)
    # moderation.router has a broad "any group message" catch-all handler,
    # so it must be included LAST - otherwise it would swallow messages
    # (including the operator's own commands sent inside a group) before
    # the more specific routers above get a chance to handle them.
    dp.include_router(moderation.router)

    # Background jobs: auto-delete of sent posts, sending of due scheduled posts,
    # and the optional Telethon userbot that watches source channels for reposting.
    asyncio.create_task(run_scheduler_loop(bot))
    asyncio.create_task(run_post_send_loop(bot))
    asyncio.create_task(run_userbot(bot))

    try:
        await dp.start_polling(bot)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    asyncio.run(main())
