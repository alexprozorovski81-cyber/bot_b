"""Обработчики пополнения баланса."""
import logging
from decimal import Decimal, InvalidOperation

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery
from sqlalchemy import select

from bot import keyboards as kb
from bot import texts
from bot.config import settings
from bot.services.payment_service import (
    create_card_payment, check_card_payment,
    create_usdt_payment, check_usdt_arrival,
    USDT_TO_RUB_RATE,
)
from bot.services.user_service import get_or_create_user
from db.database import AsyncSessionLocal
from db.models import (
    Payment, PaymentMethod, User,
    Transaction, TransactionType,
)


logger = logging.getLogger(__name__)
router = Router()


class DepositStates(StatesGroup):
    waiting_amount = State()


@router.message(F.text == texts.BTN_DEPOSIT)
async def deposit_menu(message: Message) -> None:
    async with AsyncSessionLocal() as session:
        user, _ = await get_or_create_user(
            session, message.from_user.id,
            message.from_user.username,
            message.from_user.first_name,
        )
        balance = user.balance_rub

    await message.answer(
        texts.DEPOSIT_MENU.format(
            balance=f"{balance:.2f}",
            min_rub=f"{settings.min_deposit_rub:.0f}",
            min_usdt=f"{settings.min_deposit_usdt:.0f}",
        ),
        reply_markup=kb.deposit_methods_kb(),
        parse_mode="HTML",
    )


# YooKassa-обработчики отключены — используется только USDT/TON.
# Оставлены как заглушки чтобы не падал при старых callback_data в истории.

@router.callback_query(F.data == "dep:card")
async def deposit_card_disabled(callback: CallbackQuery) -> None:
    await callback.answer(
        "Оплата картой временно недоступна. Используй USDT (TON).",
        show_alert=True,
    )


@router.callback_query(F.data == "dep:card")
async def deposit_card_amounts(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "💳 <b>Пополнение картой / СБП</b>\n\nВыбери сумму:",
        reply_markup=kb.deposit_amount_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("dep:amount:"))
async def deposit_card_create(callback: CallbackQuery) -> None:
    amount = Decimal(callback.data.split(":")[2])

    async with AsyncSessionLocal() as session:
        user, _ = await get_or_create_user(
            session, callback.from_user.id,
            callback.from_user.username,
            callback.from_user.first_name,
        )

        if not (settings.yookassa_shop_id and settings.yookassa_secret_key):
            await callback.answer(
                "ЮKassa не настроена. Обратись к администратору.",
                show_alert=True,
            )
            return

        try:
            payment, pay_url = await create_card_payment(session, user, amount)
        except Exception as e:
            logger.exception("Ошибка создания платежа")
            await callback.answer(f"Ошибка: {e}", show_alert=True)
            return

    await callback.message.edit_text(
        texts.DEPOSIT_LINK.format(
            amount=f"{amount:.2f}",
            method="Карта / СБП",
        ),
        reply_markup=kb.payment_link_kb(pay_url, str(payment.id)),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "dep:custom_amount")
