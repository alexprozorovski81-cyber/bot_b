"""
Админские команды бота:
  /admin           — главное меню
  /events          — список событий
  /addevent        — создать новое событие (пошаговый диалог)
  /resolve         — разрешить событие (выбор исхода-победителя)
  /cancel_event    — отменить событие с возвратом средств
  /stats           — общая статистика платформы

Доступ только для пользователей из ADMIN_IDS в .env.
"""
import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton,
    InlineKeyboardMarkup, Message,
)
from sqlalchemy import func, select

from bot.config import settings
from bot.services.event_images import pick_event_image
from bot.services.resolution_service import resolve_event, cancel_event
from db.database import AsyncSessionLocal
from db.models import (
    Bet, Category, Event, EventStatus, Outcome,
    Transaction, TransactionType, User,
)


logger = logging.getLogger(__name__)
router = Router()


def is_admin(user_id: int) -> bool:
    return user_id in settings.admin_id_list


# ── FSM для /addevent ────────────────────────────────────────────────────────

class AddEventStates(StatesGroup):
    title = State()
    category = State()
    outcomes = State()
    closes_days = State()
    source = State()
    confirm = State()


# ── FSM для модерации событий ─────────────────────────────────────────────────

class ModerationStates(StatesGroup):
    waiting_photo = State()
    wiki_preview = State()  # wiki_url хранится в state data


@router.message(Command("addevent"))
async def cmd_addevent_start(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return

    await state.set_state(AddEventStates.title)
    await message.answer(
        "<b>➕ Создание нового события</b>\n\n"
        "Шаг 1/5: Введи <b>название события</b> в форме вопроса.\n\n"
        "<i>Пример: Выиграет ли Зенит РПЛ в сезоне 2025/26?</i>\n\n"
        "Напиши /cancel чтобы отменить.",
        parse_mode="HTML",
    )


@router.message(AddEventStates.title, Command("cancel"))
@router.message(AddEventStates.category, Command("cancel"))
@router.message(AddEventStates.outcomes, Command("cancel"))
@router.message(AddEventStates.closes_days, Command("cancel"))
@router.message(AddEventStates.source, Command("cancel"))
@router.message(AddEventStates.confirm, Command("cancel"))
async def cmd_addevent_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("❌ Создание события отменено.")


@router.message(AddEventStates.title)
async def addevent_title(message: Message, state: FSMContext) -> None:
    title = message.text.strip()
    if len(title) < 10:
        await message.answer("Название слишком короткое. Попробуй снова:")
        return

    await state.update_data(title=title)
    await state.set_state(AddEventStates.category)

    async with AsyncSessionLocal() as session:
        cats = (await session.execute(
            select(Category).order_by(Category.sort_order)
        )).scalars().all()

    buttons = [[InlineKeyboardButton(
        text=f"{c.emoji} {c.name}",
        callback_data=f"addev:cat:{c.id}:{c.slug}",
    )] for c in cats]

    await message.answer(
        "Шаг 2/5: Выбери <b>категорию</b>:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
    )


@router.callback_query(AddEventStates.category, F.data.startswith("addev:cat:"))
async def addevent_category(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    cat_id = int(parts[2])
    cat_slug = parts[3]
    await state.update_data(category_id=cat_id, category_slug=cat_slug)
    await state.set_state(AddEventStates.outcomes)
    await callback.message.edit_text(
        "Шаг 3/5: Введи <b>варианты исходов</b> через запятую.\n\n"
        "<i>Пример: Да, Нет\n"
        "или: Зенит, Краснодар, Спартак, Другой</i>",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AddEventStates.outcomes)
async def addevent_outcomes(message: Message, state: FSMContext) -> None:
    outcomes = [o.strip() for o in message.text.split(",") if o.strip()]
    if len(outcomes) < 2:
        await message.answer("Нужно минимум 2 варианта через запятую. Попробуй снова:")
        return
    if len(outcomes) > 8:
        await message.answer("Максимум 8 вариантов. Попробуй снова:")
        return

    await state.update_data(outcomes=outcomes)
    await state.set_state(AddEventStates.closes_days)
    await message.answer(
        "Шаг 4/5: Через сколько <b>дней</b> закрыть приём ставок?\n\n"
        "<i>Пример: 30 (через месяц)\n"
        "или: 180 (через полгода)</i>",
        parse_mode="HTML",
    )


@router.message(AddEventStates.closes_days)
async def addevent_closes(message: Message, state: FSMContext) -> None:
    try:
        days = int(message.text.strip())
        if days < 1 or days > 1000:
            raise ValueError
    except ValueError:
        await message.answer("Введи число от 1 до 1000:")
        return

    await state.update_data(closes_days=days)
    await state.set_state(AddEventStates.source)
    await message.answer(
        "Шаг 5/5: Укажи <b>источник для проверки результата</b>.\n\n"
        "<i>Пример: premierliga.ru\n"
        "или: cbr.ru\n"
        "или пропусти — напиши <code>-</code></i>",
        parse_mode="HTML",
    )


@router.message(AddEventStates.source)
async def addevent_source(message: Message, state: FSMContext) -> None:
    source_text = message.text.strip()
    source = None if source_text == "-" else source_text
    await state.update_data(source=source)
    await state.set_state(AddEventStates.confirm)

    data = await state.get_data()
    closes_at = datetime.now(timezone.utc) + timedelta(days=data["closes_days"])
    outcomes_text = "\n".join(f"  • {o}" for o in data["outcomes"])

    await message.answer(
        "<b>📋 Проверь данные события:</b>\n\n"
        f"<b>Название:</b> {data['title']}\n"
        f"<b>Исходы:</b>\n{outcomes_text}\n"
        f"<b>Закрытие:</b> {closes_at:%d.%m.%Y}\n"
        f"<b>Источник:</b> {source or 'не указан'}\n\n"
        "Всё верно?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Создать", callback_data="addev:confirm"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="addev:abort"),
            ]
        ]),
        parse_mode="HTML",
    )


