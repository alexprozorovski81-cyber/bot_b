"""
Сервис актуального курса USDT/RUB.

Источник: CoinGecko (бесплатный API, без ключа).
Кэш: 15 минут — чтобы не превысить rate limit.
Fallback: settings.usdt_to_rub_rate из .env.
"""
import logging
import time
from decimal import Decimal

import httpx

from bot.config import settings

logger = logging.getLogger(__name__)

_cached_rate: Decimal | None = None
_cache_ts: float = 0.0
_CACHE_TTL = 900  # 15 минут


async def get_usdt_rub_rate() -> Decimal:
    """Возвращает актуальный курс 1 USDT → RUB с кэшированием 15 мин."""
    global _cached_rate, _cache_ts

    now = time.monotonic()
    if _cached_rate is not None and (now - _cache_ts) < _CACHE_TTL:
        return _cached_rate

    rate = await _fetch_coingecko()
    if rate:
        _cached_rate = rate
        _cache_ts = now
        logger.info("USDT/RUB rate updated: %s", rate)
        return rate

    # Fallback к значению из конфига
    if _cached_rate is not None:
        logger.warning("CoinGecko unavailable, using stale cached rate: %s", _cached_rate)
        return _cached_rate

    logger.warning("CoinGecko unavailable, using config fallback rate: %s", settings.usdt_to_rub_rate)
    return settings.usdt_to_rub_rate


async def _fetch_coingecko() -> Decimal | None:
    """Запрашивает курс USDT/RUB с CoinGecko."""
    url = "https://api.coingecko.com/api/v3/simple/price"
    params = {"ids": "tether", "vs_currencies": "rub"}
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            rub = data["tether"]["rub"]
            return Decimal(str(rub)).quantize(Decimal("0.01"))
    except Exception as e:
        logger.warning("CoinGecko rate fetch error: %s", e)
        return None
