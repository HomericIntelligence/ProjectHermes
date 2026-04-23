"""Pydantic models for ProjectHermes webhook payloads and NATS events."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class WebhookPayload(BaseModel):
    """Incoming webhook payload from an external service."""

    event: str
    data: dict[str, Any]
    timestamp: str
    signature: str | None = None


class AgentEvent(BaseModel):
    """Structured representation of an agent lifecycle event."""

    host: str
    name: str
    event: str
    agent_id: str
    metadata: dict[str, Any] = {}


class TaskEvent(BaseModel):
    """Structured representation of a task state-change event."""

    team_id: str
    task_id: str
    event: str
    status: str
    metadata: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    """Response body for GET /health."""

    status: str
    nats_connected: bool
    shutting_down: bool = False
    hmac_validation_enabled: bool = False


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
