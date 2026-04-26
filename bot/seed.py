"""
Заполняет БД начальными данными:
  - 6 категорий
  - 20 актуальных российских событий

Запуск: python -m bot.seed
"""
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from db.database import AsyncSessionLocal
from db.models import Category, Event, EventStatus, Outcome


CATEGORIES = [
    {"slug": "politics", "name": "Политика", "emoji": "🏛️", "sort_order": 1},
    {"slug": "sports",   "name": "Спорт",    "emoji": "⚽", "sort_order": 2},
    {"slug": "economy",  "name": "Экономика", "emoji": "📈", "sort_order": 3},
    {"slug": "crypto",   "name": "Крипта",   "emoji": "₿",  "sort_order": 4},
    {"slug": "tech",     "name": "Технологии","emoji": "💻", "sort_order": 5},
    {"slug": "world",    "name": "Мир",       "emoji": "🌍", "sort_order": 6},
]


def _events_data(now: datetime) -> list[dict]:
    return [
        # ── ПОЛИТИКА ──────────────────────────────────────────────────────────
        {
            "slug": "ru-ceasefire-2026",
            "category": "politics",
            "image_url": "/miniapp/images/ceasefire.svg",
            "title": "Будет ли подписано перемирие на Украине до конца 2026?",
            "description": (
                "Будет ли официально подписано и вступит ли в силу перемирие "
                "между Россией и Украиной (любой формат) до 31.12.2026.\n\n"
                "Источник: международные СМИ, ООН."
            ),
            "outcomes": ["Да, перемирие будет", "Нет, не будет"],
            "closes_at": now + timedelta(days=245),
            "oracle_hint": "Reuters / ТАСС",
        },
        {
            "slug": "ru-trump-meeting-2026",
            "category": "politics",
            "image_url": "/miniapp/images/politics.svg",
            "title": "Встретится ли Путин с Трампом лично до конца 2026?",
            "description": (
                "Состоится ли официальная очная встреча президентов России и США "
                "до 31 декабря 2026 года.\n\n"
                "Источник: kremlin.ru, whitehouse.gov."
            ),
            "outcomes": ["Да, встретятся", "Нет"],
            "closes_at": now + timedelta(days=245),
            "oracle_hint": "kremlin.ru",
        },
        {
            "slug": "ru-sanctions-2026",
            "category": "politics",
            "image_url": "/miniapp/images/politics.svg",
            "title": "Введёт ли ЕС новый пакет санкций против РФ до конца 2026?",
            "description": (
                "Будет ли официально принят и вступит в силу новый пакет "
                "санкций Европейского союза против России до 31.12.2026.\n\n"
                "Источник: ec.europa.eu, ТАСС."
            ),
            "outcomes": ["Да", "Нет"],
            "closes_at": now + timedelta(days=245),
            "oracle_hint": "ec.europa.eu",
        },
        {
            "slug": "ru-duma-law-2026",
            "category": "politics",
            "image_url": "/miniapp/images/politics.svg",
            "title": "Примет ли Госдума закон о 4-дневной рабочей неделе в 2026?",
            "description": (
                "Будет ли принят и подписан президентом РФ федеральный закон "
                "об обязательной 4-дневной рабочей неделе до 31.12.2026.\n\n"
                "Источник: duma.gov.ru"
            ),
            "outcomes": ["Да, примут", "Нет, не примут"],
            "closes_at": now + timedelta(days=245),
            "oracle_hint": "duma.gov.ru",
        },
        {
            "slug": "ru-gubern-2026",
            "category": "politics",
            "image_url": "/miniapp/images/politics.svg",
            "title": "Сменится ли мэр Москвы до конца 2026 года?",
            "description": (
                "Уйдёт ли Сергей Собянин с поста мэра Москвы по любой причине "
                "(отставка, назначение на другую должность) до 31.12.2026.\n\n"
                "Источник: kremlin.ru, официальные пресс-релизы."
            ),
            "outcomes": ["Да, сменится", "Нет, останется"],
            "closes_at": now + timedelta(days=245),
            "oracle_hint": "kremlin.ru / ТАСС",
        },

        # ── СПОРТ ─────────────────────────────────────────────────────────────
        {
            "slug": "rpl-champion-2526",
            "category": "sports",
            "image_url": "/miniapp/images/football.svg",
            "title": "Кто станет чемпионом РПЛ сезона 2025/26?",
            "description": (
                "Победитель чемпионата России по футболу в сезоне 2025/26 "
                "по итогам всех туров (финал в мае 2026).\n\n"
                "Источник: premierliga.ru"
            ),
            "outcomes": ["Зенит", "Краснодар", "Спартак", "Другой"],
            "closes_at": now + timedelta(days=45),
            "oracle_hint": "premierliga.ru",
        },
        {
            "slug": "khl-champion-2526",
            "category": "sports",
            "image_url": "/miniapp/images/hockey.svg",
            "title": "Кто выиграет Кубок Гагарина КХЛ 2025/26?",
            "description": (
                "Победитель плей-офф КХЛ сезона 2025/26 (финал апрель–май 2026).\n\n"
                "Источник: khl.ru"
            ),
            "outcomes": ["ЦСКА", "СКА", "Металлург Мг", "Другой"],
            "closes_at": now + timedelta(days=30),
            "oracle_hint": "khl.ru",
        },
        {
            "slug": "worldcup-2026-winner",
            "category": "sports",
            "image_url": "/miniapp/images/worldcup.svg",
            "title": "Кто выиграет Чемпионат мира по футболу 2026?",
            "description": (
                "Победитель ЧМ-2026 (США/Канада/Мексика), финал в июле 2026.\n\n"
                "Источник: FIFA.com"
            ),
            "outcomes": ["Аргентина", "Бразилия", "Франция", "Другие"],
            "closes_at": now + timedelta(days=100),
            "oracle_hint": "FIFA.com",
        },
        {
            "slug": "spartak-top3-2526",
            "category": "sports",
            "image_url": "/miniapp/images/football.svg",
            "title": "Войдёт ли Спартак в топ-3 РПЛ по итогам сезона 2025/26?",
            "description": (
                "Окажется ли ФК Спартак Москва на 1–3 месте турнирной таблицы "
                "по итогам сезона 2025/26.\n\n"
                "Источник: premierliga.ru"
            ),
            "outcomes": ["Да, топ-3", "Нет"],
            "closes_at": now + timedelta(days=45),
            "oracle_hint": "premierliga.ru",
        },
        {
            "slug": "ru-tennis-wimbledon-2026",
            "category": "sports",
            "image_url": "/miniapp/images/tennis.svg",
            "title": "Выиграет ли российский теннисист Wimbledon 2026?",
            "description": (
                "Станет ли спортсмен с российским паспортом (нейтральный статус) "
                "победителем в одиночном разряде Wimbledon 2026.\n\n"
                "Источник: wimbledon.com"
            ),
            "outcomes": ["Да", "Нет"],
            "closes_at": now + timedelta(days=90),
            "oracle_hint": "wimbledon.com",
        },

        # ── ЭКОНОМИКА ─────────────────────────────────────────────────────────
        {
            "slug": "cbr-rate-below-15-2026",
            "category": "economy",
            "image_url": "/miniapp/images/economy.svg",
            "title": "Снизит ли ЦБ РФ ключевую ставку ниже 15% до конца 2026?",
            "description": (
                "Установит ли Банк России ключевую ставку на уровне строго ниже 15% "
                "по итогам любого заседания совета директоров до 31.12.2026.\n\n"
                "Источник: cbr.ru — официальный сайт Банка России."
            ),
            "outcomes": ["Да, ниже 15%", "Нет, 15% и выше"],
            "closes_at": now + timedelta(days=245),
            "oracle_hint": "cbr.ru",
        },
        {
            "slug": "usd-rub-100-2026",
            "category": "economy",
            "image_url": "/miniapp/images/economy.svg",
            "title": "Превысит ли курс доллара 100 ₽ до конца 2026?",
            "description": (
                "Будет ли официальный курс ЦБ РФ USD/RUB выше 100,00 ₽ "
                "хотя бы один день до 31 декабря 2026.\n\n"
                "Источник: cbr.ru — ежедневные курсы."
            ),
            "outcomes": ["Да, выше 100 ₽", "Нет, останется ниже"],
            "closes_at": now + timedelta(days=245),
            "oracle_hint": "cbr.ru/currency_base/daily/",
        },
        {
            "slug": "ru-inflation-2026",
            "category": "economy",
            "image_url": "/miniapp/images/economy.svg",
            "title": "Снизится ли инфляция в РФ ниже 7% по итогам 2026 года?",
            "description": (
                "Будет ли официальный показатель инфляции (ИПЦ) в России "
                "за 2026 год ниже 7% по данным Росстата.\n\n"
                "Источник: rosstat.gov.ru"
            ),
            "outcomes": ["Да, ниже 7%", "Нет, 7% и выше"],
            "closes_at": now + timedelta(days=245),
            "oracle_hint": "rosstat.gov.ru",
        },
        {
            "slug": "ru-gdp-growth-2026",
            "category": "economy",
            "image_url": "/miniapp/images/economy.svg",
            "title": "Вырастет ли ВВП России более чем на 1% в 2026 году?",
            "description": (
                "Будет ли прирост ВВП России за 2026 год (по предварительной оценке "
                "Росстата или Минэка) выше 1%.\n\n"
                "Источник: economy.gov.ru, rosstat.gov.ru"
            ),
            "outcomes": ["Да, рост > 1%", "Нет, 1% и ниже"],
            "closes_at": now + timedelta(days=245),
            "oracle_hint": "rosstat.gov.ru / economy.gov.ru",
        },

        # ── КРИПТА ────────────────────────────────────────────────────────────
        {
            "slug": "btc-150k-2026",
            "category": "crypto",
            "image_url": "/miniapp/images/btc.svg",
            "title": "Достигнет ли Bitcoin $150 000 до конца 2026?",
            "description": (
                "Будет ли курс BTC/USD на любой бирже из топ-10 (Binance, Coinbase, "
                "Kraken и др.) выше $150 000 хотя бы на одной свече до 31.12.2026.\n\n"
                "Источник: CoinGecko / Binance API."
            ),
            "outcomes": ["Да", "Нет"],
            "closes_at": now + timedelta(days=245),
            "oracle_hint": "coingecko_threshold:bitcoin:150000",
        },
        {
            "slug": "ton-price-10-2026",
            "category": "crypto",
            "image_url": "/miniapp/images/ton.svg",
            "title": "Достигнет ли TON цены $10 до конца 2026?",
            "description": (
                "Будет ли курс TON/USD на любой бирже из топ-10 выше $10,00 "
                "хотя бы на одной свече до 31.12.2026.\n\n"
                "Источник: CoinGecko."
            ),
            "outcomes": ["Да, $10+", "Нет"],
            "closes_at": now + timedelta(days=245),
            "oracle_hint": "coingecko_threshold:the-open-network:10",
        },
        {
            "slug": "eth-flippening-2026",
            "category": "crypto",
            "image_url": "/miniapp/images/eth.svg",
            "title": "Обгонит ли Ethereum капитализацию Bitcoin в 2026?",
            "description": (
                "Превысит ли рыночная капитализация Ethereum капитализацию Bitcoin "
                "хотя бы на одну минуту до 31.12.2026.\n\n"
                "Источник: CoinMarketCap / CoinGecko."
            ),
            "outcomes": ["Да", "Нет"],
            "closes_at": now + timedelta(days=245),
            "oracle_hint": "coingecko_flippening",
        },

        # ── ТЕХНОЛОГИИ ────────────────────────────────────────────────────────
        {
            "slug": "ru-gosuslugi-ai-2026",
            "category": "tech",
            "image_url": "/miniapp/images/tech.svg",
            "title": "Запустит ли Госуслуги ИИ-ассистента для граждан в 2026?",
            "description": (
                "Будет ли публично запущен ИИ-чат-ассистент (на базе любой модели) "
                "на портале Госуслуг (gosuslugi.ru) для всех пользователей до 31.12.2026.\n\n"
                "Источник: gosuslugi.ru, Минцифры."
            ),
            "outcomes": ["Да, запустят", "Нет"],
            "closes_at": now + timedelta(days=245),
            "oracle_hint": "Минцифры / gosuslugi.ru",
        },
        {
            "slug": "yandex-gpt-new-2026",
            "category": "tech",
            "image_url": "/miniapp/images/tech.svg",
            "title": "Выпустит ли Яндекс новую большую языковую модель в 2026?",
            "description": (
                "Будет ли публично анонсирована и доступна новая флагманская LLM "
                "от Яндекса (YandexGPT 5 или под другим названием) до 31.12.2026.\n\n"
                "Источник: ya.ru, yandex.cloud"
            ),
            "outcomes": ["Да", "Нет"],
            "closes_at": now + timedelta(days=245),
            "oracle_hint": "ya.ru / yandex.cloud",
        },

        # ── МИР ───────────────────────────────────────────────────────────────
        {
            "slug": "us-recession-2026",
            "category": "world",
            "image_url": "/miniapp/images/world.svg",
            "title": "Войдут ли США в рецессию в 2026 году?",
            "description": (
                "Зафиксируют ли США два последовательных квартала с отрицательным "
                "ростом ВВП в 2026 году (официальные данные BEA).\n\n"
                "Источник: bea.gov, Bloomberg."
            ),
            "outcomes": ["Да, рецессия будет", "Нет"],
            "closes_at": now + timedelta(days=245),
            "oracle_hint": "bea.gov / Bloomberg",
        },
    ]