@router.callback_query(AddEventStates.confirm, F.data == "addev:confirm")
async def addevent_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()

    now = datetime.now(timezone.utc)
    closes_at = now + timedelta(days=data["closes_days"])

    import re
    slug = re.sub(r"[^a-z0-9]+", "-", data["title"].lower())[:80].strip("-")
    slug = f"custom-{slug}-{int(now.timestamp())}"

    # Выбор картинки: новость → Wiki → SVG по категории
    image_url = await pick_event_image(
        title=data["title"],
        category_slug=data.get("category_slug"),
        prefilled=data.get("prefill_image_url"),
        slug=slug,
    )

    async with AsyncSessionLocal() as session:
        event = Event(
            slug=slug,
            title=data["title"],
            description=data["title"],
            image_url=image_url,
            article_url=data.get("prefill_article_url"),
            category_id=data["category_id"],
            status=EventStatus.ACTIVE,
            liquidity_b=Decimal("1000.00"),
            closes_at=closes_at,
            resolves_at=closes_at + timedelta(days=7),
            resolution_source=data.get("source"),
        )
        session.add(event)
        await session.flush()

        for i, outcome_title in enumerate(data["outcomes"]):
            session.add(Outcome(
                event_id=event.id,
                title=outcome_title,
                sort_order=i,
            ))

        await session.commit()
        event_id = event.id
        logger.info(
            f"Event #{event_id} '{event.title[:60]}' created and committed "
            f"(slug={event.slug}, image={image_url})"
        )

    await callback.message.edit_text(
        f"<b>✅ Событие #{event_id} создано!</b>\n\n"
        f"«{data['title']}»\n\n"
        f"Уже доступно в Mini App. Чтобы разрешить: <code>/resolve {event_id}</code>",
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "addev:abort")
async def addevent_abort(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("❌ Создание события отменено.")
    await callback.answer()


# ── Кнопка "Создать событие" из новостного уведомления ──────────────────────

@router.callback_query(F.data.startswith("news:addevent"))
async def news_addevent_btn(callback: CallbackQuery, state: FSMContext) -> None:
    """Нажатие «➕ Создать событие» под новостным уведомлением."""
    if not is_admin(callback.from_user.id):
        await callback.answer("Только для администратора", show_alert=True)
        return

    # Извлекаем хэш новости из callback_data (news:addevent:{hash})
    parts = callback.data.split(":")
    news_hash = parts[2] if len(parts) > 2 else None

    # Получаем сохранённые данные новости (заголовок + изображение)
    news_item = None
    if news_hash:
        from bot.services.news_service import get_item_by_hash
        news_item = get_item_by_hash(news_hash)

    await callback.message.edit_reply_markup(reply_markup=None)

    hint = ""
    if news_item:
        hint = (
            f"\n\n💡 <i>Изображение из статьи сохранено "
            f"{'✅' if news_item.get('image_url') else '❌ (не найдено)'}</i>"
        )
        # Сохраняем image_url в FSM-state чтобы использовать при создании
        await state.update_data(
            prefill_image_url=news_item.get("image_url"),
            prefill_title=news_item.get("title", ""),
            prefill_article_url=news_item.get("article_url"),
        )

    await state.set_state(AddEventStates.title)
    await callback.message.answer(
        "<b>➕ Создание события по новости</b>\n\n"
        "Шаг 1/5: Введи <b>название события</b> в форме вопроса.\n"
        "<i>(Переформулируй заголовок новости в вопрос)</i>"
        f"{hint}\n\n"
        "<i>Пример: Выиграет ли Зенит РПЛ в сезоне 2025/26?</i>\n\n"
        "Напиши /cancel чтобы отменить.",
        parse_mode="HTML",
    )
    await callback.answer()


# ── Быстрая публикация события из новости (один тап) ─────────────────────────

@router.callback_query(F.data.startswith("news:quick:"))
async def news_quick_publish(callback: CallbackQuery) -> None:
    """Нажатие «⚡ Быстро опубликовать» — создаёт ACTIVE событие одним тапом."""
    if not is_admin(callback.from_user.id):
        await callback.answer("Только для администратора", show_alert=True)
        return

    parts = callback.data.split(":")
    news_hash = parts[2] if len(parts) > 2 else None
    if not news_hash:
        await callback.answer("Ошибка: хэш новости не найден", show_alert=True)
        return

    from bot.services.news_service import get_item_by_hash
    news_item = get_item_by_hash(news_hash)
    if not news_item:
        await callback.answer("Новость устарела. Запусти /newscheck снова.", show_alert=True)
        return

    await callback.answer("⏳ Создаю событие...")
    await callback.message.edit_reply_markup(reply_markup=None)

    title = news_item.get("title", "")
    category_slug = news_item.get("category", "world")
    article_url = news_item.get("article_url")
    description = (news_item.get("description") or title)[:300]

    now = datetime.now(timezone.utc)
    closes_at = now + timedelta(days=7)

    slug_hash = hashlib.md5(title.encode()).hexdigest()[:8]
    slug = f"news-{slug_hash}-{now.year}"

    # Подтягиваем категорию из БД
    async with AsyncSessionLocal() as session:
        from sqlalchemy import select as sa_select
        cat_result = await session.execute(
            sa_select(Category).where(Category.slug == category_slug)
        )
        category = cat_result.scalar_one_or_none()
        if not category:
            cat_result = await session.execute(sa_select(Category).limit(1))
            category = cat_result.scalar_one_or_none()
        if not category:
            await callback.message.answer("❌ Категории не найдены в БД")
            return

        image_url = await pick_event_image(
            title=title,
            category_slug=category.slug,
            prefilled=news_item.get("image_url"),
            slug=slug,
        )

        # Проверяем уникальность slug
        existing = await session.execute(
            sa_select(Event).where(Event.slug == slug)
        )
        if existing.scalar_one_or_none():
            slug = f"news-{slug_hash}-{int(now.timestamp())}"

        event = Event(
            slug=slug,
            title=title,
            description=description,
            image_url=image_url,
            article_url=article_url,
            category_id=category.id,
            status=EventStatus.ACTIVE,
            liquidity_b=Decimal("1000.00"),
            closes_at=closes_at,
            resolves_at=closes_at + timedelta(days=7),
            resolution_source=article_url,
        )
        session.add(event)
        await session.flush()

        for i, outcome_title in enumerate(["Да", "Нет"]):
            session.add(Outcome(
                event_id=event.id,
                title=outcome_title,
                sort_order=i,
            ))

        await session.commit()
        event_id = event.id
        logger.info(
            f"Quick event #{event_id} '{title[:60]}' created from news "
            f"(slug={slug}, image={image_url})"
        )

    await callback.message.answer(
        f"<b>✅ Событие #{event_id} опубликовано!</b>\n\n"
        f"«{title}»\n\n"
        f"📂 Категория: {category.name}\n"
        f"⏰ Закрывается: {closes_at.strftime('%d.%m.%Y')}\n\n"
        f"Чтобы разрешить: <code>/resolve {event_id}</code>",
        parse_mode="HTML",
    )


# ── Ручная проверка парсера ──────────────────────────────────────────────────

@router.message(Command("newscheck"))
async def cmd_newscheck(message: Message) -> None:
    """
    /newscheck — ручной запуск парсера новостей (игнорирует кэш).
    Показывает что нашлось прямо в чате без рассылки уведомлений.
    """
    if not is_admin(message.from_user.id):
        return

    await message.answer("🔍 Проверяю RSS-ленты...")

    from bot.services.news_service import fetch_news_suggestions
    try:
        items = await fetch_news_suggestions(ignore_cache=True)
    except Exception as e:
        await message.answer(f"❌ Ошибка парсера: {e}")
        return

    if not items:
        await message.answer(
            "📭 Подходящих новостей не найдено.\n\n"
            "<i>Возможные причины:\n"
            "• RSS-ленты временно недоступны\n"
            "• Ни один заголовок не совпал с ключевыми словами\n"
            "Проверь логи Amvera (ищи строки RSS OK / RSS error)</i>",
            parse_mode="HTML",
        )
        return

    await message.answer(f"✅ Найдено {len(items)} новостей. Отправляю...")

    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    for it in items:
        caption = (
            f"<b>{it['source']}</b>\n\n"
            f"<b>{it['title']}</b>\n"
            f"<i>Категория: {it['category']}</i>"
            + (f"\n🔗 <a href='{it['article_url']}'>Источник</a>" if it.get("article_url") else "")
            + (f"\n🖼 Фото: ✅" if it.get("image_url") else "\n🖼 Фото: ❌ не найдено")
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="➕ Создать событие",
                callback_data=f"news:addevent:{it['hash']}",
            ),
            InlineKeyboardButton(
                text="⚡ Быстро опубликовать",
                callback_data=f"news:quick:{it['hash']}",
            ),
        ]])
        try:
            if it.get("image_url"):
                await message.answer_photo(
                    photo=it["image_url"],
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=kb,
                )
            else:
                await message.answer(caption, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            # Фото не загрузилось — отправляем без него
            await message.answer(
                caption + f"\n\n⚠️ <i>Не удалось загрузить фото: {e}</i>",
                parse_mode="HTML",
                reply_markup=kb,
            )


# ── Backfill картинок для существующих событий ──────────────────────────────

@router.message(Command("updateimages"))
async def cmd_updateimages(message: Message) -> None:
    """
    /updateimages — пройтись по активным событиям и догрузить Wiki-картинки.

    Обновляет:
      - события с NULL image_url (любым результатом pick_event_image)
      - события с SVG-фоллбеком (/miniapp/images/...) — только если Wikipedia
        нашла реальное http(s) фото; иначе SVG остаётся.
    """
    if not is_admin(message.from_user.id):
        return

    await message.answer("🖼 Обновляю картинки событий... Это займёт минуту.")

    updated = 0
    skipped = 0
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Event, Category)
            .join(Category, Event.category_id == Category.id)
            .where(Event.status.in_([EventStatus.ACTIVE, EventStatus.LOCKED]))
        )
        rows = result.all()

        for event, category in rows:
            try:
                new_url = await pick_event_image(
                    title=event.title,
                    category_slug=category.slug,
                    prefilled=None,
                    slug=event.slug,
                )
            except Exception as e:
                logger.warning(f"updateimages: skip #{event.id} — {e}")
                skipped += 1
                continue

            old = event.image_url or ""
            got_real_photo = new_url.startswith(("http://", "https://"))
            # Обновляем если: было пусто, или нашли реальное фото (заменяем SVG тоже)
            if not old or (got_real_photo and new_url != old):
                event.image_url = new_url
                updated += 1
                logger.info(f"updateimages: #{event.id} → {new_url[:60]}")
            else:
                skipped += 1

        await session.commit()

    await message.answer(
        f"✅ Готово: обновлено <b>{updated}</b>, пропущено <b>{skipped}</b>.",
        parse_mode="HTML",
    )


