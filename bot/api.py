"""
FastAPI приложение — обслуживает Mini App.

Аутентификация через Telegram WebApp initData (HMAC-SHA256).
"""
import hashlib
import hmac
import json
import logging
import time
from decimal import Decimal
from pathlib import Path
from typing import Annotated
from urllib.parse import parse_qsl

from fastapi import FastAPI, HTTPException, Header, Depends, Request
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
    Category, Comment, Event, EventStatus, Outcome, Bet, User,
    Achievement, UserAchievement, WithdrawalRequest, WithdrawStatus,
    Transaction, TransactionType, Payment, PaymentMethod, UserStats,
)


logger = logging.getLogger(__name__)
app = FastAPI(title="PredictBet API")
app.include_router(webhooks_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def no_cache_api(request: Request, call_next):
    """Запрещаем кеширование /api/* ответов браузером и прокси."""
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    return response


@app.get("/api/config")
async def public_config():
    """Публичная конфигурация для Mini App (не требует авторизации)."""
    return {"bot_username": settings.bot_username}


@app.get("/health")
async def healthcheck():
    """Healthcheck для мониторинга и load balancer."""
    from db.database import engine
    try:
        async with engine.connect() as conn:
            await conn.execute(__import__("sqlalchemy").text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    return {"status": "ok" if db_ok else "degraded", "db": db_ok}


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

        # Проверяем свежесть данных — не старше 24 часов (стандарт Telegram)
        auth_date = int(parsed.get("auth_date", 0))
        if auth_date == 0 or time.time() - auth_date > 86400:
            raise ValueError("initData expired")

        user_json = parsed.get("user")
        if not user_json:
            raise ValueError("user отсутствует")

        return json.loads(user_json)

    except Exception as e:
        logger.warning(f"initData validation failed: {e}")
        raise HTTPException(status_code=401, detail="Invalid initData")


async def get_current_user(
    request: Request,
    x_telegram_init_data: Annotated[str | None, Header(alias="X-Telegram-Init-Data")] = None,
    x_device_fingerprint: Annotated[str | None, Header(alias="X-Device-Fingerprint")] = None,
    x_forwarded_for: Annotated[str | None, Header(alias="X-Forwarded-For")] = None,
) -> dict:
    """Зависимость FastAPI — текущий пользователь из заголовка."""
    # dev_mode разрешён только с localhost (защита от случайного включения на проде)
    if settings.dev_mode and not x_telegram_init_data:
        client_host = request.client.host if request.client else ""
        if client_host in ("127.0.0.1", "::1", "localhost"):
            return {
                "id": settings.dev_telegram_id, "first_name": "Dev", "username": "dev",
                "_ip": "127.0.0.1", "_fp": None,
            }
    if not x_telegram_init_data:
        raise HTTPException(status_code=401, detail="Unauthorized: open via Telegram")
    user_data = validate_init_data(x_telegram_init_data)
    # Добавляем IP и fingerprint для anti-fraud
    ip = (x_forwarded_for.split(",")[0].strip() if x_forwarded_for
          else (request.client.host if request.client else None))
    user_data["_ip"] = ip
    user_data["_fp"] = x_device_fingerprint
    return user_data


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


def is_admin_user(tg_user: dict) -> bool:
    """Проверяет, является ли пользователь Telegram администратором платформы."""
    return int(tg_user.get("id", 0)) in settings.admin_id_list


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
        ip=tg_user.get("_ip"),
        fingerprint=tg_user.get("_fp"),
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
    timeframe: str | None = None,
    limit: int = 30,
):
    """Список активных событий с текущими коэффициентами."""
    # MODERATION жёстко исключён — только ACTIVE видят пользователи
    query = select(Event).where(Event.status == EventStatus.ACTIVE)
    if category:
        cat_result = await session.execute(
            select(Category).where(Category.slug == category)
        )
        cat = cat_result.scalar_one_or_none()
        if cat:
            query = query.where(Event.category_id == cat.id)

    if timeframe:
        query = query.where(Event.timeframe == timeframe)

    query = query.order_by(Event.closes_at).limit(limit)
    result = await session.execute(query)
    events = result.scalars().all()

    from sqlalchemy import func
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

        vol_result = await session.execute(
            select(func.coalesce(func.sum(Bet.amount_rub), 0)).where(Bet.event_id == event.id)
        )
        volume = vol_result.scalar() or 0

        players_result = await session.execute(
            select(func.count(func.distinct(Bet.user_id))).where(Bet.event_id == event.id)
        )
        players = players_result.scalar() or 0

        response.append({
            "id": event.id,
            "slug": event.slug,
            "title": event.title,
            "description": event.description,
            "image_url": event.image_url,
            "article_url": event.article_url,
            "closes_at": event.closes_at.isoformat(),
            "category_id": event.category_id,
            "timeframe": event.timeframe,
            "volume_rub": float(volume),
            "players_count": players,
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
    tg_user: Annotated[dict, Depends(get_current_user)],
):
    """Детали одного события с расширенной статистикой."""
    result = await session.execute(select(Event).where(Event.id == event_id))
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(404, "Event not found")

    # События в MODERATION не видны обычным пользователям
    if event.status == EventStatus.MODERATION and not is_admin_user(tg_user):
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

    # Похожие события той же категории
    similar_result = await session.execute(
        select(Event)
        .where(Event.category_id == event.category_id)
        .where(Event.id != event.id)
        .where(Event.status == EventStatus.ACTIVE)
        .order_by(Event.closes_at)
        .limit(3)
    )
    similar_events = similar_result.scalars().all()

    return {
        "id": event.id,
        "slug": event.slug,
        "title": event.title,
        "description": event.description,
        "image_url": event.image_url,
        "article_url": event.article_url,
        "status": event.status.value,
        "closes_at": event.closes_at.isoformat(),
        "category": {"emoji": cat.emoji, "name": cat.name} if cat else None,
        "stats": {
            "volume_rub": float(volume),
            "players_count": players,
        },
        "similar_events": [
            {
                "id": e.id,
                "title": e.title,
                "image_url": e.image_url,
                "closes_at": e.closes_at.isoformat(),
            }
            for e in similar_events
        ],
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
        ip=tg_user.get("_ip"),
        fingerprint=tg_user.get("_fp"),
    )

    try:
        bet = await place_bet(
            session,
            user.id,
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


@app.get("/api/deposit/ton/rate")
async def ton_rate():
    """Текущий курс USDT/RUB для отображения в Mini App."""
    from bot.services.rate_service import get_usdt_rub_rate
    rate = await get_usdt_rub_rate()
    return {"rate_rub": float(rate), "usdt_wallet": settings.usdt_wallet_address}


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
        ip=tg_user.get("_ip"),
        fingerprint=tg_user.get("_fp"),
    )

    from bot.services.rate_service import get_usdt_rub_rate
    rate = await get_usdt_rub_rate()
    amount_rub = (Decimal(str(body.amount_usdt)) * rate).quantize(Decimal("0.01"))

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
    # Загружаем владельца запроса
    user, _ = await get_or_create_user(
        session,
        telegram_id=tg_user["id"],
        username=tg_user.get("username"),
        first_name=tg_user.get("first_name"),
    )

    result = await session.execute(
        select(Payment).where(Payment.id == deposit_id)
    )
    payment = result.scalar_one_or_none()
    if not payment:
        raise HTTPException(404, "Депозит не найден")

    # Проверяем что депозит принадлежит текущему пользователю
    if payment.user_id != user.id:
        raise HTTPException(403, "Forbidden")

    if payment.status == "succeeded":
        await session.refresh(user)
        return {
            "status": "confirmed",
            "credited_rub": float(payment.amount_rub),
            "new_balance_rub": float(user.balance_rub),
        }

    # Проверяем через Toncenter
    from bot.services.payment_service import check_usdt_toncenter
    found = await check_usdt_toncenter(session, payment)
    if found:
        await session.refresh(user)
        return {
            "status": "confirmed",
            "credited_rub": float(payment.amount_rub),
            "new_balance_rub": float(user.balance_rub),
        }

    return {"status": "pending"}


# ── Комментарии ────────────────────────────────────────────────────────────────

@app.get("/api/events/{event_id}/comments")
async def get_comments(
    event_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """Список комментариев к событию (публичный)."""
    result = await session.execute(
        select(Comment, User)
        .join(User, Comment.user_id == User.id)
        .where(Comment.event_id == event_id)
        .order_by(Comment.created_at.desc())
        .limit(50)
    )
    rows = result.all()
    return [
        {
            "id": comment.id,
            "text": comment.text,
            "created_at": comment.created_at.isoformat(),
            "username": user.username or user.first_name or "Аноним",
        }
        for comment, user in rows
    ]


class CommentRequest(BaseModel):
    text: str


@app.post("/api/events/{event_id}/comments")
async def add_comment(
    event_id: int,
    body: CommentRequest,
    tg_user: Annotated[dict, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """Добавить комментарий. Только для пользователей со ставкой на событие."""
    if not body.text or not body.text.strip():
        raise HTTPException(400, "Текст не может быть пустым")
    if len(body.text) > 500:
        raise HTTPException(400, "Максимум 500 символов")

    user, _ = await get_or_create_user(
        session,
        telegram_id=tg_user["id"],
        username=tg_user.get("username"),
        first_name=tg_user.get("first_name"),
    )

    bet_result = await session.execute(
        select(Bet).where(Bet.event_id == event_id, Bet.user_id == user.id).limit(1)
    )
    if not bet_result.scalar_one_or_none():
        raise HTTPException(403, "Только участники могут оставлять комментарии")

    comment = Comment(
        event_id=event_id,
        user_id=user.id,
        text=body.text.strip(),
    )
    session.add(comment)
    await session.commit()
    await session.refresh(comment)

    return {
        "id": comment.id,
        "text": comment.text,
        "created_at": comment.created_at.isoformat(),
        "username": user.username or user.first_name or "Аноним",
    }


# ── Крипто-депозит через NOWPayments ─────────────────────────────────────────

class CryptoDepositRequest(BaseModel):
    currency: str   # "eth" | "btc" | "sol"
    amount_usd: float


@app.post("/api/deposit/crypto/init")
async def crypto_deposit_init(
    body: CryptoDepositRequest,
    tg_user: Annotated[dict, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """Создаёт инвойс NOWPayments. Возвращает payment_url для редиректа."""
    currency = body.currency.lower()
    if currency not in ("eth", "btc", "sol"):
        raise HTTPException(400, "Поддерживаются: eth, btc, sol")
    if body.amount_usd < 1:
        raise HTTPException(400, "Минимум $1")

    user, _ = await get_or_create_user(
        session,
        telegram_id=tg_user["id"],
        username=tg_user.get("username"),
        first_name=tg_user.get("first_name"),
    )

    from bot.services.payment_service import create_nowpayments_invoice
    try:
        payment, payment_url = await create_nowpayments_invoice(
            session, user, Decimal(str(body.amount_usd)), currency
        )
    except ValueError as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        logger.exception("NOWPayments invoice error")
        raise HTTPException(502, f"Ошибка платёжного шлюза: {e}")

    return {
        "deposit_id": payment.id,
        "currency": currency,
        "amount_usd": body.amount_usd,
        "payment_url": payment_url,
    }


# ── Вывод выигрышей ───────────────────────────────────────────────────────────

class WithdrawRequest(BaseModel):
    amount_coins: float
    network: str         # "usdt_ton" | "eth" | "btc" | "sol"
    wallet_address: str


@app.post("/api/withdraw/request")
async def withdraw_request(
    body: WithdrawRequest,
    tg_user: Annotated[dict, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """Создаёт заявку на вывод с анти-фрод проверками. Монеты списываются сразу."""
    from bot.services.withdrawal_service import create_withdrawal, WithdrawError

    valid_networks = {"usdt_ton", "eth", "btc", "sol"}
    if body.network not in valid_networks:
        raise HTTPException(400, f"Сеть должна быть одной из: {', '.join(valid_networks)}")
    if not body.wallet_address or len(body.wallet_address) < 10:
        raise HTTPException(400, "Некорректный адрес кошелька")

    user, _ = await get_or_create_user(
        session,
        telegram_id=tg_user["id"],
        username=tg_user.get("username"),
        first_name=tg_user.get("first_name"),
    )

    try:
        withdrawal = await create_withdrawal(
            session,
            user,
            Decimal(str(body.amount_coins)),
            body.network,
            body.wallet_address,
        )
    except WithdrawError as e:
        raise HTTPException(400, str(e))

    await session.refresh(user)
    return {
        "id": withdrawal.id,
        "amount_coins": float(withdrawal.amount_coins),
        "amount_usdt": float(withdrawal.amount_usdt or 0),
        "network": withdrawal.network,
        "wallet_address": withdrawal.wallet_address,
        "status": withdrawal.status.value,
        "created_at": withdrawal.created_at.isoformat(),
        "new_balance": float(user.balance_rub),
    }


@app.get("/api/withdraw/status")
async def withdraw_status(
    tg_user: Annotated[dict, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """История заявок на вывод текущего пользователя."""
    user_result = await session.execute(
        select(User).where(User.telegram_id == tg_user["id"])
    )
    user = user_result.scalar_one_or_none()
    if not user:
        return []

    result = await session.execute(
        select(WithdrawalRequest)
        .where(WithdrawalRequest.user_id == user.id)
        .order_by(WithdrawalRequest.created_at.desc())
        .limit(20)
    )
    withdrawals = result.scalars().all()

    return [
        {
            "id": w.id,
            "amount_coins": float(w.amount_coins),
            "amount_usdt": float(w.amount_usdt or 0),
            "network": w.network,
            "wallet_address": w.wallet_address,
            "status": w.status.value,
            "tx_hash": w.tx_hash,
            "admin_note": w.admin_note,
            "created_at": w.created_at.isoformat(),
            "processed_at": w.processed_at.isoformat() if w.processed_at else None,
        }
        for w in withdrawals
    ]


@app.get("/api/withdraw/info")
async def withdraw_info(tg_user: Annotated[dict, Depends(get_current_user)]):
    """Актуальный курс и лимиты для формы вывода."""
    from bot.services.rate_service import get_usdt_rub_rate
    rate = await get_usdt_rub_rate()
    return {
        "rate_rub": float(rate),
        "min_withdraw_coins": float(settings.min_withdraw_coins),
        "auto_enabled": settings.withdraw_auto_enabled,
        "cooldown_h": settings.withdraw_cooldown_h,
    }


# ── Ачивки ────────────────────────────────────────────────────────────────────

@app.get("/api/me/achievements")
async def my_achievements(
    tg_user: Annotated[dict, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """Все ачивки платформы с пометкой earned и датой получения."""
    all_result = await session.execute(
        select(Achievement).order_by(Achievement.id)
    )
    all_achievements = all_result.scalars().all()

    user_result = await session.execute(
        select(User).where(User.telegram_id == tg_user["id"])
    )
    user = user_result.scalar_one_or_none()

    earned_map: dict[int, str] = {}
    if user:
        ua_result = await session.execute(
            select(UserAchievement).where(UserAchievement.user_id == user.id)
        )
        for ua in ua_result.scalars().all():
            earned_map[ua.achievement_id] = ua.unlocked_at.isoformat()

    return [
        {
            "id": a.id,
            "slug": a.slug,
            "name": a.name,
            "emoji": a.emoji,
            "description": a.description,
            "rarity": a.rarity,
            "earned": a.id in earned_map,
            "unlocked_at": earned_map.get(a.id),
        }
        for a in all_achievements
    ]


# ── Лидерборд и лента активности ─────────────────────────────────────────────

def _anonymize(username: str | None, first_name: str | None, chars: int = 3) -> str:
    name = (username or first_name or "Игрок").strip()
    if len(name) <= chars:
        return name + "***"
    return name[:chars] + "***"


@app.get("/api/leaderboard")
async def get_leaderboard(
    session: Annotated[AsyncSession, Depends(get_db)],
    period: str = "week",
):
    """Топ-20 пользователей по net_profit за период (week|month|all)."""
    if period not in ("week", "month", "all"):
        raise HTTPException(400, "period must be week|month|all")

    result = await session.execute(
        select(UserStats, User)
        .join(User, UserStats.user_id == User.id)
        .where(UserStats.period == period)
        .order_by(UserStats.net_profit.desc())
        .limit(20)
    )
    rows = result.all()

    return [
        {
            "rank": i + 1,
            "display_name": _anonymize(user.username, user.first_name),
            "net_profit": float(stats.net_profit),
            "bets_count": stats.bets_count,
            "win_count": stats.win_count,
        }
        for i, (stats, user) in enumerate(rows)
    ]


@app.get("/api/activity")
async def get_activity(
    tg_user: Annotated[dict, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
    limit: int = 30,
):
    """Лента последних ставок (публичная, без сумм, без ставок текущего юзера)."""
    limit = min(limit, 50)

    # Определяем user_id текущего юзера, чтобы исключить его ставки
    self_result = await session.execute(
        select(User.id).where(User.telegram_id == tg_user["id"])
    )
    self_user_id = self_result.scalar_one_or_none()

    query = (
        select(Bet, User, Event, Outcome)
        .join(User, Bet.user_id == User.id)
        .join(Event, Bet.event_id == Event.id)
        .join(Outcome, Bet.outcome_id == Outcome.id)
        .where(Event.status == EventStatus.ACTIVE)
        .order_by(Bet.created_at.desc())
        .limit(limit)
    )
    if self_user_id:
        query = query.where(Bet.user_id != self_user_id)

    result = await session.execute(query)
    rows = result.all()

    return [
        {
            "username": _anonymize(user.username, user.first_name, chars=2),
            "event_title": event.title,
            "outcome_title": outcome.title,
            "created_at": bet.created_at.isoformat(),
        }
        for bet, user, event, outcome in rows
    ]


# Раздаём Mini App статикой
_MINIAPP_DIR = Path(__file__).parent.parent / "miniapp"
app.mount("/miniapp", StaticFiles(directory=str(_MINIAPP_DIR), html=True), name="miniapp")


@app.get("/")
async def root():
    return {"status": "ok", "service": "PredictBet"}
