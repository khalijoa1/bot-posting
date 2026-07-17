"""Shared helpers for post-composition handlers (auto-delete duration UI)
and app-wide navigation (the persistent main-menu keyboard)."""
from aiogram import types


def main_menu_kb() -> types.ReplyKeyboardMarkup:
    """The persistent main-menu keyboard.

    Every flow (compose, add channel, add category, link replacer, etc.)
    should restore this keyboard when it finishes or is cancelled, instead
    of removing the keyboard or leaving a stale sub-menu keyboard behind.
    Previously many flows did the latter, which left the user with no way
    back except typing /start again.
    """
    return types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="📨 MESSAGING")],
            [types.KeyboardButton(text="📍 CHANNELS")],
            [types.KeyboardButton(text="📁 CATEGORIES")],
            [types.KeyboardButton(text="🛡️ MODERATION")],
            [types.KeyboardButton(text="⚙️ SETTINGS")],
            [types.KeyboardButton(text="📊 ANALYTICS")],
            [types.KeyboardButton(text="❓ HELP")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False
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