# ── Модерация событий (/moderation) ─────────────────────────────────────────

@router.message(Command("moderation"))
async def cmd_moderation(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Event)
            .where(Event.status == EventStatus.MODERATION)
            .order_by(Event.id)
            .limit(10)
        )
        events = result.scalars().all()

    if not events:
        await message.answer("✅ Очередь модерации пуста.")
        return

    await message.answer(
        f"<b>🔍 Очередь модерации: {len(events)} событий</b>\n\n"
        "Выбери действие для каждого события.",
        parse_mode="HTML",
    )

    for ev in events:
        text = (
            f"<b>#{ev.id}</b> {ev.title[:80]}\n"
            f"<i>Фото: {'✅ есть' if ev.image_url else '❌ нет'}</i>"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="📷 Загрузить фото", callback_data=f"mod:photo:{ev.id}"),
                InlineKeyboardButton(text="🔍 Wiki-фото", callback_data=f"mod:wiki:{ev.id}"),
            ],
            [
                InlineKeyboardButton(text="✅ Опубликовать", callback_data=f"mod:publish:{ev.id}"),
                InlineKeyboardButton(text="🗑 Удалить", callback_data=f"mod:delete:{ev.id}"),
            ],
        ])
        await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("mod:photo:"))
async def mod_photo_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Только для администратора", show_alert=True)
        return

    event_id = int(callback.data.split(":")[2])
    await state.update_data(mod_event_id=event_id)
    await state.set_state(ModerationStates.waiting_photo)
    await callback.message.answer(
        f"📷 Отправь фото для события #{event_id}.\n"
        "Минимум 400×300 пикселей. Напиши /cancel чтобы отменить."
    )
    await callback.answer()


