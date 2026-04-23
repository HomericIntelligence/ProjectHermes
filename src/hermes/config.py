"""Configuration for ProjectHermes, loaded from environment variables / .env file."""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_MIN_SECRET_LENGTH = 32


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
    hermes_public_url: Optional[str] = None
    webhook_secret: str = ""
    nats_connect_timeout: float = 5.0
    nats_publish_timeout: float = 5.0
    agamemnon_timeout: float = 10.0
    shutdown_timeout: float = 10.0
    enable_dead_letter: bool = True
    log_json: bool = False

    @model_validator(mode="after")
    def _set_public_url_default(self) -> "Settings":
        if self.hermes_public_url is None:
            self.hermes_public_url = f"http://localhost:{self.hermes_port}"
        return self

    @field_validator("webhook_secret")
    @classmethod
    def _secret_min_length(cls, v: str) -> str:
        if v and len(v) < _MIN_SECRET_LENGTH:
            raise ValueError(
                f"WEBHOOK_SECRET must be at least {_MIN_SECRET_LENGTH} characters when set"
            )
        return v


@lru_cache
def get_settings() -> Settings:
    """Return the cached Settings instance."""
    return Settings()


# Module-level singleton — allows tests to mutate settings in-place.
settings: Settings = get_settings()
