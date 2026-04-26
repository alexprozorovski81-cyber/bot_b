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
    database_url: str = "sqlite+aiosqlite:///./predictbet.db"
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

    # Разработка
    dev_mode: bool = False
    dev_telegram_id: int = 0

    # Платформа
    platform_fee_percent: Decimal = Decimal("2.0")
    initial_liquidity: Decimal = Decimal("1000")
    min_deposit_rub: Decimal = Decimal("100")
    min_deposit_usdt: Decimal = Decimal("1")
    support_username: str = "support"


settings = Settings()  # type: ignore[call-arg]
