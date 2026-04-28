"""
Модели базы данных для PredictBet.
Используется SQLAlchemy 2.0 с async поддержкой.
"""
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum as PyEnum

from sqlalchemy import (
    BigInteger, Boolean, DateTime, ForeignKey, Numeric,
    String, Text, Enum, Index
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class EventStatus(str, PyEnum):
    """Статус события на платформе."""
    DRAFT = "draft"          # Черновик (не опубликован)
    ACTIVE = "active"        # Можно ставить
    LOCKED = "locked"        # Ставки закрыты, ждём результата
    RESOLVED = "resolved"    # Результат определён, выплаты произведены
    CANCELLED = "cancelled"  # Отменено, средства возвращены


class TransactionType(str, PyEnum):
    """Тип транзакции по балансу пользователя."""
    DEPOSIT = "deposit"          # Пополнение (карта/USDT)
    WITHDRAW = "withdraw"        # Вывод
    BET_PLACE = "bet_place"      # Списание при ставке
    BET_PAYOUT = "bet_payout"    # Выплата выигрыша
    BET_REFUND = "bet_refund"    # Возврат при отмене события
    FEE = "fee"                  # Комиссия платформы
    BONUS = "bonus"              # Бонус (welcome, рефералка)


class PaymentMethod(str, PyEnum):
    """Способ оплаты."""
    YOOKASSA_CARD = "yookassa_card"
    YOOKASSA_SBP = "yookassa_sbp"
    USDT_TON = "usdt_ton"
    STARS = "stars"
    ETH = "eth"
    BTC = "btc"
    SOL = "sol"


class WithdrawStatus(str, PyEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    PAID = "paid"


class AchievementCondition(str, PyEnum):
    FIRST_BET      = "first_bet"
    BETS_COUNT     = "bets_count"
    WIN_COUNT      = "win_count"
    VOLUME_TOTAL   = "volume_total"
    DEPOSIT_FIRST  = "deposit_first"
    PERFECT_STREAK = "perfect_streak"
    COMMENT_FIRST  = "comment_first"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(128))

    # Балансы хранятся в копейках (для рублей) и микро-USDT (для крипты).
    # Используем Numeric — это safe для денег, в отличие от float.
    balance_rub: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), default=Decimal("0.00"), nullable=False
    )

    # Реферальная система
    referrer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    referral_code: Mapped[str] = mapped_column(String(16), unique=True)

    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    # Связи
    bets: Mapped[list["Bet"]] = relationship(back_populates="user")
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="user")


class Category(Base):
    """Категории событий: Политика, Крипта, Спорт, Технологии, Развлечения и т.д."""
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(64))
    emoji: Mapped[str] = mapped_column(String(8))  # Эмодзи категории
    sort_order: Mapped[int] = mapped_column(default=0)

    events: Mapped[list["Event"]] = relationship(back_populates="category")


class Event(Base):
    """Событие на платформе. Например: 'Выиграет ли Реал Лигу Чемпионов 2026?'"""
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(256))
    description: Mapped[str] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(String(512))

    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"))

    status: Mapped[EventStatus] = mapped_column(
        Enum(EventStatus), default=EventStatus.DRAFT, index=True
    )

    # LMSR параметр ликвидности. Чем больше — тем стабильнее цены.
    liquidity_b: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), default=Decimal("1000.00")
    )

    # Когда закрывается приём ставок и когда нужно разрешить событие
    closes_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    resolves_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # ID победившего исхода (заполняется при разрешении)
    winning_outcome_id: Mapped[int | None] = mapped_column(ForeignKey("outcomes.id"))

    # Источник правды для разрешения (URL, описание)
    resolution_source: Mapped[str | None] = mapped_column(Text)

    # URL статьи-новости, из которой создано событие
    article_url: Mapped[str | None] = mapped_column(String(512))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    category: Mapped["Category"] = relationship(back_populates="events")
    outcomes: Mapped[list["Outcome"]] = relationship(
        back_populates="event",
        foreign_keys="Outcome.event_id",
    )
    bets: Mapped[list["Bet"]] = relationship(back_populates="event")


class Outcome(Base):
    """
    Возможный исход события.
    Для бинарного рынка: 2 outcome (YES/NO).
    Для категориального: N outcome (например, кандидаты на выборах).
    """
    __tablename__ = "outcomes"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    title: Mapped[str] = mapped_column(String(128))  # "Да", "Нет", "Кандидат А"

    # Сколько акций этого исхода уже куплено (q в формуле LMSR)
    shares_outstanding: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), default=Decimal("0.0000")
    )

    sort_order: Mapped[int] = mapped_column(default=0)

    event: Mapped["Event"] = relationship(
        back_populates="outcomes",
        foreign_keys=[event_id],
    )
    bets: Mapped[list["Bet"]] = relationship(back_populates="outcome")


