"""
Сервис разрешения событий и выплат.

Поток для ВСЕХ событий:
  1. resolve_event()  → EventStatus.PENDING_VERIFY (исход сохранён, выплат нет)
  2. Admin подтверждает → confirm_resolution() → RESOLVED + выплаты
  3. Admin отклоняет  → reject_resolution()  → LOCKED (пересмотр)

Логика выплат:
  MARKET (LMSR)    — 1 ₽ за акцию минус комиссия
  FIXED_ODDS       — ставка × зафиксированный коэффициент
"""
import logging
from decimal import Decimal

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.config import settings
from bot.notifier import notify_user
from bot.services import market_engine
from db.models import (
    Bet, Event, EventStatus, EventType, Outcome, Transaction,
    TransactionType, User,
)

logger = logging.getLogger(__name__)


async def resolve_event(
    session: AsyncSession,
    event_id: int,
    winning_outcome_id: int,
) -> dict:
    """
    Переводит событие в PENDING_VERIFY и уведомляет admin'а.
    Фактические выплаты происходят только после confirm_resolution().
    """
    event_result = await session.execute(select(Event).where(Event.id == event_id))
    event = event_result.scalar_one_or_none()
    if not event:
        raise ValueError(f"Event {event_id} not found")

    if event.status == EventStatus.RESOLVED:
        raise ValueError("Событие уже разрешено")
    if event.status == EventStatus.PENDING_VERIFY:
        raise ValueError("Событие уже ожидает подтверждения")

    outcome_result = await session.execute(
        select(Outcome).where(
            Outcome.id == winning_outcome_id,
            Outcome.event_id == event_id,
        )
    )
    winning_outcome = outcome_result.scalar_one_or_none()
    if not winning_outcome:
        raise ValueError("Этот исход не принадлежит указанному событию")

    event.status = EventStatus.PENDING_VERIFY
    event.winning_outcome_id = winning_outcome_id
    await session.commit()

    await _notify_admins_pending_verify(event, winning_outcome)

    logger.info("Event %s → PENDING_VERIFY, outcome=%s", event_id, winning_outcome.title)
    return {"status": "pending_verify", "winning_outcome": winning_outcome.title}


async def confirm_resolution(session: AsyncSession, event_id: int) -> dict:
    """
    Выполняет фактические выплаты и переводит событие в RESOLVED.
    Вызывается из callback admin'а.
    """
    event_result = await session.execute(select(Event).where(Event.id == event_id))
    event = event_result.scalar_one_or_none()
    if not event:
        raise ValueError(f"Event {event_id} not found")
    if event.status != EventStatus.PENDING_VERIFY:
        raise ValueError(f"Событие не в статусе PENDING_VERIFY: {event.status}")

    winning_outcome_id = event.winning_outcome_id
    outcome_result = await session.execute(
        select(Outcome).where(Outcome.id == winning_outcome_id)
    )
    winning_outcome = outcome_result.scalar_one()

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

        user_result = await session.execute(select(User).where(User.id == bet.user_id))
        user = user_result.scalar_one()

        if is_winner:
            payout, fee = _calc_payout(event, bet, fee_pct)

            balance_before = user.balance_rub
            user.balance_rub += payout
            balance_after = user.balance_rub

            bet.is_settled = True
            bet.payout_rub = payout

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
            bet.is_settled = True
            bet.payout_rub = Decimal("0")
            losers.append((user, bet))

    event.status = EventStatus.RESOLVED
    await session.commit()

    # Уведомления после commit
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
        await notify_user(
            user.telegram_id,
            texts.BET_LOST.format(
                event_title=event.title,
                winning_outcome=winning_outcome.title,
            ),
            sticker_key="lose",
        )

    # Settle express legs for this event
    try:
        from bot.services.express_service import settle_express_legs
        from db.database import AsyncSessionLocal
        async with AsyncSessionLocal() as s:
            await settle_express_legs(event_id, winning_outcome_id, s)
    except Exception as exc:
        logger.warning("Express settlement error for event %s: %s", event_id, exc)

    logger.info(
        "Event %s RESOLVED: %d winners, %d losers, payout=%s, fees=%s",
        event_id, len(winners), len(losers), total_payout, total_fees,
    )
    return {
        "winners_count": len(winners),
        "losers_count": len(losers),
        "total_payout": total_payout,
        "fees_collected": total_fees,
        "winning_outcome": winning_outcome.title,
    }


async def reject_resolution(session: AsyncSession, event_id: int) -> None:
    """
    Отклоняет предложенный исход, возвращает событие в LOCKED.
    Admin пересматривает вручную.
    """
    event_result = await session.execute(select(Event).where(Event.id == event_id))
    event = event_result.scalar_one_or_none()
    if not event:
        raise ValueError(f"Event {event_id} not found")
    if event.status != EventStatus.PENDING_VERIFY:
        raise ValueError(f"Событие не в статусе PENDING_VERIFY: {event.status}")

    event.status = EventStatus.LOCKED
    event.winning_outcome_id = None
    await session.commit()
    logger.info("Event %s resolution rejected → back to LOCKED", event_id)


async def cancel_event(
    session: AsyncSession,
    event_id: int,
    reason: str = "Отменено администратором",
) -> dict:
    """Отменяет событие и возвращает все ставки пользователям."""
    event_result = await session.execute(select(Event).where(Event.id == event_id))
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
        user_result = await session.execute(select(User).where(User.id == bet.user_id))
        user = user_result.scalar_one()

        balance_before = user.balance_rub
        user.balance_rub += bet.amount_rub
        balance_after = user.balance_rub

        bet.is_settled = True
        bet.payout_rub = bet.amount_rub

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

    return {"refunded_count": len(refunded), "total_refund": total_refund}


# ── Internal helpers ──────────────────────────────────────────────────────────

def _calc_payout(event: Event, bet: Bet, fee_pct: Decimal) -> tuple[Decimal, Decimal]:
    """Рассчитывает выплату в зависимости от типа события."""
    if event.event_type == EventType.FIXED_ODDS:
        gross = (bet.amount_rub * bet.avg_odds).quantize(Decimal("0.01"))
        fee = (gross * fee_pct / Decimal("100")).quantize(Decimal("0.01"))
        return (gross - fee).quantize(Decimal("0.01")), fee
    else:
        return market_engine.calculate_payout(bet.shares, True, fee_pct)


async def _notify_admins_pending_verify(event: Event, winning_outcome: Outcome) -> None:
    try:
        from bot import notifier
        bot = notifier._bot
        if not bot or not settings.admin_id_list:
            return

        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="✅ Подтвердить выплаты",
                callback_data=f"resolve:confirm:{event.id}",
            ),
            InlineKeyboardButton(
                text="❌ Отменить итог",
                callback_data=f"resolve:reject:{event.id}",
            ),
        ]])
        event_type_label = "📊 LMSR" if event.event_type == EventType.MARKET else "📌 Фикс. коэф."
        text = (
            f"⏳ <b>Ожидает подтверждения</b> [{event_type_label}]\n\n"
            f"«{event.title}»\n\n"
            f"Победивший исход: <b>{winning_outcome.title}</b>\n\n"
            f"Подтверди выплаты пользователям:"
        )
        for admin_id in settings.admin_id_list:
            try:
                await bot.send_message(admin_id, text, parse_mode="HTML", reply_markup=kb)
            except Exception as e:
                logger.warning("notify admin %s failed: %s", admin_id, e)
    except Exception as e:
        logger.warning("_notify_admins_pending_verify failed: %s", e)
