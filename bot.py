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
from services.storage_channel import run_storage_sync
from services import half_reply
from services import file_share
from services.half_reply import run_half_reply_loop

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
    dp.include_router(half_reply.router)
    dp.include_router(file_share.router)
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

    # Slash-command menu (the list that pops up when you type "/")
    try:
        from aiogram.types import BotCommand as _BC
        _cmds = [
            ("compose", "Post to channels (schedule, auto-delete, repeat)"),
            ("bulkpost", "Forward several messages, post them all at once"),
            ("post_category", "Post to a category"),
            ("myposts", "View your posts"),
            ("edit", "Edit a post"),
            ("delete", "Delete a post"),
            ("repeat", "Set repeat: /repeat 123 6h"),
            ("autodelete", "Set auto-delete: /autodelete 123 6h (0 = off)"),
            ("pause", "Stop a post repeating: /pause 123"),
            ("replacer", "Replace links"),
            ("replace_link", "Replace a link in all posts: OLD NEW [yes]"),
            ("scan_links", "Edit every channel post that has an old link"),
            ("swap_add", "Save a link swap rule: OLD NEW"),
            ("swap_list", "Show saved link swap rules"),
            ("swap_remove", "Remove a link swap rule"),
            ("check_channels", "Find and remove deleted channels"),
            ("add_channel", "Add a channel"),
            ("list_channels", "View channels"),
            ("delete_channel", "Remove a channel"),
            ("add_category", "Create a category"),
            ("list_categories", "View categories"),
            ("add_group", "Register a group"),
            ("list_groups", "View groups"),
            ("remove_group", "Stop moderating a group"),
            ("moderation", "Configure moderation rules"),
            ("add_source", "Watch a channel"),
            ("list_sources", "View watched channels"),
            ("remove_source", "Stop watching a channel"),
            ("add_rule", "Forward source to your channel"),
            ("list_rules", "View forwarding rules"),
            ("remove_rule", "Remove a forwarding rule"),
            ("autoapprove", "Auto-approve join requests"),
            ("autocomment", "Fixed comment under every post"),
            ("broadcast", "Message everyone who sent a join request"),
            ("analytics", "Stats and channel growth"),
            ("files", "File sharing"),
            ("reply_pause", "Pause auto replies"),
            ("reply_resume", "Resume auto replies"),
            ("help", "Show all commands"),
            ("cancel", "Cancel what you are doing"),
        ]
        await bot.set_my_commands([_BC(command=c, description=t) for c, t in _cmds])
    except Exception as _exc:
        logging.getLogger(__name__).warning("set_my_commands failed: %s", _exc)

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
    asyncio.create_task(run_storage_sync(bot))
    asyncio.create_task(run_half_reply_loop(bot))

    try:
        await dp.start_polling(bot)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    asyncio.run(main())
