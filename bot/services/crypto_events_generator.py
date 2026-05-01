"""
Генератор краткосрочных intraday-событий из CoinGecko.
Создаёт прогнозы на 1/3/6 часов для BTC, ETH, TON.
Вызывается из cron каждые 30 минут.
"""
import json
import logging
import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from db.models import Category, Event, EventStatus, Outcome

logger = logging.getLogger(__name__)

COIN_META: dict[str, dict] = {
    "bitcoin": {
        "symbol": "BTC",
        "nearest": 100,   # порог округляется до $100
    },
    "ethereum": {
        "symbol": "ETH",
        "nearest": 10,    # до $10
    },
    "the-open-network": {
        "symbol": "TON",
        "nearest": 0.01,  # до $0.01
    },
}


def _round_to_nearest(price: float, nearest: float) -> float:
    """Округляет цену до заданного шага (например nearest=100 → $67,200)."""
    if nearest <= 0:
        return price
    return round(round(price / nearest) * nearest, 10)


def _format_price(price: float, nearest: float) -> str:
    if nearest >= 1:
        return f"${price:,.0f}"
    decimals = max(0, -int(math.floor(math.log10(nearest))))
    return f"${price:,.{decimals}f}"


async def _fetch_prices(coin_ids: list[str]) -> dict[str, float]:
    """Fetch текущих цен в USD через CoinGecko simple/price."""
    url = "https://api.coingecko.com/api/v3/simple/price"
    params = {"ids": ",".join(coin_ids), "vs_currencies": "usd"}
    async with httpx.AsyncClient(timeout=12.0) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
    return {
        coin_id: data[coin_id]["usd"]
        for coin_id in coin_ids
        if coin_id in data and "usd" in data[coin_id]
    }


async def _fetch_coin_image(coin_id: str) -> str | None:
    """Fetch лого монеты с CoinGecko /coins/{id}."""
    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}"
    params = {
        "localization": "false",
        "tickers": "false",
        "market_data": "false",
        "community_data": "false",
        "developer_data": "false",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            return data.get("image", {}).get("large")
    except Exception as exc:
        logger.warning(f"CoinGecko image fetch failed for {coin_id}: {exc}")
        return None


async def _has_active_event_for_coin(session: AsyncSession, coin_id: str) -> bool:
    """Проверяет, есть ли уже ACTIVE событие для этой монеты."""
    result = await session.execute(
        select(Event).where(
            Event.status == EventStatus.ACTIVE,
            Event.auto_resolve_source == "coingecko_price",
        )
    )
    for event in result.scalars().all():
        if not event.auto_resolve_payload:
            continue
        try:
            payload = json.loads(event.auto_resolve_payload)
            if payload.get("coin_id") == coin_id:
                return True
        except Exception:
            continue
    return False


async def generate_short_term_events(session: AsyncSession) -> int:
    """
    Создаёт intraday-события для настроенных монет и горизонтов.
    Возвращает количество созданных событий.
    """
    if not settings.short_term_enabled:
        return 0

    coins = settings.short_term_coins_list
    horizons = settings.short_term_horizons_list

    if not coins or not horizons:
        return 0

    # Получаем цены для всех монет одним запросом
    try:
        prices = await _fetch_prices(coins)
    except Exception as exc:
        logger.error(f"CoinGecko price fetch failed: {exc}")
        return 0

    # Категория crypto
    cat_result = await session.execute(
        select(Category).where(Category.slug == "crypto")
    )
    cat = cat_result.scalar_one_or_none()
    if not cat:
        logger.warning("Category 'crypto' not found — skipping short-term events")
        return 0

    created = 0
    now = datetime.now(timezone.utc)

    for coin_id in coins:
        current_price = prices.get(coin_id)
        if current_price is None:
            logger.warning(f"No price for {coin_id}")
            continue

        if await _has_active_event_for_coin(session, coin_id):
            logger.info(f"Active market already exists for {coin_id}, skipping")
            continue

        meta = COIN_META.get(coin_id, {"symbol": coin_id.upper(), "nearest": 1})
        symbol = meta["symbol"]
        nearest = meta["nearest"]

        # Лого монеты (CoinGecko гарантирует наличие — сразу ACTIVE)
        image_url = await _fetch_coin_image(coin_id)

        for horizon_h in horizons:
            closes_at = now + timedelta(hours=horizon_h)
            resolves_at = closes_at + timedelta(minutes=30)

            threshold = _round_to_nearest(current_price * 1.005, nearest)
            price_fmt = _format_price(threshold, nearest)

            slug = (
                f"intra-{symbol.lower()}-"
                f"{int(now.timestamp())}-{horizon_h}h"
            )

            # Дедупликация по slug
            dup = await session.execute(select(Event).where(Event.slug == slug))
            if dup.scalar_one_or_none():
                continue

            title = f"Будет ли {symbol} выше {price_fmt} через {horizon_h} ч?"
            description = (
                f"Рынок закрывается в {closes_at.strftime('%H:%M UTC')}. "
                f"Текущая цена {symbol}: {_format_price(current_price, nearest)}. "
                f"Порог: {price_fmt}. Источник: CoinGecko."
            )

            payload = json.dumps(
                {
                    "coin_id": coin_id,
                    "symbol": symbol,
                    "threshold_usd": threshold,
                    "direction": "above",
                    "captured_price": current_price,
                },
                ensure_ascii=False,
            )

            event = Event(
                slug=slug,
                title=title,
                description=description,
                image_url=image_url,
                category_id=cat.id,
                status=EventStatus.ACTIVE,
                timeframe="intraday",
                liquidity_b=Decimal("500.00"),
                closes_at=closes_at,
                resolves_at=resolves_at,
                auto_resolve_source="coingecko_price",
                auto_resolve_payload=payload,
            )
            session.add(event)
            await session.flush()

            session.add(Outcome(event_id=event.id, title="Да (выше)", sort_order=0))
            session.add(Outcome(event_id=event.id, title="Нет (ниже)", sort_order=1))

            await session.commit()
            created += 1
            logger.info(f"Short-term event created: {title}")

    return created
