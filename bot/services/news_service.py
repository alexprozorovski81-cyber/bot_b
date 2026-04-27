"""
Парсер новостей — находит горячие события и уведомляет администратора.

Источники RSS:
- ТАСС, Lenta.ru, РБК, Чемпионат

Извлечение фото (по приоритету):
  1. <enclosure> в RSS-item
  2. <media:content> / <media:thumbnail> в RSS-item
  3. <img> в <description> RSS-item
  4. og:image со страницы статьи (отдельный HTTP-запрос)

Запускается каждые 30 минут из main.py (cron).
"""
import hashlib
import logging
import re
from xml.etree import ElementTree

import httpx

logger = logging.getLogger(__name__)

# Браузерный User-Agent — без него ТАСС/РБК/Лента могут вернуть 403
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}

# Пространства имён для media:content / media:thumbnail
_MEDIA_NS = [
    "http://search.yahoo.com/mrss/",
    "http://video.search.yahoo.com/mrss/",
]

RSS_SOURCES = [
    # ── Политика / Главные новости ─────────────────────────────────
    {
        "name": "ТАСС — Главные новости",
        "url": "https://tass.ru/rss/v2.xml",
        "category": "politics",
    },
    {
        "name": "РИА Новости",
        "url": "https://ria.ru/export/rss2/archive/index.xml",
        "category": "politics",
    },
    {
        "name": "Интерфакс",
        "url": "https://www.interfax.ru/rss.asp",
        "category": "politics",
    },
    # ── Экономика / Финансы ────────────────────────────────────────
    {
        "name": "РБК — Топ новости",
        "url": "https://rss.rbc.ru/rbc_top.rss",
        "category": "economy",
    },
    {
        "name": "Коммерсантъ",
        "url": "https://www.kommersant.ru/RSS/main.xml",
        "category": "economy",
    },
    {
        "name": "Lenta.ru — Новости",
        "url": "https://lenta.ru/rss/news",
        "category": "economy",
    },
    # ── Крипта ────────────────────────────────────────────────────
    {
        "name": "РБК Крипто",
        "url": "https://rss.rbc.ru/rbc_crypto.rss",
        "category": "crypto",
    },
    # ── Мировой спорт ─────────────────────────────────────────────
    {
        "name": "Чемпионат — Мировые события",
        "url": "https://www.championat.com/rss/news_all.rss",
        "category": "sports",
    },
    {
        "name": "Sports.ru — Мировой футбол",
        "url": "https://www.sports.ru/rss/football_world.xml",
        "category": "sports",
    },
]

# Ключевые слова для базовой фильтрации (широкий фильтр)
# Тонкая фильтрация (РФ-фокус / мировой спорт) делается в auto_events_service.py
KEYWORDS = [
    # политика РФ и мировая
    "путин", "госдума", "правительство", "санкци", "переговоры",
    "перемирие", "выборы", "кремль", "министр", "президент",
    "украин", "нато", "трамп", "зеленск", "байден",
    "мид", "шольц", "макрон", "байден", "сша",
    # экономика
    "цб рф", "ключевая ставка", "доллар", "инфляци", "ввп",
    "рубль", "банк росс", "бюджет", "нефт", "газ", "экономик",
    "газпром", "роснефть", "сбер",
    # мировой спорт
    "чемпионат мира", "олимпиад", "лига чемпионов",
    "fifa", "uefa", "nba", "нба", "ufc", "формула 1",
    "уимблдон", "wimbledon", "ролан гаррос",
    "сборная", "гран-при", "кубок мира",
    # крипта
    "биткоин", "bitcoin", "btc", "ethereum", "крипт",
    # технологии
    "искусственный интеллект", "chatgpt", "openai",
]

# Хэши уже отправленных новостей (сбрасывается при рестарте)
_sent_hashes: set[str] = set()

# Кэш последних новостей для lookup по хэшу (для кнопки «Создать событие»)
_recent_items: dict[str, dict] = {}
_MAX_RECENT = 50


def _news_hash(title: str) -> str:
    return hashlib.md5(title.lower().encode()).hexdigest()[:12]


def _is_interesting(title: str) -> bool:
    t = title.lower()
    return any(kw in t for kw in KEYWORDS)


def get_item_by_hash(h: str) -> dict | None:
    """Возвращает новость из кэша по хэшу (для кнопки создания события)."""
    return _recent_items.get(h)


