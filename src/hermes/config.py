# SPDX-License-Identifier: MIT
"""Configuration for ProjectHermes, loaded from environment variables / .env file."""

from __future__ import annotations

import logging
import re
import ssl
from functools import lru_cache
from ipaddress import ip_address
from typing import Optional

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_config_logger = logging.getLogger(__name__)

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
    dead_letter_api_key: str = ""
    shutdown_timeout: float = Field(default=10.0, gt=0)
    max_payload_bytes: int = 1_048_576
    enable_dead_letter: bool = True
    dead_letter_max_size: int = Field(default=1000, ge=1)
    dead_letter_ttl_seconds: int = Field(default=86400, ge=0)
    dead_letter_alert_threshold: float = Field(default=0.8, gt=0, le=1.0)
    dead_letter_page_size_default: int = Field(default=100, ge=1)
    dead_letter_page_size_max: int = Field(default=500, ge=1)
    log_json: bool = False
    active_subjects_max: int = 1000
    webhook_rate_limit: str = "60/minute"
    # ``webhook_rate_limit_key`` selects the slowapi key strategy for /webhook.
    # Currently only "ip" (== ``slowapi.util.get_remote_address``) is wired up in
    # ``hermes.rate_limit``; "endpoint" is reserved for a future per-route key strategy. The
    # validator below rejects unknown values at startup so misconfiguration fails loud.
    webhook_rate_limit_key: str = "ip"
    subjects_rate_limit: str = "60/minute"
    publish_retries: int = Field(default=3, ge=1)
    publish_retry_base_delay: float = Field(default=0.1, gt=0)
    nats_reconnect_interval: float = Field(default=5.0, gt=0)
    nats_reconnect_hard_timeout: float = Field(default=5.0, gt=0)

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

    @field_validator("webhook_rate_limit", "subjects_rate_limit")
    @classmethod
    def _validate_rate_limit(cls, v: str) -> str:
        if not re.match(r"^\d+\s*/\s*(second|minute|hour|day)$", v):
            raise ValueError(f"Rate limit must be like '100/minute', got: {v!r}")
        return v

    @field_validator("webhook_rate_limit_key")
    @classmethod
    def _validate_rate_limit_key(cls, v: str) -> str:
        allowed = {"ip", "endpoint"}
        if v not in allowed:
            raise ValueError(
                f"WEBHOOK_RATE_LIMIT_KEY must be one of {sorted(allowed)}, got: {v!r}"
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

    @field_validator("dead_letter_api_key")
    @classmethod
    def _dead_letter_key_min_length(cls, v: str) -> str:
        if v and len(v) < _MIN_SECRET_LENGTH:
            raise ValueError(
                f"DEAD_LETTER_API_KEY must be at least {_MIN_SECRET_LENGTH} characters when set"
            )
        return v

    @model_validator(mode="after")
    def _warn_dead_letter_key_unset(self) -> "Settings":
        if not self.dead_letter_api_key:
            _config_logger.warning(
                "DEAD_LETTER_API_KEY is not set — GET /dead-letters and DELETE /dead-letters "
                "are unauthenticated and accessible to any client that can reach Hermes."
            )
        return self

    # TLS configuration (all optional; no TLS by default)
    tls_ca_bundle: str | None = None
    tls_cert_file: str | None = None
    tls_key_file: str | None = None
    tls_verify: bool = True

    @model_validator(mode="after")
    def _warn_hmac_disabled_in_production(self) -> "Settings":
        """Emit a loud WARNING when WEBHOOK_SECRET is unset while bound to all interfaces.

        Production is inferred when ``hermes_host`` is ``0.0.0.0`` (listening on all interfaces).
        Without a secret, any source can POST to /webhook without authentication.
        """
        if not self.webhook_secret and self.hermes_host == "0.0.0.0":
            _config_logger.warning(
                "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n"
                "  WEBHOOK_SECRET is NOT SET while HERMES_HOST=0.0.0.0 (PRODUCTION)\n"
                "  HMAC webhook signature validation is DISABLED — any source can\n"
                "  POST to /webhook without authentication.  Set WEBHOOK_SECRET to\n"
                "  a random string of at least 32 characters to enable validation.\n"
                "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!",
            )
        return self

    @model_validator(mode="after")
    def _warn_tls_verify_disabled(self) -> "Settings":
        """Emit a loud WARNING when TLS verification is disabled in a production-like environment.

        Production is inferred when ``hermes_host`` is ``0.0.0.0`` (listening on all interfaces),
        which is the conventional deployment binding.  This is a defence-in-depth guard; the
        authoritative production signal is the operator's configuration management.
        """
        if not self.tls_verify and self.hermes_host == "0.0.0.0":
            _config_logger.warning(
                "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n"
                "  TLS_VERIFY=false IS SET WHILE HERMES_HOST=0.0.0.0 (PRODUCTION)\n"
                "  TLS certificate verification is DISABLED — this is INSECURE and\n"
                "  MUST NOT be used in production.  Set TLS_VERIFY=true or provide\n"
                "  a valid CA bundle via TLS_CA_BUNDLE.\n"
                "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!",
            )
        return self

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
