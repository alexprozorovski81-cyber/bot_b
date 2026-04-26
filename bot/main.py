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


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def run_bot() -> None:
    """Запуск Telegram-бота."""
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    notifier.set_bot(bot)

    dp = Dispatcher(storage=MemoryStorage())
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
    """Cron-задачи: оракулы каждые 5 мин, новости каждые 30 мин."""
    logger.info("Cron task started (oracles every 5min, news every 30min)")
    await asyncio.sleep(15)  # Ждём пока бот запустится

    news_tick = 0
    while True:
        # -- Оракулы (каждые 5 мин) --
        try:
            resolved = await check_oracles()
            if resolved > 0:
                logger.info(f"Cron: resolved {resolved} events via oracles")
        except Exception as e:
            logger.exception(f"Cron oracle error: {e}")

        # -- Новости (каждые 30 мин = 6 тиков по 5 мин) --
        news_tick += 1
        if news_tick >= 6:
            news_tick = 0
            await _run_news_scan()

        await asyncio.sleep(300)


async def _run_news_scan() -> None:
    """Парсит новости и отправляет интересные админу."""
    try:
        bot = notifier._bot
        if not bot or not settings.admin_id_list:
            return

        suggestions = await fetch_news_suggestions()
        for item in suggestions:
            from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, URLInputFile
            caption = (
                f"<b>📰 {item['source']}</b>\n\n"
                f"<b>{item['title']}</b>\n\n"
                f"Хочешь создать рынок прогнозов по этой теме?"
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text="➕ Создать событие",
                    callback_data=f"news:addevent:{item['hash']}",
                ),
            ]])
            for admin_id in settings.admin_id_list:
                try:
                    if item.get("image_url"):
                        # Отправляем фото со статьи с подписью
                        await bot.send_photo(
                            admin_id,
                            photo=item["image_url"],
                            caption=caption,
                            parse_mode="HTML",
                            reply_markup=kb,
                        )
                    else:
                        # Фото не нашлось — отправляем текст
                        await bot.send_message(
                            admin_id,
                            caption,
                            parse_mode="HTML",
                            reply_markup=kb,
                        )
                except Exception as e:
                    logger.warning(f"News notify error for {admin_id}: {e}")

        # Курс ЦБ РФ — отправляем если значительное изменение
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

    # Создаём таблицы
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
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
