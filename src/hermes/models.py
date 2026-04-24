"""Pydantic models for ProjectHermes webhook payloads and NATS events."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class HermesEventBase(BaseModel):
    """Shared base for all NATS wire-format event models."""

    model_config = ConfigDict(frozen=True)

    schema_version: int = Field(default=1, ge=1, description="Wire format schema version")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AgentEvent(HermesEventBase):
    """Structured representation of an agent lifecycle event."""

    host: str
    name: str
    event: str
    agent_id: str
    metadata: dict[str, Any] = {}


class TaskEvent(HermesEventBase):
    """Structured representation of a task state-change event."""

    team_id: str
    task_id: str
    event: str
    status: str
    metadata: dict[str, Any] = {}


class WebhookPayload(BaseModel):
    """Incoming webhook payload from an external service (inbound DTO)."""

    event: str
    data: dict[str, Any]
    timestamp: datetime
    signature: str | None = None


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    """Response body for GET /health."""

    status: str
    nats_connected: bool
    shutting_down: bool = False
    hmac_validation_enabled: bool = False
    hermes_public_url: str | None = None


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


class VersionResponse(BaseModel):
    """Response body for GET /version."""

    version: str
