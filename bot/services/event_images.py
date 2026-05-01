"""
Выбор картинки для события — единая точка для /addevent, новостей и backfill.

Приоритет источников:
  1. prefilled — фото уже подтянуто (из RSS-новости)
  2. Wikipedia по slug (SLUG_WIKI_MAP — для seed-событий)
  3. Wikipedia по очищенному заголовку (ru → en)
  4. SVG по категории — гарантированный фоллбек
"""
import logging
import re

from bot.services.wiki_images import (
    SLUG_WIKI_MAP,
    fetch_wiki_image,
    fetch_wiki_image_by_query,
)

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

# Русские вопросительные конструкции, которые мешают поиску в Wikipedia
_QUESTION_RE = re.compile(
    r"^(будет|выиграет|встретится|примет|победит|введёт|введет|"
    r"произойдёт|состоится|подпишет|достигнет|превысит|упадёт|"
    r"вырастет|поднимется|сможет|станет|займёт|получит|запустит|"
    r"выйдет|пройдёт|снизится|обновит|обгонит|обойдёт|перешагнёт)\s+ли\s+",
    re.IGNORECASE,
)


def clean_title_for_wiki(title: str) -> str:
    """Убирает вопросительные конструкции и знак «?» для точного поиска в Wiki."""
    cleaned = _QUESTION_RE.sub("", title.strip())
    cleaned = cleaned.rstrip("?").strip()
    # Убираем хвосты типа "до конца 2026", "в 2026 году", "в сезоне 2025/26"
    cleaned = re.sub(r"\s+(до конца|в \d{4}|к \d{4}|в сезоне).*$", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def category_fallback(category_slug: str | None) -> str:
    return CATEGORY_SVG_MAP.get(category_slug or "", DEFAULT_SVG)


def _is_real_photo(url: str | None) -> bool:
    """True если URL — реальное фото (http/https), не локальный SVG."""
    if not url:
        return False
    return url.strip().startswith(("http://", "https://"))


def has_real_photo(url: str | None) -> bool:
    """True если URL — внешнее фото (не /miniapp/ SVG/плейсхолдер)."""
    if not url:
        return False
    u = url.strip()
    return u.startswith("https://") and "/miniapp/" not in u


def _is_valid_url(url: str | None) -> bool:
    if not url:
        return False
    u = url.strip()
    return u.startswith(("http://", "https://", "/miniapp/"))


async def pick_event_image(
    title: str,
    category_slug: str | None,
    prefilled: str | None = None,
    slug: str | None = None,
    strict: bool = False,
) -> str | None:
    """
    Возвращает image_url для события.
    strict=False (по умолчанию): гарантированно возвращает непустую строку (SVG как fallback).
    strict=True: возвращает None если настоящего фото не нашлось.
    """
    if _is_real_photo(prefilled):
        logger.info(f"Image: prefilled (news) for '{title[:50]}'")
        return prefilled  # type: ignore[return-value]

    # Приоритет 2: slug → SLUG_WIKI_MAP (точные маппинги для seed-событий)
    if slug and slug in SLUG_WIKI_MAP:
        try:
            wiki_url = await fetch_wiki_image(SLUG_WIKI_MAP[slug])
            if _is_real_photo(wiki_url):
                logger.info(f"Image: wiki slug map [{slug}] for '{title[:50]}'")
                return wiki_url  # type: ignore[return-value]
        except Exception as e:
            logger.warning(f"Image: wiki slug map failed [{slug}]: {e}")

    # Приоритет 3: поиск по очищенному заголовку
    search_query = clean_title_for_wiki(title)
    if search_query:
        try:
            wiki_url = await fetch_wiki_image_by_query(search_query)
        except Exception as e:
            logger.warning(f"Image: wiki query crashed for '{search_query[:50]}': {e}")
            wiki_url = None

        if _is_real_photo(wiki_url):
            logger.info(f"Image: wiki query '{search_query[:40]}' for '{title[:40]}'")
            return wiki_url  # type: ignore[return-value]

    if strict:
        logger.info(f"Image: no real photo found (strict mode) for '{title[:50]}'")
        return None

    fallback = category_fallback(category_slug)
    logger.info(f"Image: SVG fallback ({category_slug}) for '{title[:50]}'")
    return fallback
