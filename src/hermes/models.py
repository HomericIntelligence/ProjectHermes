# SPDX-License-Identifier: MIT
"""Pydantic models for ProjectHermes webhook payloads and NATS events."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class HermesEventBase(BaseModel):
    """Shared base for all NATS wire-format event models."""

    model_config = ConfigDict(frozen=True)

    schema_version: int = Field(default=1, ge=1, description="Wire format schema version")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("timestamp")
    @classmethod
    def require_timezone(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        return v


class WebhookPayload(BaseModel):
    """Incoming webhook payload from an external service (inbound DTO)."""

    event: str
    data: dict[str, Any]
    timestamp: datetime
    signature: str | None = None

    @field_validator("timestamp")
    @classmethod
    def require_timezone(cls, v: datetime) -> datetime:
        """Reject naive (timezone-unaware) timestamps at the boundary."""
        if v.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        return v


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class TimeoutSettings(BaseModel):
    """Timeout values (seconds) surfaced in /health for observability."""

    nats_connect: float
    nats_publish: float
    agamemnon: float


class HealthResponse(BaseModel):
    """Response body for GET /health."""

    status: str
    nats_connected: bool
    shutting_down: bool = False
    hmac_validation_enabled: bool = False
    hermes_public_url: str | None = None
    inflight_requests: int = 0
    dead_letter_count: int = 0
    timeouts: TimeoutSettings | None = None


class WebhookAcceptedResponse(BaseModel):
    """Response body for POST /webhook (202 Accepted)."""

    status: str
    event: str


class SubjectsResponse(BaseModel):
    """Response body for GET /subjects."""

    subjects: list[str]


class ErrorResponse(BaseModel):
    """Standard error response body."""

    detail: str
    request_id: str | None = None


class VersionResponse(BaseModel):
    """Response body for GET /version."""

    version: str


class DeadLettersResponse(BaseModel):
    """Response body for GET /dead-letters (paginated)."""

    total: int
    offset: int
    limit: int | None
    items: list[dict[str, Any]]
