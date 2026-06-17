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

    # Consecutive unhealthy checks required before an alert fires (anti-flapping).
    failure_threshold: int = 3

    # Days of check history to retain in the database (0 = keep forever).
    retention_days: int = 30

    # Alert providers — leave empty to disable
    discord_webhook_url: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""


settings = Settings()
