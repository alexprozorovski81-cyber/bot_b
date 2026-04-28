"""
Сервис вывода средств пользователями.

Схема:
1. Пользователь создаёт заявку → баланс замораживается (списывается)
2. Если авто-вывод включён и сумма в лимитах → обрабатывается автоматически
3. Иначе → статус pending, ждёт ручного одобрения администратором

Анти-фрод проверки:
- Минимум N разрешённых ставок (settled bets)
- Сумма вывода ≤ deposits + payouts − уже_выведено
- Cooldown 24ч между выводами
- KYC-порог: крупные суммы — только ручной вывод
"""
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from db.models import (
    Bet, Transaction, TransactionType,
    User, WithdrawalRequest, WithdrawStatus,
)

logger = logging.getLogger(__name__)


class WithdrawError(Exception):
    """Ошибка при создании заявки на вывод."""


async def create_withdrawal(
    session: AsyncSession,
    user: User,
    amount_coins: Decimal,
    network: str,
    wallet_address: str,
) -> WithdrawalRequest:
    """
    Создаёт заявку на вывод средств после прохождения анти-фрод проверок.

    При успехе: баланс списывается, создаётся WITHDRAW транзакция.
    Если откажут позже — вернём через BONUS транзакцию.
    """
    from bot.services.rate_service import get_usdt_rub_rate

    min_withdraw = settings.min_withdraw_coins
    if amount_coins < min_withdraw:
        raise WithdrawError(f"Минимальная сумма вывода: {min_withdraw:.0f} монет")

    if user.balance_rub < amount_coins:
        raise WithdrawError(
            f"Недостаточно средств. Баланс: {user.balance_rub:.2f} монет"
        )

    # ── Анти-фрод: минимум settled ставок ──────────────────────────────────
    settled_count = await session.scalar(
        select(func.count()).where(
            Bet.user_id == user.id,
            Bet.is_settled == True,  # noqa: E712
        )
    ) or 0
    if settled_count < settings.withdraw_min_settled_bets:
        raise WithdrawError(
            f"Вывод доступен после завершения хотя бы "
            f"{settings.withdraw_min_settled_bets} ставки(ок)"
        )

    # ── Анти-фрод: cooldown ─────────────────────────────────────────────────
    cooldown_since = datetime.now(timezone.utc) - timedelta(hours=settings.withdraw_cooldown_h)
    recent_wd = await session.scalar(
        select(func.count()).where(
            WithdrawalRequest.user_id == user.id,
            WithdrawalRequest.created_at >= cooldown_since,
            WithdrawalRequest.status != WithdrawStatus.REJECTED,
        )
    ) or 0
    if recent_wd > 0:
        raise WithdrawError(
            f"Повторный вывод доступен через {settings.withdraw_cooldown_h} часов"
        )

    # ── Анти-фрод: сумма ≤ (пополнения + выигрыши − уже выведено) ──────────
    deposits_sum = await session.scalar(
        select(func.coalesce(func.sum(Transaction.amount_rub), 0)).where(
            Transaction.user_id == user.id,
            Transaction.type == TransactionType.DEPOSIT,
        )
    ) or Decimal("0")
    payouts_sum = await session.scalar(
        select(func.coalesce(func.sum(Transaction.amount_rub), 0)).where(
            Transaction.user_id == user.id,
            Transaction.type == TransactionType.BET_PAYOUT,
        )
    ) or Decimal("0")
    bonuses_sum = await session.scalar(
        select(func.coalesce(func.sum(Transaction.amount_rub), 0)).where(
            Transaction.user_id == user.id,
            Transaction.type == TransactionType.BONUS,
        )
    ) or Decimal("0")
    already_withdrawn = await session.scalar(
        select(func.coalesce(func.sum(WithdrawalRequest.amount_coins), 0)).where(
            WithdrawalRequest.user_id == user.id,
            WithdrawalRequest.status.in_([
                WithdrawStatus.PENDING, WithdrawStatus.APPROVED, WithdrawStatus.PAID
            ]),
        )
    ) or Decimal("0")
    max_withdrawable = deposits_sum + payouts_sum + bonuses_sum - already_withdrawn
    if amount_coins > max_withdrawable:
        raise WithdrawError(
            f"Сумма превышает доступный лимит вывода: {max_withdrawable:.0f} монет"
        )

    # ── Получаем актуальный курс ─────────────────────────────────────────────
    rate = await get_usdt_rub_rate()
    amount_usdt = (amount_coins / rate).quantize(Decimal("0.000001"))

    # ── Списываем баланс сразу (заморозка) ───────────────────────────────────
    balance_before = user.balance_rub
    user.balance_rub -= amount_coins

    tx = Transaction(
        user_id=user.id,
        type=TransactionType.WITHDRAW,
        amount_rub=-amount_coins,
        balance_before=balance_before,
        balance_after=user.balance_rub,
        description=f"Запрос вывода {amount_usdt:.2f} USDT → {network.upper()}",
    )
    session.add(tx)

    wd = WithdrawalRequest(
        user_id=user.id,
        amount_coins=amount_coins,
        amount_usdt=amount_usdt,
        rate_at_request=rate,
        network=network,
        wallet_address=wallet_address,
        status=WithdrawStatus.PENDING,
    )
    session.add(wd)
    await session.flush()
    await session.commit()
    await session.refresh(wd)

    # ── Уведомляем администраторов ───────────────────────────────────────────
    await _notify_admins_new_withdrawal(wd, user)

    logger.info(
        "Withdrawal created: id=%s user=%s amount=%s USDT network=%s",
        wd.id, user.id, amount_usdt, network,
    )
    return wd


