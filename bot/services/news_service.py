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

# Браузерный User-Agent — без него ТАСС/РБК/Лента возвращают 403
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}

RSS_SOURCES = [
    {
        "name": "ТАСС — Главные новости",
        "url": "https://tass.ru/rss/v2.xml",
        "category": "politics",
    },
    {
        "name": "Lenta.ru — Новости",
        "url": "https://lenta.ru/rss/news",
        "category": "economy",
    },
    {
        "name": "РБК — Топ новости",
        "url": "https://rss.rbc.ru/rbc_top.rss",
        "category": "economy",
    },
    {
        "name": "Чемпионат — Спорт",
        "url": "https://www.championat.com/rss/news_all.rss",
        "category": "sports",
    },
]

# Ключевые слова — фильтруем только значимые новости
KEYWORDS = [
    # политика
    "путин", "госдума", "правительство", "санкции", "переговоры",
    "перемирие", "закон", "выборы", "губернатор", "назначен",
    "кремль", "министр", "президент", "украин", "нато", "трамп",
    # экономика
    "цб рф", "ключевая ставка", "доллар", "инфляция", "ввп", "рубль",
    "банк росс", "минфин", "бюджет", "нефт", "газ", "экономик",
    # спорт
    "зенит", "спартак", "цска", "краснодар", "рпл", "кхл",
    "чемпионат", "финал", "победитель", "кубок", "локомотив",
    "сборная", "лига", "матч", "турнир",
    # крипта/технологии
    "биткоин", "bitcoin", "крипт", "яндекс", "сбер", "искусственный",
]

# Уже отправленные новости (в памяти — сбрасывается при рестарте)
_sent_hashes: set[str] = set()


def _news_hash(title: str) -> str:
    return hashlib.md5(title.lower().encode()).hexdigest()[:12]


def _is_interesting(title: str) -> bool:
    t = title.lower()
    return any(kw in t for kw in KEYWORDS)


async def fetch_news_suggestions(ignore_cache: bool = False) -> list[dict]:
    """
    Парсит RSS-ленты и возвращает список новых интересных новостей.
    Каждый элемент: {"title": ..., "category": ..., "source": ...}

    ignore_cache=True — повторно отправляет уже виденные заголовки (для /newscheck).
    """
    suggestions = []

    async with httpx.AsyncClient(timeout=15, headers=_HEADERS, follow_redirects=True) as client:
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

                    if not ignore_cache:
                        _sent_hashes.add(h)
                    suggestions.append({
                        "title": title,
                        "category": source["category"],
                        "source": source["name"],
                        "hash": h,
                    })

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
