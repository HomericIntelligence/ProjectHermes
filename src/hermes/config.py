"""Configuration for ProjectHermes, loaded from environment variables / .env file."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings resolved from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    maestro_url: str = "http://172.20.0.1:23000"
    maestro_api_key: str = ""
    nats_url: str = "nats://localhost:4222"
    hermes_port: int = 8080
    webhook_secret: str = ""


settings = Settings()
