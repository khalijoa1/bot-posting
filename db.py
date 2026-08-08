        # ONE-TIME DIAGNOSTIC #2 (requested by operator): the operator
        # says buttonless recurring posts still exist even though the DIAG
        # above (posts.repeat_interval_seconds) came back empty. There is
        # a SEPARATE recurring feature - models.RecurringMessage
        # (recurring_messages table, sent by services/recurring.py) for
        # per-moderated-group repeating announcements, with its own
        # buttons_json field, entirely independent of the Post table this
        # migration has been cleaning up so far. Listing every row here so
        # it's clear whether THIS is what the operator means. Safe to
        # remove once seen.
        res_diag2 = await conn.exec_driver_sql(
            "SELECT id, group_id, enabled, interval_seconds, buttons_json "
            "FROM recurring_messages ORDER BY id"
        )
        diag2_rows = res_diag2.fetchall()
        logger.warning(
            "DIAG2: %d recurring_message(s) exist: %s",
            len(diag2_rows),
            [
                {
                    "id": r[0],
                    "group_id": r[1],
                    "enabled": r[2],
                    "interval_seconds": r[3],
                    "buttons_json": r[4],
                }
                for r in diag2_rows
            ],
        )

import logging
import re
from collections.abc import AsyncIterator
from datetime import datetime

from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from config import get_settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


