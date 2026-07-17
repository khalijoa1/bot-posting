"""Shared helpers for post-composition handlers (auto-delete duration UI)
and app-wide navigation (the persistent main-menu keyboard)."""
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
                types.InlineKeyboardButton(text="⚙️ Settings", callback_data="menu:settings"),
                types.InlineKeyboardButton(text="📊 Analytics", callback_data="menu:analytics"),
            ],
            [types.InlineKeyboardButton(text="❓ Help", callback_data="menu:help")],
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
