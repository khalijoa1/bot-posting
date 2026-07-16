from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update
from sqlalchemy import select

from config import get_settings

GROUP_CHAT_TYPES = {"group", "supergroup"}

# Reply-keyboard button texts used by the operator's private menus. Blocked
# even inside a registered moderated group, so a message that happens to
# match one of these exactly can't be used to drive the operator's private
# flows (composing posts, adding channels, etc.) from a group chat.
_MENU_BUTTON_TEXTS = {
    "📨 MESSAGING", "📍 CHANNELS", "📁 CATEGORIES", "⚙️ SETTINGS", "📊 ANALYTICS", "❓ HELP",
    "🛡️ MODERATION",
    "✏️ Compose & Post", "📨 Post to Category", "📋 View My Posts", "✎️ Edit Post",
    "🗑️ Delete Post", "🔗 Link Replacer", "🔙 Back", "🔙 Cancel", "❌ Cancel",
    "➕ Add Channel", "📋 List Channels", "🗑️ Delete Channel",
    "➕ Add Category", "📋 List Categories",
    "🔐 Auto-Approve Members", "🛡️ Moderation", "⏭️ Skip",
}


async def _is_moderated_group(chat_id: int) -> bool:
    """Whether the operator has explicitly registered this chat with /add_group."""
    from db import session
    from models import ModeratedGroup

    async with session() as s:
        q = select(ModeratedGroup.id).where(ModeratedGroup.chat_id == chat_id)
        res = await s.execute(q)
        return res.scalar() is not None


class AllowlistMiddleware(BaseMiddleware):
    """Silently drops any update from a user not in ALLOWED_USER_IDS.

    Without this, anyone who finds the bot on Telegram could issue commands
    that post into the operator's channels.

    One narrow, opt-in exception: plain (non-command, non-menu-button)
    text/media messages inside a group the operator has explicitly
    registered with /add_group are let through regardless of sender, so the
    moderation feature can see and act on every member's messages. Commands
    and the operator's private-menu button texts stay gated everywhere,
    including inside moderated groups, so this can't be used to drive the
    operator's private flows from a group.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = None

        if isinstance(event, Update):
            if event.message:
                user = event.message.from_user
                msg = event.message
                text = msg.text or msg.caption or ""
                is_group = msg.chat.type in GROUP_CHAT_TYPES
                is_command_or_menu = text.startswith("/") or text in _MENU_BUTTON_TEXTS
                if is_group and not is_command_or_menu and await _is_moderated_group(msg.chat.id):
                    return await handler(event, data)
            elif event.callback_query:
                user = event.callback_query.from_user

        if user is not None and user.id not in get_settings().allowed_user_id_set:
            return None

        return await handler(event, data)
