"""Сборка всех роутеров бота."""
from aiogram import Router

from bot.handlers import start, deposit


def get_main_router() -> Router:
    """Возвращает главный роутер со всеми хэндлерами."""
    main = Router()
    main.include_router(start.router)
    main.include_router(deposit.router)
    return main
