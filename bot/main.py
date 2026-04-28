"""
Точка входа PredictBet.

Запускает в одном процессе:
  - Telegram-бот (aiogram, long polling)
  - FastAPI (для Mini App, на uvicorn)
  - Cron-задачи (оракулы, авто-разрешение, очистка)
"""
import asyncio
import logging
import os

import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from bot import notifier
from bot.config import settings
from bot.handlers import get_main_router
from bot.handlers import admin
from bot.services.oracle_service import check_oracles
from bot.services.news_service import fetch_news_suggestions, fetch_cbr_rate
from bot.services.auto_events_service import process_auto_events
from bot.services.withdrawal_service import process_pending_withdrawals


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def _make_storage():
    """Redis FSM storage если Redis доступен, иначе MemoryStorage."""
    if settings.redis_url and settings.redis_url != "redis://localhost:6379/0":
        try:
            from aiogram.fsm.storage.redis import RedisStorage
            storage = RedisStorage.from_url(settings.redis_url)
            logger.info("FSM storage: Redis (%s)", settings.redis_url)
            return storage
        except Exception as e:
            logger.warning("Redis unavailable (%s), falling back to MemoryStorage", e)
    try:
        from aiogram.fsm.storage.redis import RedisStorage
        storage = RedisStorage.from_url(settings.redis_url)
        # Проверяем соединение
        await storage.redis.ping()
        logger.info("FSM storage: Redis")
        return storage
    except Exception:
        logger.info("FSM storage: MemoryStorage (Redis not available)")
        return MemoryStorage()


async def run_bot() -> None:
    """Запуск Telegram-бота."""
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    notifier.set_bot(bot)

    # Используем Redis если доступен, иначе MemoryStorage
    storage = await _make_storage()
    dp = Dispatcher(storage=storage)
    dp.include_router(admin.router)
    dp.include_router(get_main_router())

    logger.info("Bot starting (long polling)...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


async def run_api() -> None:
    """Запуск FastAPI для Mini App."""
    # Zeabur (и другие PaaS) передают порт через $PORT
    port = int(os.environ.get("PORT", settings.api_port))
    config = uvicorn.Config(
        "bot.api:app",
        host="0.0.0.0",
        port=port,
        log_level="info",
    )
    server = uvicorn.Server(config)
    await server.serve()


async def run_cron() -> None:
    """
    Cron-задачи:
    - авто-выводы каждые 2 мин
    - оракулы каждые 10 мин (5 тиков по 2 мин)
    - новости + авто-события каждые 30 мин (15 тиков)
    """
    logger.info("Cron task started (withdrawals every 2min, oracles every 10min, news every 30min)")
    await asyncio.sleep(15)  # Ждём пока бот запустится

    from db.database import AsyncSessionLocal
    oracle_tick = 0
    news_tick = 0

    while True:
        # -- Авто-выводы (каждые 2 мин) --
        try:
            async with AsyncSessionLocal() as session:
                paid = await process_pending_withdrawals(session)
                if paid > 0:
                    logger.info(f"Cron: auto-paid {paid} withdrawals")
        except Exception as e:
            logger.exception(f"Cron withdrawal error: {e}")

        # -- Проверяем pending USDT депозиты (каждые 10 мин = 5 тиков) --
        oracle_tick += 1
        if oracle_tick >= 5:
            oracle_tick = 0
            try:
                resolved = await check_oracles()
                if resolved > 0:
                    logger.info(f"Cron: resolved {resolved} events via oracles")
            except Exception as e:
                logger.exception(f"Cron oracle error: {e}")

            # Проверяем pending USDT-депозиты старше 5 мин
            try:
                await _check_pending_deposits()
            except Exception as e:
                logger.exception(f"Cron deposit check error: {e}")

        # -- Новости + авто-события (каждые 30 мин = 15 тиков по 2 мин) --
        news_tick += 1
        if news_tick >= 15:
            news_tick = 0
            await _run_news_scan()

        await asyncio.sleep(120)


async def _check_pending_deposits() -> None:
    """Проверяет pending USDT-депозиты старше 5 минут через Toncenter."""
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import select
    from db.database import AsyncSessionLocal
    from db.models import Payment, PaymentMethod
    from bot.services.payment_service import check_usdt_toncenter

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Payment).where(
                Payment.method == PaymentMethod.USDT_TON,
                Payment.status == "pending",
                Payment.is_deposit == True,  # noqa: E712
                Payment.created_at <= cutoff,
            ).limit(20)
        )
        pending = list(result.scalars().all())

    for payment in pending:
        try:
            async with AsyncSessionLocal() as session:
                found = await check_usdt_toncenter(session, payment)
                if found:
                    logger.info(f"Cron: confirmed pending deposit payment_id={payment.id}")
        except Exception as e:
            logger.warning(f"Deposit check error for payment {payment.id}: {e}")


