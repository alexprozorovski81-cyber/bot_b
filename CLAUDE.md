# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the project

```bash
# Install dependencies
pip install -r requirements.txt

# Run everything (bot + API + cron) in one process
python -m bot.main

# Seed the database manually
python -m bot.seed

# Test LMSR market engine math
python bot/services/market_engine.py
```

Copy `.env.example` to `.env` and fill in `BOT_TOKEN`, `ADMIN_IDS`, `DATABASE_URL`, etc.

## Deployment (Amvera)

- Git remote: `https://git.msk0.amvera.ru/jjkkeerr1/predictbet` — push to **`master`** branch to trigger a build
- Persistence: SQLite **must** live at `/data/predictbet.db` (Amvera's `persistenceMount`). Default in `bot/config.py` is already set to that path; override via `DATABASE_URL` env var if needed
- `amvera.yml` controls build (`pip`), run command (`python -m bot.main`), port (8000→80), and persistence mount

## Architecture

The entire application runs as **three concurrent asyncio tasks** inside a single Python process (`bot/main.py`):

| Task | Description |
|---|---|
| `run_bot()` | aiogram long-polling Telegram bot |
| `run_api()` | uvicorn/FastAPI serving the Mini App HTML + REST API |
| `run_cron()` | Periodic tasks: oracle checks every 5 min, news scan every 30 min |

### Database

SQLAlchemy 2.0 async with SQLite (aiosqlite). All models are in `db/models.py`. The engine and session factory live in `db/database.py`. Always use `AsyncSessionLocal` as an async context manager in handlers/services.

Key models: `User`, `Category`, `Event` (with `EventStatus` enum), `Outcome`, `Bet`, `Transaction`, `Payment`.

Schema is created on startup via `Base.metadata.create_all`. There are no Alembic migrations in active use — schema changes require manual migration or a fresh DB.

### Telegram bot (`bot/handlers/`)

- `admin.py` — all admin commands (`/addevent`, `/resolve`, `/cancel_event`, `/stats`, `/updateimages`). Uses aiogram FSM (`AddEventStates`) for multi-step event creation. Admin check: `is_admin(user_id)` from `settings.admin_id_list`
- `start.py` + `deposit.py` — user-facing handlers
- Router priority: `admin.router` is included **before** `get_main_router()` in the dispatcher

### FastAPI Mini App (`bot/api.py`)

- Auth: every API request must carry `X-Init-Data` header (Telegram WebApp `initData`, verified by HMAC-SHA256 in `validate_init_data()`)
- Static files mounted at `/miniapp` — serves `miniapp/index.html`, `miniapp/app.js`, `miniapp/styles.css`, `miniapp/images/*.svg`
- Key endpoints: `GET /api/events`, `GET /api/events/{id}`, `POST /api/bet/quote`, `POST /api/bet/place`, `GET /api/me`, `GET /api/categories`, `GET /api/my/bets`

### LMSR market engine (`bot/services/market_engine.py`)

All pricing is **Logarithmic Market Scoring Rule**. The platform seeds each market with `liquidity_b = 1000`. Key functions: `get_prices(q, b)` → probabilities, `get_odds(q, b)` → display odds, `calculate_bet_cost()` → exact cost of a purchase, `calculate_shares_for_amount()` → binary search for inverse. Each winning share pays out exactly **1 RUB** at resolution; platform takes `fee_percent` (default 2%) from profit only.

### Image selection (`bot/services/event_images.py`)

`pick_event_image(title, category_slug, prefilled, slug)` — priority chain:
1. `prefilled` URL from RSS news photo
2. Wikipedia via `SLUG_WIKI_MAP` (exact article for seed events)
3. Wikipedia opensearch on cleaned title (strips Russian question phrases)
4. Fallback: `/miniapp/images/{category}.svg`

### News / auto-events (`bot/services/news_service.py`)

RSS feeds (ТАСС, Lenta, РБК, Чемпионат) are polled every 30 min. Matching articles are sent to admins with an inline "➕ Создать событие" button. Clicking it pre-fills the FSM with the article title and image. **News items are cached in memory** (`_recent_items`, `_sent_hashes`) — cache is lost on restart.

### Payment flow

YooKassa (cards/SBP) via `bot/services/payment_service.py`; USDT/TON via `bot/handlers/webhooks.py`. Webhooks hit `/webhooks/yookassa` and `/webhooks/ton`.

## Key conventions

- Money is always `Decimal`, never `float`
- All DB sessions are async; use `async with AsyncSessionLocal() as session:` — never share sessions across tasks
- `bot/notifier.py` holds a module-level bot instance (`notifier._bot`) used by services to send Telegram messages without importing the bot directly into service layer
- `EventStatus.DRAFT` exists in the model but new events created via `/addevent` are immediately `ACTIVE` — DRAFT is unused
- The `master` branch is what Amvera deploys; `main` branch is kept in sync on GitHub
