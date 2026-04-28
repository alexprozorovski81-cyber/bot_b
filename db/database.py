"""Подключение к БД."""
from sqlalchemy.ext.asyncio import (
    AsyncSession, async_sessionmaker, create_async_engine,
)

from bot.config import settings


engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


def is_sqlite() -> bool:
    """True если используется SQLite (не поддерживает SELECT FOR UPDATE)."""
    return settings.database_url.startswith("sqlite")


async def get_session() -> AsyncSession:
    """Получить сессию (использовать как async context manager)."""
    return AsyncSessionLocal()
