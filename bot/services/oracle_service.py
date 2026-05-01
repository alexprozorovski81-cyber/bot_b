"""
Сервис автоматического разрешения событий через внешние оракулы.

Поддерживается:
  - Binance (основной, без ключа) — цены BTC, ETH, TON
  - CoinGecko (резерв) — капитализация для flippening
  - Manual — для всех остальных, разрешает админ через /resolve

Запускается каждые 5 минут через cron-task в bot/main.py.
"""
import asyncio
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
ORACLE_RULES: dict[str, dict] = {
    "btc-150k-2026": {
        "type": "binance_threshold",
        "symbol": "BTCUSDT",
        "threshold_usd": 150000,
        "yes_outcome_slug": "Да",
        "no_outcome_slug": "Нет",
    },
    "ton-price-10-2026": {
        "type": "binance_threshold",
        "symbol": "TONUSDT",
        "threshold_usd": 10,
        "yes_outcome_slug": "Да, $10+",
        "no_outcome_slug": "Нет",
    },
    "eth-flippening-2026": {
        "type": "flippening",
        "yes_outcome_slug": "Да",
        "no_outcome_slug": "Нет",
    },
}

# Binance symbol → приблизительное кол-во монет в обращении (для капитализации)
# Обновляется редко — точность достаточна для flippening-проверки
_CIRCULATING_SUPPLY = {
    "BTCUSDT": Decimal("19_700_000"),
    "ETHUSDT": Decimal("120_200_000"),
}


async def _binance_price(symbol: str) -> Decimal | None:
    """Текущая цена по паре Binance (например BTCUSDT). Без API-ключа."""
    url = "https://api.binance.com/api/v3/ticker/price"
    params = {"symbol": symbol}
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            price = resp.json().get("price")
            return Decimal(str(price)) if price else None
        except Exception as e:
            logger.warning(f"Binance price error for {symbol}: {e}")
            return None


async def _coingecko_market_cap(coin_id: str) -> Decimal | None:
    """Капитализация монеты в USD (резервный источник — CoinGecko)."""
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
    Проверяет, достигла ли цена монеты порога через Binance.
    Возвращает "yes" / None (если нет или ошибка).
    """
    price = await _binance_price(rule["symbol"])
    if price is None:
        return None
    threshold = Decimal(str(rule["threshold_usd"]))
    if price >= threshold:
        return "yes"
    return None


async def _check_flippening_rule(rule: dict) -> str | None:
    """
    ETH flippening: сначала пробуем через Binance × supply,
    при ошибке — через CoinGecko.
    """
    btc_price = await _binance_price("BTCUSDT")
    eth_price = await _binance_price("ETHUSDT")

    if btc_price and eth_price:
        btc_cap = btc_price * _CIRCULATING_SUPPLY["BTCUSDT"]
        eth_cap = eth_price * _CIRCULATING_SUPPLY["ETHUSDT"]
        if eth_cap > btc_cap:
            return "yes"
        return None  # Условие не выполнено

    # Резерв: CoinGecko (может давать 429, поэтому запасной вариант)
    await asyncio.sleep(2)
    btc_cap = await _coingecko_market_cap("bitcoin")
    await asyncio.sleep(2)
    eth_cap = await _coingecko_market_cap("ethereum")
    if btc_cap is None or eth_cap is None:
        return None
    if eth_cap > btc_cap:
        return "yes"
    return None


async def resolve_crypto_price_event(session: AsyncSession, event: Event) -> bool:
    """
    Авто-разрешение события с auto_resolve_source="coingecko_price".
    Возвращает True при успехе, False при ошибке (событие переходит в LOCKED).
    """
    import json

    try:
        payload = json.loads(event.auto_resolve_payload or "{}")
    except Exception:
        logger.error(f"Invalid auto_resolve_payload for event #{event.id}")
        return False

    coin_id = payload.get("coin_id")
    threshold = payload.get("threshold_usd")
    direction = payload.get("direction", "above")

    if not coin_id or threshold is None:
        logger.error(f"Missing coin_id or threshold in payload for event #{event.id}")
        return False

    try:
        url = "https://api.coingecko.com/api/v3/simple/price"
        params = {"ids": coin_id, "vs_currencies": "usd"}
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
        if coin_id not in data:
            raise ValueError(f"CoinGecko returned no data for {coin_id}")
        current_price = Decimal(str(data[coin_id]["usd"]))
    except Exception as exc:
        logger.warning(
            f"CoinGecko error for event #{event.id} ({coin_id}): {exc} — setting LOCKED"
        )
        event.status = EventStatus.LOCKED
        await session.commit()
        return False

    threshold_dec = Decimal(str(threshold))
    yes_wins = (
        current_price >= threshold_dec
        if direction == "above"
        else current_price <= threshold_dec
    )

    outcomes_result = await session.execute(
        select(Outcome)
        .where(Outcome.event_id == event.id)
        .order_by(Outcome.sort_order)
    )
    outcomes = list(outcomes_result.scalars().all())
    if len(outcomes) < 2:
        logger.error(f"Event #{event.id} has fewer than 2 outcomes")
        return False

    winner = outcomes[0] if yes_wins else outcomes[1]
    try:
        summary = await resolve_event(session, event.id, winner.id)
        result_word = "ДА" if yes_wins else "НЕТ"
        logger.info(
            f"Auto-resolved event #{event.id}: {coin_id} "
            f"{float(current_price):.2f} vs {float(threshold_dec):.2f} "
            f"→ {result_word} ({winner.title})"
        )
        await notify_admins(
            f"🤖 Крипто-оракул разрешил #{event.id}\n"
            f"«{event.title[:60]}»\n"
            f"Цена: ${float(current_price):,.2f} / Порог: ${float(threshold_dec):,.2f}\n"
            f"Победил: {winner.title}\n"
            f"Выплачено: {summary['total_payout']:.2f} ₽"
        )
        return True
    except Exception as exc:
        logger.exception(f"resolve_event failed for crypto event #{event.id}: {exc}")
        return False


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

        # 2. Проверяем статичные ORACLE_RULES (долгосрочные события)
        for slug, rule in ORACLE_RULES.items():
            ev_result = await session.execute(
                select(Event).where(Event.slug == slug)
            )
            event = ev_result.scalar_one_or_none()
            if not event or event.status == EventStatus.RESOLVED:
                continue

            now = datetime.now(timezone.utc)

            result_type = None
            if rule["type"] == "binance_threshold":
                result_type = await _check_threshold_rule(rule)
            elif rule["type"] == "flippening":
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

        # 3. Авто-разрешение intraday crypto-событий через CoinGecko
        now = datetime.now(timezone.utc)
        crypto_result = await session.execute(
            select(Event).where(
                Event.status == EventStatus.ACTIVE,
                Event.auto_resolve_source == "coingecko_price",
                Event.closes_at <= now,
            )
        )
        for event in crypto_result.scalars().all():
            try:
                ok = await resolve_crypto_price_event(session, event)
                if ok:
                    resolved_count += 1
            except Exception as exc:
                logger.exception(
                    f"Crypto oracle error for event #{event.id}: {exc}"
                )

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
