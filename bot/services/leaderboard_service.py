"""
Сервис лидерборда: пересчёт UserStats по периодам.
Вызывается из cron каждые 10 минут.
"""
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import is_sqlite
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
    if is_sqlite():
        from sqlalchemy.dialects.sqlite import insert as dialect_insert
    else:
        from sqlalchemy.dialects.postgresql import insert as dialect_insert

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

        if not rows:
            continue

        values = [
            {
                "user_id": row.user_id,
                "period": period_key,
                "net_profit": Decimal(str(row.net_profit or 0)),
                "bets_count": int(row.bets_count or 0),
                "win_count": int(row.win_count or 0),
                "updated_at": now,
            }
            for row in rows
        ]

        stmt = dialect_insert(UserStats).values(values).on_conflict_do_update(
            index_elements=["user_id", "period"],
            set_={
                "net_profit": dialect_insert(UserStats).excluded.net_profit,
                "bets_count": dialect_insert(UserStats).excluded.bets_count,
                "win_count": dialect_insert(UserStats).excluded.win_count,
                "updated_at": dialect_insert(UserStats).excluded.updated_at,
            },
        )
        await session.execute(stmt)
        upserted += len(values)

    await session.commit()
    logger.debug("Leaderboard refresh: %d rows upserted", upserted)
    return upserted
