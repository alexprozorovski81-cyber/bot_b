"""
Сервис разрешения событий и выплат.

Когда событие разрешается:
  1. Все ставки на победивший исход → выплата (1 ₽ за акцию минус комиссия)
  2. Все ставки на проигравшие исходы → 0 ₽ (деньги уже списаны)
  3. Каждому игроку идёт уведомление в Telegram
"""
import logging
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.config import settings
from bot.notifier import notify_user
from bot.services import market_engine
from db.models import (
    Bet, Event, EventStatus, Outcome, Transaction,
    TransactionType, User,
)


logger = logging.getLogger(__name__)


async def resolve_event(
    session: AsyncSession,
    event_id: int,
    winning_outcome_id: int,
) -> dict:
    """
    Разрешает событие и проводит выплаты.

    Args:
        session: активная сессия БД
        event_id: ID события
        winning_outcome_id: ID победившего исхода

    Returns:
        Сводка: {winners_count, losers_count, total_payout, fees_collected}
    """
    # 1. Загружаем событие
    event_result = await session.execute(
        select(Event).where(Event.id == event_id)
    )
    event = event_result.scalar_one_or_none()
    if not event:
        raise ValueError(f"Event {event_id} not found")

    if event.status == EventStatus.RESOLVED:
        raise ValueError("Событие уже разрешено")

    # 2. Проверяем что winning_outcome принадлежит этому событию
    outcome_result = await session.execute(
        select(Outcome).where(
            Outcome.id == winning_outcome_id,
            Outcome.event_id == event_id,
        )
    )
    winning_outcome = outcome_result.scalar_one_or_none()
    if not winning_outcome:
        raise ValueError("Этот исход не принадлежит указанному событию")

    # 3. Загружаем все ставки события
    bets_result = await session.execute(
        select(Bet).where(
            Bet.event_id == event_id,
            Bet.is_settled == False,  # noqa: E712
        )
    )
    bets = list(bets_result.scalars().all())

    fee_pct = settings.platform_fee_percent
    winners = []
    losers = []
    total_payout = Decimal("0")
    total_fees = Decimal("0")

    for bet in bets:
        is_winner = bet.outcome_id == winning_outcome_id

        # Достаём пользователя
        user_result = await session.execute(
            select(User).where(User.id == bet.user_id)
        )
        user = user_result.scalar_one()

        if is_winner:
            payout, fee = market_engine.calculate_payout(
                bet.shares, True, fee_pct
            )

            # Зачисляем выигрыш
            balance_before = user.balance_rub
            user.balance_rub += payout
            balance_after = user.balance_rub

            bet.is_settled = True
            bet.payout_rub = payout

            # Транзакции
            fee_note = f" (комиссия {fee:.2f})" if fee > 0 else ""
            session.add(Transaction(
                user_id=user.id,
                type=TransactionType.BET_PAYOUT,
                amount_rub=payout,
                balance_before=balance_before,
                balance_after=balance_after,
                bet_id=bet.id,
                description=f"Выигрыш по «{event.title[:60]}»{fee_note}",
            ))

            total_payout += payout
            total_fees += fee
            winners.append((user, bet, payout, fee))
        else:
            # Проигрыш — деньги уже были списаны при ставке
            bet.is_settled = True
            bet.payout_rub = Decimal("0")
            losers.append((user, bet))

    # 4. Обновляем событие
    event.status = EventStatus.RESOLVED
    event.winning_outcome_id = winning_outcome_id

    await session.commit()

    # 5. Уведомления (после commit — чтобы данные в БД точно были)
    for user, bet, payout, fee in winners:
        await notify_user(
            user.telegram_id,
            texts.BET_WON.format(
                event_title=event.title,
                outcome=winning_outcome.title,
                payout=f"{payout:.2f}",
                fee=f"{fee:.2f}",
                balance=f"{user.balance_rub:.2f}",
            ),
            sticker_key="win",
        )

    for user, bet in losers:
        # Достаём название проигравшего исхода
        out_result = await session.execute(
            select(Outcome).where(Outcome.id == bet.outcome_id)
        )
        out = out_result.scalar_one()
        await notify_user(
            user.telegram_id,
            texts.BET_LOST.format(
                event_title=event.title,
                winning_outcome=winning_outcome.title,
            ),
            sticker_key="lose",
        )

    logger.info(
        f"Event {event_id} resolved: {len(winners)} winners, "
        f"{len(losers)} losers, payout={total_payout}, fees={total_fees}"
    )

    return {
        "winners_count": len(winners),
        "losers_count": len(losers),
        "total_payout": total_payout,
        "fees_collected": total_fees,
        "winning_outcome": winning_outcome.title,
    }


async def cancel_event(
    session: AsyncSession,
    event_id: int,
    reason: str = "Отменено администратором",
) -> dict:
    """
    Отменяет событие и возвращает все ставки пользователям.
    """
    event_result = await session.execute(
        select(Event).where(Event.id == event_id)
    )
    event = event_result.scalar_one_or_none()
    if not event:
        raise ValueError(f"Event {event_id} not found")

    if event.status in (EventStatus.RESOLVED, EventStatus.CANCELLED):
        raise ValueError(f"Событие уже в финальном статусе: {event.status}")

    bets_result = await session.execute(
        select(Bet).where(
            Bet.event_id == event_id,
            Bet.is_settled == False,  # noqa: E712
        )
    )
    bets = list(bets_result.scalars().all())

    refunded = []
    total_refund = Decimal("0")

    for bet in bets:
        user_result = await session.execute(
            select(User).where(User.id == bet.user_id)
        )
        user = user_result.scalar_one()

        balance_before = user.balance_rub
        user.balance_rub += bet.amount_rub
        balance_after = user.balance_rub

        bet.is_settled = True
        bet.payout_rub = bet.amount_rub  # вернули полную сумму

        session.add(Transaction(
            user_id=user.id,
            type=TransactionType.BET_REFUND,
            amount_rub=bet.amount_rub,
            balance_before=balance_before,
            balance_after=balance_after,
            bet_id=bet.id,
            description=f"Возврат: {reason}",
        ))

        total_refund += bet.amount_rub
        refunded.append((user, bet))

    event.status = EventStatus.CANCELLED
    await session.commit()

    for user, bet in refunded:
        await notify_user(
            user.telegram_id,
            f"<b>↩️ Событие отменено</b>\n\n"
            f"«{event.title}»\n\n"
            f"Причина: {reason}\n"
            f"Возвращено: <b>{bet.amount_rub:.2f} ₽</b>",
        )

    return {
        "refunded_count": len(refunded),
        "total_refund": total_refund,
    }
