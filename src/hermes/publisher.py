"""NATS JetStream publisher for ProjectHermes."""

from __future__ import annotations

import json
import logging
from typing import Any

import nats
from nats.aio.client import Client as NATSClient
from nats.js import JetStreamContext

from hermes.models import WebhookPayload

logger = logging.getLogger(__name__)

# Event types that map to agent subjects
_AGENT_EVENTS = {"agent.created", "agent.updated", "agent.deleted"}
# Event types that map to task subjects
_TASK_EVENTS = {"task.updated", "task.completed", "task.failed"}


class Publisher:
    """Publishes external webhook payloads to NATS JetStream."""

    def __init__(self) -> None:
        self._nc: NATSClient | None = None
        self._js: JetStreamContext | None = None
        self._active_subjects: set[str] = set()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self, url: str) -> None:
        """Connect to the NATS server, obtain JetStream context, and ensure streams exist."""
        self._nc = await nats.connect(url)
        self._js = self._nc.jetstream()
        await self._ensure_streams()
        logger.info("Connected to NATS at %s", url)

    async def _ensure_streams(self) -> None:
        """Create JetStream streams if they don't exist yet."""
        from nats.js.api import StreamConfig
        jsm = self._nc.jsm()
        for name, subjects in (
            ("homeric-agents", ["hi.agents.>"]),
            ("homeric-tasks",  ["hi.tasks.>"]),
        ):
            try:
                await jsm.find_stream(subjects[0])
            except Exception:
                await jsm.add_stream(StreamConfig(name=name, subjects=subjects))
                logger.info("Created JetStream stream: %s (%s)", name, subjects)

    async def disconnect(self) -> None:
        """Drain and close the NATS connection."""
        if self._nc is not None:
            await self._nc.drain()
            self._nc = None
            self._js = None
            logger.info("Disconnected from NATS")

    @property
    def is_connected(self) -> bool:
        return self._nc is not None and not self._nc.is_closed

    @property
    def active_subjects(self) -> list[str]:
        return sorted(self._active_subjects)

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------

    async def publish(self, payload: WebhookPayload) -> None:
        """Route a webhook payload to the appropriate NATS subject."""
        if self._js is None:
            raise RuntimeError("Publisher is not connected to NATS")

        subject = self._resolve_subject(payload)
        if subject is None:
            logger.warning("No subject mapping for event type %r; dropping", payload.event)
            return

        message = json.dumps(
            {
                "event": payload.event,
                "data": payload.data,
                "timestamp": payload.timestamp,
            }
        ).encode()

        await self._js.publish(subject, message)
        self._active_subjects.add(subject)
        logger.info("Published to %s", subject)

    # ------------------------------------------------------------------
    # Subject resolution
    # ------------------------------------------------------------------

    def _resolve_subject(self, payload: WebhookPayload) -> str | None:
        """Return the NATS subject for a given webhook payload, or None."""
        if payload.event in _AGENT_EVENTS:
            return self._parse_agent_subject(payload.data, payload.event)
        if payload.event in _TASK_EVENTS:
            return self._parse_task_subject(payload.data, payload.event)
        return None

    def _parse_agent_subject(self, data: dict[str, Any], event: str) -> str:
        """Build ``hi.agents.{host}.{name}.{event}`` from agent event data.

        Falls back to ``unknown`` tokens when fields are missing so messages
        are never silently dropped due to incomplete payloads.
        """
        host = _slug(data.get("hostId") or data.get("host") or "unknown")
        name = _slug(data.get("name") or "unknown")
        # Strip the "agent." prefix to get the bare verb (created/updated/deleted)
        verb = event.split(".", 1)[-1] if "." in event else event
        return f"hi.agents.{host}.{name}.{verb}"

    def _parse_task_subject(self, data: dict[str, Any], event: str) -> str:
        """Build ``hi.tasks.{team_id}.{task_id}.{event}`` from task event data."""
        team_id = _slug(data.get("teamId") or data.get("team_id") or "unknown")
        task_id = _slug(data.get("id") or data.get("task_id") or "unknown")
        verb = event.split(".", 1)[-1] if "." in event else event
        return f"hi.tasks.{team_id}.{task_id}.{verb}"


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _slug(value: str) -> str:
    """Sanitise a token for use in a NATS subject (replace spaces/dots)."""
    return str(value).replace(" ", "-").replace(".", "-").lower()