async def deposit_custom_amount(
    callback: CallbackQuery, state: FSMContext
) -> None:
    await state.set_state(DepositStates.waiting_amount)
    await callback.message.edit_text(
        texts.DEPOSIT_AMOUNT_PROMPT.format(
            min_amount=f"{settings.min_deposit_rub:.0f} ₽",
        ),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(DepositStates.waiting_amount)
async def deposit_custom_amount_received(
    message: Message, state: FSMContext
) -> None:
    try:
        amount = Decimal(message.text.replace(",", ".").strip())
    except (InvalidOperation, AttributeError):
        await message.answer("⚠️ Введи число, например: <code>1500</code>", parse_mode="HTML")
        return

    if amount < settings.min_deposit_rub:
        await message.answer(
            f"⚠️ Минимальная сумма: {settings.min_deposit_rub} ₽"
        )
        return
    if amount > Decimal("500000"):
        await message.answer("⚠️ Максимум за раз — 500 000 ₽")
        return

    await state.clear()

    async with AsyncSessionLocal() as session:
        user, _ = await get_or_create_user(
            session, message.from_user.id,
            message.from_user.username,
            message.from_user.first_name,
        )
        try:
            payment, pay_url = await create_card_payment(session, user, amount)
        except Exception as e:
            logger.exception("Ошибка создания платежа")
            await message.answer(f"⚠️ Ошибка: {e}")
            return

    await message.answer(
        texts.DEPOSIT_LINK.format(
            amount=f"{amount:.2f}",
            method="Карта / СБП",
        ),
        reply_markup=kb.payment_link_kb(pay_url, str(payment.id)),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("dep:check:"))
async def check_deposit(callback: CallbackQuery) -> None:
    """Проверка платежа по запросу пользователя."""
    payment_id = int(callback.data.split(":")[2])

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Payment).where(Payment.id == payment_id))
        payment = result.scalar_one_or_none()

        if not payment:
            await callback.answer("Платёж не найден", show_alert=True)
            return

        if payment.status == "succeeded":
            await callback.answer("Уже зачислено!", show_alert=True)
            return

        new_status = await check_card_payment(session, payment)

        if new_status == "succeeded":
            # Зачисляем баланс
            user_result = await session.execute(
                select(User).where(User.id == payment.user_id)
            )
            user = user_result.scalar_one()

            balance_before = user.balance_rub
            user.balance_rub += payment.amount_rub
            balance_after = user.balance_rub

            tx = Transaction(
                user_id=user.id,
                type=TransactionType.DEPOSIT,
                amount_rub=payment.amount_rub,
                balance_before=balance_before,
                balance_after=balance_after,
                payment_id=payment.id,
                description="Пополнение картой",
            )
            session.add(tx)
            await session.commit()

            await callback.message.edit_text(
                texts.DEPOSIT_SUCCESS.format(
                    amount=f"{payment.amount_rub:.2f}",
                    balance=f"{balance_after:.2f}",
                ),
                reply_markup=kb.back_to_menu_kb(),
                parse_mode="HTML",
            )
            file_id = texts.STICKERS.get("deposit_success")
            if file_id:
                try:
                    await callback.message.answer_sticker(file_id)
                except Exception:
                    pass
        elif new_status == "canceled":
            await callback.answer("Платёж отменён", show_alert=True)
        else:
            await callback.answer(
                "Платёж ещё не завершён. Попробуй через минуту.",
                show_alert=True,
            )


@router.callback_query(F.data == "dep:usdt")
async def deposit_usdt(callback: CallbackQuery) -> None:
    if not settings.usdt_wallet_address:
        await callback.answer(
            "USDT не настроен. Используй карту или СБП.",
            show_alert=True,
        )
        return

    async with AsyncSessionLocal() as session:
        user, _ = await get_or_create_user(
            session, callback.from_user.id,
            callback.from_user.username,
            callback.from_user.first_name,
        )
        # Создаём pending-платёж на 10 USDT по умолчанию,
        # пользователь может прислать любую сумму >= минимума
        payment = await create_usdt_payment(
            session, user, Decimal("10")
        )

    await callback.message.edit_text(
        texts.DEPOSIT_USDT.format(
            address=settings.usdt_wallet_address,
            min_usdt=f"{settings.min_deposit_usdt:.0f}",
            rate=f"{USDT_TO_RUB_RATE:.0f}",
        ),
        reply_markup=kb.usdt_sent_kb(str(payment.id)),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("dep:usdt_sent:"))
async def check_usdt(callback: CallbackQuery) -> None:
    payment_id = int(callback.data.split(":")[2])

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Payment).where(Payment.id == payment_id))
        payment = result.scalar_one_or_none()

        if not payment:
            await callback.answer("Платёж не найден", show_alert=True)
            return

        await callback.answer("🔍 Проверяю транзакцию...", show_alert=False)

        found = await check_usdt_arrival(session, payment)
        if not found:
            await callback.answer(
                "⏳ Транзакция ещё не пришла. Попробуй через 1–2 минуты.",
                show_alert=True,
            )
            return

        # Зачислить баланс
        user_result = await session.execute(
            select(User).where(User.id == payment.user_id)
        )
        user = user_result.scalar_one()

        balance_before = user.balance_rub
        user.balance_rub += payment.amount_rub
        balance_after = user.balance_rub

        tx = Transaction(
            user_id=user.id,
            type=TransactionType.DEPOSIT,
            amount_rub=payment.amount_rub,
            balance_before=balance_before,
            balance_after=balance_after,
            payment_id=payment.id,
            description="Пополнение USDT",
        )
        session.add(tx)
        await session.commit()

        await callback.message.edit_text(
            texts.DEPOSIT_SUCCESS.format(
                amount=f"{payment.amount_rub:.2f}",
                balance=f"{balance_after:.2f}",
            ),
            reply_markup=kb.back_to_menu_kb(),
            parse_mode="HTML",
        )


@router.callback_query(F.data == "dep:cancel")
@router.callback_query(F.data == "dep:back")
async def deposit_cancel(callback: CallbackQuery) -> None:
    await callback.message.delete()
    await callback.answer()
