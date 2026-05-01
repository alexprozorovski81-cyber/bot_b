"""Конфигурация приложения через Pydantic Settings."""
from decimal import Decimal
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Telegram
    bot_token: str = ""
    bot_username: str = "predictbet_bot"
    miniapp_url: str = "http://localhost:8000/miniapp/"
    admin_ids: str = ""

    @property
    def admin_id_list(self) -> list[int]:
        return [int(x.strip()) for x in self.admin_ids.split(",") if x.strip()]

    @property
    def cors_origins(self) -> list[str]:
        origins = ["https://web.telegram.org", "https://t.me"]
        url = self.miniapp_url.rstrip("/")
        if url and url not in origins:
            origins.append(url)
        return origins

    # Database
    # Default — абсолютный путь в /data (persistenceMount на Amvera).
    # Локально переопределяется через .env (DATABASE_URL=...).
    database_url: str = "sqlite+aiosqlite:////data/predictbet.db"
    redis_url: str = "redis://localhost:6379/0"

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_secret_key: str = "dev-secret-change-in-production"

    # YooKassa
    yookassa_shop_id: str = ""
    yookassa_secret_key: str = ""
    yookassa_verify_ip: bool = True  # проверять IP входящих webhook-ов

    # USDT (TON)
    ton_api_key: str = ""
    usdt_wallet_address: str = ""
    usdt_jetton_master: str = ""
    usdt_to_rub_rate: Decimal = Decimal("95.00")  # единый курс для всего приложения

    # NOWPayments (ETH/BTC/SOL)
    nowpayments_api_key: str = ""
    nowpayments_ipn_secret: str = ""  # для HMAC-верификации webhook
    nowpayments_usd_to_rub: Decimal = Decimal("92.00")  # курс USD→монеты

    # Telegram Stars
    stars_coins_rate: int = 2  # сколько монет за 1 Star (1 Star ≈ $0.013 ≈ 1.2₽, даём 2 монеты)

    # Разработка
    dev_mode: bool = False
    dev_telegram_id: int = 0

    # Платформа
    platform_fee_percent: Decimal = Decimal("2.0")
    initial_liquidity: Decimal = Decimal("1000")
    min_deposit_rub: Decimal = Decimal("100")
    min_deposit_usdt: Decimal = Decimal("1")
    min_withdraw_coins: Decimal = Decimal("100")  # минимальная сумма вывода
    support_username: str = "support"

    # TON горячий кошелёк (для авто-вывода USDT)
    ton_hot_wallet_mnemonic: str = ""   # 24 слова через пробел
    withdraw_auto_enabled: bool = False  # включить авто-вывод через TON SDK
    withdraw_max_auto_usdt: Decimal = Decimal("500")  # макс. сумма авто-вывода за раз
    withdraw_daily_limit_usdt: Decimal = Decimal("2000")  # дневной лимит авто-вывода
    withdraw_min_settled_bets: int = 1   # мин. разрешённых ставок для вывода
    withdraw_cooldown_h: int = 24        # минимум часов между выводами

    # Claude API (для авто-генерации вопросов из новостей)
    anthropic_api_key: str = ""

    # Авто-события из новостей
    auto_events_enabled: bool = True      # включить автопубликацию
    auto_events_per_run: int = 2          # макс. событий за один cron-тик
    auto_events_min_interval_h: int = 2   # минимум часов между дублями из одной темы

    # Краткосрочные crypto-события (intraday, авто-резолв через CoinGecko)
    short_term_enabled: bool = True
    short_term_coins: str = "bitcoin,ethereum,the-open-network"
    short_term_horizons_hours: str = "1,3,6"

    @property
    def short_term_coins_list(self) -> list[str]:
        return [c.strip() for c in self.short_term_coins.split(",") if c.strip()]

    @property
    def short_term_horizons_list(self) -> list[int]:
        return [int(h.strip()) for h in self.short_term_horizons_hours.split(",") if h.strip()]


settings = Settings()  # type: ignore[call-arg]

# Startup validation — fail fast if critical env vars are missing
if not settings.bot_token:
    raise RuntimeError("BOT_TOKEN is not set. Copy .env.example → .env and fill in the values.")
if not settings.admin_ids:
    raise RuntimeError("ADMIN_IDS is not set. Set comma-separated Telegram user IDs of admins.")
if settings.api_secret_key == "dev-secret-change-in-production":
    import logging as _logging
    _logging.getLogger(__name__).warning(
        "API_SECRET_KEY is using the default dev value — change it in production!"
    )