async def process_pending_withdrawals(session: AsyncSession) -> int:
    """
    Cron-задача: обрабатывает pending-заявки автоматически (если включено).
    Возвращает количество обработанных заявок.
    """
    if not settings.withdraw_auto_enabled:
        return 0

    from bot.services.ton_withdraw_service import send_usdt, get_hot_wallet_usdt_balance

    # Проверяем баланс горячего кошелька
    hot_balance = await get_hot_wallet_usdt_balance()
    if hot_balance < Decimal("1"):
        logger.warning("Hot wallet USDT balance too low: %s", hot_balance)
        return 0

    # Берём pending-заявки на USDT TON в порядке очереди
    result = await session.execute(
        select(WithdrawalRequest).where(
            WithdrawalRequest.status == WithdrawStatus.PENDING,
            WithdrawalRequest.network == "usdt_ton",
            WithdrawalRequest.retry_count < 3,
        ).order_by(WithdrawalRequest.created_at).limit(5)
    )
    pending = list(result.scalars().all())
    processed = 0

    for wd in pending:
        amount_usdt = wd.amount_usdt
        if not amount_usdt:
            continue

        # Лимит авто-вывода
        if amount_usdt > settings.withdraw_max_auto_usdt:
            logger.info("Withdrawal %s exceeds auto limit (%s USDT), skipping", wd.id, amount_usdt)
            continue

        if hot_balance < amount_usdt:
            logger.warning("Hot wallet balance %s < withdrawal %s USDT", hot_balance, amount_usdt)
            break

        tx_hash = await send_usdt(
            to_address=wd.wallet_address,
            amount_usdt=amount_usdt,
            comment=f"PredictBet withdrawal #{wd.id}",
        )

        if tx_hash:
            wd.status = WithdrawStatus.PAID
            wd.tx_hash = tx_hash
            wd.is_auto = True
            wd.processed_at = datetime.now(timezone.utc)
            hot_balance -= amount_usdt
            processed += 1
            await _notify_user_paid(wd)
            logger.info("Auto withdrawal paid: id=%s tx=%s", wd.id, tx_hash)
        else:
            wd.retry_count += 1
            logger.warning("Auto withdrawal failed: id=%s retry=%s", wd.id, wd.retry_count)

    await session.commit()
    return processed


async def admin_approve_withdrawal(
    session: AsyncSession,
    withdrawal_id: int,
    tx_hash: str | None = None,
) -> WithdrawalRequest:
    """Администратор подтверждает вывод (ручной)."""
    result = await session.execute(
        select(WithdrawalRequest).where(WithdrawalRequest.id == withdrawal_id)
    )
    wd = result.scalar_one_or_none()
    if not wd:
        raise ValueError(f"Withdrawal {withdrawal_id} not found")
    if wd.status != WithdrawStatus.PENDING:
        raise ValueError(f"Withdrawal {withdrawal_id} is not pending (status={wd.status})")

    wd.status = WithdrawStatus.PAID
    wd.tx_hash = tx_hash
    wd.processed_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(wd)

    await _notify_user_paid(wd)
    return wd


