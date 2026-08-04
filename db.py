import logging
import re
from collections.abc import AsyncIterator

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from config import get_settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


engine = create_async_engine(get_settings().database_url, echo=False)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


# Same normalization handlers/sources.py's _normalize_identifier() applies
# to newly-typed identifiers - duplicated here (rather than imported) so
# this one-time data cleanup below has no dependency on the handlers
# package, matching every other migration in this function being
# self-contained SQL/PRAGMA calls.
_TME_URL_RE = re.compile(r"^(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/", re.IGNORECASE)


def _clean_identifier(raw: str) -> str:
    raw = raw.strip()
    raw = _TME_URL_RE.sub("", raw).strip()
    raw = raw[1:].strip() if raw.startswith("@") else raw
    raw = raw.split("?", 1)[0].split("/", 1)[0].strip()
    return raw


async def init_db() -> None:
    import models  # noqa: F401 - registers models on Base.metadata

    async with engine.begin() as conn:
        # One-time schema fix: the "posts" table was originally created with
        # only text/photo support, and SQLite bakes the content_type enum
        # into a CHECK constraint at table-creation time - adding VIDEO/ALBUM
        # to the Python enum doesn't update that constraint on an
        # already-existing table, so those posts would fail to insert.
        # SQLite can't ALTER a CHECK constraint in place, so this rebuilds
        # the table (rename -> recreate with the new schema -> copy every
        # existing row across -> drop the renamed-old copy) instead of
        # dropping it, so no existing posts are lost. post_targets keeps
        # referencing the same post ids throughout and isn't touched.
        result = await conn.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='posts'"
        )
        row = result.fetchone()
        if row and row[0] and "album" not in row[0].lower():
            # Defensive: a *previous* migration (the one that added VIDEO
            # support) used this same posts_old -> rebuild -> drop dance and,
            # going by production logs, left a posts_old table behind
            # without cleaning it up - which then made THIS migration's own
            # rename fail with "there is already another table ... named
            # posts_old" the moment it ran (crash-looping the whole bot).
            # Dropping any leftover first makes the rename idempotent no
            # matter what state a prior interrupted migration left behind.
            await conn.exec_driver_sql("DROP TABLE IF EXISTS posts_old")
            await conn.exec_driver_sql("ALTER TABLE posts RENAME TO posts_old")
            # SQLite ties index names to the database, not the table they
            # were created on - renaming "posts" to "posts_old" does NOT
            # rename ix_posts_owner_user_id along with it, so that index
            # name stays claimed by the now-renamed table. Recreating
            # "posts" (via models.Post.__table__.create below, which
            # includes its own ix_posts_owner_user_id) would then collide
            # with that leftover name and crash-loop the whole bot on every
            # restart - drop it explicitly first so the name is free again.
            await conn.exec_driver_sql("DROP INDEX IF EXISTS ix_posts_owner_user_id")
            await conn.run_sync(lambda sync_conn: models.Post.__table__.create(sync_conn))
            await conn.exec_driver_sql(
                "INSERT INTO posts (id, owner_user_id, content_type, text, photo_file_id, "
                "video_file_id, status, scheduled_time, auto_delete_seconds, delete_at, created_at) "
                "SELECT id, owner_user_id, content_type, text, photo_file_id, "
                "video_file_id, status, scheduled_time, auto_delete_seconds, delete_at, created_at FROM posts_old"
            )
            await conn.exec_driver_sql("DROP TABLE posts_old")

        # Simple additive column: existing post_targets rows just get NULL
        # here, which is fine (they only ever sent a single message anyway).
        result2 = await conn.exec_driver_sql("PRAGMA table_info('post_targets')")
        cols = [r[1] for r in result2.fetchall()]
        if cols and "extra_message_ids" not in cols:
            await conn.exec_driver_sql("ALTER TABLE post_targets ADD COLUMN extra_message_ids TEXT")

        # Simple additive column: powers the recurring-post feature. NULL
        # for every existing post just means "doesn't repeat", which is the
        # correct default for posts created before this feature existed.
        result3 = await conn.exec_driver_sql("PRAGMA table_info('posts')")
        post_cols = [r[1] for r in result3.fetchall()]
        if post_cols and "repeat_interval_seconds" not in post_cols:
            await conn.exec_driver_sql("ALTER TABLE posts ADD COLUMN repeat_interval_seconds INTEGER")

        # Simple additive columns: optional inline button on a composed post
        # (handlers/compose.py) - mirrors repost_rules' own
        # inline_button_text/inline_button_url below. NULL on every
        # existing post just means "no button", identical to today's
        # behavior.
        if post_cols and "button_text" not in post_cols:
            await conn.exec_driver_sql("ALTER TABLE posts ADD COLUMN button_text VARCHAR(64)")
        if post_cols and "button_url" not in post_cols:
            await conn.exec_driver_sql("ALTER TABLE posts ADD COLUMN button_url VARCHAR(512)")

        # Simple additive columns: per-rule inline button / prefix text /
        # link preview mode on forwarded posts. NULL on every existing rule
        # just means "no button, no prefix, Telegram's default preview" -
        # identical to today's behavior.
        result4 = await conn.exec_driver_sql("PRAGMA table_info('repost_rules')")
        rule_cols = [r[1] for r in result4.fetchall()]
        if rule_cols and "inline_button_text" not in rule_cols:
            await conn.exec_driver_sql("ALTER TABLE repost_rules ADD COLUMN inline_button_text VARCHAR(64)")
        if rule_cols and "inline_button_url" not in rule_cols:
            await conn.exec_driver_sql("ALTER TABLE repost_rules ADD COLUMN inline_button_url VARCHAR(512)")
        if rule_cols and "prefix_text" not in rule_cols:
            await conn.exec_driver_sql("ALTER TABLE repost_rules ADD COLUMN prefix_text VARCHAR(512)")
        if rule_cols and "link_preview_mode" not in rule_cols:
            await conn.exec_driver_sql("ALTER TABLE repost_rules ADD COLUMN link_preview_mode VARCHAR(16)")

        # Simple additive column: force-subscribe gate on join-request
        # approval. NULL/empty means no gate - approval works exactly like
        # before.
        result5 = await conn.exec_driver_sql("PRAGMA table_info('channels')")
        chan_cols = [r[1] for r in result5.fetchall()]
        if chan_cols and "required_join_json" not in chan_cols:
            await conn.exec_driver_sql("ALTER TABLE channels ADD COLUMN required_join_json TEXT")

        # Simple additive column: message DMed to a user the moment their
        # join request comes in, before approval happens (see
        # handlers/join_requests.py). NULL means no pre-approval message is
        # sent - existing channels keep behaving exactly as before.
        if chan_cols and "pre_approval_message" not in chan_cols:
            await conn.exec_driver_sql("ALTER TABLE channels ADD COLUMN pre_approval_message TEXT")

        # Simple additive columns: per-group welcome message (sent when
        # someone new joins a moderated group - see
        # handlers/group_messages.py and models.ModeratedGroup). NULL/False
        # on every existing group just means "no welcome message
        # configured yet" - moderation keeps behaving exactly as before
        # until an operator sets one up.
        result7 = await conn.exec_driver_sql("PRAGMA table_info('moderated_groups')")
        mg_cols = [r[1] for r in result7.fetchall()]
        if mg_cols and "welcome_enabled" not in mg_cols:
            await conn.exec_driver_sql(
                "ALTER TABLE moderated_groups ADD COLUMN welcome_enabled BOOLEAN DEFAULT 0"
            )
        if mg_cols and "welcome_text" not in mg_cols:
            await conn.exec_driver_sql("ALTER TABLE moderated_groups ADD COLUMN welcome_text TEXT")
        if mg_cols and "welcome_media_type" not in mg_cols:
            await conn.exec_driver_sql("ALTER TABLE moderated_groups ADD COLUMN welcome_media_type VARCHAR(16)")
        if mg_cols and "welcome_media_file_id" not in mg_cols:
            await conn.exec_driver_sql("ALTER TABLE moderated_groups ADD COLUMN welcome_media_file_id VARCHAR(255)")
        if mg_cols and "welcome_buttons_json" not in mg_cols:
            await conn.exec_driver_sql("ALTER TABLE moderated_groups ADD COLUMN welcome_buttons_json TEXT")

        # recurring_messages is a brand-new table (models.RecurringMessage) -
        # create_all below handles it automatically, no ALTER needed.
        await conn.run_sync(Base.metadata.create_all)

        # One-time data cleanup: handlers/sources.py's "Add Source" flow
        # (and the /add_source command) previously stored whatever an
        # operator typed verbatim - including a full https://t.me/<name>
        # share link if that's what they pasted (arguably the most natural
        # thing to paste, straight from Telegram's own "Copy Link" button).
        # services/reposter.py only ever matches against the numeric chat
        # id or the bare/"@"-prefixed username Telethon reports, so any
        # source stored as a full URL never matched anything and silently
        # never forwarded - discovered via the repost-debug logging added
        # to reposter.py, which showed known DB identifiers like
        # 'https://t.me/bnnkenya' sitting next to incoming candidates like
        # 'bnnkenya'/'@bnnkenya' that could never line up. This normalizes
        # every existing row the same way new ones are normalized on the
        # way in (_clean_identifier above), so already-configured sources
        # start matching without operators having to remove and re-add
        # each one by hand. Only rows that actually change are touched;
        # already-clean identifiers (numeric ids, bare usernames) are left
        # completely alone. Table is small (one row per watched channel)
        # so a plain Python loop here is fine - no need for a bulk SQL
        # expression that would need per-database string-function support.
        result6 = await conn.exec_driver_sql("SELECT id, identifier FROM source_channels")
        for src_id, identifier in result6.fetchall():
            if not identifier:
                continue
            cleaned = _clean_identifier(identifier)
            if cleaned and cleaned != identifier:
                try:
                    await conn.exec_driver_sql(
                        "UPDATE source_channels SET identifier = ? WHERE id = ?",
                        (cleaned, src_id),
                    )
                except IntegrityError:
                    # `identifier` is UNIQUE - if some other source was
                    # already added using the clean form (e.g. one source
                    # added as "@bnnkenya" and another, separately, as
                    # "https://t.me/bnnkenya"), normalizing this row would
                    # collide with that existing row. Leave this one as-is
                    # rather than crash-looping the whole bot on startup;
                    # it'll show up as a duplicate the operator can remove
                    # by hand via Forwarding -> Remove Source.
                    logger.warning(
                        "Skipped cleaning source_channels.id=%s identifier=%r -> %r: "
                        "would collide with an existing source",
                        src_id, identifier, cleaned,
                    )


def session() -> AsyncSession:
    return async_session_factory()
