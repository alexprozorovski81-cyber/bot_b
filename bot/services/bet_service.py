"""
Сервис размещения ставок.
Связывает LMSR-движок с базой данных и балансами пользователей.
"""
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.services import market_engine
from db.models import (
    Bet, Event, EventStatus, Outcome,
    Transaction, TransactionType, User,
)


class BetError(Exception):
    """Ошибка размещения ставки."""


async def get_event_with_outcomes(
    session: AsyncSession, event_id: int
) -> tuple[Event, list[Outcome]]:
    """Загружает событие со всеми исходами. Возвращает кортеж."""
    result = await session.execute(
        select(Event).where(Event.id == event_id)
    )
    event = result.scalar_one_or_none()
    if not event:
        raise BetError("Событие не найдено")

    outcomes_result = await session.execute(
        select(Outcome).where(Outcome.event_id == event_id).order_by(Outcome.sort_order)
    )
    outcomes = list(outcomes_result.scalars().all())
    return event, outcomes


async def quote_bet(
    session: AsyncSession,
    event_id: int,
    outcome_id: int,
    amount_rub: Decimal,
) -> dict:
    """
    Котировка ставки до размещения — сколько акций получит пользователь
    и какой средний коэффициент. Не списывает баланс.
    """
    event, outcomes = await get_event_with_outcomes(session, event_id)

    if event.status != EventStatus.ACTIVE:
        raise BetError("Ставки на это событие закрыты")

    outcome_index = next(
        (i for i, o in enumerate(outcomes) if o.id == outcome_id),
        None,
    )
    if outcome_index is None:
        raise BetError("Исход не найден")

    q = [o.shares_outstanding for o in outcomes]
    b = event.liquidity_b

    # Считаем сколько акций получит пользователь
    shares = market_engine.calculate_shares_for_amount(
        q, b, outcome_index, amount_rub
    )

    # Текущий коэф (до сделки)
    current_odds = market_engine.get_odds(q, b)[outcome_index]

    # Эффективный коэф = amount / shares (с учётом slippage)
    if shares > 0:
        avg_odds = (Decimal("1") / (amount_rub / shares)).quantize(Decimal("0.0001"))
    else:
        avg_odds = Decimal("0")

    # Потенциальная выплата = shares * 1₽ за вычетом комиссии (упрощённо)
    potential_gross = shares
    fee_pct = Decimal("2")  # из настроек
    potential_net = (potential_gross * (Decimal("100") - fee_pct) / Decimal("100"))

    return {
        "event": event,
        "outcomes": outcomes,
        "outcome_index": outcome_index,
        "outcome_title": outcomes[outcome_index].title,
        "shares": shares,
        "current_odds": current_odds,
        "avg_odds": avg_odds,
        "potential_payout": potential_net.quantize(Decimal("0.01")),
    }


async def place_bet(
    session: AsyncSession,
    user: User,
    event_id: int,
    outcome_id: int,
    amount_rub: Decimal,
) -> Bet:
    """
    Размещение ставки атомарно:
      1. Проверяем баланс
      2. Считаем котировку
      3. Списываем средства
      4. Обновляем shares_outstanding исхода
      5. Создаём Bet и Transaction

    Всё внутри одной транзакции БД.
    """
    if amount_rub < Decimal("10"):
        raise BetError("Минимальная ставка — 10 ₽")

    if user.balance_rub < amount_rub:
        raise BetError(
            f"Недостаточно средств. На балансе: {user.balance_rub} ₽"
        )

    quote = await quote_bet(session, event_id, outcome_id, amount_rub)
    event = quote["event"]
    outcome = quote["outcomes"][quote["outcome_index"]]

    # Списываем баланс
    balance_before = user.balance_rub
    user.balance_rub -= amount_rub
    balance_after = user.balance_rub

    # Обновляем количество акций исхода
    outcome.shares_outstanding += quote["shares"]

    # Создаём ставку
    bet = Bet(
        user_id=user.id,
        event_id=event.id,
        outcome_id=outcome.id,
        amount_rub=amount_rub,
        shares=quote["shares"],
        avg_odds=quote["avg_odds"],
    )
    session.add(bet)
    await session.flush()

    # Транзакция списания
    tx = Transaction(
        user_id=user.id,
        type=TransactionType.BET_PLACE,
        amount_rub=-amount_rub,
        balance_before=balance_before,
        balance_after=balance_after,
        bet_id=bet.id,
        description=f"Ставка на «{outcome.title}» — {event.title[:80]}",
    )
    session.add(tx)

    await session.commit()
    await session.refresh(bet)

    # Проверяем и выдаём ачивки (fire-and-forget)
    try:
        from bot.services.achievement_service import check_and_award
        await check_and_award(user.id, session)
    except Exception:
        pass

    return bet
