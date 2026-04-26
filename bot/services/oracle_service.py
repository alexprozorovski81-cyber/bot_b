"""
Сервис автоматического разрешения событий через внешние оракулы.

Поддерживается:
  - CoinGecko (для крипто-цен) — для событий вида "Достигнет ли BTC $X"
  - Manual — для всех остальных, разрешает админ через /resolve

Запускается раз в час через cron-task в bot/main.py.
"""
import logging
from datetime import datetime, timezone
from decimal import Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.notifier import notify_admins
from bot.services.resolution_service import resolve_event
from db.database import AsyncSessionLocal
from db.models import Event, EventStatus, Outcome


logger = logging.getLogger(__name__)


# Маппинг slug события → правило для оракула
# Это упрощённая версия — в проде это хранится в БД с extensible-схемой
ORACLE_RULES: dict[str, dict] = {
    "btc-100k-2026": {
        "type": "coingecko_threshold",
        "coin": "bitcoin",
        "threshold_usd": 150000,
        "yes_outcome_slug": "Да",
        "no_outcome_slug": "Нет",
    },
    "eth-flippening": {
        "type": "coingecko_flippening",
        "yes_outcome_slug": "Да",
        "no_outcome_slug": "Нет",
    },
}


async def _coingecko_price(coin_id: str) -> Decimal | None:
    """Текущая цена монеты в USD."""
    url = "https://api.coingecko.com/api/v3/simple/price"
    params = {"ids": coin_id, "vs_currencies": "usd"}

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            price = data.get(coin_id, {}).get("usd")
            return Decimal(str(price)) if price else None
        except Exception as e:
            logger.warning(f"CoinGecko error for {coin_id}: {e}")
            return None


async def _coingecko_market_cap(coin_id: str) -> Decimal | None:
    """Капитализация монеты в USD."""
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {"vs_currency": "usd", "ids": coin_id}

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            if data:
                cap = data[0].get("market_cap")
                return Decimal(str(cap)) if cap else None
        except Exception as e:
            logger.warning(f"CoinGecko marketcap error for {coin_id}: {e}")
    return None


async def _check_threshold_rule(rule: dict) -> str | None:
    """
    Проверяет, достигла ли цена монеты порога.
    Возвращает "yes" / "no" / None (если не определилось).
    """
    price = await _coingecko_price(rule["coin"])
    if price is None:
        return None

    threshold = Decimal(str(rule["threshold_usd"]))
    if price >= threshold:
        return "yes"
    return None  # Пока цена ниже — событие ещё не разрешено


async def _check_flippening_rule(rule: dict) -> str | None:
    """
    Проверяет, обогнал ли ETH капитализацию BTC.
    """
    btc_cap = await _coingecko_market_cap("bitcoin")
    eth_cap = await _coingecko_market_cap("ethereum")
    if btc_cap is None or eth_cap is None:
        return None
    if eth_cap > btc_cap:
        return "yes"
    return None


async def check_oracles() -> int:
    """
    Проходит по всем правилам оракулов, проверяет условия,
    разрешает события если условия выполнены.

    Returns:
        Количество разрешённых событий за этот проход.
    """
    resolved_count = 0

    async with AsyncSessionLocal() as session:
        # 1. Сначала закрываем приём ставок для просроченных событий
        await _lock_expired_events(session)

        # 2. Проверяем оракулы
        for slug, rule in ORACLE_RULES.items():
            ev_result = await session.execute(
                select(Event).where(Event.slug == slug)
            )
            event = ev_result.scalar_one_or_none()
            if not event or event.status == EventStatus.RESOLVED:
                continue

            # Если событие просрочено — закрываем как NO (если YES не выполнен)
            now = datetime.now(timezone.utc)

            result_type = None
            if rule["type"] == "coingecko_threshold":
                result_type = await _check_threshold_rule(rule)
            elif rule["type"] == "coingecko_flippening":
                result_type = await _check_flippening_rule(rule)

            # Если резолв-дата прошла, а условие не выполнилось — это NO
            resolves_at = event.resolves_at
            if resolves_at is not None and resolves_at.tzinfo is None:
                resolves_at = resolves_at.replace(tzinfo=timezone.utc)
            if not result_type and resolves_at is not None and resolves_at < now:
                result_type = "no"

            if result_type:
                outcomes_result = await session.execute(
                    select(Outcome).where(Outcome.event_id == event.id)
                    .order_by(Outcome.sort_order)
                )
                outcomes = list(outcomes_result.scalars().all())

                target_slug = (
                    rule["yes_outcome_slug"] if result_type == "yes"
                    else rule["no_outcome_slug"]
                )
                winner = next((o for o in outcomes if o.title == target_slug), None)
                if winner:
                    try:
                        summary = await resolve_event(session, event.id, winner.id)
                        resolved_count += 1
                        await notify_admins(
                            f"🤖 Оракул разрешил событие #{event.id}\n"
                            f"«{event.title[:60]}»\n"
                            f"Победил: {winner.title}\n"
                            f"Выплачено: {summary['total_payout']:.2f} ₽"
                        )
                    except Exception as e:
                        logger.exception(f"Resolve failed for {event.id}: {e}")

    return resolved_count


async def _lock_expired_events(session: AsyncSession) -> None:
    """Перевод просроченных событий из ACTIVE в LOCKED."""
    now = datetime.now(timezone.utc)
    result = await session.execute(
        select(Event).where(
            Event.status == EventStatus.ACTIVE,
            Event.closes_at < now,
        )
    )
    for event in result.scalars().all():
        event.status = EventStatus.LOCKED
        logger.info(f"Event {event.id} locked (closed at {event.closes_at})")
    await session.commit()
