"""
Сервис авто-создания событий из новостей.

Логика:
  1. Берём новость из RSS
  2. Проверяем релевантность (РФ-фокус, мировой спорт)
  3. Генерируем вопрос «Да/Нет» по шаблонным правилам
  4. Определяем категорию и дедлайн
  5. Создаём Event + Outcomes в БД

Чтобы включить AI-генерацию вопросов:
  - Установи: pip install anthropic
  - Добавь в .env: ANTHROPIC_API_KEY=sk-ant-...
  - Раскомментируй блок AI_QUESTION_GENERATION ниже
"""
import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from db.database import AsyncSessionLocal
from db.models import Category, Event, EventStatus, EventType, Outcome

logger = logging.getLogger(__name__)


# ─── Фильтрация ──────────────────────────────────────────────────────────────

# Ключевые слова для ПОЛИТИКИ/ЭКОНОМИКИ (РФ-фокус)
_POLITICS_KEYWORDS = [
    "путин", "кремль", "госдума", "правительство рф", "президент рф",
    "санкци", "переговоры", "перемирие", "украин", "нато", "зеленск",
    "трамп", "байден", "байдена", "сша и россия", "запад",
    "министр", "мид рф", "силуанов", "мишустин", "набиуллина",
    "выборы", "референдум", "конституци",
    "ключевая ставка", "цб рф", "инфляци", "ввп росси", "бюджет рф",
    "рубль", "доллар сша", "курс доллара",
    "газпром", "роснефть", "сбербанк", "лукойл",
    "нефт", "газ", "экспорт", "импорт",
]

# Ключевые слова для МИРОВОГО СПОРТА (только крупные события)
_WORLD_SPORTS_KEYWORDS = [
    "чемпионат мира", "кубок мира", "олимпиад", "паралимпиад",
    "лига чемпионов", "лига европы", "лига конференций",
    "евро-202", "чемпионат европы по футбол",
    "fifa", "uefa", "уефа", "фифа",
    "nba", "нба", "nhl", "нхл",
    "ufc", "мма", "бокс", "чемпион мира по бокс",
    "формула 1", "f1 ", " гран-при", "гран при",
    "уимблдон", "wimbledon", "ролан гаррос", "us open", "australian open",
    "сборная росси", "сборная мира",
    "world cup", "champions league",
]

# Ключевые слова для КРИПТЫ
_CRYPTO_KEYWORDS = [
    "биткоин", "bitcoin", "btc", "ethereum", "эфириум",
    "криптовалют", "binance", "coinbase",
    "блокчейн", "web3", "defi", "nft", "токен",
    "ton", "тон крипт",
]

# Ключевые слова для ТЕХНОЛОГИЙ (мирового уровня)
_TECH_KEYWORDS = [
    "искусственный интеллект", "chatgpt", "openai", "claude", "gemini",
    "яндекс gpt", "сбергпт", "giga chat",
    "apple", "google", "microsoft", "tesla", "nvidia",
    "квантов", "чипы", "полупроводник",
]

# Жёсткий СТОП-лист — эти темы не берём никогда
_STOP_KEYWORDS = [
    "погода", "гороскоп", "рецепт", "сонник", "гадани",
    "дтп", "авари", "убийств", "смерт", "пожар", "наводнен",
    "скидки", "распродажа", "акция",
    "звезда", "знаменитост", "шоу-бизн", "тнт", "кино",
    "мелодрам", "сериал", "инстаграм", "тикток",
    # региональный спорт — отсеиваем (оставляем только мировой)
    "зенит", "спартак", "цска", "локомотив", "краснодар",
    "рпл", "кхл", "кубок росси", "чемпионат росси",
    "пляжный", "мини-футбол", "регби",
]


