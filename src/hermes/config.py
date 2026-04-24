# SPDX-License-Identifier: MIT
"""Configuration for ProjectHermes, loaded from environment variables / .env file."""

from __future__ import annotations

import re
import ssl
from functools import lru_cache
from ipaddress import ip_address
from typing import Optional

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_MIN_SECRET_LENGTH = 32
_HOSTNAME_RE = re.compile(
    r"^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*$"
)


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
    nats_connect_timeout: float = Field(default=5.0, gt=0)
    nats_publish_timeout: float = Field(default=5.0, gt=0)
    nats_retry_attempts: int = Field(default=3, ge=1)
    nats_retry_interval: float = Field(default=5.0, gt=0)
    agamemnon_timeout: float = Field(default=10.0, gt=0)
    shutdown_timeout: float = Field(default=10.0, gt=0)
    max_payload_bytes: int = 1_048_576
    enable_dead_letter: bool = True
    log_json: bool = False
    active_subjects_max: int = 1000
    webhook_rate_limit: str = "60/minute"
    webhook_rate_limit_key: str = "ip"
    publish_retries: int = Field(default=3, ge=1)
    publish_retry_base_delay: float = Field(default=0.1, gt=0)

    @field_validator("hermes_public_url", mode="before")
    @classmethod
    def _validate_and_normalize_public_url(cls, v: str | None) -> str | None:
        if v is None:
            return v
        from urllib.parse import urlparse

        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"HERMES_PUBLIC_URL must be http or https, got: {v!r}")
        return v.rstrip("/")

    @model_validator(mode="after")
    def _set_public_url_default(self) -> "Settings":
        if self.hermes_public_url is None:
            self.hermes_public_url = f"http://localhost:{self.hermes_port}"
        return self

    @field_validator("hermes_host")
    @classmethod
    def _validate_host(cls, v: str) -> str:
        try:
            ip_address(v)
            return v
        except ValueError:
            pass
        if _HOSTNAME_RE.match(v):
            return v
        raise ValueError(f"HERMES_HOST must be a valid IP or hostname, got: {v!r}")

    @field_validator("webhook_rate_limit")
    @classmethod
    def _validate_rate_limit(cls, v: str) -> str:
        if not re.match(r"^\d+\s*/\s*(second|minute|hour|day)$", v):
            raise ValueError(
                f"WEBHOOK_RATE_LIMIT must be like '100/minute', got: {v!r}"
            )
        return v

    @field_validator("webhook_secret")
    @classmethod
    def _secret_min_length(cls, v: str) -> str:
        if v and len(v) < _MIN_SECRET_LENGTH:
            raise ValueError(
                f"WEBHOOK_SECRET must be at least {_MIN_SECRET_LENGTH} characters when set"
            )
        return v

    # TLS configuration (all optional; no TLS by default)
    tls_ca_bundle: str | None = None
    tls_cert_file: str | None = None
    tls_key_file: str | None = None
    tls_verify: bool = True

    def build_ssl_context(self) -> ssl.SSLContext | None:
        """Return an SSLContext for NATS TLS connections, or None if TLS is not configured.

        An SSLContext is returned when any of the following is true:
        - ``tls_ca_bundle`` is set (custom CA bundle)
        - Both ``tls_cert_file`` and ``tls_key_file`` are set (mTLS client cert)
        - ``nats_url`` uses the ``tls://`` scheme

        Returns ``None`` when plaintext NATS is in use and no cert fields are set.
        """
        needs_tls = (
            self.tls_ca_bundle is not None
            or (self.tls_cert_file is not None and self.tls_key_file is not None)
            or self.nats_url.startswith("tls://")
        )
        if not needs_tls:
            return None

        ctx = ssl.create_default_context()

        if not self.tls_verify:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

        if self.tls_ca_bundle is not None:
            ctx.load_verify_locations(cafile=self.tls_ca_bundle)

        if self.tls_cert_file is not None and self.tls_key_file is not None:
            ctx.load_cert_chain(certfile=self.tls_cert_file, keyfile=self.tls_key_file)

        return ctx

    def httpx_verify(self) -> bool | str:
        """Return the value to pass as ``verify=`` to ``httpx.AsyncClient``.

        Returns the CA bundle path when set, otherwise the ``tls_verify`` bool.
        """
        if self.tls_ca_bundle is not None:
            return self.tls_ca_bundle
        return self.tls_verify


@lru_cache
def get_settings() -> Settings:
    """Return the cached Settings instance."""
    return Settings()


# Module-level singleton — allows tests to mutate settings in-place.
settings: Settings = get_settings()