@router.message(ModerationStates.waiting_photo, Command("cancel"))
async def mod_photo_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("❌ Загрузка фото отменена.")


@router.message(ModerationStates.waiting_photo, F.photo)
async def mod_photo_upload(message: Message, state: FSMContext) -> None:
    from PIL import Image as PilImage
    import io

    data = await state.get_data()
    event_id = data.get("mod_event_id")
    if not event_id:
        await state.clear()
        return

    photo = message.photo[-1]
    bot = message.bot

    file = await bot.get_file(photo.file_id)

    # Скачиваем в память для валидации через Pillow
    buf = io.BytesIO()
    await bot.download_file(file.file_path, destination=buf)
    buf.seek(0)

    try:
        img = PilImage.open(buf)
        w, h = img.size
        if w < 400 or h < 300:
            await message.answer(
                f"⚠️ Фото слишком маленькое ({w}×{h}). Нужно минимум 400×300."
            )
            return
        aspect = w / h
        if aspect < 0.5 or aspect > 2.0:
            await message.answer(
                f"⚠️ Нестандартное соотношение сторон ({aspect:.2f}). "
                "Допустимо от 0.5 до 2.0."
            )
            return
    except Exception as e:
        await message.answer(f"⚠️ Не удалось прочитать изображение: {e}")
        return

    # Сохраняем на диск
    uploads_dir = Path("miniapp/images/uploads")
    uploads_dir.mkdir(parents=True, exist_ok=True)
    dest_path = uploads_dir / f"{event_id}.jpg"

    buf.seek(0)
    dest_path.write_bytes(buf.read())

    image_url = f"/miniapp/images/uploads/{event_id}.jpg"

    async with AsyncSessionLocal() as session:
        ev_result = await session.execute(select(Event).where(Event.id == event_id))
        event = ev_result.scalar_one_or_none()
        if not event:
            await message.answer(f"Событие #{event_id} не найдено")
            await state.clear()
            return

        event.image_url = image_url
        event.status = EventStatus.ACTIVE
        await session.commit()

    await state.clear()
    await message.answer(
        f"✅ Фото загружено, событие #{event_id} опубликовано!\n"
        f"URL: <code>{image_url}</code>",
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("mod:wiki:"))
async def mod_wiki_search(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Только для администратора", show_alert=True)
        return

    event_id = int(callback.data.split(":")[2])

    async with AsyncSessionLocal() as session:
        ev_result = await session.execute(select(Event).where(Event.id == event_id))
        event = ev_result.scalar_one_or_none()

    if not event:
        await callback.answer("Событие не найдено", show_alert=True)
        return

    await callback.answer("🔍 Ищу Wikipedia...")

    async with AsyncSessionLocal() as session:
        cat_result = await session.execute(
            select(Category).where(Category.id == event.category_id)
        )
        cat = cat_result.scalar_one_or_none()
        cat_slug = cat.slug if cat else None

    wiki_url = await pick_event_image(
        title=event.title,
        category_slug=cat_slug,
        prefilled=None,
        slug=event.slug,
        strict=False,
    )

    if not wiki_url or "/miniapp/" in wiki_url:
        await callback.message.answer(
            f"❌ Wikipedia не нашла подходящего фото для события #{event_id}.\n"
            "Попробуй загрузить фото вручную."
        )
        return

    await state.update_data(mod_event_id=event_id, wiki_url=wiki_url)
    await state.set_state(ModerationStates.wiki_preview)

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="✅ Использовать это фото",
            callback_data=f"mod:wiki_confirm:{event_id}",
        ),
        InlineKeyboardButton(text="❌ Отмена", callback_data="mod:wiki_cancel"),
    ]])

    try:
        await callback.message.answer_photo(
            photo=wiki_url,
            caption=f"Найдено фото с Wikipedia для #{event_id}.\nПодтвердить?",
            reply_markup=kb,
        )
    except Exception:
        await callback.message.answer(
            f"Найдено фото: {wiki_url}\nПодтвердить?",
            reply_markup=kb,
        )


