"""
Получение изображений-превью из Wikipedia для событий.

Используется:
  - при первичном seed (новые установки)
  - при /updateimages (обновление существующих событий в БД)

Wikipedia REST API: GET /api/rest_v1/page/summary/{article}
  → JSON с полем thumbnail.source (PNG/JPEG, стабильный URL Wikimedia CDN)
"""
import asyncio
import logging
import re

import httpx

logger = logging.getLogger(__name__)

# ── slug события → статья английской Википедии ───────────────────────────────
SLUG_WIKI_MAP: dict[str, str] = {
    # политика
    "ru-ceasefire-2026":        "Russo-Ukrainian_War",
    "ru-trump-meeting-2026":    "Donald_Trump",
    "ru-sanctions-2026":        "European_Union",
    "ru-duma-law-2026":         "State_Duma",
    "ru-gubern-2026":           "Moscow",
    # спорт
    "rpl-champion-2526":        "Russian_Premier_League",
    "khl-champion-2526":        "Kontinental_Hockey_League",
    "worldcup-2026-winner":     "2026_FIFA_World_Cup",
    "spartak-top3-2526":        "FC_Spartak_Moscow",
    "ru-tennis-wimbledon-2026": "The_Championships,_Wimbledon",
    # экономика
    "cbr-rate-below-15-2026":   "Central_Bank_of_Russia",
    "usd-rub-100-2026":         "Russian_ruble",
    "ru-inflation-2026":        "Inflation",
    "ru-gdp-growth-2026":       "Economy_of_Russia",
    # крипта
    "btc-150k-2026":            "Bitcoin",
    "ton-price-10-2026":        "Toncoin",
    "eth-flippening-2026":      "Ethereum",
    # технологии
    "ru-gosuslugi-ai-2026":     "Artificial_intelligence",
    "yandex-gpt-new-2026":      "Yandex",
    # мир
    "us-recession-2026":        "Recession",
}

_HEADERS = {
    "User-Agent": "PredictBet/1.0 (Telegram prediction market bot) Python/httpx",
    "Accept": "application/json",
}

# Желаемая ширина превью (px) — достаточно для banner 800px + thumbnail
_THUMB_WIDTH = 800


async def fetch_wiki_image(article: str, width: int = _THUMB_WIDTH) -> str | None:
    """
    Возвращает URL изображения из Wikipedia REST API.

    Приоритет:
      1. thumbnail.source   — отрендеренный PNG нужного размера
      2. originalimage.source — оригинал (SVG или JPEG)

    Возвращает None при 404, сетевой ошибке и т.п.
    """
    url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{article}"
    try:
        async with httpx.AsyncClient(
            timeout=12,
            headers=_HEADERS,
            follow_redirects=True,
        ) as client:
            resp = await client.get(url)
            if resp.status_code == 404:
                logger.warning(f"Wikipedia: статья не найдена — {article}")
                return None
            resp.raise_for_status()
            data = resp.json()

            thumb = data.get("thumbnail")
            if thumb and thumb.get("source"):
                src: str = thumb["source"]
                # Масштабируем thumbnail до нужного размера
                src = re.sub(r"/\d+px-", f"/{width}px-", src)
                return src

            orig = data.get("originalimage")
            if orig and orig.get("source"):
                return orig["source"]

    except Exception as e:
        logger.warning(f"Wikipedia image error [{article}]: {e}")
    return None


async def fetch_wiki_image_by_query(query: str) -> str | None:
    """
    Поиск картинки в Wikipedia по произвольному запросу (заголовок события).

    Алгоритм:
      1. opensearch на ru.wikipedia.org → берём title первой найденной статьи
      2. fetch_wiki_image(title) → URL thumbnail
      3. Если не нашлось в ru — повторяем на en.wikipedia.org

    Возвращает None если ничего не найдено или сетевая ошибка.
    """
    cleaned = (query or "").strip()
    if len(cleaned) < 3:
        return None

    for lang in ("ru", "en"):
        try:
            async with httpx.AsyncClient(timeout=10, headers=_HEADERS, follow_redirects=True) as client:
                resp = await client.get(
                    f"https://{lang}.wikipedia.org/w/api.php",
                    params={
                        "action": "opensearch",
                        "search": cleaned,
                        "limit": 1,
                        "namespace": 0,
                        "format": "json",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                # opensearch возвращает [query, [titles], [descriptions], [urls]]
                titles = data[1] if len(data) > 1 else []
                if not titles:
                    continue

                article_title = titles[0].replace(" ", "_")
                # fetch_wiki_image работает с en.wikipedia.org захардкоженно — для ru
                # используем тот же endpoint, но с lang
                img = await _fetch_wiki_image_lang(article_title, lang)
                if img:
                    logger.info(f"Wiki search OK [{lang}]: '{cleaned}' → {article_title}")
                    return img
        except Exception as e:
            logger.warning(f"Wiki search error [{lang}] for '{cleaned}': {e}")

    logger.info(f"Wiki search FAIL: '{cleaned}' — нет картинки ни в ru, ни в en")
    return None


async def _fetch_wiki_image_lang(article: str, lang: str = "en", width: int = _THUMB_WIDTH) -> str | None:
    """Как fetch_wiki_image, но с выбираемым языком Википедии."""
    url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{article}"
    try:
        async with httpx.AsyncClient(timeout=12, headers=_HEADERS, follow_redirects=True) as client:
            resp = await client.get(url)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json()

            thumb = data.get("thumbnail")
            if thumb and thumb.get("source"):
                src: str = thumb["source"]
                src = re.sub(r"/\d+px-", f"/{width}px-", src)
                return src

            orig = data.get("originalimage")
            if orig and orig.get("source"):
                return orig["source"]
    except Exception as e:
        logger.warning(f"Wiki summary error [{lang}/{article}]: {e}")
    return None


async def fetch_images_for_slugs(slugs: list[str]) -> dict[str, str]:
    """
    Параллельно загружает изображения для нескольких событий.

    Возвращает словарь {slug: image_url} — только там, где нашлось изображение.
    """
    async def _one(slug: str) -> tuple[str, str | None]:
        article = SLUG_WIKI_MAP.get(slug)
        if not article:
            return slug, None
        img = await fetch_wiki_image(article)
        if img:
            logger.info(f"Wiki image OK  : {slug} → {img[:70]}...")
        else:
            logger.warning(f"Wiki image FAIL: {slug} ({article})")
        return slug, img

    pairs = await asyncio.gather(*[_one(s) for s in slugs])
    return {slug: url for slug, url in pairs if url}
