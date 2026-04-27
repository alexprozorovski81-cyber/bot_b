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

    # Claude API (для авто-генерации вопросов из новостей)
    anthropic_api_key: str = ""

    # Авто-события из новостей
    auto_events_enabled: bool = True      # включить автопубликацию
    auto_events_per_run: int = 2          # макс. событий за один cron-тик
    auto_events_min_interval_h: int = 2   # минимум часов между дублями из одной темы


settings = Settings()  # type: ignore[call-arg]
