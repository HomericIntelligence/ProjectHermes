"""Configuration for ProjectHermes, loaded from environment variables / .env file."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings resolved from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    nats_url: str = "nats://localhost:4222"
    hermes_host: str = "127.0.0.1"
    hermes_port: int = 8080
    webhook_secret: str = ""
    nats_connect_timeout: float = 5.0
    nats_publish_timeout: float = 5.0
    agamemnon_timeout: float = 10.0
    enable_dead_letter: bool = True


@lru_cache
def get_settings() -> Settings:
    """Return the cached Settings instance."""
    return Settings()


# Module-level singleton — allows tests to mutate settings in-place.
settings: Settings = get_settings()