def _is_relevant(title: str, category: str) -> bool:
    """Проверяет, подходит ли новость для создания события."""
    low = title.lower()

    # Сначала отсеиваем стоп-слова
    if any(kw in low for kw in _STOP_KEYWORDS):
        return False

    if category == "sports":
        # Только мировые спортивные события
        return any(kw in low for kw in _WORLD_SPORTS_KEYWORDS)

    if category == "politics":
        return any(kw in low for kw in _POLITICS_KEYWORDS)

    if category == "economy":
        return any(kw in low for kw in _POLITICS_KEYWORDS + _CRYPTO_KEYWORDS)

    if category == "crypto":
        return any(kw in low for kw in _CRYPTO_KEYWORDS)

    if category == "tech":
        return any(kw in low for kw in _TECH_KEYWORDS)

    return False


# ─── Генерация вопроса (шаблонные правила) ───────────────────────────────────

def _template_question(title: str, category: str) -> str | None:
    """
    Преобразует заголовок новости в вопрос «Да/Нет» по шаблонам.
    Возвращает None если заголовок не удалось преобразовать.
    """
    t = title.strip().rstrip(".")
    low = t.lower()

    # Уже вопрос — используем напрямую
    if t.endswith("?"):
        return t

    # Курс валюты / цена актива
    m = re.search(r"курс.*?(\d[\d\s,.]*)\s*[₽руб]", low)
    if m:
        threshold = m.group(1).strip()
        return f"Превысит ли курс доллара {threshold} ₽ до конца квартала?"

    m = re.search(r"bitcoin|биткоин|btc.*?(\$[\d\s,.]+|\d[\d\s,.]*\s*\$)", low)
    if m:
        price = m.group(1).strip()
        return f"Достигнет ли Bitcoin {price} до конца года?"

    # Ключевая ставка
    if re.search(r"ключевая ставка|цб рф.*(снизи|повыси|измен)", low):
        if "снизи" in low:
            return "Снизит ли ЦБ РФ ключевую ставку на ближайшем заседании?"
        if "повыси" in low:
            return "Повысит ли ЦБ РФ ключевую ставку на ближайшем заседании?"
        return "Изменит ли ЦБ РФ ключевую ставку на ближайшем заседании?"

    # Санкции
    if re.search(r"санкци", low):
        if re.search(r"введ[её]т?|нов[ыйые]|расширит|ужесточ", low):
            return f"Будут ли введены новые санкции против России в ближайшие 3 месяца?"
        if re.search(r"отмен|снят|ослаб", low):
            return "Будут ли сняты/ослаблены санкции против России до конца года?"
        return "Приведут ли санкции к существенным изменениям в ближайшие 3 месяца?"

    # Переговоры / встреча
    if re.search(r"переговоры|встреч[аи]|саммит|диалог", low):
        actors = re.search(r"(путин|зеленск|трамп|байден|шольц|макрон)", low)
        if actors:
            name = actors.group(1).capitalize()
            return f"Завершатся ли переговоры с участием {name} конкретным соглашением?"
        return "Завершатся ли переговоры подписанием соглашения?"

    # Перемирие
    if re.search(r"перемири|прекращение огня|мирн[ыйые] перегов", low):
        return "Будет ли подписано перемирие на Украине до конца 2026 года?"

    # Выборы
    if re.search(r"выборы|выборах|избирательн", low):
        country = re.search(
            r"в\s+(сша|германии|франции|великобритании|израиле|иране|японии|индии|бразилии)", low
        )
        if country:
            c = country.group(1).capitalize()
            return f"Сменится ли правящая партия по итогам выборов в {c}?"
        return f"Приведут ли выборы к смене власти?"

    # ВВП / инфляция / экономика
    if re.search(r"ввп|экономик.*(рост|спад|рецесси)", low):
        return "Покажет ли ВВП России рост по итогам года?"
    if re.search(r"инфляци.*(снизи|упадёт|замедли)", low):
        return "Снизится ли инфляция в РФ ниже 7% к концу 2026 года?"
    if re.search(r"рецесси", low):
        return "Войдёт ли экономика в рецессию до конца 2026 года?"

    # Лига чемпионов / мировой спорт
    if re.search(r"лига чемпионов|champions league", low):
        winner = re.search(
            r"(реал|барселона|манчестер|бавария|пск|ливерпул|челси|арсенал|интер|ювентус)", low
        )
        if winner:
            club = winner.group(1).capitalize()
            return f"Выиграет ли {club} Лигу чемпионов?"
        return "Выиграет ли фаворит Лигу чемпионов этого сезона?"

    if re.search(r"чемпионат мира|world cup|fifa", low):
        return "Выиграет ли фаворит чемпионат мира?"

    if re.search(r"олимпиад", low):
        return "Займёт ли Россия место в топ-10 медального зачёта Олимпиады?"

    if re.search(r"ufc|чемпион.*бокс|мма", low):
        champion = re.search(r"(джонс|фьюри|усик|хабиб|конор|поветкин|волкановски)", low)
        if champion:
            name = champion.group(1).capitalize()
            return f"Победит ли {name} в предстоящем бою?"
        return "Победит ли действующий чемпион в предстоящем бою?"

    if re.search(r"формула 1|гран.при|f1", low):
        return "Выиграет ли лидер чемпионата следующий Гран-при?"

    if re.search(r"nba|нба", low):
        return "Выиграет ли фаворит чемпионат NBA этого сезона?"

    # Биткоин / крипта без цены
    if re.search(r"биткоин|bitcoin|btc", low):
        return "Вырастет ли Bitcoin выше $100 000 до конца 2026 года?"
    if re.search(r"ethereum|эфириум|eth\b", low):
        return "Вырастет ли Ethereum выше $5 000 до конца 2026 года?"

    # ИИ / технологии
    if re.search(r"искусственный интеллект|chatgpt|openai|claude", low):
        return "Приведёт ли новый прорыв в ИИ к существенному изменению рынка до конца года?"

    # Нефть/газ
    if re.search(r"нефт.*(цена|стоимость|\$)", low):
        return "Упадёт ли цена нефти ниже $60 за баррель до конца года?"
    if re.search(r"газ.*(цена|рост|паден)", low):
        return "Вырастут ли цены на газ в Европе до конца квартала?"

    # Ничего не подошло
    return None