async def seed() -> None:
    async with AsyncSessionLocal() as session:
        # 1. Категории
        cat_map: dict[str, int] = {}
        for cat_data in CATEGORIES:
            existing = await session.execute(
                select(Category).where(Category.slug == cat_data["slug"])
            )
            cat = existing.scalar_one_or_none()
            if not cat:
                cat = Category(**{k: v for k, v in cat_data.items()})
                session.add(cat)
                await session.flush()
            cat_map[cat.slug] = cat.id
            print(f"  cat: {cat.emoji} {cat.name}")

        await session.commit()

        # 2. События
        now = datetime.now(timezone.utc)
        created = 0
        skipped = 0
        for ev_data in _events_data(now):
            existing = await session.execute(
                select(Event).where(Event.slug == ev_data["slug"])
            )
            if existing.scalar_one_or_none():
                skipped += 1
                continue

            event = Event(
                slug=ev_data["slug"],
                title=ev_data["title"],
                description=ev_data["description"],
                image_url=ev_data.get("image_url"),
                category_id=cat_map[ev_data["category"]],
                status=EventStatus.ACTIVE,
                liquidity_b=Decimal("1000.00"),
                closes_at=ev_data["closes_at"],
                resolves_at=ev_data["closes_at"] + timedelta(days=7),
                resolution_source=ev_data.get("oracle_hint"),
            )
            session.add(event)
            await session.flush()

            for i, outcome_title in enumerate(ev_data["outcomes"]):
                outcome = Outcome(
                    event_id=event.id,
                    title=outcome_title,
                    sort_order=i,
                )
                session.add(outcome)

            print(f"  + {ev_data['title'][:70]}")
            created += 1

        await session.commit()
        print(f"\nGotovo: sozdano={created}, propusheno={skipped}")


if __name__ == "__main__":
    asyncio.run(seed())
