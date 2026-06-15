"""Application settings loaded from environment / .env via pydantic-settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MONITOR_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "infra-health-monitor"
    debug: bool = False

    database_url: str = "sqlite:///./monitor.db"
    redis_url: str = "redis://localhost:6379/0"
    services_file: str = "services.yaml"

    default_timeout: float = 5.0

    cpu_threshold: float = 90.0
    memory_threshold: float = 90.0
    disk_threshold: float = 90.0

    # Alert providers — leave empty to disable
    discord_webhook_url: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""


settings = Settings()