# ═══════════════════════════════════════════════════════════════════
# AI_QUESTION_GENERATION (раскомментируй когда будет API ключ)
# ═══════════════════════════════════════════════════════════════════
#
# async def _ai_question(title: str, category: str) -> str | None:
#     """Генерирует вопрос «Да/Нет» через Claude API."""
#     if not settings.anthropic_api_key:
#         return None
#     try:
#         import anthropic
#         client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
#         prompt = (
#             f"Ты создаёшь вопросы для рынка предсказаний (как Polymarket) на русском языке.\n"
#             f"Категория: {category}\n"
#             f"Новость: «{title}»\n\n"
#             f"Напиши ОДИН вопрос с ответом Да/Нет для рынка прогнозов. "
#             f"Требования:\n"
#             f"- Короткий и конкретный (до 15 слов)\n"
#             f"- Верифицируемый — есть чёткий критерий истины\n"
#             f"- Временной горизонт: 1–12 месяцев\n"
#             f"- Только вопрос, никаких пояснений\n"
#             f"- Заканчивается знаком «?»"
#         )
#         message = await client.messages.create(
#             model="claude-haiku-4-5-20251001",
#             max_tokens=100,
#             messages=[{"role": "user", "content": prompt}],
#         )
#         q = message.content[0].text.strip()
#         if q and q.endswith("?"):
#             return q
#     except Exception as e:
#         logger.warning("Claude API error: %s", e)
#     return None
#
# ═══════════════════════════════════════════════════════════════════


# ─── Определение категории и дедлайна ────────────────────────────────────────

_CATEGORY_SLUG_MAP = {
    "politics": "politics",
    "economy": "economy",
    "sports": "sports",
    "crypto": "crypto",
    "tech": "tech",
    "world": "world",
}


def _detect_category(title: str, source_category: str) -> str:
    """Уточняет категорию события по тексту заголовка."""
    low = title.lower()
    if any(kw in low for kw in _CRYPTO_KEYWORDS):
        return "crypto"
    if any(kw in low for kw in _TECH_KEYWORDS):
        return "tech"
    if any(kw in low for kw in _WORLD_SPORTS_KEYWORDS):
        return "sports"
    return source_category


