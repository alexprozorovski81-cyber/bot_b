"""Обработчики команд /start, главного меню и информационных кнопок."""
import logging
from decimal import Decimal, InvalidOperation

from aiogram import Router, F
from aiogram.filters import CommandStart, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

from bot import keyboards as kb
from bot import texts
from bot.config import settings
from bot.services.user_service import get_or_create_user, get_user_stats
from db.database import AsyncSessionLocal


logger = logging.getLogger(__name__)
router = Router()


async def _send_sticker_if_set(message: Message, key: str) -> None:
    """Отправить стикер из texts.STICKERS, если он настроен."""
    file_id = texts.STICKERS.get(key)
    if file_id:
        try:
            await message.answer_sticker(file_id)
        except Exception as e:
            logger.warning(f"Не удалось отправить стикер {key}: {e}")


@router.message(CommandStart(deep_link=True))
@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject | None = None) -> None:
    """
    Обработка /start с возможным реферальным кодом.
    Например: /start ref_ABC123 — приглашение от друга.
    """
    referrer_telegram_id: int | None = None
    if command and command.args and command.args.startswith("ref_"):
        try:
            referrer_telegram_id = int(command.args.removeprefix("ref_"))
        except ValueError:
            pass

    async with AsyncSessionLocal() as session:
        user, is_new = await get_or_create_user(
            session=session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            referrer_telegram_id=referrer_telegram_id,
        )

        if is_new:
            await _send_sticker_if_set(message, "welcome")
            await message.answer(
                texts.WELCOME_NEW,
                reply_markup=kb.main_menu_kb(),
                parse_mode="HTML",
            )
            # Отдельное уведомление о начисленном бонусе
            await message.answer(
                f"🎁 <b>Вам начислено 500 монет!</b>\n\n"
                f"Это стартовый бонус — используй его, чтобы опробовать площадку.\n"
                f"Твой баланс: <b>500 монет</b> 💰",
                parse_mode="HTML",
            )
        else:
            stats = await get_user_stats(session, user)
            await message.answer(
                texts.WELCOME_BACK.format(
                    name=user.first_name or "друг",
                    balance=f"{user.balance_rub:.2f}",
                    active_bets=stats["active"],
                ),
                reply_markup=kb.main_menu_kb(),
                parse_mode="HTML",
            )


@router.message(F.text == texts.BTN_PROFILE)
async def show_profile(message: Message) -> None:
    async with AsyncSessionLocal() as session:
        user, _ = await get_or_create_user(
            session=session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
        )
        stats = await get_user_stats(session, user)

    await _send_sticker_if_set(message, "profile")

    ref_link = f"https://t.me/{settings.bot_username}?start=ref_{user.telegram_id}"
    await message.answer(
        texts.PROFILE.format(
            user_id=user.telegram_id,
            balance=f"{user.balance_rub:.2f}",
            total_bets=stats["total_bets"],
            wins=stats["wins"],
            winrate=stats["winrate"],
            profit=f"{stats['profit']:+.2f}",
            ref_link=ref_link,
        ),
        parse_mode="HTML",
    )


@router.message(F.text == texts.BTN_ABOUT)
async def show_about(message: Message) -> None:
    await _send_sticker_if_set(message, "about")
    await message.answer(
        texts.ABOUT_PLATFORM.format(
            fee=f"{settings.platform_fee_percent:.0f}",
        ),
        parse_mode="HTML",
    )


@router.message(F.text == texts.BTN_WITHDRAW)
async def start_withdraw(message: Message) -> None:
    """Показывает меню вывода — направляет в мини-апп."""
    async with AsyncSessionLocal() as session:
        user, _ = await get_or_create_user(
            session, message.from_user.id,
            message.from_user.username,
            message.from_user.first_name,
        )
    await message.answer(
        texts.WITHDRAW_MENU.format(
            balance=f"{user.balance_rub:.0f}",
            min_withdraw=f"{settings.min_withdraw_coins:.0f}",
        ),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="💸 Открыть форму вывода",
                web_app=__import__("aiogram").types.WebAppInfo(url=settings.miniapp_url)
                if settings.miniapp_url.startswith("https://")
                else None,
            ) if settings.miniapp_url.startswith("https://")
            else InlineKeyboardButton(
                text="💸 Открыть мини-апп",
                callback_data="main:menu",
            )
        ]]),
    )


@router.message(F.text == texts.BTN_SUPPORT)
async def show_support(message: Message) -> None:
    await _send_sticker_if_set(message, "support")
    await message.answer(
        texts.SUPPORT_TEXT.format(
            support_username=settings.support_username,
            channel_username=f"{settings.bot_username}_news",
        ),
        reply_markup=kb.support_kb(),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@router.callback_query(F.data == "main:menu")
async def back_to_main(callback: CallbackQuery) -> None:
    await callback.message.delete()
    await callback.answer()
