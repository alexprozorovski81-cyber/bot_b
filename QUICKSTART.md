# 🚀 Быстрый запуск (3 минуты, без Docker и PostgreSQL)

Это версия для тестов на твоём ноутбуке. БД — SQLite, всё в одном процессе.

## 1. Установка зависимостей

```bash
cd predictbet
python -m venv venv
source venv/bin/activate            # Linux / macOS
# venv\Scripts\activate             # Windows

pip install -r requirements.txt
```

## 2. Настройка бота

Создай бота через `@BotFather` в Telegram:
- `/newbot` → придумай имя
- Сохрани токен, выглядит как `1234567890:AAAA-bbb...`
- Узнай свой Telegram ID через `@userinfobot`

Создай `.env`:
```bash
cp .env.example .env
```

Минимальные строчки в `.env` (остальное оставь как есть):
```
BOT_TOKEN=твой_токен
BOT_USERNAME=твой_бот_без_at
ADMIN_IDS=твой_telegram_id
```

## 3. Создать БД и наполнить тестовыми данными

```bash
python -m bot.init_db    # создаст SQLite-файл predictbet.db
python -m bot.seed       # загрузит 6 категорий и 10 событий
```

## 4. Запустить!

```bash
python -m bot.main
```

Это запустит:
- Telegram-бота (long polling)
- API на `http://localhost:8000`
- Cron оракулов (каждые 5 минут)

В Telegram открой своего бота, нажми `/start` — получишь welcome-бонус 500 ₽.

## 5. Открыть Mini App

Для теста на компьютере: открой `http://localhost:8000/miniapp/` в браузере (без авторизации увидишь данные, но ставить нельзя).

Для теста в Telegram нужен HTTPS:

```bash
# В новом терминале установи ngrok если нет
ngrok http 8000

# Скопируй HTTPS URL → https://abc123.ngrok-free.app
```

В `.env` укажи:
```
MINIAPP_URL=https://abc123.ngrok-free.app/miniapp/
```

В `@BotFather`: `/mybots` → твой бот → `Bot Settings` → `Menu Button` → впиши тот же URL.

Перезапусти `python -m bot.main`. Теперь кнопка `🎯 Открыть площадку` в боте откроет Mini App.

## 6. Команды админа

В Telegram у бота:
- `/admin` — главное меню админа
- `/events` — список активных событий
- `/resolve <event_id>` — разрешить событие, выбрать победителя кнопками
- `/stats` — статистика всей платформы
- `/grant <telegram_id> <сумма>` — начислить пользователю баланс

## 7. Тест end-to-end

```bash
python test_smoke.py
```

Этот тест проверит работу API, формулы LMSR, флоу ставки и разрешения событий. Если всё зелёное — система рабочая.

## Что дальше?

Когда захочешь продакшн:
1. Перейти на PostgreSQL — изменить `DATABASE_URL` в `.env`
2. Применить миграции: `alembic upgrade head`
3. Подключить ЮKassa и USDT — заполнить ключи в `.env`
4. Развернуть на VPS — см. `docs/DEPLOY.md`
5. Настроить webhook ЮKassa: `https://твой-домен/webhooks/yookassa` в кабинете ЮKassa

## Что я могу делать сейчас (ничего не подключая)

- Регистрироваться в боте, получать welcome-бонус
- Открывать Mini App, выбирать события из 6 категорий
- Ставить виртуальные баллы (бонус) на любые исходы
- Видеть как меняются коэффициенты после ставок
- Видеть портфель ставок и профиль
- Через `/admin` разрешать события и выплачивать выигрыши

Это полностью рабочий MVP с виртуальной валютой — можно запускать и тестировать с реальными пользователями. Добавление приёма реальных денег — это финальный шаг с заполнением ключей в `.env`.
