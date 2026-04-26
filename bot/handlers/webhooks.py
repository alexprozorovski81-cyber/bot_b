"""
Webhook эндпоинт для ЮKassa.

Регистрируется в личном кабинете ЮKassa:
  https://your-domain.com/webhooks/yookassa

Документация: https://yookassa.ru/developers/using-api/webhooks
"""
import logging
from decimal import Decimal

from fastapi import APIRouter, Request, HTTPException
from sqlalchemy import select

from db.database import AsyncSessionLocal
from db.models import (
    Payment, Transaction, TransactionType, User,
)


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/yookassa")
async def yookassa_webhook(request: Request) -> dict:
    """
    Обработка уведомлений от ЮKassa.

    Когда платёж переходит в succeeded — автоматически зачисляем баланс.
    Идемпотентно: если уже обработали — просто игнорируем.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON")

    event_type = body.get("event")
    obj = body.get("object", {})
    yk_payment_id = obj.get("id")

    if not yk_payment_id:
        return {"status": "no_id"}

    logger.info(f"YooKassa webhook: {event_type} for {yk_payment_id}")

    if event_type != "payment.succeeded":
        return {"status": "ignored"}

    async with AsyncSessionLocal() as session:
        # Находим платёж по external_id
        result = await session.execute(
            select(Payment).where(Payment.external_id == yk_payment_id)
        )
        payment = result.scalar_one_or_none()

        if not payment:
            logger.warning(f"Payment {yk_payment_id} not found in DB")
            return {"status": "not_found"}

        # Идемпотентность — если уже зачислен, выходим
        if payment.status == "succeeded":
            return {"status": "already_processed"}

        # Достаём пользователя
        user_result = await session.execute(
            select(User).where(User.id == payment.user_id)
        )
        user = user_result.scalar_one()

        # Зачисляем средства
        balance_before = user.balance_rub
        user.balance_rub += payment.amount_rub
        balance_after = user.balance_rub

        payment.status = "succeeded"

        tx = Transaction(
            user_id=user.id,
            type=TransactionType.DEPOSIT,
            amount_rub=payment.amount_rub,
            balance_before=balance_before,
            balance_after=balance_after,
            payment_id=payment.id,
            description="Пополнение через ЮKassa (webhook)",
        )
        session.add(tx)
        await session.commit()

        logger.info(
            f"User {user.telegram_id} deposit succeeded: "
            f"+{payment.amount_rub} ₽ (balance now {balance_after})"
        )

        # Отправим пользователю уведомление в Telegram
        try:
            from bot.notifier import notify_user
            from bot import texts
            await notify_user(
                user.telegram_id,
                texts.DEPOSIT_SUCCESS.format(
                    amount=f"{payment.amount_rub:.2f}",
                    balance=f"{balance_after:.2f}",
                ),
                sticker_key="deposit_success",
            )
        except Exception as e:
            logger.warning(f"Failed to notify user: {e}")

        return {"status": "ok"}
