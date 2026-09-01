import asyncio
import logging

from aiogram import Bot, Dispatcher, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from config import get_settings
from db import init_db
from handlers import (
    menu,
    compose,
    bulkpost,
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
    broadcast,
    group_messages,
    moderation,
)
from middleware import AllowlistMiddleware
from services.scheduler import run_scheduler_loop, run_post_send_loop
from services.stats import run_channel_stats_loop
from services.telethon_client import run_userbot
from services.recurring import run_recurring_messages_loop
from services.dashboard import run_dashboard

logging.basicConfig(level=logging.INFO)


async def main() -> None:
    settings_obj = get_settings()
    await init_db()

    bot = Bot(token=settings_obj.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())

    if settings_obj.dashboard_url:
        try:
            await bot.set_chat_menu_button(
                menu_button=types.MenuButtonWebApp(
                    text="📊 Dashboard",
                    web_app=types.WebAppInfo(url=settings_obj.dashboard_url),
                )
            )
        except Exception:
            logging.exception("Failed to set dashboard menu button")

    dp.update.outer_middleware(AllowlistMiddleware())

    # Include all handlers in proper order
    dp.include_router(menu.router)
    dp.include_router(compose.router)
    dp.include_router(bulkpost.router)
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
    dp.include_router(broadcast.router)
    # group_messages.router has a `F.new_chat_members` handler that must be
    # included BEFORE moderation.router - moderation.router's broad "any
    # group message" catch-all would otherwise also match a "new member
    # joined" service message, and aiogram stops walking further routers
    # once an earlier one's handler matches, so ordering here decides
    # which one actually gets it.
    dp.include_router(group_messages.router)
    # moderation.router has a broad "any group message" catch-all handler,
    # so it must be included LAST - otherwise it would swallow messages
    # (including the operator's own commands sent inside a group) before
    # the more specific routers above get a chance to handle them.
    dp.include_router(moderation.router)

    # Background jobs: auto-delete of sent posts, sending of due scheduled posts,
    # the optional Telethon userbot that watches source channels for reposting,
    # periodic channel member-count snapshots for /analytics growth stats, and
    # sending due recurring group messages.
    asyncio.create_task(run_scheduler_loop(bot))
    asyncio.create_task(run_post_send_loop(bot))
    asyncio.create_task(run_userbot(bot))
    asyncio.create_task(run_channel_stats_loop(bot))
    asyncio.create_task(run_recurring_messages_loop(bot))
    asyncio.create_task(run_dashboard())

    try:
        await dp.start_polling(bot)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    asyncio.run(main())