# timeout=30 sets sqlite3's busy_timeout (seconds) on every new connection -
# instead of raising "database is locked" the instant a write collides with
# another connection, SQLite retries internally for up to 30s first. This
# app has several concurrent writers sharing one file (the compose handler
# plus multiple background loops in services/*.py), which was producing
# "database is locked" errors under normal load - e.g. someone using
# Compose & Post at the same moment run_post_send_loop's own commit was
# in flight.
engine = create_async_engine(
    get_settings().database_url, echo=False, connect_args={"timeout": 30}
)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record):
    """WAL (write-ahead log) mode lets readers and writers proceed
    concurrently instead of the default rollback-journal mode's exclusive
    whole-file lock on every write - the other half of the fix above
    (busy_timeout alone just makes lock collisions wait instead of
    failing; WAL makes most of them stop happening at all). synchronous=
    NORMAL is the standard pairing with WAL: still durable against
    application/OS crashes, just not fsync-per-transaction like the
    default FULL setting, which would otherwise erase most of the
    concurrency benefit WAL is here for."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()

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

        # Simple additive columns: auto-comment (a fixed message the bot
        # replies with in a channel's linked discussion group under every
        # post - see handlers/settings.py). Default auto_comment_enabled
        # to true (1) so it's on by default per the "all channels" scope
        # this feature launched with, but auto_comment_text stays NULL, so
        # nothing actually posts until an operator writes a message.
        if chan_cols and "auto_comment_enabled" not in chan_cols:
            await conn.exec_driver_sql(
                "ALTER TABLE channels ADD COLUMN auto_comment_enabled BOOLEAN DEFAULT 1"
            )
        if chan_cols and "auto_comment_text" not in chan_cols:
            await conn.exec_driver_sql("ALTER TABLE channels ADD COLUMN auto_comment_text TEXT")

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
                    # rather than crash-loop the whole bot on startup;
                    # it'll show up as a duplicate the operator can remove
                    # by hand via Forwarding -> Remove Source.
                    logger.warning(
                        "Skipped cleaning source_channels.id=%s identifier=%r -> %r: "
                        "would collide with an existing source",
                        src_id, identifier, cleaned,
                    )

        # One-time data repair: a bug in services/scheduler.py's
        # run_post_send_loop (now fixed) unconditionally marked a repeating
        # post SENT even when every target failed to send, leaving it with
        # no sent_at anywhere. run_scheduler_loop's recycle query for
        # no-auto-delete repeaters keys off max(target.sent_at), so a post
        # with none got skipped forever - permanently stuck. This resets
        # any such row back to SCHEDULED with scheduled_time=now so the
        # normal send loop picks it up on its next pass.
        # NOTE ON STATUS LITERALS: models.Post.status is a SQLAlchemy
        # Enum(PostStatus) column. SQLAlchemy's Enum type, unless given
        # values_callable, stores/compares using the Python enum MEMBER
        # NAME ("SCHEDULED", "SENT", "DELETED", ...) - NOT PostStatus's
        # str .value ("scheduled", "sent", "deleted") - even though
        # PostStatus happens to subclass str. Confirmed empirically: the
        # ORM's own compiled query for run_post_send_loop literal-binds
        # `posts.status = 'SCHEDULED'` (uppercase). Every raw-SQL status
        # literal below MUST use the uppercase member-name form to match
        # what the app's real ORM queries look for - a previous version of
        # this migration used lowercase 'scheduled'/'sent'/'deleted',
        # which silently wrote/matched rows the send loop's own query
        # could never see (this was the root cause of a "nothing was
        # sent" report after an operator-requested kickstart).
        result8 = await conn.exec_driver_sql(
            "SELECT id FROM posts WHERE status = 'SENT' AND repeat_interval_seconds IS NOT NULL "
            "AND auto_delete_seconds IS NULL AND id NOT IN "
            "(SELECT DISTINCT post_id FROM post_targets WHERE sent_at IS NOT NULL)"
        )
        stuck_ids = [r[0] for r in result8.fetchall()]
        if stuck_ids:
            now_str = datetime.utcnow().isoformat(sep=" ")
            for pid in stuck_ids:
                await conn.exec_driver_sql(
                    "UPDATE post_targets SET message_id = NULL, extra_message_ids = NULL, "
                    "sent_at = NULL WHERE post_id = ?",
                    (pid,),
                )
                await conn.exec_driver_sql(
                    "UPDATE posts SET status = 'SCHEDULED', scheduled_time = ?, delete_at = NULL "
                    "WHERE id = ?",
                    (now_str, pid),
                )
            logger.warning("Resumed %d stuck repeating post(s): %s", len(stuck_ids), stuck_ids)

        # ONE-TIME KICKSTART (requested by operator): force every currently
        # repeating post to send right now, on this boot, then carry on
        # with its normal repeat cycle exactly as before. This is
        # intentionally temporary - once it has run one time, remove this
        # block in a follow-up commit so it doesn't pointlessly re-fire on
        # every future restart.
        now_str2 = datetime.utcnow().isoformat(sep=" ")

        # Posts still waiting for their first/next send - just pull their
        # scheduled_time to now so the normal send loop picks them up on
        # its next pass instead of waiting.
        res_a = await conn.exec_driver_sql(
            "SELECT id FROM posts WHERE status = 'SCHEDULED' AND repeat_interval_seconds IS NOT NULL"
        )
        ids_a = [r[0] for r in res_a.fetchall()]
        for pid in ids_a:
            await conn.exec_driver_sql(
                "UPDATE posts SET scheduled_time = ? WHERE id = ?", (now_str2, pid)
            )

        # Posts already SENT with no auto-delete configured - clear their
        # per-target sent state and put them back to SCHEDULED so they
        # resend immediately, same recycle path used for the stuck-post
        # repair above.
        res_b = await conn.exec_driver_sql(
            "SELECT id FROM posts WHERE status = 'SENT' AND repeat_interval_seconds IS NOT NULL "
            "AND auto_delete_seconds IS NULL"
        )
        ids_b = [r[0] for r in res_b.fetchall()]
        for pid in ids_b:
            await conn.exec_driver_sql(
                "UPDATE post_targets SET message_id = NULL, extra_message_ids = NULL, "
                "sent_at = NULL WHERE post_id = ?",
                (pid,),
            )
            await conn.exec_driver_sql(
                "UPDATE posts SET status = 'SCHEDULED', scheduled_time = ?, delete_at = NULL "
                "WHERE id = ?",
                (now_str2, pid),
            )

        # Posts already SENT with auto-delete configured - the scheduler's
        # delete_at-based recycle path handles these normally, so just pull
        # their delete_at to now so that path fires immediately instead of
        # waiting out the rest of the auto-delete window.
        res_c = await conn.exec_driver_sql(
            "SELECT id FROM posts WHERE status = 'SENT' AND repeat_interval_seconds IS NOT NULL "
            "AND auto_delete_seconds IS NOT NULL"
        )
        ids_c = [r[0] for r in res_c.fetchall()]
        for pid in ids_c:
            await conn.exec_driver_sql("UPDATE posts SET delete_at = ? WHERE id = ?", (now_str2, pid))

        # Posts that got stuck in DELETED status despite still having a
        # repeat interval set - this happens for any repeating post that
        # hit a failed send/delete cycle BEFORE the scheduler's "stopped
        # repeating permanently after a failed send" fix landed. Once a
        # post falls into DELETED, nothing ever looks at it again: both
        # recycle paths above only ever query status IN ('scheduled',
        # 'sent'). If repeat_interval_seconds is still set, the operator
        # clearly wanted it to keep looping - so it's revived the same
        # way as the SENT/no-auto-delete case, not left dead forever.
        res_d = await conn.exec_driver_sql(
            "SELECT id FROM posts WHERE status = 'DELETED' AND repeat_interval_seconds IS NOT NULL"
        )
        ids_d = [r[0] for r in res_d.fetchall()]
        for pid in ids_d:
            await conn.exec_driver_sql(
                "UPDATE post_targets SET message_id = NULL, extra_message_ids = NULL, "
                "sent_at = NULL WHERE post_id = ?",
                (pid,),
            )
            await conn.exec_driver_sql(
                "UPDATE posts SET status = 'SCHEDULED', scheduled_time = ?, delete_at = NULL "
                "WHERE id = ?",
                (now_str2, pid),
            )

        if ids_a or ids_b or ids_c or ids_d:
            logger.warning(
                "Kickstarted repeating posts - immediate: %s, via delete+recycle: %s, revived from DELETED: %s",
                ids_a + ids_b, ids_c, ids_d,
            )

        # ONE-TIME CLEANUP (requested by operator): stop every currently-
        # repeating post that has NO inline button configured (button_text
        # and button_url both set - see handlers/compose.py). Every
        # repeating post that DOES have a button is left alone so the
        # kickstart logic above (res_a/b/c/d) - now gated to buttoned posts
        # only, see the added button_text/button_url clause on each of its
        # queries - picks it up and resends it promptly, now that
        # services/scheduler.py actually attaches the button on every send.
        res_nobtn = await conn.exec_driver_sql(
            "SELECT id, status FROM posts WHERE repeat_interval_seconds IS NOT NULL "
            "AND (button_text IS NULL OR button_url IS NULL)"
        )
        buttonless = res_nobtn.fetchall()
        now_str4 = datetime.utcnow().isoformat(sep=" ")
        stopped_ids = []
        for pid, status in buttonless:
            await conn.exec_driver_sql(
                "UPDATE posts SET repeat_interval_seconds = NULL WHERE id = ?", (pid,)
            )
            if status == "SENT":
                # Leave message_id/sent_at alone and just pull delete_at to
                # now - run_scheduler_loop's normal SENT+delete_at<=now path
                # then deletes the live channel message(s) using that intact
                # message_id, and finalizes the post as DELETED instead of
                # recycling it, since repeat_interval_seconds is now NULL.
                await conn.exec_driver_sql(
                    "UPDATE posts SET delete_at = ? WHERE id = ?", (now_str4, pid)
                )
            else:
                # Nothing live to delete yet (still SCHEDULED/DRAFT) - just
                # stop it outright.
                await conn.exec_driver_sql(
                    "UPDATE posts SET status = 'CANCELED' WHERE id = ?", (pid,)
                )
            stopped_ids.append(pid)
        if stopped_ids:
            logger.warning(
                "Stopped %d buttonless repeating post(s): %s", len(stopped_ids), stopped_ids
        )


        # ONE-TIME DIAGNOSTIC (requested by operator, follow-up to the
        # buttonless-cleanup block above): the previous deploy of that
        # cleanup logged nothing at all - no "Stopped N buttonless
        # repeating post(s)" and no "Kickstarted repeating posts" - which
        # is ambiguous from the logs alone (it could mean zero buttonless
        # recurring posts exist, OR that something about the query/timing
        # missed real rows). This unconditionally lists every post that is
        # STILL configured to repeat right now, with its button_text/
        # button_url/status, so the real current state is directly visible
        # in the boot log instead of inferred from silence. Safe to remove
        # once the operator has seen this output.
        res_diag = await conn.exec_driver_sql(
            "SELECT id, status, button_text, button_url, repeat_interval_seconds "
            "FROM posts WHERE repeat_interval_seconds IS NOT NULL ORDER BY id"
        )
        diag_rows = res_diag.fetchall()
        logger.warning(
            "DIAG: %d post(s) currently have repeat_interval_seconds set: %s",
            len(diag_rows),
            [
                {
                    "id": r[0],
                    "status": r[1],
                    "button_text": r[2],
                    "button_url": r[3],
                    "repeat_interval_seconds": r[4],
                }
                for r in diag_rows
            ],
        )

def session() -> AsyncSession:
    return async_session_factory()
