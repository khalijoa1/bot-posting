"""Shared helpers for post-composition handlers (auto-delete duration UI,
album/media-group buffering) and app-wide navigation (the persistent
main-menu keyboard)."""
import asyncio

from aiogram import types


def main_menu_kb() -> types.InlineKeyboardMarkup:
    """The main-menu inline keyboard.

    Every flow (compose, add channel, add category, link replacer, etc.)
    should attach this when it finishes or is cancelled, instead of
    removing the keyboard or leaving a stale sub-menu keyboard behind.
    Previously many flows did the latter, which left the user with no way
    back except typing /start again.

    This used to be a persistent ReplyKeyboardMarkup (a tall stack of
    buttons pinned under the text box). It's now an inline keyboard
    attached directly to each menu message instead: tapping a button edits
    that same message in place rather than sending a new one, which is
    faster to navigate and keeps the chat from filling up with one throwaway
    keyboard message per tap.
    """
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(text="📨 Messaging", callback_data="menu:messaging"),
                types.InlineKeyboardButton(text="📍 Channels", callback_data="menu:channels"),
            ],
            [
                types.InlineKeyboardButton(text="📁 Categories", callback_data="menu:categories"),
                types.InlineKeyboardButton(text="🛡️ Moderation", callback_data="menu:moderation"),
            ],
            [
                types.InlineKeyboardButton(text="📡 Forwarding", callback_data="fwd:root"),
                types.InlineKeyboardButton(text="⚙️ Settings", callback_data="menu:settings"),
            ],
            [
                types.InlineKeyboardButton(text="📊 Analytics", callback_data="menu:analytics"),
                types.InlineKeyboardButton(text="❓ Help", callback_data="menu:help"),
            ],
        ]
    )


def nav_kb(rows: list[list[tuple[str, str]]]) -> types.InlineKeyboardMarkup:
    """Build an inline keyboard from rows of (label, callback_data) pairs.
    Small helper so submenu keyboards in menu.py stay short and readable.
    """
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text=label, callback_data=data) for label, data in row]
            for row in rows
        ]
    )


AUTO_DELETE_PRESETS = [
    ("30 min", 1800),
    ("2 hours", 7200),
    ("1 day", 86400),
]


def auto_delete_kb(prefix: str) -> types.InlineKeyboardMarkup:
    """Inline keyboard for choosing an auto-delete duration.

    `prefix` namespaces callback_data per caller, e.g. "ad" or "cad", so two
    different handlers' auto-delete menus never collide.
    """
    rows = [[types.InlineKeyboardButton(text="🚫 No auto-delete", callback_data=f"{prefix}_no")]]
    for label, seconds in AUTO_DELETE_PRESETS:
        rows.append([types.InlineKeyboardButton(text=label, callback_data=f"{prefix}_{seconds}")])
    rows.append([types.InlineKeyboardButton(text="Custom", callback_data=f"{prefix}_custom")])
    rows.append([types.InlineKeyboardButton(text="❌ Cancel", callback_data=f"{prefix}_cancel")])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def parse_duration(text: str) -> int | None:
    """Parse '30m' / '2h' / '1d' / 'no' into seconds (None means no auto-delete).

    Raises ValueError on anything else.
    """
    text = text.strip().lower()
    if text == "no":
        return None
    unit = text[-1]
    value = int(text[:-1])
    multiplier = {"m": 60, "h": 3600, "d": 86400}.get(unit)
    if multiplier is None or value <= 0:
        raise ValueError(f"bad duration: {text}")
    return value * multiplier


# ---------------------------------------------------------------------------
# Album (media-group) buffering.
#
# Telegram delivers a multi-photo/video post ("album") as a SEPARATE Update
# per item, all sharing the same media_group_id - it is never a single
# message. Without buffering, only the first item of a 3-video album would
# ever reach the rest of a compose flow (the state had already moved on to
# "select channels" by the time item 2 arrived), which is exactly the
# "3 videos + a caption came out differently" bug. This collects every item
# for a media_group_id and fires a callback once no further item has
# arrived for a short debounce window.
# ---------------------------------------------------------------------------

_album_buffers: dict[str, list[dict]] = {}
_album_tasks: dict[str, asyncio.Task] = {}


async def collect_album_item(message: types.Message, item: dict, on_ready) -> None:
    """Buffer one album item; once the album looks complete, call
    `await on_ready(message, items)` exactly once with every collected item
    (each item is the small dict passed in, in arrival order).
    """
    mgid = message.media_group_id
    if not mgid:
        # Not actually part of an album - handle it immediately as a
        # single-item "album" so callers don't need a separate code path.
        await on_ready(message, [item])
        return

    _album_buffers.setdefault(mgid, []).append(item)

    existing = _album_tasks.get(mgid)
    if existing and not existing.done():
        existing.cancel()

    async def _finalize():
        try:
            await asyncio.sleep(0.9)
        except asyncio.CancelledError:
            return
        items = _album_buffers.pop(mgid, [])
        _album_tasks.pop(mgid, None)
        if items:
            await on_ready(message, items)

    _album_tasks[mgid] = asyncio.create_task(_finalize())
