"""
Прямая инициализация схемы БД из моделей (без alembic).
Используется для быстрого старта в dev/test окружении.

В production используй alembic upgrade head.
"""
import asyncio
import logging

from db.database import engine
from db.models import Base


logger = logging.getLogger(__name__)


async def init_db() -> None:
    """Создаёт все таблицы в БД, если их нет."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database schema initialized")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(init_db())
    print("OK: Схема БД создана")
