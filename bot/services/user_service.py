"""Сервис работы с пользователями."""
import secrets
from decimal import Decimal

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import User, Bet, Transaction, TransactionType


WELCOME_BONUS = Decimal("500.00")  # Стартовый бонус новым пользователям


async def get_or_create_user(
    session: AsyncSession,
    telegram_id: int,
    username: str | None,
    first_name: str | None,
    referrer_telegram_id: int | None = None,
) -> tuple[User, bool]:
    """
    Возвращает пользователя или создаёт нового с welcome-бонусом.

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

    # Уникальный реферальный код
    ref_code = secrets.token_urlsafe(8)[:12]

    user = User(
        telegram_id=telegram_id,
        username=username,
        first_name=first_name,
        balance_rub=WELCOME_BONUS,
        referrer_id=referrer_id,
        referral_code=ref_code,
    )
    session.add(user)
    await session.flush()  # Чтобы получить user.id

    # Записываем welcome-бонус как транзакцию
    bonus_tx = Transaction(
        user_id=user.id,
        type=TransactionType.BONUS,
        amount_rub=WELCOME_BONUS,
        balance_before=Decimal("0.00"),
        balance_after=WELCOME_BONUS,
        description="Welcome bonus",
    )
    session.add(bonus_tx)
    await session.commit()

    return user, True


async def get_user_stats(session: AsyncSession, user: User) -> dict:
    """Статистика для отображения в профиле."""
    # Всего ставок
    total_bets_q = select(func.count(Bet.id)).where(Bet.user_id == user.id)
    total_bets = (await session.execute(total_bets_q)).scalar() or 0

    # Активные (не разрешённые)
    active_q = select(func.count(Bet.id)).where(
        Bet.user_id == user.id,
        Bet.is_settled == False,  # noqa: E712
    )
    active = (await session.execute(active_q)).scalar() or 0

    # Выигрыши
    wins_q = select(func.count(Bet.id)).where(
        Bet.user_id == user.id,
        Bet.is_settled == True,  # noqa: E712
        Bet.payout_rub > 0,
    )
    wins = (await session.execute(wins_q)).scalar() or 0

    # Чистая прибыль = сумма выплат - сумма ставок (только по разрешённым)
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
