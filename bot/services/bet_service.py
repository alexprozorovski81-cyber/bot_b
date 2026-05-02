"""
Сервис размещения ставок.
Связывает LMSR-движок с базой данных и балансами пользователей.
"""
import asyncio
import logging
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.services import market_engine
from db.database import is_sqlite, is_postgres
from db.models import (
    Bet, Event, EventStatus, Outcome,
    Transaction, TransactionType, User,
)

logger = logging.getLogger(__name__)

# Per-event asyncio locks для SQLite (нет SELECT FOR UPDATE)
_event_locks: dict[int, asyncio.Lock] = {}


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

    shares = market_engine.calculate_shares_for_amount(
        q, b, outcome_index, amount_rub
    )

    current_odds = market_engine.get_odds(q, b)[outcome_index]

    if amount_rub > 0 and shares > 0:
        avg_odds = (shares / amount_rub).quantize(Decimal("0.0001"))
    else:
        avg_odds = Decimal("0")

    potential_gross = shares
    fee_pct = Decimal("2")
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


async def _place_bet_inner(
    session: AsyncSession,
    user: User,
    event_id: int,
    outcome_id: int,
    amount_rub: Decimal,
) -> Bet:
    """Внутренняя логика ставки — вызывается уже под блокировкой события."""
    # Перечитываем свежие данные после захвата лока
    quote = await quote_bet(session, event_id, outcome_id, amount_rub)
    event = quote["event"]
    outcome = quote["outcomes"][quote["outcome_index"]]

    # Проверяем: событие ещё не закрыто по времени
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    closes_at = event.closes_at
    if closes_at.tzinfo is None:
        closes_at = closes_at.replace(tzinfo=timezone.utc)
    if closes_at <= now:
        raise BetError("Приём ставок на это событие завершён")

    # Проверяем: нет ставки на ДРУГОЙ исход этого события
    existing_r = await session.execute(
        select(Bet).where(
            Bet.user_id == user.id,
            Bet.event_id == event_id,
            Bet.is_settled == False,  # noqa: E712
        )
    )
    existing_bet = existing_r.scalar_one_or_none()
    if existing_bet and existing_bet.outcome_id != outcome_id:
        conflicting_r = await session.execute(
            select(Outcome).where(Outcome.id == existing_bet.outcome_id)
        )
        conflicting = conflicting_r.scalar_one_or_none()
        conflict_title = conflicting.title if conflicting else "другой исход"
        raise BetError(
            f"Вы уже поставили на «{conflict_title}». "
            "На одно событие можно ставить только на один исход."
        )

    # Перепроверяем баланс под локом (мог измениться)
    await session.refresh(user)
    if user.balance_rub < amount_rub:
        raise BetError(
            f"Недостаточно средств. На балансе: {user.balance_rub:.2f} монет"
        )

    shares_before = outcome.shares_outstanding
    balance_before = user.balance_rub

    user.balance_rub -= amount_rub
    balance_after = user.balance_rub

    outcome.shares_outstanding += quote["shares"]

    logger.info(
        "BET place: user=%s event=%s outcome=%s amount=%s shares_before=%s",
        user.id, event_id, outcome_id, amount_rub, shares_before,
    )

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

    # Логическая защита: shares_outstanding не должен уменьшиться
    if outcome.shares_outstanding < shares_before:
        logger.error(
            "BET integrity error: shares_outstanding decreased! "
            "before=%s after=%s user=%s event=%s",
            shares_before, outcome.shares_outstanding, user.id, event_id,
        )
        raise BetError("Внутренняя ошибка: нарушена целостность shares")

    logger.info(
        "BET done: user=%s balance %s→%s shares_outstanding %s→%s",
        user.id, balance_before, balance_after,
        shares_before, outcome.shares_outstanding,
    )

    return bet


async def place_bet(
    session: AsyncSession,
    user_id: int,
    event_id: int,
    outcome_id: int,
    amount_rub: Decimal,
) -> Bet:
    """
    Размещение ставки атомарно с защитой от race condition.

    SQLite: asyncio.Lock per event_id (нет SELECT FOR UPDATE).
    PostgreSQL: SELECT FOR UPDATE на Event сериализует параллельные ставки.
    """
    if amount_rub < Decimal("10"):
        raise BetError("Минимальная ставка — 10 монет")

    # Блокируем строку пользователя
    user_q = select(User).where(User.id == user_id)
    if not is_sqlite():
        user_q = user_q.with_for_update()
    user_result = await session.execute(user_q)
    user = user_result.scalar_one_or_none()
    if not user:
        raise BetError("Пользователь не найден")

    # Быстрая проверка до лока (оптимистичная)
    if user.balance_rub < amount_rub:
        raise BetError(
            f"Недостаточно средств. На балансе: {user.balance_rub:.2f} монет"
        )

    if is_postgres():
        # PostgreSQL: блокируем строку Event — сериализует все ставки на событие
        event_q = select(Event).where(Event.id == event_id).with_for_update()
        event_result = await session.execute(event_q)
        if not event_result.scalar_one_or_none():
            raise BetError("Событие не найдено")
        bet = await _place_bet_inner(session, user, event_id, outcome_id, amount_rub)
    else:
        # SQLite: asyncio.Lock per event_id
        if event_id not in _event_locks:
            _event_locks[event_id] = asyncio.Lock()
        async with _event_locks[event_id]:
            bet = await _place_bet_inner(session, user, event_id, outcome_id, amount_rub)

    await session.refresh(bet)

    try:
        from bot.services.achievement_service import check_and_award
        await check_and_award(user.id, session)
    except Exception:
        pass

    return bet
