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
        # into a CHECK constraint at table-creation time - adding VIDEO to
        # the Python enum doesn't update that constraint on an
        # already-existing table, so video posts would fail to insert.
        # SQLite can't ALTER a CHECK constraint in place, so this rebuilds
        # the table (rename -> recreate with the new schema -> copy every
        # existing row across -> drop the renamed-old copy) instead of
        # dropping it, so no existing posts are lost. post_targets keeps
        # referencing the same post ids throughout and isn't touched.
        result = await conn.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='posts'"
        )
        row = result.fetchone()
        if row and row[0] and "video" not in row[0].lower():
            await conn.exec_driver_sql("ALTER TABLE posts RENAME TO posts_old")
            await conn.run_sync(lambda sync_conn: models.Post.__table__.create(sync_conn))
            await conn.exec_driver_sql(
                "INSERT INTO posts (id, owner_user_id, content_type, text, photo_file_id, "
                "status, scheduled_time, auto_delete_seconds, delete_at, created_at) "
                "SELECT id, owner_user_id, content_type, text, photo_file_id, "
                "status, scheduled_time, auto_delete_seconds, delete_at, created_at FROM posts_old"
            )
            await conn.exec_driver_sql("DROP TABLE posts_old")

        await conn.run_sync(Base.metadata.create_all)


def session() -> AsyncSession:
    return async_session_factory()