def _extract_image_from_rss_item(item: ElementTree.Element) -> str | None:
    """Извлекает URL изображения из RSS-элемента (без HTTP-запросов)."""

    # 1. <enclosure url="..." type="image/..."/>
    enclosure = item.find("enclosure")
    if enclosure is not None:
        url = enclosure.get("url", "")
        mime = enclosure.get("type", "")
        if url and ("image" in mime or url.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))):
            return url

    # 2. <media:content> / <media:thumbnail> с пространством имён
    for ns in _MEDIA_NS:
        for tag in ("content", "thumbnail"):
            el = item.find(f"{{{ns}}}{tag}")
            if el is not None:
                url = el.get("url", "")
                if url:
                    return url

    # 3. <img> внутри <description> (некоторые RSS встраивают HTML)
    desc_el = item.find("description")
    if desc_el is not None and desc_el.text:
        m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', desc_el.text)
        if m:
            return m.group(1)

    return None


async def _fetch_og_image(url: str, client: httpx.AsyncClient) -> str | None:
    """Загружает страницу статьи и извлекает og:image."""
    try:
        resp = await client.get(url, timeout=8)
        resp.raise_for_status()
        html = resp.text[:30_000]  # читаем только head

        # og:image content="..." property="og:image"  — оба порядка атрибутов
        for pattern in (
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
        ):
            m = re.search(pattern, html, re.IGNORECASE)
            if m:
                img = m.group(1).strip()
                if img.startswith("http"):
                    return img
    except Exception as e:
        logger.debug(f"og:image fetch error for {url}: {e}")
    return None


async def fetch_news_suggestions(ignore_cache: bool = False) -> list[dict]:
    """
    Парсит RSS-ленты, извлекает фото и возвращает список новых интересных новостей.

    Каждый элемент:
      {
        "title": str,
        "category": str,
        "source": str,
        "hash": str,
        "image_url": str | None,   # лучшее фото для события
        "article_url": str | None, # ссылка на статью
      }

    ignore_cache=True — повторно включает уже виденные заголовки (для /newscheck).
    """
    suggestions: list[dict] = []

    async with httpx.AsyncClient(
        timeout=15,
        headers=_HEADERS,
        follow_redirects=True,
    ) as client:
        for source in RSS_SOURCES:
            try:
                resp = await client.get(source["url"])
                resp.raise_for_status()
                root = ElementTree.fromstring(resp.content)

                items_found = 0
                items_matched = 0

                for item in root.iter("item"):
                    title_el = item.find("title")
                    if title_el is None or not title_el.text:
                        continue
                    items_found += 1
                    title = title_el.text.strip()

                    if not _is_interesting(title):
                        continue
                    items_matched += 1

                    h = _news_hash(title)
                    if h in _sent_hashes and not ignore_cache:
                        continue

                    # URL статьи
                    link_el = item.find("link")
                    article_url = link_el.text.strip() if (link_el is not None and link_el.text) else None

                    # Изображение: сначала из RSS, потом og:image
                    image_url = _extract_image_from_rss_item(item)
                    if not image_url and article_url:
                        image_url = await _fetch_og_image(article_url, client)

                    if not ignore_cache:
                        _sent_hashes.add(h)

                    entry = {
                        "title": title,
                        "category": source["category"],
                        "source": source["name"],
                        "hash": h,
                        "image_url": image_url,
                        "article_url": article_url,
                    }
                    suggestions.append(entry)

                    # Сохраняем в кэш для lookup по хэшу
                    _recent_items[h] = entry
                    if len(_recent_items) > _MAX_RECENT:
                        # Удаляем самый старый ключ
                        oldest = next(iter(_recent_items))
                        del _recent_items[oldest]

                logger.info(
                    f"RSS OK [{source['name']}]: "
                    f"{items_found} статей, {items_matched} совпали с ключевыми словами"
                )

            except Exception as e:
                logger.warning(f"RSS error [{source['name']}]: {e}")

    return suggestions[:5]  # максимум 5 за раз


async def fetch_cbr_rate() -> float | None:
    """Возвращает текущий официальный курс USD/RUB с cbr.ru."""
    url = "https://www.cbr.ru/scripts/XML_daily.asp"
    try:
        async with httpx.AsyncClient(timeout=10, headers=_HEADERS) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            root = ElementTree.fromstring(resp.content)
            for valute in root.iter("Valute"):
                char_code = valute.find("CharCode")
                value_el = valute.find("Value")
                if char_code is not None and char_code.text == "USD":
                    if value_el is not None and value_el.text:
                        return float(value_el.text.replace(",", "."))
    except Exception as e:
        logger.warning(f"CBR rate error: {e}")
    return None
