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
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton,
    InlineKeyboardMarkup, Message,
)
from sqlalchemy import func, select

from bot.config import settings
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

    async with AsyncSessionLocal() as session:
        event = Event(
            slug=slug,
            title=data["title"],
            description=data["title"],
            image_url=data.get("prefill_image_url"),   # фото из новости
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


# ── Остальные команды ────────────────────────────────────────────────────────

@router.message(Command("admin"))
async def admin_menu(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    text = (
        "<b>⚙️ Админ-панель</b>\n\n"
        "Доступные команды:\n"
        "• /events — список активных событий\n"
        "• /addevent — создать новое событие\n"
        "• /newscheck — проверить RSS прямо сейчас\n"
        "• /resolve &lt;event_id&gt; — разрешить событие\n"
        "• /cancel_event &lt;event_id&gt; — отменить с возвратом\n"
        "• /stats — статистика платформы\n"
        "• /grant &lt;user_id&gt; &lt;amount&gt; — начислить пользователю\n"
    )
    await message.answer(text, parse_mode="HTML")


@router.message(Command("events"))
async def list_events(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Event).where(
                Event.status.in_([EventStatus.ACTIVE, EventStatus.LOCKED])
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

        lines.append(
            f"<b>#{ev.id}</b> {ev.title[:60]}\n"
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
