"""
Глобальный notifier — позволяет API эндпоинтам и cron-задачам
отправлять сообщения в Telegram через бота.

Бот регистрирует свой инстанс при старте, остальные модули используют
notify_user() и notify_admins().
"""
import logging
from typing import Optional

from aiogram import Bot

from bot.config import settings
from bot import texts


logger = logging.getLogger(__name__)

_bot: Optional[Bot] = None


def set_bot(bot: Bot) -> None:
    """Регистрирует Bot инстанс для использования в notifier."""
    global _bot
    _bot = bot


def get_bot() -> Optional[Bot]:
    return _bot


async def notify_user(
    telegram_id: int,
    text: str,
    sticker_key: str | None = None,
) -> bool:
    """
    Отправить сообщение пользователю.
    Возвращает True если успешно, False при ошибке (например, бот заблокирован).
    """
    if not _bot:
        logger.warning("Bot instance not set in notifier")
        return False

    try:
        if sticker_key:
            file_id = texts.STICKERS.get(sticker_key)
            if file_id:
                try:
                    await _bot.send_sticker(telegram_id, file_id)
                except Exception as e:
                    logger.debug(f"Sticker send failed: {e}")

        await _bot.send_message(
            telegram_id,
            text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return True
    except Exception as e:
        logger.warning(f"Failed to notify {telegram_id}: {e}")
        return False


async def notify_admins(text: str) -> None:
    """Уведомить всех администраторов."""
    if not _bot:
        return
    for admin_id in settings.admin_id_list:
        try:
            await _bot.send_message(admin_id, text, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"Failed to notify admin {admin_id}: {e}")