# Коэффициенты для FIXED_ODDS по категории (маржа ~5-10%)
_ODDS_BY_CATEGORY: dict[str, tuple[Decimal, Decimal]] = {
    "politics": (Decimal("1.85"), Decimal("1.95")),
    "sports":   (Decimal("1.90"), Decimal("1.90")),
    "crypto":   (Decimal("1.75"), Decimal("2.05")),
    "economy":  (Decimal("1.85"), Decimal("1.95")),
}
_DEFAULT_ODDS = (Decimal("1.85"), Decimal("1.95"))


def _get_deadline_and_type(category: str, title: str) -> tuple[datetime, EventType]:
    """
    Определяет дедлайн и тип рынка для авто-события из RSS.

    Все события из новостных RSS — краткосрочные FIXED_ODDS (1-30 дней),
    потому что порождены сегодняшними новостями.
    """
    now = datetime.now(timezone.utc)
    low = title.lower()

    if category == "sports":
        # Конкретный матч/бой — 7 дней; общий спорт — 14 дней
        if any(kw in low for kw in ["финал", "полуфинал", "матч", "бой", "гран-при", "поединок"]):
            return now + timedelta(days=7), EventType.FIXED_ODDS
        return now + timedelta(days=14), EventType.FIXED_ODDS

    if category == "crypto":
        return now + timedelta(days=21), EventType.FIXED_ODDS

    if category == "economy":
        if "ключевая ставка" in low or "заседани" in low:
            return now + timedelta(days=14), EventType.FIXED_ODDS
        return now + timedelta(days=21), EventType.FIXED_ODDS

    if category == "politics":
        if any(kw in low for kw in ["переговоры", "встреч", "саммит", "подпишет", "заявит"]):
            return now + timedelta(days=14), EventType.FIXED_ODDS
        return now + timedelta(days=21), EventType.FIXED_ODDS

    return now + timedelta(days=21), EventType.FIXED_ODDS


def _get_deadline(category: str, title: str) -> datetime:
    """Обратная совместимость — используется в тестах."""
    deadline, _ = _get_deadline_and_type(category, title)
    return deadline


# ─── Slug из заголовка ───────────────────────────────────────────────────────

def _make_slug(title: str) -> str:
    """Генерирует уникальный slug: auto-YYYYMMDD-{hash}."""
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    h = hashlib.md5(title.lower().encode()).hexdigest()[:8]
    return f"auto-{date_str}-{h}"


# ─── Публикация события ──────────────────────────────────────────────────────

async def create_auto_event(
    session: AsyncSession,
    question: str,
    category_slug: str,
    image_url: str | None,
    article_url: str | None,
    deadline: datetime,
    description: str = "",
    event_type: EventType = EventType.FIXED_ODDS,
    odds_yes: Decimal | None = None,
    odds_no: Decimal | None = None,
) -> Event | None:
    """Создаёт событие в БД. Возвращает None если slug уже есть."""
    from bot.services.event_images import pick_event_image
    from bot.services.image_validator import validate_image_url

    slug = _make_slug(question)

    # Проверяем дубль
    exists = await session.execute(select(Event).where(Event.slug == slug))
    if exists.scalar_one_or_none():
        return None

    # Ищем категорию
    cat_result = await session.execute(
        select(Category).where(Category.slug == category_slug)
    )
    cat = cat_result.scalar_one_or_none()
    if not cat:
        # fallback на politics
        cat_result = await session.execute(select(Category).where(Category.slug == "politics"))
        cat = cat_result.scalar_one_or_none()
    if not cat:
        logger.warning("No categories in DB yet, skipping auto-event")
        return None

    # Выбираем фото: strict=True → None если только SVG доступен
    picked_url = await pick_event_image(
        title=question,
        category_slug=category_slug,
        prefilled=image_url,
        strict=True,
    )

    image_ok = False
    final_image_url: str | None = None
    if picked_url:
        valid, reason = await validate_image_url(picked_url)
        if valid:
            image_ok = True
            final_image_url = picked_url
        else:
            logger.info(
                "Auto-event image validation failed (%s) for '%s'", reason, question[:50]
            )

    event_status = EventStatus.ACTIVE if image_ok else EventStatus.MODERATION

    event = Event(
        slug=slug,
        title=question,
        description=description or f"Автоматически создано по материалам {article_url or 'новостных лент'}.",
        image_url=final_image_url,
        article_url=article_url,
        category_id=cat.id,
        status=event_status,
        liquidity_b=Decimal("1000.00"),
        closes_at=deadline,
        resolves_at=deadline + timedelta(days=7),
        event_type=event_type,
        odds_yes=odds_yes,
        odds_no=odds_no,
    )
    session.add(event)
    await session.flush()

    # Всегда Да / Нет
    for i, title in enumerate(["Да", "Нет"]):
        session.add(Outcome(event_id=event.id, title=title, sort_order=i))

    await session.commit()
    await session.refresh(event)

    logger.info(
        "Auto-event created [%s] status=%s: %s",
        category_slug, event_status.value, question,
    )

    if event_status == EventStatus.MODERATION:
        await _notify_admins_moderation(event)

    return event


