# Telegram Multi-Channel Auto-Poster Bot

A single-operator Telegram bot to compose posts, send them to multiple channels at once, schedule them for later, and edit them after they're sent.

## Setup

1. **Create a bot**: message [@BotFather](https://t.me/BotFather) on Telegram, run `/newbot`, and copy the token it gives you.
2. **Find your Telegram user id**: message [@userinfobot](https://t.me/userinfobot) and copy your numeric id.
3. Copy `.env.example` to `.env` and fill in:
   - `BOT_TOKEN` — from BotFather
   - `ALLOWED_USER_IDS` — your user id (comma-separate if more than one person should control the bot)
4. Install dependencies:
   ```
   python -m venv .venv
   .venv\Scripts\python.exe -m pip install -e .
   ```
5. Run it:
   ```
   .venv\Scripts\python.exe bot.py
   ```

## Registering a channel

1. Add your bot as an **admin** of the Telegram channel (needs "Post messages" and "Edit messages" permissions).
2. Forward any message from that channel to the bot in a private chat.
3. The bot confirms registration. Repeat for each channel.

## Usage

- `/post` — send text or a photo+caption, pick which registered channels to send it to (toggle individual channels, or use a "Select all: `<category>`" button to grab a whole group at once), choose **Send now** or **Schedule** (UTC date/time), then optionally set an **auto-delete** duration (e.g. `30m`, `2h`, `1d`, or `no`).
- `/channels` — list and remove registered channels.
- `/newcategory <name>` — create a category to group channels (e.g. "News", "VIP"). A channel can belong to multiple categories.
- `/categories` — list categories (with channel counts) and delete them.
- `/setcategory` — pick a channel, then toggle which categories it belongs to.
- `/scheduled` — view pending scheduled posts, with a **Cancel** button for each.
- `/edit` — pick a recently sent post and send replacement text; it updates across every channel it was posted to.

## Notes / limitations (MVP)

- Editing only changes text/caption — it can't swap a text post to a photo post or vice versa.
- Scheduled times are UTC. Auto-delete duration is measured from when the post is actually sent (so a scheduled post's delete timer starts at send time, not compose time).
- The background scheduler checks for due posts/deletions every 30 seconds, so timing can be off by up to that much.
- Deleting a category only removes the grouping — the channels themselves are untouched.
