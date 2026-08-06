"""Userbot client (Telethon) that watches source channels and reposts per rule.

Runs alongside the aiogram Bot as a background task. The aiogram Bot posts into
destination channels (where it's an admin); this userbot only needs to *see*
the source channels, which it can do by following public channels with a real
Telegram account, even when the aiogram Bot itself isn't a member there.

Requires TELETHON_API_ID / TELETHON_API_HASH (from https://my.telegram.org)
and a logged-in session. Run `python scripts/telethon_login.py` once locally
to generate TELETHON_SESSION_STRING. If those aren't configured, this feature
is simply skipped - the rest of the bot works fine without it.
"""
import logging

from aiogram import Bot

from config import get_settings
from services.reposter import handle_incoming_message

logger = logging.getLogger(__name__)

# Set once run_userbot() successfully connects. Reused by
# approve_pending_join_requests() below so handlers/settings.py can issue
# ad-hoc MTProto calls (listing pending join requests is a "users only"
# method - the Bot API has no equivalent) without needing its own client.
_client = None


def build_client():
    """Build a Telethon client, or return None if it isn't configured."""
    settings = get_settings()
    if not settings.telethon_api_id or not settings.telethon_api_hash:
        logger.info("Telethon API credentials not set - repost-from-source feature disabled.")
        return None

    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
    except ImportError:
        logger.warning("telethon is not installed - repost-from-source feature disabled.")
        return None

    session = StringSession(settings.telethon_session_string) if settings.telethon_session_string else settings.telethon_session_name
    return TelegramClient(session, settings.telethon_api_id, settings.telethon_api_hash)


async def run_userbot(bot: Bot) -> None:
    """Connect the userbot and listen for new messages in any chat it can see.

    handle_incoming_message() checks whether the chat is a registered
    SourceChannel and no-ops otherwise, so it's safe to listen broadly rather
    than needing to know the channel list up front (channels can be
    added/removed at runtime via /add_source without restarting).
    """
    global _client

    client = build_client()
    if client is None:
        return

    from telethon import events

    @client.on(events.NewMessage())
    async def _on_new_message(event: events.NewMessage.Event) -> None:
        try:
            await handle_incoming_message(bot, event)
        except Exception:
            logger.exception("Error handling incoming message for repost")

    try:
        await client.start()
    except Exception:
        logger.exception(
            "Telethon userbot failed to start - check TELETHON_API_ID/HASH and that "
            "TELETHON_SESSION_STRING (from scripts/telethon_login.py) is set."
        )
        return

    _client = client
    logger.info("Telethon userbot connected - watching for source channel posts.")
    await client.run_until_disconnected()


async def approve_pending_join_requests(chat_id: int) -> tuple[int, str | None]:
    """Bulk-approve every currently pending join request for `chat_id`,
    including ones submitted before the bot (or this userbot account) was
    ever made an admin there.

    Why this needs Telethon rather than the regular Bot API: listing
    pending join requests (MTProto messages.getChatInviteImporters with
    requested=True) is documented as "only users can use this method" -
    bots can approve/decline an individual request once they know its
    user_id (that's what handlers/join_requests.py does for new,
    live requests), but there's no Bot API method to go back and discover
    requests that came in before the bot could see them. A real user
    account (this Telethon userbot) can list them, so this uses it for
    both listing and approving.

    Requires the Telethon account to itself be an admin of `chat_id` with
    rights to manage join requests - if it's logged in as the same
    account that owns/administers the channel, that's already satisfied.

    Returns (approved_count, error_message). error_message is None on
    success, even if approved_count is 0 (that just means no pending
    requests were found).
    """
    if _client is None:
        return 0, (
            "Telethon userbot isn't connected (TELETHON_API_ID/HASH not "
            "configured, or it failed to start) - approving old join "
            "requests requires it; the Bot API can't list them on its own."
        )

    from telethon.errors import RPCError
    from telethon.tl.functions.messages import (
        GetChatInviteImportersRequest,
        HideChatJoinRequestRequest,
    )
    from telethon.tl.types import InputUser, InputUserEmpty

    try:
        peer = await _client.get_input_entity(chat_id)
    except Exception as e:
        return 0, (
            f"Couldn't find chat {chat_id} on the userbot account - it needs "
            f"to already be a member/admin of this chat for this to work "
            f"({e})."
        )

    approved = 0
    offset_date = 0
    offset_user = InputUserEmpty()

    while True:
        try:
            result = await _client(GetChatInviteImportersRequest(
                peer=peer,
                requested=True,
                q=None,
                offset_date=offset_date,
                offset_user=offset_user,
                limit=100,
            ))
        except RPCError as e:
            return approved, f"Telegram rejected the request while listing pending requests: {e}"
        except Exception as e:
            return approved, f"Failed to list pending join requests: {e}"

        if not result.importers:
            break

        users_by_id = {u.id: u for u in result.users}
        for importer in result.importers:
            user = users_by_id.get(importer.user_id)
            if user is None:
                continue
            try:
                await _client(HideChatJoinRequestRequest(
                    peer=peer,
                    user_id=InputUser(user_id=user.id, access_hash=user.access_hash),
                    approved=True,
                ))
                approved += 1
            except Exception:
                logger.exception(
                    "Failed to approve backlog join request user_id=%s in chat_id=%s",
                    importer.user_id, chat_id,
                )

        if len(result.importers) < 100:
            break
        last = result.importers[-1]
        offset_date = last.date
        offset_user = InputUser(
            user_id=users_by_id[last.user_id].id,
            access_hash=users_by_id[last.user_id].access_hash,
        )

    return approved, None


