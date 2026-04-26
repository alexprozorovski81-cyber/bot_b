"""
FastAPI приложение — обслуживает Mini App.

Аутентификация через Telegram WebApp initData (HMAC-SHA256).
"""
import hashlib
import hmac
import json
import logging
from decimal import Decimal
from typing import Annotated
from urllib.parse import parse_qsl

from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from bot.config import settings
from bot.handlers.webhooks import router as webhooks_router
from bot.services import market_engine
from bot.services.bet_service import quote_bet, place_bet, BetError
from bot.services.user_service import get_or_create_user, get_user_stats
from db.database import AsyncSessionLocal
from db.models import (
    Category, Event, EventStatus, Outcome, Bet, User,
)


logger = logging.getLogger(__name__)
app = FastAPI(title="PredictBet API")
app.include_router(webhooks_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ----- Аутентификация через Telegram WebApp -----

def validate_init_data(init_data: str) -> dict:
    """
    Валидирует initData от Telegram WebApp по HMAC-SHA256.
    Документация: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app

    Возвращает данные пользователя из initData.
    """
    try:
        parsed = dict(parse_qsl(init_data))
        received_hash = parsed.pop("hash", None)
        if not received_hash:
            raise ValueError("hash отсутствует")

        # Сортируем оставшиеся параметры и собираем data_check_string
        data_check = "\n".join(
            f"{k}={v}" for k, v in sorted(parsed.items())
        )

        # secret_key = HMAC_SHA256(bot_token, "WebAppData")
        secret_key = hmac.new(
            b"WebAppData",
            settings.bot_token.encode(),
            hashlib.sha256,
        ).digest()

        calculated_hash = hmac.new(
            secret_key,
            data_check.encode(),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(calculated_hash, received_hash):
            raise ValueError("Невалидный hash")

        user_json = parsed.get("user")
        if not user_json:
            raise ValueError("user отсутствует")

        return json.loads(user_json)

    except Exception as e:
        logger.warning(f"initData validation failed: {e}")
        raise HTTPException(status_code=401, detail="Invalid initData")


async def get_current_user(
    x_telegram_init_data: Annotated[str | None, Header(alias="X-Telegram-Init-Data")] = None,
) -> dict:
    """Зависимость FastAPI — текущий пользователь из заголовка."""
    if settings.dev_mode and not x_telegram_init_data:
        return {"id": settings.dev_telegram_id, "first_name": "Dev", "username": "dev"}
    if not x_telegram_init_data:
        raise HTTPException(status_code=401, detail="Invalid initData")
    return validate_init_data(x_telegram_init_data)


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


# ----- Эндпоинты -----

@app.get("/api/me")
async def me(
    tg_user: Annotated[dict, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """Информация о текущем пользователе."""
    user, _ = await get_or_create_user(
        session,
        telegram_id=tg_user["id"],
        username=tg_user.get("username"),
        first_name=tg_user.get("first_name"),
    )
    stats = await get_user_stats(session, user)
    return {
        "id": user.id,
        "telegram_id": user.telegram_id,
        "username": user.username,
        "first_name": user.first_name,
        "balance_rub": float(user.balance_rub),
        "stats": stats,
    }


@app.get("/api/categories")
async def list_categories(session: Annotated[AsyncSession, Depends(get_db)]):
    """Список категорий."""
    result = await session.execute(
        select(Category).order_by(Category.sort_order)
    )
    cats = result.scalars().all()
    return [
        {"id": c.id, "slug": c.slug, "name": c.name, "emoji": c.emoji}
        for c in cats
    ]


@app.get("/api/events")
async def list_events(
    session: Annotated[AsyncSession, Depends(get_db)],
    category: str | None = None,
    limit: int = 30,
):
    """Список активных событий с текущими коэффициентами."""
    query = select(Event).where(Event.status == EventStatus.ACTIVE)
    if category:
        cat_result = await session.execute(
            select(Category).where(Category.slug == category)
        )
        cat = cat_result.scalar_one_or_none()
        if cat:
            query = query.where(Event.category_id == cat.id)

    query = query.order_by(Event.closes_at).limit(limit)
    result = await session.execute(query)
    events = result.scalars().all()

    response = []
    for event in events:
        outcomes_result = await session.execute(
            select(Outcome).where(Outcome.event_id == event.id)
            .order_by(Outcome.sort_order)
        )
        outcomes = outcomes_result.scalars().all()

        q = [o.shares_outstanding for o in outcomes]
        b = event.liquidity_b
        prices = market_engine.get_prices(q, b) if outcomes else []
        odds = market_engine.get_odds(q, b) if outcomes else []

        response.append({
            "id": event.id,
            "slug": event.slug,
            "title": event.title,
            "description": event.description,
            "image_url": event.image_url,
            "closes_at": event.closes_at.isoformat(),
            "category_id": event.category_id,
            "outcomes": [
                {
                    "id": o.id,
                    "title": o.title,
                    "price": float(prices[i]),
                    "odds": float(odds[i]),
                }
                for i, o in enumerate(outcomes)
            ],
        })
    return response


@app.get("/api/events/{event_id}")
async def get_event(
    event_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """Детали одного события с расширенной статистикой."""
    result = await session.execute(select(Event).where(Event.id == event_id))
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(404, "Event not found")

    outcomes_result = await session.execute(
        select(Outcome).where(Outcome.event_id == event.id)
        .order_by(Outcome.sort_order)
    )
    outcomes = outcomes_result.scalars().all()
    q = [o.shares_outstanding for o in outcomes]
    prices = market_engine.get_prices(q, event.liquidity_b)
    odds = market_engine.get_odds(q, event.liquidity_b)

    # Категория
    cat_result = await session.execute(
        select(Category).where(Category.id == event.category_id)
    )
    cat = cat_result.scalar_one_or_none()

    # Объём и число уникальных игроков
    from sqlalchemy import func
    vol_result = await session.execute(
        select(func.coalesce(func.sum(Bet.amount_rub), 0)).where(Bet.event_id == event.id)
    )
    volume = vol_result.scalar() or 0

    players_result = await session.execute(
        select(func.count(func.distinct(Bet.user_id))).where(Bet.event_id == event.id)
    )
    players = players_result.scalar() or 0

    return {
        "id": event.id,
        "slug": event.slug,
        "title": event.title,
        "description": event.description,
        "image_url": event.image_url,
        "status": event.status.value,
        "closes_at": event.closes_at.isoformat(),
        "category": {"emoji": cat.emoji, "name": cat.name} if cat else None,
        "stats": {
            "volume_rub": float(volume),
            "players_count": players,
        },
        "outcomes": [
            {
                "id": o.id,
                "title": o.title,
                "price": float(prices[i]),
                "odds": float(odds[i]),
                "shares_outstanding": float(o.shares_outstanding),
            }
            for i, o in enumerate(outcomes)
        ],
    }


class QuoteRequest(BaseModel):
    event_id: int
    outcome_id: int
    amount_rub: float


@app.post("/api/bet/quote")
async def bet_quote(
    body: QuoteRequest,
    tg_user: Annotated[dict, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """Котировка ставки — без списания средств."""
    try:
        result = await quote_bet(
            session,
            body.event_id,
            body.outcome_id,
            Decimal(str(body.amount_rub)),
        )
    except BetError as e:
        raise HTTPException(400, str(e))

    return {
        "shares": float(result["shares"]),
        "current_odds": float(result["current_odds"]),
        "avg_odds": float(result["avg_odds"]),
        "potential_payout": float(result["potential_payout"]),
        "outcome_title": result["outcome_title"],
    }


class PlaceBetRequest(BaseModel):
    event_id: int
    outcome_id: int
    amount_rub: float


@app.post("/api/bet/place")
async def bet_place(
    body: PlaceBetRequest,
    tg_user: Annotated[dict, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """Размещение ставки."""
    user, _ = await get_or_create_user(
        session,
        telegram_id=tg_user["id"],
        username=tg_user.get("username"),
        first_name=tg_user.get("first_name"),
    )

    try:
        bet = await place_bet(
            session,
            user,
            body.event_id,
            body.outcome_id,
            Decimal(str(body.amount_rub)),
        )
    except BetError as e:
        raise HTTPException(400, str(e))

    return {
        "bet_id": bet.id,
        "shares": float(bet.shares),
        "amount_rub": float(bet.amount_rub),
        "avg_odds": float(bet.avg_odds),
        "new_balance": float(user.balance_rub),
    }


@app.get("/api/my/bets")
async def my_bets(
    tg_user: Annotated[dict, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """История ставок пользователя."""
    user_result = await session.execute(
        select(User).where(User.telegram_id == tg_user["id"])
    )
    user = user_result.scalar_one_or_none()
    if not user:
        return []

    result = await session.execute(
        select(Bet).where(Bet.user_id == user.id)
        .order_by(Bet.created_at.desc()).limit(50)
    )
    bets = result.scalars().all()

    response = []
    for bet in bets:
        event_r = await session.execute(select(Event).where(Event.id == bet.event_id))
        outcome_r = await session.execute(select(Outcome).where(Outcome.id == bet.outcome_id))
        event = event_r.scalar_one()
        outcome = outcome_r.scalar_one()

        response.append({
            "id": bet.id,
            "event_title": event.title,
            "outcome_title": outcome.title,
            "amount_rub": float(bet.amount_rub),
            "shares": float(bet.shares),
            "avg_odds": float(bet.avg_odds),
            "is_settled": bet.is_settled,
            "payout_rub": float(bet.payout_rub) if bet.payout_rub else None,
            "created_at": bet.created_at.isoformat(),
        })
    return response


# ── TON / USDT депозит ──────────────────────────────────────────────────────

USDT_TO_RUB = Decimal("90")  # курс USDT→RUB, можно сделать динамическим


@app.get("/api/deposit/ton/rate")
async def ton_rate():
    """Текущий курс USDT/RUB для отображения в Mini App."""
    return {"rate_rub": float(USDT_TO_RUB), "usdt_wallet": settings.usdt_wallet_address}


class TonDepositInitRequest(BaseModel):
    amount_usdt: float


@app.post("/api/deposit/ton/init")
async def ton_deposit_init(
    body: TonDepositInitRequest,
    tg_user: Annotated[dict, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """Создаёт запись о депозите. Возвращает deposit_id который идёт memo в транзакции."""
    if body.amount_usdt < 1:
        raise HTTPException(400, "Минимум 1 USDT")

    user, _ = await get_or_create_user(
        session,
        telegram_id=tg_user["id"],
        username=tg_user.get("username"),
        first_name=tg_user.get("first_name"),
    )

    amount_rub = Decimal(str(body.amount_usdt)) * USDT_TO_RUB

    from db.models import Payment, PaymentMethod
    payment = Payment(
        user_id=user.id,
        method=PaymentMethod.USDT_TON,
        amount_rub=amount_rub,
        status="pending",
        is_deposit=True,
        description=f"USDT deposit {body.amount_usdt}",
    )
    session.add(payment)
    await session.commit()
    await session.refresh(payment)

    return {
        "deposit_id": payment.id,
        "amount_usdt": body.amount_usdt,
        "amount_rub": float(amount_rub),
        "platform_wallet": settings.usdt_wallet_address,
        "memo": str(payment.id),
    }


@app.get("/api/deposit/ton/status/{deposit_id}")
async def ton_deposit_status(
    deposit_id: int,
    tg_user: Annotated[dict, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """Проверяет статус депозита — вызывается клиентом каждые 30 сек."""
    from db.models import Payment, Transaction, TransactionType
    result = await session.execute(
        select(Payment).where(Payment.id == deposit_id)
    )
    payment = result.scalar_one_or_none()
    if not payment:
        raise HTTPException(404, "Депозит не найден")

    if payment.status == "succeeded":
        user_r = await session.execute(select(User).where(User.id == payment.user_id))
        user = user_r.scalar_one()
        return {
            "status": "confirmed",
            "credited_rub": float(payment.amount_rub),
            "new_balance_rub": float(user.balance_rub),
        }

    # Проверяем через Toncenter
    from bot.services.payment_service import check_usdt_toncenter
    found = await check_usdt_toncenter(session, payment)
    if found:
        user_r = await session.execute(select(User).where(User.id == payment.user_id))
        user = user_r.scalar_one()
        return {
            "status": "confirmed",
            "credited_rub": float(payment.amount_rub),
            "new_balance_rub": float(user.balance_rub),
        }

    return {"status": "pending"}


# Раздаём Mini App статикой
app.mount("/miniapp", StaticFiles(directory="miniapp", html=True), name="miniapp")


@app.get("/")
async def root():
    return {"status": "ok", "service": "PredictBet"}
