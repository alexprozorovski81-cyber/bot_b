"""
Сервис экспресс-ставок (только для FIXED_ODDS событий).

Экспресс — комбинация 2-5 независимых ставок.
Выигрыш только если ВСЕ ноги сыграли.
Итоговый коэффициент = произведение всех ног.
Отменённая нога → её коэффициент = 1.0 (пересчёт).
"""
import logging
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import (
    Bet, Event, EventStatus, EventType, ExpressLeg,
    Express, Outcome, Transaction, TransactionType, User,
)

logger = logging.getLogger(__name__)

EXPRESS_MIN_LEGS = 2
EXPRESS_MAX_LEGS = 5
SELL_PENALTY = Decimal("0.25")  # 25% комиссия при продаже


class ExpressError(Exception):
    pass


async def create_express(
    session: AsyncSession,
    user_id: int,
    legs: list[dict],  # [{"event_id": int, "outcome_id": int}, ...]
    stake: Decimal,
) -> Express:
    """
    Создаёт экспресс-ставку.

    Args:
        legs: список ног, каждая с event_id и outcome_id
        stake: размер общей ставки

    Returns:
        Созданный объект Express
    """
    if stake < Decimal("10"):
        raise ExpressError("Минимальная ставка экспресс — 10 монет")

    n = len(legs)
    if n < EXPRESS_MIN_LEGS:
        raise ExpressError(f"Минимум {EXPRESS_MIN_LEGS} события в экспрессе")
    if n > EXPRESS_MAX_LEGS:
        raise ExpressError(f"Максимум {EXPRESS_MAX_LEGS} событий в экспрессе")

    # Проверяем уникальность событий
    event_ids = [leg["event_id"] for leg in legs]
    if len(set(event_ids)) != len(event_ids):
        raise ExpressError("Нельзя добавить одно событие дважды")

    # Загружаем пользователя
    user_r = await session.execute(select(User).where(User.id == user_id))
    user = user_r.scalar_one_or_none()
    if not user:
        raise ExpressError("Пользователь не найден")
    if user.balance_rub < stake:
        raise ExpressError(f"Недостаточно средств. На балансе: {user.balance_rub:.2f} монет")

    now = datetime.now(timezone.utc)
    leg_data: list[tuple[Event, Outcome, Decimal]] = []

    for leg in legs:
        event_r = await session.execute(select(Event).where(Event.id == leg["event_id"]))
        event = event_r.scalar_one_or_none()
        if not event:
            raise ExpressError(f"Событие #{leg['event_id']} не найдено")
        if event.event_type != EventType.FIXED_ODDS:
            raise ExpressError(
                f"«{event.title[:50]}» — не подходит для экспресса. "
                "Только краткосрочные события с фиксированным коэффициентом."
            )
        if event.status != EventStatus.ACTIVE:
            raise ExpressError(f"«{event.title[:50]}» — ставки закрыты")

        closes_at = event.closes_at
        if closes_at.tzinfo is None:
            closes_at = closes_at.replace(tzinfo=timezone.utc)
        if closes_at <= now:
            raise ExpressError(f"«{event.title[:50]}» — приём ставок завершён")

        outcome_r = await session.execute(
            select(Outcome).where(
                Outcome.id == leg["outcome_id"],
                Outcome.event_id == event.id,
            )
        )
        outcome = outcome_r.scalar_one_or_none()
        if not outcome:
            raise ExpressError(f"Исход для события #{leg['event_id']} не найден")

        odds_yes = event.odds_yes or Decimal("1.85")
        odds_no = event.odds_no or Decimal("1.95")
        leg_odds = odds_yes if outcome.title == "Да" else odds_no

        leg_data.append((event, outcome, leg_odds))

    # Считаем итоговый коэффициент
    total_odds = Decimal("1")
    for _, _, o in leg_data:
        total_odds *= o
    total_odds = total_odds.quantize(Decimal("0.0001"))
    potential_payout = (stake * total_odds).quantize(Decimal("0.01"))

    # Списываем ставку
    balance_before = user.balance_rub
    user.balance_rub -= stake

    express = Express(
        user_id=user_id,
        stake=stake,
        total_odds=total_odds,
        potential_payout=potential_payout,
        status="active",
    )
    session.add(express)
    await session.flush()

    for (event, outcome, leg_odds) in leg_data:
        session.add(ExpressLeg(
            express_id=express.id,
            event_id=event.id,
            outcome_id=outcome.id,
            odds=leg_odds,
            result="pending",
        ))

    session.add(Transaction(
        user_id=user_id,
        type=TransactionType.BET_PLACE,
        amount_rub=-stake,
        balance_before=balance_before,
        balance_after=user.balance_rub,
        description=(
            f"Экспресс ×{len(leg_data)} — коэф. {total_odds}, "
            f"потенциал {potential_payout:.2f} ₽"
        ),
    ))

    await session.commit()
    await session.refresh(express)

    logger.info(
        "Express #%s created: user=%s legs=%d total_odds=%s stake=%s",
        express.id, user_id, len(leg_data), total_odds, stake,
    )
    return express


