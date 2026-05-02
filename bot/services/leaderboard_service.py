"""
Сервис лидерборда: пересчёт UserStats по периодам.
Вызывается из cron каждые 10 минут.
"""
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Bet, UserStats

logger = logging.getLogger(__name__)


async def refresh_user_stats(session: AsyncSession) -> int:
    """
    Пересчитывает UserStats для всех активных пользователей
    по периодам: week (7д), month (30д), all (всё время).

    net_profit = sum(payout_rub - amount_rub) по settled ставкам.
    win_count  = кол-во settled ставок с payout_rub > 0.

    Возвращает количество обновлённых/созданных строк.
    """
    now = datetime.now(timezone.utc)
    periods: dict[str, datetime | None] = {
        "week": now - timedelta(days=7),
        "month": now - timedelta(days=30),
        "all": None,
    }

    upserted = 0

    for period_key, cutoff in periods.items():
        bet_query = select(
            Bet.user_id,
            func.count(Bet.id).label("bets_count"),
            func.sum(
                func.coalesce(Bet.payout_rub, Decimal("0")) - Bet.amount_rub
            ).label("net_profit"),
            func.sum(
                case((Bet.payout_rub > 0, 1), else_=0)
            ).label("win_count"),
        ).where(
            Bet.is_settled == True  # noqa: E712
        )

        if cutoff is not None:
            bet_query = bet_query.where(Bet.created_at >= cutoff)

        bet_query = bet_query.group_by(Bet.user_id)

        result = await session.execute(bet_query)
        rows = result.all()

        for row in rows:
            user_id = row.user_id
            net_profit = Decimal(str(row.net_profit or 0))
            bets_count = int(row.bets_count or 0)
            win_count = int(row.win_count or 0)

            existing = await session.execute(
                select(UserStats).where(
                    UserStats.user_id == user_id,
                    UserStats.period == period_key,
                )
            )
            stats = existing.scalar_one_or_none()

            if stats:
                stats.net_profit = net_profit
                stats.bets_count = bets_count
                stats.win_count = win_count
                stats.updated_at = now
            else:
                session.add(
                    UserStats(
                        user_id=user_id,
                        period=period_key,
                        net_profit=net_profit,
                        bets_count=bets_count,
                        win_count=win_count,
                        updated_at=now,
                    )
                )
            upserted += 1

    await session.commit()
    logger.debug("Leaderboard refresh: %d rows upserted", upserted)
    return upserted
