"""
Сервис платежей.

ЮKassa — карты, СБП. Использует официальный SDK.
USDT — приём через TON, проверка по tonapi.io / Toncenter v3.
"""
import logging
import uuid
from decimal import Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from yookassa import Configuration as YKConfig, Payment as YKPayment

from bot.config import settings
from db.models import Payment, PaymentMethod, Transaction, TransactionType, User

logger = logging.getLogger(__name__)


# Курс USDT → RUB. В проде берём из API биржи.
USDT_TO_RUB_RATE = Decimal("95.00")


def _setup_yookassa() -> None:
    """Конфигурируем ЮKassa SDK при первом обращении."""
    if settings.yookassa_shop_id and settings.yookassa_secret_key:
        YKConfig.account_id = settings.yookassa_shop_id
        YKConfig.secret_key = settings.yookassa_secret_key


async def create_card_payment(
    session: AsyncSession,
    user: User,
    amount_rub: Decimal,
) -> tuple[Payment, str]:
    """
    Создаёт платёж в ЮKassa и возвращает URL для оплаты.

    Returns:
        (Payment объект из БД, URL для оплаты)
    """
    _setup_yookassa()

    idempotence_key = str(uuid.uuid4())
    yk_payment = YKPayment.create({
        "amount": {
            "value": f"{amount_rub:.2f}",
            "currency": "RUB",
        },
        "confirmation": {
            "type": "redirect",
            "return_url": f"https://t.me/{settings.bot_username}",
        },
        "capture": True,
        "description": f"Пополнение баланса PredictBet, user {user.telegram_id}",
        "metadata": {
            "user_id": str(user.id),
            "telegram_id": str(user.telegram_id),
        },
    }, idempotence_key)

    payment = Payment(
        user_id=user.id,
        method=PaymentMethod.YOOKASSA_CARD,
        amount_rub=amount_rub,
        external_id=yk_payment.id,
        status="pending",
        is_deposit=True,
    )
    session.add(payment)
    await session.commit()
    await session.refresh(payment)

    return payment, yk_payment.confirmation.confirmation_url


async def check_card_payment(
    session: AsyncSession,
    payment: Payment,
) -> str:
    """
    Проверяет статус ЮKassa-платежа и обновляет в БД.

    Returns:
        Новый статус: "pending", "succeeded", "canceled".
    """
    _setup_yookassa()
    yk_payment = YKPayment.find_one(payment.external_id)
    payment.status = yk_payment.status
    await session.commit()
    return yk_payment.status


async def create_usdt_payment(
    session: AsyncSession,
    user: User,
    amount_usdt: Decimal,
) -> Payment:
    """
    Создаёт запись об ожидаемом USDT-платеже.
    Реальная проверка прихода — через check_usdt_arrival().
    """
    amount_rub = (amount_usdt * USDT_TO_RUB_RATE).quantize(Decimal("0.01"))
    payment = Payment(
        user_id=user.id,
        method=PaymentMethod.USDT_TON,
        amount_rub=amount_rub,
        status="pending",
        is_deposit=True,
    )
    session.add(payment)
    await session.commit()
    await session.refresh(payment)
    return payment


async def check_usdt_arrival(
    session: AsyncSession,
    payment: Payment,
) -> bool:
    """
    Проверяет приход USDT через tonapi.io.

    Возвращает True если транзакция найдена.

    ВАЖНО: для production нужна более надёжная проверка —
    с user-specific комментарием в транзакции, чтобы матчить пользователя.
    Это упрощённая версия для демо.
    """
    if not settings.ton_api_key or not settings.usdt_wallet_address:
        return False

    expected_usdt = payment.amount_rub / USDT_TO_RUB_RATE

    headers = {"Authorization": f"Bearer {settings.ton_api_key}"}
    url = (
        f"https://tonapi.io/v2/accounts/{settings.usdt_wallet_address}"
        f"/jettons/{settings.usdt_jetton_master}/history?limit=20"
    )

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError:
            return False

    # Ищем входящую транзакцию с нужной суммой
    # USDT использует 6 знаков после запятой, так что amount там в micro-units
    target_micro = int(expected_usdt * Decimal("1000000"))

    for event in data.get("events", []):
        for action in event.get("actions", []):
            if action.get("type") != "JettonTransfer":
                continue
            transfer = action.get("JettonTransfer", {})
            if transfer.get("recipient", {}).get("address") != settings.usdt_wallet_address:
                continue
            amount = int(transfer.get("amount", 0))
            # Допускаем 1% погрешность из-за курса
            if abs(amount - target_micro) <= target_micro * 0.01:
                payment.status = "succeeded"
                payment.external_id = event.get("event_id")
                await session.commit()
                return True

    return False


async def check_usdt_toncenter(
    session: AsyncSession,
    payment: Payment,
) -> bool:
    """
    Проверяет приход USDT через Toncenter v3 API.

    Ищет входящий Jetton Transfer на платформенный кошелёк
    с memo/comment равным str(payment.id).
    При успехе: зачисляет баланс пользователю, создаёт Transaction, отправляет уведомление.

    Returns:
        True если транзакция найдена и обработана.
    """
    if not settings.usdt_wallet_address:
        return False

    headers = {}
    if settings.ton_api_key:
        headers["X-API-Key"] = settings.ton_api_key

    memo_target = str(payment.id)
    url = (
        f"https://toncenter.com/api/v3/jetton/transfers"
        f"?direction=in&address={settings.usdt_wallet_address}&limit=50"
    )

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as e:
            logger.warning("Toncenter API error: %s", e)
            return False

    jetton_transfers = data.get("jetton_transfers", [])
    for transfer in jetton_transfers:
        comment = transfer.get("comment") or ""
        if comment.strip() != memo_target:
            continue

        # Транзакция найдена — зачисляем баланс
        user_result = await session.execute(select(User).where(User.id == payment.user_id))
        user = user_result.scalar_one_or_none()
        if not user:
            return False

        balance_before = user.balance_rub
        user.balance_rub += payment.amount_rub

        payment.status = "succeeded"
        payment.external_id = transfer.get("transaction_hash") or transfer.get("hash")

        tx = Transaction(
            user_id=user.id,
            type=TransactionType.DEPOSIT,
            amount_rub=payment.amount_rub,
            balance_before=balance_before,
            balance_after=user.balance_rub,
            payment_id=payment.id,
            description="USDT deposit via TON",
        )
        session.add(tx)
        await session.commit()

        # Уведомление в Telegram
        try:
            from bot.main import bot as tg_bot
            await tg_bot.send_message(
                user.telegram_id,
                f"✅ Пополнение подтверждено!\n"
                f"Зачислено: *{payment.amount_rub:,.0f} ₽*\n"
                f"Новый баланс: *{user.balance_rub:,.0f} ₽*",
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.warning("Failed to send deposit notification: %s", e)

        logger.info("USDT deposit confirmed: payment_id=%s user_id=%s amount=%s",
                    payment.id, user.id, payment.amount_rub)
        return True

    return False
