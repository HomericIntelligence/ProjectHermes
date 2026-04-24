# SPDX-License-Identifier: MIT
"""NATS JetStream publisher for ProjectHermes."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import OrderedDict, deque
from typing import Any

import nats
from nats.aio.client import Client as NATSClient
from nats.js import JetStreamContext

from hermes.metrics import (
    ACTIVE_SUBJECTS,
    DEAD_LETTERS,
    PUBLISH_LATENCY,
    WEBHOOKS_PUBLISHED,
)
from hermes.models import WebhookPayload

logger = logging.getLogger(__name__)

# Event types that map to agent subjects
AGENT_EVENTS: frozenset[str] = frozenset({"agent.created", "agent.updated", "agent.deleted"})
# Event types that map to task subjects
TASK_EVENTS: frozenset[str] = frozenset({"task.updated", "task.completed", "task.failed"})


_DEAD_LETTER_SUBJECT_PREFIX = "hi.deadletter"
_SLUG_MAX_LEN = 64  # Maximum characters per NATS subject slug token


class Publisher:
    """Publishes external webhook payloads to NATS JetStream."""

    def __init__(self, enable_dead_letter: bool = True, max_subjects: int | None = None) -> None:
        from hermes.config import get_settings

        self._nc: NATSClient | None = None
        self._js: JetStreamContext | None = None
        self._max_subjects: int = max_subjects if max_subjects is not None else get_settings().active_subjects_max
        self._active_subjects: OrderedDict[str, None] = OrderedDict()
        self._stream_names: list[str] = []
        self._dead_letters: deque[dict[str, Any]] = deque(maxlen=1000)
        self._enable_dead_letter = enable_dead_letter
        self._connected: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self, url: str, connect_timeout: float = 5.0) -> None:
        """Connect to the NATS server, obtain JetStream context, and ensure streams exist."""
        async def _on_disconnected() -> None:
            self._connected = False
            logger.warning("NATS disconnected")

        async def _on_reconnected() -> None:
            self._connected = True
            logger.info("NATS reconnected")

        self._nc = await nats.connect(
            url,
            allow_reconnect=False,
            connect_timeout=connect_timeout,
            disconnected_cb=_on_disconnected,
            reconnected_cb=_on_reconnected,
        )
        self._connected = True
        self._js = self._nc.jetstream()
        await self._ensure_streams()
        logger.info("Connected to NATS at %s", url)

    async def _ensure_streams(self) -> None:
        """Create JetStream streams if they don't exist yet."""
        from nats.js.api import StreamConfig
        from nats.js.errors import NotFoundError
        assert self._nc is not None
        jsm = self._nc.jsm()
        for name, subjects in (
            ("homeric-agents",      ["hi.agents.>"]),
            ("homeric-tasks",       ["hi.tasks.>"]),
            ("homeric-deadletter",  ["hi.deadletter.>"]),
        ):
            try:
                await jsm.stream_info(name)
            except NotFoundError:
                await jsm.add_stream(StreamConfig(name=name, subjects=subjects))
                logger.info("Created JetStream stream: %s (%s)", name, subjects)
            if name not in self._stream_names:
                self._stream_names.append(name)

    async def disconnect(self) -> None:
        """Drain and close the NATS connection."""
        if self._nc is not None:
            await self._nc.drain()
            self._nc = None
            self._js = None
            self._connected = False
            logger.info("Disconnected from NATS")

    @property
    def is_connected(self) -> bool:
        return self._connected and self._nc is not None

    @property
    def active_subjects(self) -> list[str]:
        return sorted(self._active_subjects)

    @property
    def stream_names(self) -> list[str]:
        return list(self._stream_names)

    @property
    def dead_letters(self) -> list[dict[str, Any]]:
        return list(self._dead_letters)

    @property
    def dead_letter_count(self) -> int:
        return len(self._dead_letters)

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------

    async def publish(
        self,
        payload: WebhookPayload,
        publish_timeout: float = 5.0,
        *,
        request_id: str = "",
        publish_retries: int | None = None,
        publish_retry_base_delay: float | None = None,
    ) -> None:
        """Route a webhook payload to the appropriate NATS subject.

        Args:
            payload: The incoming webhook payload to publish.
            publish_timeout: Timeout in seconds for the NATS publish call.
            request_id: Correlation ID from the originating HTTP request; included
                in the NATS message to enable end-to-end tracing.
            publish_retries: Max total attempts (default from Settings).
            publish_retry_base_delay: Base delay in seconds for exponential backoff
                (default from Settings). Actual delay = base * 2^attempt, capped at 2s.
        """
        if self._js is None:
            raise RuntimeError("Publisher is not connected to NATS")

        from hermes.config import get_settings

        settings = get_settings()
        retries = publish_retries if publish_retries is not None else settings.publish_retries
        base_delay = (
            publish_retry_base_delay
            if publish_retry_base_delay is not None
            else settings.publish_retry_base_delay
        )

        subject = self._resolve_subject(payload)

        message = json.dumps(
            {
                "event": payload.event,
                "data": payload.data,
                "timestamp": payload.timestamp.isoformat(),
                "request_id": request_id,
            }
        ).encode()

        if subject is None:
            if self._enable_dead_letter:
                dead_subject = f"{_DEAD_LETTER_SUBJECT_PREFIX}.{_slug(payload.event)}"
                await self._js.publish(dead_subject, message, timeout=publish_timeout)
                self._dead_letters.append({"event": payload.event, "subject": dead_subject})
                DEAD_LETTERS.labels(event_type=payload.event).inc()
                self._track_subject(dead_subject)
                logger.warning(
                    "No subject mapping for event type %r; dead-lettered to %s",
                    payload.event,
                    dead_subject,
                )
            else:
                logger.warning("No subject mapping for event type %r; dropping", payload.event)
            return

        _RETRYABLE = (nats.errors.TimeoutError, nats.errors.NoRespondersError)

        last_exc: Exception | None = None
        for attempt in range(retries):
            try:
                t0 = time.perf_counter()
                await self._js.publish(subject, message, timeout=publish_timeout)
                PUBLISH_LATENCY.observe(time.perf_counter() - t0)
                WEBHOOKS_PUBLISHED.labels(subject_prefix=subject.split(".")[1]).inc()
                self._track_subject(subject)
                logger.info("Published to %s (request_id=%s)", subject, request_id)
                return
            except _RETRYABLE as exc:
                last_exc = exc
                if attempt < retries - 1:
                    delay = min(base_delay * (2 ** attempt), 2.0)
                    logger.warning(
                        "Transient NATS error on attempt %d/%d for %s; retrying in %.3fs: %s",
                        attempt + 1,
                        retries,
                        subject,
                        delay,
                        exc,
                    )
                    await asyncio.sleep(delay)

        raise last_exc  # type: ignore[misc]

    def _track_subject(self, subject: str) -> None:
        """Add subject to the LRU OrderedDict, evicting the oldest if at capacity."""
        if subject in self._active_subjects:
            self._active_subjects.move_to_end(subject)
        else:
            if len(self._active_subjects) >= self._max_subjects:
                evicted, _ = self._active_subjects.popitem(last=False)
                logger.warning("active_subjects LRU cap (%d) reached; evicted %s", self._max_subjects, evicted)
            self._active_subjects[subject] = None
        ACTIVE_SUBJECTS.set(len(self._active_subjects))

    # ------------------------------------------------------------------
    # Subject resolution
    # ------------------------------------------------------------------

    def _resolve_subject(self, payload: WebhookPayload) -> str | None:
        """Return the NATS subject for a given webhook payload, or None."""
        if payload.event in AGENT_EVENTS:
            return self._parse_agent_subject(payload.data, payload.event)
        if payload.event in TASK_EVENTS:
            return self._parse_task_subject(payload.data, payload.event)
        return None

    def _parse_agent_subject(self, data: dict[str, Any], event: str) -> str:
        """Build ``hi.agents.{host}.{name}.{event}`` from agent event data.

        Falls back to ``unknown`` tokens when fields are missing so messages
        are never silently dropped due to incomplete payloads.
        """
        host = _slug(data.get("hostId") or data.get("host") or "") or "unknown"
        name = _slug(data.get("name") or "") or "unknown"
        # Strip the "agent." prefix to get the bare verb (created/updated/deleted)
        verb = event.split(".", 1)[-1] if "." in event else event
        return f"hi.agents.{host}.{name}.{verb}"

    def _parse_task_subject(self, data: dict[str, Any], event: str) -> str:
        """Build ``hi.tasks.{team_id}.{task_id}.{event}`` from task event data."""
        team_id = _slug(data.get("teamId") or data.get("team_id") or "") or "unknown"
        task_id = _slug(data.get("id") or data.get("task_id") or "") or "unknown"
        verb = event.split(".", 1)[-1] if "." in event else event
        return f"hi.tasks.{team_id}.{task_id}.{verb}"


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _slug(value: str) -> str:
    """Sanitise a token for use in a NATS subject (replace spaces/dots/wildcards).

    Tokens are truncated to ``_SLUG_MAX_LEN`` (64) characters to keep NATS subjects
    within reasonable bounds.
    """
    return (
        str(value)
        .strip()
        .replace(" ", "-")
        .replace(".", "-")
        .replace("*", "")
        .replace(">", "")
        .lower()
    )[:_SLUG_MAX_LEN]