async def _run_news_scan() -> None:
    """Парсит новости: авто-создаёт события + отправляет остальные админу."""
    try:
        bot = notifier._bot

        # Получаем все новости (ignore_cache=False — дедупликация по хэшу)
        suggestions = await fetch_news_suggestions()
        if not suggestions:
            return

        # ── 1. Авто-создание событий из релевантных новостей ──────────────
        auto_created = await process_auto_events(suggestions)
        if auto_created:
            logger.info(f"Auto-events created: {auto_created}")

        # ── 2. Остальные новости — отправляем админу для ручного решения ──
        if bot and settings.admin_id_list:
            from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
            for item in suggestions:
                caption = (
                    f"<b>📰 {item['source']}</b>\n\n"
                    f"<b>{item['title']}</b>\n\n"
                    f"Хочешь создать рынок прогнозов по этой теме?"
                )
                kb = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(
                        text="⚡ Быстро опубликовать",
                        callback_data=f"news:quick:{item['hash']}",
                    ),
                    InlineKeyboardButton(
                        text="✏️ Редактировать",
                        callback_data=f"news:addevent:{item['hash']}",
                    ),
                ]])
                for admin_id in settings.admin_id_list:
                    try:
                        if item.get("image_url"):
                            await bot.send_photo(
                                admin_id,
                                photo=item["image_url"],
                                caption=caption,
                                parse_mode="HTML",
                                reply_markup=kb,
                            )
                        else:
                            await bot.send_message(
                                admin_id,
                                caption,
                                parse_mode="HTML",
                                reply_markup=kb,
                            )
                    except Exception as e:
                        logger.warning(f"News notify error for {admin_id}: {e}")

        # Курс ЦБ РФ — логируем
        rate = await fetch_cbr_rate()
        if rate:
            logger.info(f"CBR USD/RUB: {rate}")

    except Exception as e:
        logger.warning(f"News scan error: {e}")


async def init_database() -> None:
    """Создаёт таблицы и заполняет начальными данными если БД пустая."""
    from db.database import engine, AsyncSessionLocal
    from db.models import Base
    from sqlalchemy import text

    # Логируем фактический путь к БД — критично для диагностики персистентности
    # на хостингах (Amvera persistenceMount=/data). Если URL не указывает на /data,
    # данные потеряются при рестарте контейнера.
    db_url = settings.database_url
    logger.info(f"Database URL: {db_url}")
    if db_url.startswith("sqlite"):
        # Достаём путь файла из sqlite URL вида sqlite+aiosqlite:////data/predictbet.db
        sqlite_path = db_url.split("://", 1)[-1].lstrip("/")
        sqlite_abs = "/" + sqlite_path if db_url.count("/") >= 4 else os.path.abspath(sqlite_path)
        logger.info(f"SQLite file resolved path: {sqlite_abs}")
        if not sqlite_abs.startswith("/data/"):
            logger.warning(
                "DB path is NOT under /data — на Amvera данные будут "
                "стираться при рестарте! Установи DATABASE_URL=sqlite+aiosqlite:////data/predictbet.db"
            )

    # Создаём таблицы (новые) и применяем inline-миграции для существующих
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # Inline-миграции: добавляем колонки если их нет (SQLite не имеет IF NOT EXISTS)
        migrations = [
            "ALTER TABLE events ADD COLUMN article_url VARCHAR(512)",
            # Withdrawals table — создаётся через create_all, но на случай старых БД
            """CREATE TABLE IF NOT EXISTS withdrawals (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                amount_coins NUMERIC(18,2) NOT NULL,
                network VARCHAR(32) NOT NULL,
                wallet_address VARCHAR(256) NOT NULL,
                status VARCHAR(16) NOT NULL DEFAULT 'pending',
                admin_note VARCHAR(512),
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                processed_at DATETIME
            )""",
            "CREATE INDEX IF NOT EXISTS ix_withdrawals_user_id ON withdrawals (user_id)",
            "CREATE INDEX IF NOT EXISTS ix_withdrawals_status ON withdrawals (status)",
            "ALTER TABLE withdrawals ADD COLUMN amount_usdt NUMERIC(18,6)",
            "ALTER TABLE withdrawals ADD COLUMN rate_at_request NUMERIC(18,4)",
            "ALTER TABLE withdrawals ADD COLUMN tx_hash VARCHAR(128)",
            "ALTER TABLE withdrawals ADD COLUMN fee_coins NUMERIC(18,2) DEFAULT 0",
            "ALTER TABLE withdrawals ADD COLUMN is_auto BOOLEAN DEFAULT 0",
            "ALTER TABLE withdrawals ADD COLUMN retry_count INTEGER DEFAULT 0",
            # Achievements
            """CREATE TABLE IF NOT EXISTS achievements (
                id INTEGER PRIMARY KEY,
                slug VARCHAR(64) UNIQUE NOT NULL,
                name VARCHAR(128) NOT NULL,
                emoji VARCHAR(8) NOT NULL,
                description VARCHAR(256) NOT NULL,
                condition_type VARCHAR(32) NOT NULL,
                condition_value INTEGER NOT NULL DEFAULT 1,
                rarity VARCHAR(16) NOT NULL DEFAULT 'common'
            )""",
            """CREATE TABLE IF NOT EXISTS user_achievements (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                achievement_id INTEGER NOT NULL REFERENCES achievements(id),
                unlocked_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
            "CREATE INDEX IF NOT EXISTS ix_user_achievements_user_id ON user_achievements (user_id)",
        ]
        for sql in migrations:
            try:
                await conn.execute(text(sql))
                logger.info(f"Migration applied: {sql}")
            except Exception:
                pass  # Колонка уже существует — ок

    logger.info("Database schema ready")

    # Заполняем данными только если БД пустая
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("SELECT COUNT(*) FROM events"))
        count = result.scalar()
        if count == 0:
            logger.info("Database is empty, seeding...")
            try:
                from bot.seed import seed
                await seed()
                logger.info("Database seeded successfully")
            except Exception as e:
                logger.warning(f"Seed error: {e}")


async def _safe_run(coro_fn, name: str) -> None:
    try:
        await coro_fn()
    except Exception as e:
        logger.exception(f"Task '{name}' crashed: {e}")
        raise


async def main() -> None:
    await init_database()
    results = await asyncio.gather(
        _safe_run(run_bot, "run_bot"),
        _safe_run(run_api, "run_api"),
        _safe_run(run_cron, "run_cron"),
        return_exceptions=True,
    )
    for r in results:
        if isinstance(r, Exception):
            logger.error(f"A task exited with exception: {r}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Stopped.")