@router.callback_query(F.data.startswith("mod:wiki_confirm:"), ModerationStates.wiki_preview)
async def mod_wiki_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Только для администратора", show_alert=True)
        return

    data = await state.get_data()
    event_id = data.get("mod_event_id")
    wiki_url = data.get("wiki_url")

    await state.clear()

    if not event_id or not wiki_url:
        await callback.answer("Данные устарели", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        ev_result = await session.execute(select(Event).where(Event.id == event_id))
        event = ev_result.scalar_one_or_none()
        if not event:
            await callback.answer("Событие не найдено", show_alert=True)
            return

        event.image_url = wiki_url
        event.status = EventStatus.ACTIVE
        await session.commit()

    await callback.message.edit_caption(
        caption=f"✅ Фото установлено, событие #{event_id} опубликовано!"
    )
    await callback.answer()


@router.callback_query(F.data == "mod:wiki_cancel")
async def mod_wiki_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.delete()
    await callback.answer("Отменено")


@router.callback_query(F.data.startswith("mod:publish:"))
async def mod_publish(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Только для администратора", show_alert=True)
        return

    event_id = int(callback.data.split(":")[2])

    async with AsyncSessionLocal() as session:
        ev_result = await session.execute(select(Event).where(Event.id == event_id))
        event = ev_result.scalar_one_or_none()
        if not event:
            await callback.answer("Событие не найдено", show_alert=True)
            return

        event.status = EventStatus.ACTIVE
        await session.commit()

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        f"✅ Событие #{event_id} опубликовано без фото."
    )
    await callback.answer()


@router.callback_query(F.data.startswith("mod:delete:"))
async def mod_delete(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Только для администратора", show_alert=True)
        return

    event_id = int(callback.data.split(":")[2])

    async with AsyncSessionLocal() as session:
        ev_result = await session.execute(select(Event).where(Event.id == event_id))
        event = ev_result.scalar_one_or_none()
        if not event:
            await callback.answer("Событие не найдено", show_alert=True)
            return

        event.status = EventStatus.CANCELLED
        await session.commit()

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(f"🗑 Событие #{event_id} отменено.")
    await callback.answer()


# ── Остальные команды ────────────────────────────────────────────────────────

@router.message(Command("admin"))
async def admin_menu(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    text = (
        "<b>⚙️ Админ-панель</b>\n\n"
        "Доступные команды:\n"
        "• /events — список активных событий\n"
        "• /moderation — очередь событий без фото\n"
        "• /addevent — создать новое событие\n"
        "• /newscheck — проверить RSS прямо сейчас\n"
        "• /updateimages — обновить картинки событий (Wiki + SVG)\n"
        "• /resolve &lt;event_id&gt; — разрешить событие\n"
        "• /cancel_event &lt;event_id&gt; — отменить с возвратом\n"
        "• /stats — статистика платформы\n"
        "• /grant &lt;user_id&gt; &lt;amount&gt; — начислить пользователю\n"
        "• /withdrawals — заявки на вывод (одобрить/отклонить)\n"
    )
    await message.answer(text, parse_mode="HTML")


@router.message(Command("events"))
async def list_events(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Event).where(
                Event.status.in_([EventStatus.ACTIVE, EventStatus.LOCKED, EventStatus.MODERATION])
            ).order_by(Event.closes_at)
        )
        events = result.scalars().all()

    if not events:
        await message.answer("Нет активных событий.")
        return

    lines = ["<b>📋 Активные события:</b>\n"]
    for ev in events:
        # Считаем объём
        async with AsyncSessionLocal() as session:
            vol_result = await session.execute(
                select(func.coalesce(func.sum(Bet.amount_rub), 0))
                .where(Bet.event_id == ev.id)
            )
            volume = vol_result.scalar() or Decimal("0")

        status_badge = ""
        if ev.status == EventStatus.MODERATION:
            status_badge = " 🔍"
        elif ev.status == EventStatus.LOCKED:
            status_badge = " 🔒"
        lines.append(
            f"<b>#{ev.id}</b>{status_badge} {ev.title[:60]}\n"
            f"  📊 объём: {volume:.0f} ₽ • до {ev.closes_at:%d.%m %H:%M}\n"
        )

    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("resolve"))
async def cmd_resolve(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "Использование: <code>/resolve event_id</code>\n"
            "Например: <code>/resolve 5</code>",
            parse_mode="HTML",
        )
        return

    try:
        event_id = int(args[1])
    except ValueError:
        await message.answer("event_id должен быть числом")
        return

    # Показываем кнопки для выбора победителя
    async with AsyncSessionLocal() as session:
        ev_result = await session.execute(select(Event).where(Event.id == event_id))
        event = ev_result.scalar_one_or_none()
        if not event:
            await message.answer(f"Событие #{event_id} не найдено")
            return

        if event.status == EventStatus.RESOLVED:
            await message.answer("⚠️ Это событие уже разрешено")
            return

        out_result = await session.execute(
            select(Outcome).where(Outcome.event_id == event_id)
            .order_by(Outcome.sort_order)
        )
        outcomes = out_result.scalars().all()

    keyboard = []
    for outcome in outcomes:
        keyboard.append([InlineKeyboardButton(
            text=f"✅ {outcome.title}",
            callback_data=f"adm:resolve:{event_id}:{outcome.id}",
        )])
    keyboard.append([InlineKeyboardButton(
        text="❌ Отмена",
        callback_data="adm:cancel_resolve",
    )])

    await message.answer(
        f"<b>🎯 Выбери победителя для:</b>\n\n«{event.title}»",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("adm:resolve:"))
async def confirm_resolve(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return

    parts = callback.data.split(":")
    event_id = int(parts[2])
    outcome_id = int(parts[3])

    await callback.message.edit_text("⏳ Провожу выплаты...")

    try:
        async with AsyncSessionLocal() as session:
            summary = await resolve_event(session, event_id, outcome_id)
    except Exception as e:
        await callback.message.edit_text(f"❌ Ошибка: {e}")
        return

    text = (
        f"<b>✅ Событие #{event_id} разрешено</b>\n\n"
        f"Победил: <b>{summary['winning_outcome']}</b>\n\n"
        f"📊 Статистика:\n"
        f"• Выигравших: <b>{summary['winners_count']}</b>\n"
        f"• Проигравших: <b>{summary['losers_count']}</b>\n"
        f"• Выплачено: <b>{summary['total_payout']:.2f} ₽</b>\n"
        f"• Комиссия: <b>{summary['fees_collected']:.2f} ₽</b>"
    )
    await callback.message.edit_text(text, parse_mode="HTML")


@router.callback_query(F.data == "adm:cancel_resolve")
async def cancel_resolve(callback: CallbackQuery) -> None:
    await callback.message.delete()


@router.message(Command("cancel_event"))
async def cmd_cancel_event(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    args = message.text.split(maxsplit=2)
    if len(args) < 2:
        await message.answer(
            "Использование: <code>/cancel_event event_id [причина]</code>",
            parse_mode="HTML",
        )
        return

    try:
        event_id = int(args[1])
    except ValueError:
        await message.answer("event_id должен быть числом")
        return

    reason = args[2] if len(args) > 2 else "Отменено администратором"

    try:
        async with AsyncSessionLocal() as session:
            summary = await cancel_event(session, event_id, reason)
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
        return

    await message.answer(
        f"<b>↩️ Событие отменено</b>\n\n"
        f"Возвратов: <b>{summary['refunded_count']}</b>\n"
        f"Сумма возврата: <b>{summary['total_refund']:.2f} ₽</b>",
        parse_mode="HTML",
    )


@router.message(Command("stats"))
async def show_stats(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    async with AsyncSessionLocal() as session:
        users_count = (await session.execute(select(func.count(User.id)))).scalar() or 0
        active_events = (await session.execute(
            select(func.count(Event.id)).where(Event.status == EventStatus.ACTIVE)
        )).scalar() or 0
        total_bets = (await session.execute(select(func.count(Bet.id)))).scalar() or 0
        total_volume = (await session.execute(
            select(func.coalesce(func.sum(Bet.amount_rub), 0))
        )).scalar() or Decimal("0")
        total_fees = (await session.execute(
            select(func.coalesce(func.sum(Transaction.amount_rub), 0))
            .where(Transaction.type == TransactionType.FEE)
        )).scalar() or Decimal("0")

    await message.answer(
        f"<b>📊 Статистика платформы</b>\n\n"
        f"👥 Пользователей: <b>{users_count}</b>\n"
        f"🎯 Активных событий: <b>{active_events}</b>\n"
        f"📈 Всего ставок: <b>{total_bets}</b>\n"
        f"💰 Общий объём: <b>{total_volume:.2f} ₽</b>\n"
        f"💼 Заработано комиссии: <b>{total_fees:.2f} ₽</b>",
        parse_mode="HTML",
    )


@router.message(Command("grant"))
async def cmd_grant(message: Message) -> None:
    """Начислить пользователю средства (например, для бонуса или компенсации)."""
    if not is_admin(message.from_user.id):
        return

    args = message.text.split()
    if len(args) < 3:
        await message.answer(
            "Использование: <code>/grant telegram_id amount</code>\n"
            "Пример: <code>/grant 123456789 500</code>",
            parse_mode="HTML",
        )
        return

    try:
        target_tg_id = int(args[1])
        amount = Decimal(args[2])
    except (ValueError, Exception):
        await message.answer("⚠️ Некорректные параметры")
        return

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == target_tg_id)
        )
        user = result.scalar_one_or_none()
        if not user:
            await message.answer(f"Пользователь {target_tg_id} не найден")
            return

        balance_before = user.balance_rub
        user.balance_rub += amount
        balance_after = user.balance_rub

        session.add(Transaction(
            user_id=user.id,
            type=TransactionType.BONUS,
            amount_rub=amount,
            balance_before=balance_before,
            balance_after=balance_after,
            description=f"Начисление от админа",
        ))
        await session.commit()

    await message.answer(
        f"✅ Зачислено <b>{amount:.2f} ₽</b> пользователю {target_tg_id}\n"
        f"Новый баланс: <b>{balance_after:.2f} ₽</b>",
        parse_mode="HTML",
    )


# ── Управление заявками на вывод ────────────────────────────────────────────

@router.message(Command("withdrawals"))
async def cmd_withdrawals(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    from db.models import WithdrawalRequest, WithdrawStatus
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(WithdrawalRequest, User)
            .join(User, WithdrawalRequest.user_id == User.id)
            .where(WithdrawalRequest.status == WithdrawStatus.PENDING)
            .order_by(WithdrawalRequest.created_at)
            .limit(20)
        )
        rows = result.all()

    if not rows:
        await message.answer("✅ Нет ожидающих заявок на вывод.")
        return

    await message.answer(f"💸 Заявок на вывод: <b>{len(rows)}</b>", parse_mode="HTML")
    for wr, user in rows:
        text = (
            f"<b>Заявка #{wr.id}</b>\n"
            f"👤 @{user.username or user.first_name} (tg:{user.telegram_id})\n"
            f"💰 Сумма: <b>{wr.amount_coins:,.0f} монет</b>\n"
            f"🌐 Сеть: <b>{wr.network.upper()}</b>\n"
            f"📋 Кошелёк: <code>{wr.wallet_address}</code>\n"
            f"🕐 {wr.created_at.strftime('%d.%m.%Y %H:%M')}"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Выплатил", callback_data=f"withdraw:approve:{wr.id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"withdraw:reject:{wr.id}"),
        ]])
        await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.regexp(r"^(withdraw|wd):approve:\d+$"))
