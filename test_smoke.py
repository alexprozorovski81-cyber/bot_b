"""
Smoke-тест: проверяет что весь стек работает.
Запускает API, делает запросы, проверяет логику ставок.
"""
import asyncio
import json
import sys
from decimal import Decimal

import httpx
import uvicorn

from bot.api import app


async def run_server():
    config = uvicorn.Config(app, host="127.0.0.1", port=8765, log_level="warning")
    server = uvicorn.Server(config)
    return server


async def test_endpoints():
    """Проверка публичных эндпоинтов (без auth)."""
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8765") as client:
        # 1. Health check
        r = await client.get("/")
        assert r.status_code == 200
        print("✓ GET / →", r.json())

        # 2. Категории (без авторизации)
        r = await client.get("/api/categories")
        assert r.status_code == 200
        cats = r.json()
        print(f"✓ GET /api/categories → {len(cats)} категорий")
        for c in cats:
            print(f"    {c['emoji']} {c['name']}")

        # 3. Список событий
        r = await client.get("/api/events")
        assert r.status_code == 200
        events = r.json()
        print(f"\n✓ GET /api/events → {len(events)} событий")
        for ev in events[:3]:
            outcomes_str = ", ".join(
                f"{o['title']}({o['price']:.2f}/×{o['odds']:.2f})"
                for o in ev["outcomes"]
            )
            print(f"    #{ev['id']} {ev['title'][:50]}")
            print(f"       {outcomes_str}")

        # 4. Детали конкретного события
        if events:
            r = await client.get(f"/api/events/{events[0]['id']}")
            assert r.status_code == 200
            ev = r.json()
            print(f"\n✓ GET /api/events/{events[0]['id']}")
            print(f"    Объём: {ev['stats']['volume_rub']} ₽")
            print(f"    Игроков: {ev['stats']['players_count']}")

        # 5. /api/me без авторизации → 422 (отсутствует обязательный заголовок) или 401
        r = await client.get("/api/me")
        assert r.status_code in (401, 422), f"Expected 401/422, got {r.status_code}"
        print(f"\n✓ GET /api/me без auth → {r.status_code} (правильно отказан)")


async def test_market_engine():
    """Тест формулы LMSR."""
    print("\n=== Тест LMSR-движка ===")
    from bot.services import market_engine

    # Бинарный рынок
    q = [Decimal("0"), Decimal("0")]
    b = Decimal("1000")
    odds = market_engine.get_odds(q, b)
    print(f"✓ Бинарный старт: коэф = {[float(x) for x in odds]} (ожидаем 2.0/2.0)")
    assert all(abs(float(o) - 2.0) < 0.01 for o in odds)

    # Покупка 200 акций
    cost = market_engine.calculate_bet_cost(q, b, 0, Decimal("200"))
    print(f"✓ 200 YES стоят: {cost} ₽")
    assert Decimal("100") < cost < Decimal("110")

    # 5 исходов
    q5 = [Decimal("0")] * 5
    odds5 = market_engine.get_odds(q5, b)
    print(f"✓ 5 исходов старт: коэф = {[float(x) for x in odds5]} (ожидаем 5.0)")
    assert all(abs(float(o) - 5.0) < 0.01 for o in odds5)


async def test_full_bet_flow():
    """Тест полного флоу ставки через сервис (без HTTP)."""
    print("\n=== Тест полного флоу ставки ===")
    from db.database import AsyncSessionLocal
    from db.models import User
    from bot.services.user_service import get_or_create_user
    from bot.services.bet_service import quote_bet, place_bet
    from sqlalchemy import select

    async with AsyncSessionLocal() as session:
        # Создаём тестового пользователя
        user, is_new = await get_or_create_user(
            session, telegram_id=999_001, username="test_user", first_name="Tester",
        )
        print(f"✓ Пользователь создан: balance = {user.balance_rub} ₽ (welcome бонус)")

        # Берём первое событие
        from db.models import Event, EventStatus, Outcome
        ev_result = await session.execute(
            select(Event).where(Event.status == EventStatus.ACTIVE).limit(1)
        )
        event = ev_result.scalar_one()

        out_result = await session.execute(
            select(Outcome).where(Outcome.event_id == event.id)
            .order_by(Outcome.sort_order)
        )
        outcomes = list(out_result.scalars().all())

        # Котировка
        quote = await quote_bet(session, event.id, outcomes[0].id, Decimal("100"))
        print(f"✓ Котировка для 100₽ на «{quote['outcome_title']}»:")
        print(f"    Получишь акций: {quote['shares']:.2f}")
        print(f"    Средний коэф: ×{quote['avg_odds']}")
        print(f"    Потенциал: {quote['potential_payout']} ₽")

        # Размещение ставки
        bet = await place_bet(session, user, event.id, outcomes[0].id, Decimal("100"))
        await session.refresh(user)
        print(f"✓ Ставка размещена! Bet ID = {bet.id}")
        print(f"    Новый баланс: {user.balance_rub} ₽")

        # Проверяем что цена изменилась после ставки
        from bot.services import market_engine
        await session.refresh(outcomes[0])
        await session.refresh(outcomes[1])
        q_new = [outcomes[0].shares_outstanding, outcomes[1].shares_outstanding]
        new_odds = market_engine.get_odds(q_new, event.liquidity_b)
        print(f"✓ Коэффициенты после ставки: {[float(x) for x in new_odds]}")
        assert new_odds[0] < new_odds[1], "Коэф YES должен снизиться после покупки YES"


async def test_resolution():
    """Тест разрешения события и выплат."""
    print("\n=== Тест разрешения события ===")
    from db.database import AsyncSessionLocal
    from db.models import Event, Outcome, User, Bet, EventStatus
    from bot.services.resolution_service import resolve_event
    from sqlalchemy import select

    async with AsyncSessionLocal() as session:
        # Найдём событие с уже размещёнными ставками
        ev_result = await session.execute(
            select(Event).where(Event.status == EventStatus.ACTIVE).limit(1)
        )
        event = ev_result.scalar_one()

        # Снимок до
        bets_before = await session.execute(
            select(Bet).where(Bet.event_id == event.id, Bet.is_settled == False)
        )
        active_bets = list(bets_before.scalars().all())
        print(f"✓ Событие #{event.id}: {len(active_bets)} активных ставок")

        if not active_bets:
            print("  Пропускаю — нет ставок")
            return

        # Загружаем исходы явно
        out_result = await session.execute(
            select(Outcome).where(Outcome.event_id == event.id)
            .order_by(Outcome.sort_order)
        )
        outcomes = list(out_result.scalars().all())

        # Разрешаем в пользу первого исхода
        winning_outcome_id = outcomes[0].id
        summary = await resolve_event(session, event.id, winning_outcome_id)
        print(f"✓ Событие разрешено!")
        print(f"    Победителей: {summary['winners_count']}")
        print(f"    Проигравших: {summary['losers_count']}")
        print(f"    Выплачено: {summary['total_payout']:.2f} ₽")
        print(f"    Комиссия платформы: {summary['fees_collected']:.2f} ₽")


async def main():
    server = await run_server()
    server_task = asyncio.create_task(server.serve())
    await asyncio.sleep(1)  # ждём что сервер поднялся

    try:
        await test_endpoints()
        await test_market_engine()
        await test_full_bet_flow()
        await test_resolution()
        print("\n🎉 ВСЕ ТЕСТЫ ПРОШЛИ!")
    except AssertionError as e:
        print(f"\n❌ ASSERTION FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ ERROR: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        server.should_exit = True
        await server_task


if __name__ == "__main__":
    asyncio.run(main())
