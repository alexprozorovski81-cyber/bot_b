"""Сервис работы с пользователями."""
import logging
import secrets
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import User, Bet, Transaction, TransactionType, RegistrationLog


logger = logging.getLogger(__name__)
WELCOME_BONUS = Decimal("500.00")  # Стартовый бонус новым пользователям


async def get_or_create_user(
    session: AsyncSession,
    telegram_id: int,
    username: str | None,
    first_name: str | None,
    referrer_telegram_id: int | None = None,
    ip: str | None = None,
    fingerprint: str | None = None,
) -> tuple[User, bool]:
    """
    Возвращает пользователя или создаёт нового с welcome-бонусом.

    При создании проверяет количество регистраций с того же IP (за 24ч)
    и fingerprint (за всё время). Если лимиты превышены — бонус не выдаётся.

    Returns:
        (user, is_new) — пользователь и флаг "был только что создан".
    """
    result = await session.execute(
        select(User).where(User.telegram_id == telegram_id)
    )
    user = result.scalar_one_or_none()

    if user:
        return user, False

    # Найти реферера, если указан
    referrer_id = None
    if referrer_telegram_id and referrer_telegram_id != telegram_id:
        ref_result = await session.execute(
            select(User).where(User.telegram_id == referrer_telegram_id)
        )
        referrer = ref_result.scalar_one_or_none()
        if referrer:
            referrer_id = referrer.id

    # Проверяем anti-fraud: количество регистраций с того же IP за последние 24ч
    grant_bonus = True
    fraud_reason = None

    if ip:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        ip_count_result = await session.execute(
            select(func.count(RegistrationLog.id)).where(
                RegistrationLog.ip_address == ip,
                RegistrationLog.created_at >= cutoff,
            )
        )
        ip_count = ip_count_result.scalar() or 0
        if ip_count >= 3:
            grant_bonus = False
            fraud_reason = f"ip_limit (ip={ip}, count={ip_count})"

    if grant_bonus and fingerprint:
        fp_count_result = await session.execute(
            select(func.count(RegistrationLog.id)).where(
                RegistrationLog.fingerprint == fingerprint,
            )
        )
        fp_count = fp_count_result.scalar() or 0
        if fp_count >= 1:
            grant_bonus = False
            fraud_reason = f"fingerprint_seen (fp={fingerprint[:8]}..., count={fp_count})"

    if not grant_bonus:
        logger.warning(
            "Welcome bonus withheld for tg=%s reason=%s", telegram_id, fraud_reason
        )

    # Уникальный реферальный код
    ref_code = secrets.token_urlsafe(8)[:12]

    user = User(
        telegram_id=telegram_id,
        username=username,
        first_name=first_name,
        balance_rub=WELCOME_BONUS if grant_bonus else Decimal("0.00"),
        referrer_id=referrer_id,
        referral_code=ref_code,
    )
    session.add(user)
    await session.flush()  # Получаем user.id

    if grant_bonus:
        bonus_tx = Transaction(
            user_id=user.id,
            type=TransactionType.BONUS,
            amount_rub=WELCOME_BONUS,
            balance_before=Decimal("0.00"),
            balance_after=WELCOME_BONUS,
            description="Welcome bonus",
        )
        session.add(bonus_tx)

    # Записываем лог регистрации
    session.add(RegistrationLog(
        telegram_id=telegram_id,
        ip_address=ip,
        fingerprint=fingerprint,
    ))

    await session.commit()

    # Алерт администраторам если бонус не выдан (подозрение на мульти-аккаунт)
    if not grant_bonus:
        try:
            from bot.notifier import notify_admins
            await notify_admins(
                f"⚠️ Multi-account suspected\n"
                f"tg_id: <code>{telegram_id}</code>\n"
                f"reason: {fraud_reason}"
            )
        except Exception as e:
            logger.warning("Failed to notify admins about multi-account: %s", e)

    return user, True


async def get_user_stats(session: AsyncSession, user: User) -> dict:
    """Статистика для отображения в профиле."""
    total_bets_q = select(func.count(Bet.id)).where(Bet.user_id == user.id)
    total_bets = (await session.execute(total_bets_q)).scalar() or 0

    active_q = select(func.count(Bet.id)).where(
        Bet.user_id == user.id,
        Bet.is_settled == False,  # noqa: E712
    )
    active = (await session.execute(active_q)).scalar() or 0

    wins_q = select(func.count(Bet.id)).where(
        Bet.user_id == user.id,
        Bet.is_settled == True,  # noqa: E712
        Bet.payout_rub > 0,
    )
    wins = (await session.execute(wins_q)).scalar() or 0

    profit_q = select(
        func.coalesce(func.sum(Bet.payout_rub - Bet.amount_rub), 0)
    ).where(
        Bet.user_id == user.id,
        Bet.is_settled == True,  # noqa: E712
    )
    profit = (await session.execute(profit_q)).scalar() or Decimal("0")

    settled = total_bets - active
    winrate = (wins * 100 // settled) if settled > 0 else 0

    return {
        "total_bets": total_bets,
        "active": active,
        "wins": wins,
        "winrate": winrate,
        "profit": profit,
    }