async def approve_withdrawal(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Только для администратора", show_alert=True)
        return

    wr_id = int(callback.data.split(":")[2])
    from bot.services.withdrawal_service import admin_approve_withdrawal

    try:
        async with AsyncSessionLocal() as session:
            await admin_approve_withdrawal(session, wr_id)
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            f"✅ Заявка #{wr_id} отмечена как <b>выплачена</b>. Пользователь уведомлён.",
            parse_mode="HTML",
        )
    except ValueError as e:
        await callback.answer(str(e), show_alert=True)
    await callback.answer()


@router.callback_query(F.data.regexp(r"^(withdraw|wd):reject:\d+$"))
async def reject_withdrawal(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Только для администратора", show_alert=True)
        return

    wr_id = int(callback.data.split(":")[2])
    await state.update_data(reject_wd_id=wr_id)
    await state.set_state("wd_reject_reason")
    await callback.message.answer(
        f"Введи причину отклонения заявки #{wr_id} (отправится пользователю):"
    )
    await callback.answer()


@router.message(StateFilter("wd_reject_reason"))
async def reject_withdrawal_reason(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    wr_id = data.get("reject_wd_id")
    if not wr_id:
        await state.clear()
        return
    reason = message.text.strip() if message.text else "Без причины"
    await state.clear()

    from bot.services.withdrawal_service import admin_reject_withdrawal
    try:
        async with AsyncSessionLocal() as session:
            await admin_reject_withdrawal(session, wr_id, reason)
        await message.answer(
            f"❌ Заявка #{wr_id} <b>отклонена</b>. Монеты возвращены, пользователь уведомлён.",
            parse_mode="HTML",
        )
    except ValueError as e:
        await message.answer(f"Ошибка: {e}")
