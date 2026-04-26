# PredictBet — Prediction Market в Telegram

Полнофункциональная платформа ставок на исходы реальных событий (по типу Polymarket), реализованная как Telegram-бот с Mini App.

**Статус:** ✅ End-to-end протестирован, готов к запуску.

## Что работает

| Компонент | Статус |
|---|---|
| Telegram-бот (aiogram 3.13) | ✅ |
| Mini App в стиле Polymarket | ✅ |
| Формула коэффициентов LMSR | ✅ |
| База данных + миграции | ✅ |
| Welcome-бонус 500 ₽ | ✅ |
| Размещение ставок | ✅ |
| Webhook ЮKassa (карты/СБП) | ✅ |
| USDT TON приём | ✅ |
| Реферальная система | ✅ |
| Админ-панель в боте | ✅ |
| Авто-разрешение крипто-событий через CoinGecko | ✅ |
| Выплаты с комиссией | ✅ |
| Уведомления игрокам | ✅ |
| Cron-задачи (оракулы) | ✅ |

## Быстрый старт

**Хочешь запустить за 3 минуты на ноутбуке без Docker/PostgreSQL?**
👉 См. [`QUICKSTART.md`](./QUICKSTART.md)

**Хочешь запустить в продакшн на VPS?**
👉 См. [`docs/DEPLOY.md`](./docs/DEPLOY.md)

## Документация

| Файл | О чём |
|---|---|
| [`QUICKSTART.md`](./QUICKSTART.md) | Запуск за 3 минуты на ноутбуке |
| [`docs/DEPLOY.md`](./docs/DEPLOY.md) | Полный гайд по деплою на VPS |
| [`docs/COEFFICIENTS.md`](./docs/COEFFICIENTS.md) | Объяснение формулы LMSR с примерами |
| [`docs/LEGAL.md`](./docs/LEGAL.md) | Юридические аспекты для РФ |

## Стек

- **Bot:** aiogram 3.13 (async)
- **API:** FastAPI + Pydantic 2
- **DB:** SQLAlchemy 2.0 async + PostgreSQL (или SQLite для тестов)
- **Mini App:** Vanilla JS + CSS (без билда — чистый JS, грузится мгновенно)
- **Платежи:** ЮKassa (карты/СБП) + USDT TON
- **Инфраструктура:** Docker Compose

## Структура проекта

```
predictbet/
├── bot/
│   ├── main.py                  # 🚀 Точка входа (бот + API + cron)
│   ├── api.py                   # FastAPI приложение для Mini App
│   ├── config.py                # Настройки из .env
│   ├── texts.py                 # 📝 Все тексты + слоты под стикеры
│   ├── keyboards.py             # Клавиатуры бота
│   ├── notifier.py              # Глобальный отправитель сообщений
│   ├── seed.py                  # Заливка тестовых данных
│   ├── init_db.py               # Создание схемы БД (быстрый старт)
│   ├── handlers/
│   │   ├── start.py             # /start, профиль, поддержка
│   │   ├── deposit.py           # Пополнение карта/USDT
│   │   ├── webhooks.py          # Webhook ЮKassa
│   │   └── admin.py             # /admin, /resolve, /stats
│   └── services/
│       ├── market_engine.py     # 🧠 Формула LMSR
│       ├── bet_service.py       # Логика ставок
│       ├── user_service.py      # Юзеры и статистика
│       ├── payment_service.py   # ЮKassa и USDT
│       ├── resolution_service.py # Выплаты по итогам
│       └── oracle_service.py    # Авто-разрешение через CoinGecko
├── miniapp/
│   ├── index.html               # Mini App
│   ├── styles.css               # Тёмная тема в стиле Polymarket
│   ├── app.js                   # Логика интерфейса
│   └── api.js                   # API-клиент с initData auth
├── db/
│   ├── models.py                # SQLAlchemy модели
│   ├── database.py              # Async session factory
│   └── alembic/                 # Миграции
├── docs/
│   ├── COEFFICIENTS.md          # 📐 Объяснение LMSR
│   ├── DEPLOY.md                # Деплой на VPS
│   └── LEGAL.md                 # Юридический разбор
├── docker-compose.yml
├── Dockerfile
├── alembic.ini
├── requirements.txt
├── .env.example
└── test_smoke.py                # 🧪 End-to-end тест
```

## Где добавить свои стикеры

В `bot/texts.py` есть словарь `STICKERS` с 8 ключами. Получи `file_id` через `@idstickerbot` и подставь:

```python
STICKERS = {
    "welcome": "CAACAgIAAxkBAAEL...",  # ← сюда file_id
    "deposit_success": "CAACAgIAAxkB...",
    ...
}
```

Стикеры автоматически отправятся в нужные моменты — приветствие, выигрыш, поддержка и т.д.

## Тесты

```bash
python test_smoke.py
```

Полный end-to-end: запускает API, проверяет все эндпоинты, симулирует ставку, разрешение и выплату.
