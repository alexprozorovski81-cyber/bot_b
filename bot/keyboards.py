"""Клавиатуры бота — главное меню, депозит, подтверждения."""
from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, ReplyKeyboardMarkup,
    WebAppInfo,
)

from bot.config import settings
from bot import texts


def main_menu_kb() -> ReplyKeyboardMarkup:
    """Главное меню — внизу экрана как обычная клавиатура."""
    use_webapp = settings.miniapp_url.startswith("https://")
    open_btn = (
        KeyboardButton(text=texts.BTN_OPEN_APP, web_app=WebAppInfo(url=settings.miniapp_url))
        if use_webapp
        else KeyboardButton(text=texts.BTN_OPEN_APP)
    )
    return ReplyKeyboardMarkup(
        keyboard=[
            [open_btn],
            [
                KeyboardButton(text=texts.BTN_PROFILE),
                KeyboardButton(text=texts.BTN_DEPOSIT),
                KeyboardButton(text=texts.BTN_WITHDRAW),
            ],
            [
                KeyboardButton(text=texts.BTN_ABOUT),
                KeyboardButton(text=texts.BTN_SUPPORT),
            ],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def deposit_methods_kb() -> InlineKeyboardMarkup:
    """Выбор способа пополнения."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 USDT (TON)", callback_data="dep:usdt")],
        [InlineKeyboardButton(text="⭐ Telegram Stars", callback_data="dep:stars")],
        [InlineKeyboardButton(text="🔷 ETH", callback_data="dep:crypto:eth"),
         InlineKeyboardButton(text="₿ BTC", callback_data="dep:crypto:btc"),
         InlineKeyboardButton(text="◎ SOL", callback_data="dep:crypto:sol")],
        [InlineKeyboardButton(text=texts.BTN_BACK, callback_data="main:menu")],
    ])


def deposit_amount_kb() -> InlineKeyboardMarkup:
    """Быстрый выбор суммы депозита."""
    amounts = [500, 1000, 2500, 5000, 10000]
    rows = []
    for i in range(0, len(amounts), 3):
        rows.append([
            InlineKeyboardButton(
                text=f"{a} ₽",
                callback_data=f"dep:amount:{a}",
            )
            for a in amounts[i:i + 3]
        ])
    rows.append([
        InlineKeyboardButton(
            text="✏️ Своя сумма",
            callback_data="dep:custom_amount",
        )
    ])
    rows.append([
        InlineKeyboardButton(text=texts.BTN_BACK, callback_data="dep:back")
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def payment_link_kb(payment_url: str, payment_id: str) -> InlineKeyboardMarkup:
    """Кнопка оплаты + проверка статуса."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить", url=payment_url)],
        [InlineKeyboardButton(
            text="🔄 Проверить оплату",
            callback_data=f"dep:check:{payment_id}",
        )],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="dep:cancel")],
    ])


def usdt_sent_kb(payment_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=texts.BTN_USDT_SENT,
            callback_data=f"dep:usdt_sent:{payment_id}",
        )],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="dep:cancel")],
    ])


def confirm_bet_kb(bet_token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=texts.BTN_CONFIRM_BET,
                callback_data=f"bet:confirm:{bet_token}",
            ),
            InlineKeyboardButton(
                text=texts.BTN_CANCEL,
                callback_data="bet:cancel",
            ),
        ],
    ])


def support_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="✉️ Написать в поддержку",
            url=f"https://t.me/{settings.support_username}",
        )],
        [InlineKeyboardButton(
            text="📢 Канал платформы",
            url=f"https://t.me/{settings.bot_username}_news",
        )],
    ])


def back_to_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 В меню", callback_data="main:menu")],
    ])