async def admin_reject_withdrawal(
    session: AsyncSession,
    withdrawal_id: int,
    reason: str,
) -> WithdrawalRequest:
    """Администратор отклоняет вывод — монеты возвращаются."""
    result = await session.execute(
        select(WithdrawalRequest).where(WithdrawalRequest.id == withdrawal_id)
    )
    wd = result.scalar_one_or_none()
    if not wd:
        raise ValueError(f"Withdrawal {withdrawal_id} not found")
    if wd.status != WithdrawStatus.PENDING:
        raise ValueError(f"Withdrawal {withdrawal_id} is not pending")

    # Возвращаем монеты
    user_result = await session.execute(select(User).where(User.id == wd.user_id))
    user = user_result.scalar_one()

    balance_before = user.balance_rub
    user.balance_rub += wd.amount_coins
    session.add(Transaction(
        user_id=user.id,
        type=TransactionType.BONUS,
        amount_rub=wd.amount_coins,
        balance_before=balance_before,
        balance_after=user.balance_rub,
        description=f"Возврат по отклонённому выводу #{wd.id}: {reason}",
    ))

    wd.status = WithdrawStatus.REJECTED
    wd.admin_note = reason
    wd.processed_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(wd)

    await _notify_user_rejected(wd, reason)
    return wd


async def _notify_admins_new_withdrawal(wd: WithdrawalRequest, user: User) -> None:
    """Уведомляет администраторов о новой заявке на вывод."""
    from bot import notifier
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    bot = notifier._bot
    if not bot or not settings.admin_id_list:
        return

    text = (
        f"<b>💸 Новый запрос на вывод #{wd.id}</b>\n\n"
        f"👤 Пользователь: <code>{user.telegram_id}</code>"
        f"{(' @' + user.username) if user.username else ''}\n"
        f"💰 Сумма: <b>{wd.amount_coins:.0f} монет</b>"
        f" (~{wd.amount_usdt:.2f} USDT)\n"
        f"🌐 Сеть: <b>{wd.network.upper()}</b>\n"
        f"📬 Адрес: <code>{wd.wallet_address}</code>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Выплатил", callback_data=f"wd:approve:{wd.id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"wd:reject:{wd.id}"),
    ]])

    for admin_id in settings.admin_id_list:
        try:
            await bot.send_message(admin_id, text, reply_markup=kb, parse_mode="HTML")
        except Exception as e:
            logger.warning("Admin notify error %s: %s", admin_id, e)


async def _notify_user_paid(wd: WithdrawalRequest) -> None:
    """Уведомляет пользователя об успешной выплате."""
    from bot import notifier
    bot = notifier._bot
    if not bot:
        return

    try:
        user_result_placeholder = wd.user_id
        from db.database import AsyncSessionLocal
        async with AsyncSessionLocal() as s:
            u = await s.get(User, wd.user_id)
            if not u:
                return
            tx_info = f"\n🔗 TX: <code>{wd.tx_hash}</code>" if wd.tx_hash else ""
            await bot.send_message(
                u.telegram_id,
                f"<b>✅ Вывод #{wd.id} выплачен!</b>\n\n"
                f"Отправлено: <b>{wd.amount_usdt:.2f} USDT</b>\n"
                f"На кошелёк: <code>{wd.wallet_address}</code>"
                f"{tx_info}",
                parse_mode="HTML",
            )
    except Exception as e:
        logger.warning("notify_user_paid error: %s", e)


async def _notify_user_rejected(wd: WithdrawalRequest, reason: str) -> None:
    """Уведомляет пользователя об отклонении заявки."""
    from bot import notifier
    bot = notifier._bot
    if not bot:
        return

    try:
        from db.database import AsyncSessionLocal
        async with AsyncSessionLocal() as s:
            u = await s.get(User, wd.user_id)
            if not u:
                return
            await bot.send_message(
                u.telegram_id,
                f"<b>❌ Заявка на вывод #{wd.id} отклонена</b>\n\n"
                f"Сумма <b>{wd.amount_coins:.0f} монет</b> возвращена на баланс.\n"
                f"Причина: {reason}",
                parse_mode="HTML",
            )
    except Exception as e:
        logger.warning("notify_user_rejected error: %s", e)