class Bet(Base):
    """Ставка пользователя на конкретный исход."""
    __tablename__ = "bets"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    outcome_id: Mapped[int] = mapped_column(ForeignKey("outcomes.id"))

    # Сколько денег пользователь вложил
    amount_rub: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    # Сколько акций он получил
    shares: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    # Средний коэффициент по этой ставке (для отображения)
    avg_odds: Mapped[Decimal] = mapped_column(Numeric(10, 4))

    # Уже разрешена? (выплата произведена)
    is_settled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    payout_rub: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    user: Mapped["User"] = relationship(back_populates="bets")
    event: Mapped["Event"] = relationship(back_populates="bets")
    outcome: Mapped["Outcome"] = relationship(back_populates="bets")


class Transaction(Base):
    """Все движения по балансу пользователя — для аудита и истории."""
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    type: Mapped[TransactionType] = mapped_column(Enum(TransactionType))
    amount_rub: Mapped[Decimal] = mapped_column(Numeric(18, 2))

    # Баланс ДО и ПОСЛЕ — критично для аудита
    balance_before: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    balance_after: Mapped[Decimal] = mapped_column(Numeric(18, 2))

    # Связанные сущности
    bet_id: Mapped[int | None] = mapped_column(ForeignKey("bets.id"))
    payment_id: Mapped[int | None] = mapped_column(ForeignKey("payments.id"))

    description: Mapped[str | None] = mapped_column(String(256))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )

    user: Mapped["User"] = relationship(back_populates="transactions")


class Payment(Base):
    """Платёж — пополнение или вывод средств."""
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    method: Mapped[PaymentMethod] = mapped_column(Enum(PaymentMethod))
    amount_rub: Mapped[Decimal] = mapped_column(Numeric(18, 2))

    # Внешний ID в платёжной системе (ЮKassa payment_id или TX hash в TON)
    external_id: Mapped[str | None] = mapped_column(String(128), index=True)

    # pending / succeeded / failed / cancelled
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)

    is_deposit: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WithdrawalRequest(Base):
    """Заявка на вывод выигрыша пользователем."""
    __tablename__ = "withdrawals"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    amount_coins: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    # Сумма в USDT и курс на момент создания (для авто-вывода)
    amount_usdt: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    rate_at_request: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    network: Mapped[str] = mapped_column(String(32))
    wallet_address: Mapped[str] = mapped_column(String(256))
    status: Mapped[WithdrawStatus] = mapped_column(
        Enum(WithdrawStatus), default=WithdrawStatus.PENDING, index=True
    )
    # TX hash в блокчейне после выплаты
    tx_hash: Mapped[str | None] = mapped_column(String(128))
    # Комиссия газа списанная с пользователя (в монетах)
    fee_coins: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    # True если вывод выполнен автоматически (TON SDK), False = ручной
    is_auto: Mapped[bool] = mapped_column(Boolean, default=False)
    # Счётчик попыток автовывода
    retry_count: Mapped[int] = mapped_column(default=0)
    admin_note: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped["User"] = relationship()


class Achievement(Base):
    """Ачивка — достижение на платформе."""
    __tablename__ = "achievements"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    emoji: Mapped[str] = mapped_column(String(8))
    description: Mapped[str] = mapped_column(String(256))
    condition_type: Mapped[AchievementCondition] = mapped_column(Enum(AchievementCondition))
    condition_value: Mapped[int] = mapped_column(default=1)
    rarity: Mapped[str] = mapped_column(String(16), default="common")


class UserAchievement(Base):
    """Связь пользователь ↔ полученная ачивка."""
    __tablename__ = "user_achievements"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    achievement_id: Mapped[int] = mapped_column(ForeignKey("achievements.id"), index=True)
    unlocked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    achievement: Mapped["Achievement"] = relationship()
    user: Mapped["User"] = relationship()


class Comment(Base):
    """Комментарий к событию — только от участников ставки."""
    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    text: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )

    event: Mapped["Event"] = relationship()
    user: Mapped["User"] = relationship()


# Дополнительные индексы для производительности
Index("ix_bets_user_event", Bet.user_id, Bet.event_id)
Index("ix_events_status_closes", Event.status, Event.closes_at)
