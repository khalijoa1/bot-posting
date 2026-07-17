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

**Easiest way — just add the bot as admin.** The moment the bot is promoted to admin in a channel, it registers itself automatically and DMs you (the operator) so you can optionally tag it with categories. No need to look up or type the numeric chat id.

1. Add your bot as an **admin** of the Telegram channel (needs "Post messages" and "Edit messages" permissions; also grant "Add users"/approve-join-requests permission if you plan to use auto-approve).
2. That's it — check the DM the bot sends you, or run `/list_channels` to confirm.

If auto-detection doesn't fire for some reason (e.g. the bot was added by someone other than an operator listed in `ALLOWED_USER_IDS`), you can still register manually: in a private chat with the bot, send `/add_channel`, then the chat id (looks like `-1001234567890`, findable via a utility bot like [@RawDataBot](https://t.me/RawDataBot)), then a title when prompted.

## Usage

- `/cancel` — universal escape hatch. Works from inside any flow (composing, adding a channel, replacing links, etc.) and returns you straight to the main menu. `/start` also always works and resets you to the main menu.
- `/compose` — send text, pick which registered channels to send it to (toggle individual channels), choose **Post Now** or **Schedule Later** (relative delay in minutes), then choose an **auto-delete** duration (`30 min` / `2 hours` / `1 day` / custom like `45m`, `3h`, `2d` / `no`). The auto-delete timer starts when the post is actually sent, not when it's composed.
- `/post_category` — pick a category and send text to every channel in it, with the same auto-delete choice as `/compose`.
- `/myposts` — list your posts and their status (draft/scheduled/sent).
- `/edit` — pick a recently sent post and send replacement text; it updates across every channel it was posted to.
- `/delete` — pick a post and delete its messages from every channel it was sent to.
- `/replacer` — bulk find-and-replace a link across all/a range/a single one of your posts.
- `/channels`, `/add_channel`, `/list_channels`, `/delete_channel` — manage channels.
- `/add_category`, `/list_categories` — group channels into categories; assign a channel to categories while adding it. Use `/assign_categories` to (re)assign an already-registered channel to categories at any time.
- `/autoapprove` — toggle auto-approval of subscriber join requests, per channel, and set/edit the welcome message DMed to a subscriber the moment they're auto-approved (tap 💬 next to a channel). Requires the channel to have "Approve new members" turned on in Telegram and the bot to be an admin there. Telegram only allows the bot to DM someone who has opened a chat with it before (e.g. pressed /start), so if a subscriber never has, the welcome DM silently fails to send - not something the bot can work around.
- `/analytics` — post counts, delivery counts, channel counts.

### Optional: repost from a source channel

The bot can also watch public Telegram channels you don't administer and automatically repost matching content into your own channels. This uses a second Telegram connection (a "userbot", via [Telethon](https://docs.telethon.dev)) logged in with a real account, since the Bot API can't read channels the bot isn't a member of.

1. Get an API id/hash from <https://my.telegram.org> → API Development Tools, and set `TELETHON_API_ID`, `TELETHON_API_HASH`, `TELETHON_PHONE` in `.env`.
2. Run `python scripts/telethon_login.py` locally once to log in and generate a session string; put it in `.env` (or your Railway variables) as `TELETHON_SESSION_STRING`.
3. `/add_source <@username_or_chat_id> [title]` — register a channel to watch.
4. `/add_rule <source> <destination_chat_id_or_channel_id> [auto_delete_seconds] [caption_template]` — copy new posts from that source into one of your registered channels. `caption_template` supports `{original_text}`, `{source_title}`, `{source_username}`.
5. `/list_sources`, `/remove_source`, `/list_rules`, `/remove_rule` — manage the above.

If `TELETHON_API_ID`/`TELETHON_API_HASH` aren't set, this feature is simply skipped and the rest of the bot works normally.

### Group moderation

The bot can also keep a Telegram **group** (not channel) clean by automatically deleting spam links and handling flooders — configurable per group.

**Easiest way — just add the bot as admin.** The moment it's promoted to admin in a group, it registers itself for moderation automatically (with sensible defaults) and DMs you a "Configure" button. No need to look up or type the chat id.

1. Add the bot to the group as an **admin** with "Delete messages" and "Ban users" permissions.
2. Tap **Configure** in the DM it sends you, or run `/moderation` any time, then choose:
   - **Links**: delete all links, delete only invite links/ad links, or allow admins but delete for everyone else.
   - **Spam**: delete the message only, delete + warn then mute repeat offenders, or delete + kick immediately.
3. `/list_groups`, `/remove_group <id>` — manage registered groups.

If auto-detection doesn't fire, `/add_group <chat_id> [title]` still works as a manual fallback.

Spam detection covers message flooding (too many messages too fast) and repeated duplicate messages. Once a group is registered, every member's plain messages there are checked — no per-message setup needed. Commands and the bot's private menus still only work for the operator, even inside a moderated group.

## Notes / limitations (MVP)

- Editing only changes text/caption — it can't swap a text post to a photo post or vice versa.
- Scheduled times are relative delays (minutes from now), not absolute UTC timestamps. Auto-delete duration is measured from when the post is actually sent (so a scheduled post's delete timer starts at send time, not compose time).
- The background scheduler checks for due posts/deletions every 30 seconds, so timing can be off by up to that much.
- Deleting a category only removes the grouping — the channels themselves are untouched.
- The repost-from-source feature requires a logged-in Telethon session (see above) and is disabled by default.
