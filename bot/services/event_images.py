"""
Выбор картинки для события — единая точка для /addevent, новостей и backfill.

Приоритет источников:
  1. prefilled — фото уже подтянуто (из RSS-новости)
  2. Wikipedia — поиск по заголовку (ru → en)
  3. SVG по категории — детерминированный фоллбек из miniapp/images/
"""
import logging

from bot.services.wiki_images import fetch_wiki_image_by_query

logger = logging.getLogger(__name__)


# Категория → готовая SVG из miniapp/images/. Согласовано с seed.py.
CATEGORY_SVG_MAP: dict[str, str] = {
    "politics": "/miniapp/images/politics.svg",
    "sports":   "/miniapp/images/football.svg",
    "economy":  "/miniapp/images/economy.svg",
    "crypto":   "/miniapp/images/btc.svg",
    "tech":     "/miniapp/images/tech.svg",
    "world":    "/miniapp/images/world.svg",
}

DEFAULT_SVG = "/miniapp/images/world.svg"


def category_fallback(category_slug: str | None) -> str:
    return CATEGORY_SVG_MAP.get(category_slug or "", DEFAULT_SVG)


def _is_valid_url(url: str | None) -> bool:
    if not url:
        return False
    u = url.strip()
    return u.startswith(("http://", "https://", "/miniapp/"))


async def pick_event_image(
    title: str,
    category_slug: str | None,
    prefilled: str | None = None,
) -> str:
    """Возвращает image_url для события — гарантированно непустой."""
    if _is_valid_url(prefilled):
        logger.info(f"Image: prefilled (news) for '{title[:50]}'")
        return prefilled  # type: ignore[return-value]

    try:
        wiki_url = await fetch_wiki_image_by_query(title)
    except Exception as e:
        logger.warning(f"Image: wiki lookup crashed for '{title[:50]}': {e}")
        wiki_url = None

    if _is_valid_url(wiki_url):
        logger.info(f"Image: wiki for '{title[:50]}'")
        return wiki_url  # type: ignore[return-value]

    fallback = category_fallback(category_slug)
    logger.info(f"Image: SVG fallback ({category_slug}) for '{title[:50]}'")
    return fallback
