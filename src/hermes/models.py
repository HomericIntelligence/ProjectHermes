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