async def scan_group_backlog_links(bot, chat_id: int, link_policy) -> dict:
    """Retroactively sweep a group's message history for links that violate
    its current Link policy, deleting matches via the aiogram bot (which
    holds the actual delete permission as the group's admin).

    Why this needs Telethon: the Bot API only ever hands a bot messages
    sent AFTER it joined/became admin - there is no "fetch history" method
    for bots. A real user account (this same userbot used elsewhere in this
    file for backlog join-request approval) CAN read a chat's full history
    via MTProto, so this borrows the already-connected userbot client to
    read old messages, then uses the regular bot (bot.delete_message) to
    actually remove the violating ones, since the userbot itself may not
    have delete rights there.

    Requires the userbot account to also be a member of the group (adding
    the aiogram bot as admin does not automatically add the userbot too -
    this is a separate, real Telegram account). Returns a dict with either
    an "error" key explaining why it couldn't run, or "scanned"/"deleted"
    counts on success.
    """
    from handlers.moderation import LINK_RE, INVITE_AD_RE
    from models import LinkPolicy

    if _client is None:
        return {"error": (
            "Telethon userbot isn't connected - backlog link scanning needs "
            "TELETHON_API_ID/HASH configured and the userbot account logged in."
        )}

    try:
        entity = await _client.get_entity(chat_id)
    except Exception as e:
        return {"error": (
            f"The userbot account can't see this group ({e}). It needs to "
            f"be a member of the group too - separately from the aiogram "
            f"bot - to read message history."
        )}

    scanned = 0
    deleted = 0
    admin_cache: dict[int, bool] = {}

    try:
        async for msg in _client.iter_messages(entity, limit=5000):
            scanned += 1
            text = msg.message or ""
            if not text:
                continue

            is_violation = False
            if link_policy == LinkPolicy.DELETE_ALL:
                is_violation = bool(LINK_RE.search(text))
            elif link_policy == LinkPolicy.DELETE_INVITES_ADS:
                is_violation = bool(INVITE_AD_RE.search(text))
            elif link_policy == LinkPolicy.ADMINS_ONLY:
                if bool(LINK_RE.search(text)) and msg.sender_id:
                    if msg.sender_id not in admin_cache:
                        try:
                            member = await bot.get_chat_member(chat_id, msg.sender_id)
                            admin_cache[msg.sender_id] = member.status in ("administrator", "creator")
                        except Exception:
                            admin_cache[msg.sender_id] = True  # unknown - err toward not deleting
                    is_violation = not admin_cache[msg.sender_id]

            if is_violation:
                try:
                    await bot.delete_message(chat_id, msg.id)
                    deleted += 1
                except Exception:
                    logger.exception(
                        "Backlog scan: failed to delete message %s in chat %s", msg.id, chat_id
                    )
    except Exception as e:
        logger.exception("Backlog link scan failed for chat_id=%s", chat_id)
        return {"error": f"Scan stopped early after an error: {e}", "scanned": scanned, "deleted": deleted}

    return {"scanned": scanned, "deleted": deleted}
