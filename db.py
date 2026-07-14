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
        await conn.run_sync(Base.metadata.create_all)


def session() -> AsyncSession:
    return async_session_factory()
