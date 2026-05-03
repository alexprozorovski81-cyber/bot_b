# Инструкция по запуску PredictBet

## Способ 1 — Локально для тестов (10 минут)

### Шаг 1. Создать бота в Telegram

1. Открой `@BotFather` в Telegram → `/newbot` → придумай имя
2. Сохрани токен — он выглядит как `1234567890:AAAA-bbb...`
3. В BotFather: `/mybots` → выбрать → `Bot Settings` → `Menu Button` →
   укажи URL твоего Mini App (для теста — `https://your-tunnel.ngrok.io/miniapp/`)

### Шаг 2. Запустить инфраструктуру

```bash
cd predictbet
cp .env.example .env
# Открой .env и впиши BOT_TOKEN, BOT_USERNAME, ADMIN_IDS

docker compose up -d postgres redis
```

### Шаг 3. Установить зависимости и применить миграции

```bash
python -m venv venv
source venv/bin/activate            # Linux/Mac
# venv\Scripts\activate             # Windows

pip install -r requirements.txt
alembic upgrade head
python -m bot.seed                  # Загрузить категории и события
```

### Шаг 4. Открыть бэкенд наружу для теста Mini App

Telegram WebApp требует HTTPS. Используй `ngrok`:

```bash
ngrok http 8000
# Скопируй HTTPS URL → https://abc123.ngrok-free.app
```

В `.env`:
```
MINIAPP_URL=https://abc123.ngrok-free.app/miniapp/
```

В BotFather подставь тот же URL в Menu Button.

### Шаг 5. Запустить!

```bash
python -m bot.main
```

Открывай бота в Telegram, нажимай `/start`, потом «🎯 Открыть площадку».

---

## Способ 2 — Production деплой на VPS

### Что нужно
- VPS на Ubuntu 22.04+ (Hetzner CX22 — €4.5/мес)
- Домен с настроенными DNS (A-запись на IP VPS)
- Установленный Docker и docker-compose

### Шаг 1. Подготовка сервера

```bash
ssh root@your-server-ip
apt update && apt upgrade -y
apt install -y docker.io docker-compose-v2 nginx certbot python3-certbot-nginx
```

### Шаг 2. Клон проекта

```bash
git clone <твой-репозиторий> /opt/predictbet
cd /opt/predictbet
cp .env.example .env
nano .env  # заполнить все секреты
```

### Шаг 3. Nginx + SSL

`/etc/nginx/sites-available/predictbet`:
```nginx
server {
    server_name predictbet.app;

    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    listen 80;
}
```

```bash
ln -s /etc/nginx/sites-available/predictbet /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
certbot --nginx -d predictbet.app
```

### Шаг 4. Запуск

```bash
docker compose up -d --build
docker compose logs -f app
```

Готово! Бот работает 24/7.

---

## Чек-лист перед запуском в прод

- [ ] BOT_TOKEN из BotFather
- [ ] ЮKassa магазин зарегистрирован, ключи в .env
- [ ] (если USDT) Кошелёк TON создан, адрес и ключ tonapi.io в .env
- [ ] Получены `file_id` стикеров через `@idstickerbot`, прописаны в `bot/texts.py`
- [ ] Заменены изображения категорий и событий на свои
- [ ] Прописан `support_username` (твой админский аккаунт)
- [ ] HTTPS работает (Mini App в Telegram требует TLS)
- [ ] В BotFather у Menu Button прописан `https://твой-домен/miniapp/`
- [ ] Сделан backup стратегии для PostgreSQL
- [ ] Настроен мониторинг (UptimeRobot для бесплатного uptime-чека)

---

## PostgreSQL Setup

### Создание базы данных

```bash
# Подключись к PostgreSQL
psql -U postgres

# Создай роль и базу
CREATE ROLE predictbet WITH LOGIN PASSWORD 'yourpassword';
CREATE DATABASE predictbet OWNER predictbet;
\q
```

В `.env`:
```
DATABASE_URL=postgresql+asyncpg://predictbet:yourpassword@localhost:5432/predictbet
```

### Применение миграций

```bash
# Единственная команда — применяет все 6 миграций последовательно
alembic upgrade head
```

При запуске через `python -m bot.main` PostgreSQL-ветка автоматически
вызывает `alembic upgrade head` через Python API.

### Локально с Docker

```bash
docker compose up -d postgres
# Подождать healthcheck (5–10 сек), затем:
alembic upgrade head
python -m bot.seed
python -m bot.main
```

### Откат миграции

```bash
alembic downgrade -1     # откатить последнюю
alembic downgrade base   # откатить все
```

### Загрузить cloudflared для локального туннеля

```bash
# Linux/Mac
wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
chmod +x cloudflared-linux-amd64 && mv cloudflared-linux-amd64 cloudflared

# Windows — скачать вручную:
# https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe
```

---

## Команды бота, которые стоит зарегистрировать в BotFather

`/setcommands` → выбрать бота → отправить:
```
start - Главное меню
profile - Мой профиль и статистика
deposit - Пополнить баланс
about - О платформе
support - Поддержка
```

## Где размещаются стикеры

Открой `bot/texts.py` — словарь `STICKERS` в самом верху. Получи file_id через @idstickerbot
и подставь в каждый ключ. Бот автоматически отправит стикер в нужный момент:

| Ключ | Когда отправляется |
|---|---|
| `welcome` | Новый пользователь сделал /start |
| `deposit_success` | Успешное пополнение |
| `win` | Выигрыш ставки |
| `lose` | Проигрыш ставки |
| `about` | Кнопка «О платформе» |
| `support` | Кнопка «Поддержка» |
| `error` | При ошибках |
| `profile` | Кнопка «Профиль» |
