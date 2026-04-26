"""
Парсер новостей — находит горячие события и уведомляет администратора.

Источники:
- Яндекс.Новости (RSS)
- Sports.ru (RSS)
- ЦБ РФ (XML API) — курс доллара

Запускается каждые 30 минут из main.py (cron).
Администратор получает в Telegram:
  "📰 Найдено событие: <заголовок>
   Создать рынок? [Да] [Нет]"
"""
import hashlib
import logging
from xml.etree import ElementTree

import httpx

from bot.config import settings

logger = logging.getLogger(__name__)

RSS_SOURCES = [
    {
        "name": "Яндекс.Новости — Политика",
        "url": "https://news.yandex.ru/politics.rss",
        "category": "politics",
    },
    {
        "name": "Яндекс.Новости — Экономика",
        "url": "https://news.yandex.ru/business.rss",
        "category": "economy",
    },
    {
        "name": "Sports.ru — Футбол Россия",
        "url": "https://www.sports.ru/rss/football/russia/",
        "category": "sports",
    },
    {
        "name": "Sports.ru — КХЛ",
        "url": "https://www.sports.ru/rss/hockey/khl/",
        "category": "sports",
    },
]

# Ключевые слова — фильтруем только значимые новости
KEYWORDS = [
    # политика
    "путин", "госдума", "правительство", "санкции", "переговоры",
    "перемирие", "закон", "выборы", "губернатор", "назначен",
    # экономика
    "цб рф", "ключевая ставка", "доллар", "инфляция", "ввп", "рубль",
    # спорт
    "зенит", "спартак", "цска", "краснодар", "рпл", "кхл",
    "чемпионат", "финал", "победитель", "кубок",
]

# Уже отправленные новости (в памяти — сбрасывается при рестарте)
_sent_hashes: set[str] = set()


def _news_hash(title: str) -> str:
    return hashlib.md5(title.lower().encode()).hexdigest()[:12]


def _is_interesting(title: str) -> bool:
    t = title.lower()
    return any(kw in t for kw in KEYWORDS)


async def fetch_news_suggestions() -> list[dict]:
    """
    Парсит RSS-ленты и возвращает список новых интересных новостей.
    Каждый элемент: {"title": ..., "category": ..., "source": ...}
    """
    suggestions = []

    async with httpx.AsyncClient(timeout=10) as client:
        for source in RSS_SOURCES:
            try:
                resp = await client.get(source["url"])
                resp.raise_for_status()
                root = ElementTree.fromstring(resp.content)

                for item in root.iter("item"):
                    title_el = item.find("title")
                    if title_el is None or not title_el.text:
                        continue
                    title = title_el.text.strip()

                    if not _is_interesting(title):
                        continue

                    h = _news_hash(title)
                    if h in _sent_hashes:
                        continue

                    _sent_hashes.add(h)
                    suggestions.append({
                        "title": title,
                        "category": source["category"],
                        "source": source["name"],
                        "hash": h,
                    })

            except Exception as e:
                logger.warning(f"RSS error {source['name']}: {e}")

    return suggestions[:5]  # максимум 5 за раз


async def fetch_cbr_rate() -> float | None:
    """Возвращает текущий официальный курс USD/RUB с cbr.ru."""
    url = "https://www.cbr.ru/scripts/XML_daily.asp"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
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
