from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

from config import get_settings


class AllowlistMiddleware(BaseMiddleware):
    """Silently drops any update from a user not in ALLOWED_USER_IDS.

    Without this, anyone who finds the bot on Telegram could issue commands
    that post into the operator's channels.
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
            elif event.callback_query:
                user = event.callback_query.from_user

        if user is not None and user.id not in get_settings().allowed_user_id_set:
            return None

        return await handler(event, data)
