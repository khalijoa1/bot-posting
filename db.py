from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from config import get_settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(get_settings().database_url, echo=False)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


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

        await conn.run_sync(Base.metadata.create_all)


def session() -> AsyncSession:
    return async_session_factory()