async def _notify_admins_moderation(event: Event) -> None:
    """Уведомляет всех админов о событии, ожидающем фото."""
    try:
        from bot.notifier import notify_admins
        await notify_admins(
            f"⚠️ Новое событие ожидает фото (#{event.id})\n\n"
            f"<b>{event.title}</b>\n"
            f"Используй /moderation чтобы добавить фото или опубликовать без него."
        )
    except Exception as exc:
        logger.warning("Failed to notify admins about moderation event: %s", exc)


# ─── Главная точка входа (вызывается из cron) ────────────────────────────────

async def process_auto_events(suggestions: list[dict]) -> tuple[int, set[str]]:
    """
    Принимает список новостей из RSS, фильтрует, генерирует вопросы и публикует события.
    Возвращает (количество созданных событий, множество hash авто-опубликованных новостей).
    """
    if not settings.auto_events_enabled:
        return 0, set()

    created = 0
    auto_hashes: set[str] = set()
    limit = settings.auto_events_per_run

    async with AsyncSessionLocal() as session:
        for item in suggestions:
            if created >= limit:
                break

            title = item.get("title", "")
            category = item.get("category", "politics")

            # 1. Фильтр релевантности
            if not _is_relevant(title, category):
                logger.debug("Auto-event filtered out: %s", title[:60])
                continue

            # 2. Определяем категорию точнее
            category = _detect_category(title, category)

            # 3. Генерируем вопрос
            question = _template_question(title, category)

            # ── Раскомментируй когда добавишь API ключ: ──────────────────
            # if not question and settings.anthropic_api_key:
            #     question = await _ai_question(title, category)
            # ─────────────────────────────────────────────────────────────

            if not question:
                logger.debug("Could not generate question for: %s", title[:60])
                continue

            # 4. Дедлайн + тип рынка
            deadline, evt_type = _get_deadline_and_type(category, title)
            odds_yes, odds_no = _ODDS_BY_CATEGORY.get(category, _DEFAULT_ODDS)

            # 5. Создаём событие
            event = await create_auto_event(
                session=session,
                question=question,
                category_slug=category,
                image_url=item.get("image_url"),
                article_url=item.get("article_url"),
                deadline=deadline,
                event_type=evt_type,
                odds_yes=odds_yes,
                odds_no=odds_no,
            )

            if event:
                created += 1
                auto_hashes.add(item["hash"])
                await _notify_admins_about_new_event(event, item.get("source", ""))

    return created, auto_hashes


async def _notify_admins_about_new_event(event: Event, source: str) -> None:
    """Отправляет adminам уведомление об автоматически созданном событии."""
    try:
        from bot.notifier import notify_admins
        await notify_admins(
            f"🤖 Авто-событие создано\n\n"
            f"<b>{event.title}</b>\n"
            f"Источник: {source}\n"
            f"Закрывается: {event.closes_at.strftime('%d.%m.%Y')}\n\n"
            f"Для отмены: /cancel_event {event.id}"
        )
    except Exception as e:
        logger.warning("Failed to notify about auto-event: %s", e)