async def settle_express_legs(
    event_id: int,
    winning_outcome_id: int,
    session: AsyncSession,
) -> None:
    """
    Обновляет результаты ног экспресса после резолва события.
    Вызывается из confirm_resolution.
    """
    legs_r = await session.execute(
        select(ExpressLeg).where(
            ExpressLeg.event_id == event_id,
            ExpressLeg.result == "pending",
        )
    )
    legs = list(legs_r.scalars().all())

    express_ids_to_check: set[int] = set()

    for leg in legs:
        leg.result = "won" if leg.outcome_id == winning_outcome_id else "lost"
        express_ids_to_check.add(leg.express_id)

    await session.flush()

    for express_id in express_ids_to_check:
        await _try_settle_express(express_id, session)

    await session.commit()


async def cancel_express_legs_for_event(
    event_id: int,
    session: AsyncSession,
) -> None:
    """
    Помечает ноги как cancelled при отмене события.
    Если в экспрессе всё остальное сыграло — пересчитывает без отменённой ноги.
    """
    legs_r = await session.execute(
        select(ExpressLeg).where(
            ExpressLeg.event_id == event_id,
            ExpressLeg.result == "pending",
        )
    )
    legs = list(legs_r.scalars().all())

    express_ids_to_check: set[int] = set()
    for leg in legs:
        leg.result = "cancelled"
        express_ids_to_check.add(leg.express_id)

    await session.flush()

    for express_id in express_ids_to_check:
        await _try_settle_express(express_id, session)

    await session.commit()


async def _try_settle_express(express_id: int, session: AsyncSession) -> None:
    """Проверяет все ноги и рассчитывает экспресс если все завершены."""
    express_r = await session.execute(
        select(Express).where(Express.id == express_id)
    )
    express = express_r.scalar_one_or_none()
    if not express or express.status != "active":
        return

    legs_r = await session.execute(
        select(ExpressLeg).where(ExpressLeg.express_id == express_id)
    )
    legs = list(legs_r.scalars().all())

    if any(leg.result == "pending" for leg in legs):
        return  # Ждём остальных ног

    # Все ноги завершены — считаем итог
    if any(leg.result == "lost" for leg in legs):
        express.status = "lost"
        express.settled_at = datetime.now(timezone.utc)
        logger.info("Express #%s LOST", express_id)
        return

    # Только won + cancelled → пересчитываем odds без cancelled
    active_legs = [leg for leg in legs if leg.result == "won"]
    if not active_legs:
        # Все отменены → возврат ставки
        final_odds = Decimal("1")
    else:
        final_odds = Decimal("1")
        for leg in active_legs:
            final_odds *= leg.odds

    payout = (express.stake * final_odds).quantize(Decimal("0.01"))

    user_r = await session.execute(select(User).where(User.id == express.user_id))
    user = user_r.scalar_one()

    balance_before = user.balance_rub
    user.balance_rub += payout

    session.add(Transaction(
        user_id=express.user_id,
        type=TransactionType.BET_PAYOUT,
        amount_rub=payout,
        balance_before=balance_before,
        balance_after=user.balance_rub,
        description=f"Выигрыш экспресса #{express_id} (×{final_odds})",
    ))

    express.status = "won"
    express.potential_payout = payout
    express.settled_at = datetime.now(timezone.utc)

    logger.info(
        "Express #%s WON: user=%s payout=%s odds=%s",
        express_id, express.user_id, payout, final_odds,
    )

    # Уведомляем пользователя
    try:
        from bot.notifier import notify_user
        await notify_user(
            user.telegram_id,
            f"🎉 <b>Экспресс выиграл!</b>\n\n"
            f"Коэффициент: ×{final_odds:.2f}\n"
            f"Выплата: <b>{payout:.2f} ₽</b>",
        )
    except Exception as exc:
        logger.warning("Express win notify failed: %s", exc)
