"""Configuration for ProjectHermes, loaded from environment variables / .env file."""

from __future__ import annotations

import ssl
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
    active_subjects_max: int = 1000

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
