# SPDX-License-Identifier: MIT
"""NATS JetStream publisher for ProjectHermes."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections import OrderedDict, deque
from typing import Any

import nats
from nats.aio.client import Client as NATSClient
from nats.js import JetStreamContext

from hermes.metrics import (
    ACTIVE_SUBJECTS,
    DEAD_LETTER_QUEUE_ALERTS,
    DEAD_LETTER_QUEUE_DEPTH,
    DEAD_LETTERS,
    NATS_RECONNECTS,
    PUBLISH_LATENCY,
    WEBHOOKS_PUBLISHED,
)
from hermes.models import WebhookPayload

logger = logging.getLogger(__name__)

# Event types that map to agent subjects
AGENT_EVENTS: frozenset[str] = frozenset({"agent.created", "agent.updated", "agent.deleted"})
# Event types that map to task subjects
TASK_EVENTS: frozenset[str] = frozenset({"task.updated", "task.completed", "task.failed"})


class UnknownEventTypeError(ValueError):
    """Raised by Publisher.publish() when an event type is unrecognised and dead-lettering is disabled."""


_DEAD_LETTER_SUBJECT_PREFIX = "hi.deadletter"
_SLUG_MAX_LEN = 64  # Maximum characters per NATS subject slug token

# Transient NATS errors that are safe to retry.  Non-retryable errors (e.g.
# AuthorizationError, BadSubjectError) propagate immediately without retrying.
# ConnectionReconnectingError and StaleConnectionError occur when NATS loses its
# connection between the is_connected check in server.py and the actual publish call
# (a TOCTOU race during reconnection).  These are transient and safe to retry.
_RETRYABLE_PUBLISH_ERRORS = (
    nats.errors.TimeoutError,
    nats.errors.NoRespondersError,
    nats.errors.DrainTimeoutError,
    nats.errors.ConnectionReconnectingError,
    nats.errors.StaleConnectionError,
)


class Publisher:
    """Publishes external webhook payloads to NATS JetStream."""

    def __init__(self, enable_dead_letter: bool = True, max_subjects: int | None = None) -> None:
        from hermes.config import get_settings

        s = get_settings()
        self._nc: NATSClient | None = None
        self._js: JetStreamContext | None = None
        self._max_subjects: int = max_subjects if max_subjects is not None else s.active_subjects_max
        self._active_subjects: OrderedDict[str, None] = OrderedDict()
        self._stream_names: list[str] = []
        self._dead_letters: deque[dict[str, Any]] = deque(maxlen=s.dead_letter_max_size)
        self._dead_letter_alert_threshold: float = s.dead_letter_alert_threshold
        self._enable_dead_letter = enable_dead_letter
        self._connected: bool = False
        self.reconnect_count: int = 0
        self.last_error: str = ""
        self._stop_event: asyncio.Event = asyncio.Event()
        self._reconnect_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self, url: str, connect_timeout: float = 5.0) -> None:
        """Connect to NATS, ensure streams exist, and start the background reconnect loop."""
        from hermes.config import get_settings

        settings = get_settings()
        await self._connect_internal(url, connect_timeout)
        logger.info("Connected to NATS at %s", url)
        self._stop_event = asyncio.Event()
        self._reconnect_task = asyncio.ensure_future(
            self._reconnect_loop(
                url,
                connect_timeout,
                settings.nats_reconnect_interval,
                settings.nats_reconnect_hard_timeout,
            )
        )

    async def _connect_internal(self, url: str, connect_timeout: float) -> None:
        """Low-level connect: open the NATS socket, register callbacks, get JetStream context."""

        async def _on_disconnected() -> None:
            self._connected = False
            self.last_error = "NATS disconnected"
            logger.warning("NATS disconnected")

        async def _on_reconnected() -> None:
            self._connected = True
            self.reconnect_count += 1
            logger.info("NATS reconnected (count=%d)", self.reconnect_count)

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

    async def _reconnect_loop(
        self,
        url: str,
        connect_timeout: float,
        reconnect_interval: float,
        hard_timeout: float,
    ) -> None:
        """Background task: detect NATS connection loss and attempt to reconnect."""
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=reconnect_interval)
                return  # stop_event fired during sleep
            except asyncio.TimeoutError:
                pass
            if self._stop_event.is_set():
                return
            if self._nc is not None and not self._nc.is_closed:
                continue  # connection still alive
            logger.warning("NATS connection lost; attempting reconnect")
            try:
                await asyncio.wait_for(
                    self._connect_internal(url, connect_timeout), timeout=hard_timeout
                )
                self.reconnect_count += 1
                NATS_RECONNECTS.labels(result="success").inc()
                logger.info("NATS reconnected via external loop (count=%d)", self.reconnect_count)
            except Exception as exc:
                self.last_error = str(exc)
                NATS_RECONNECTS.labels(result="failed").inc()
                logger.error("NATS reconnect failed: %s", exc)

    async def _ensure_streams(self) -> None:
        """Create JetStream streams if they don't exist yet."""
        from datetime import timedelta

        from nats.js.api import StreamConfig
        from nats.js.errors import NotFoundError

        from hermes.config import get_settings

        assert self._nc is not None
        s = get_settings()
        jsm = self._nc.jsm()

        dead_letter_ttl = (
            timedelta(seconds=s.dead_letter_ttl_seconds)
            if s.dead_letter_ttl_seconds > 0
            else None
        )

        stream_configs: list[tuple[str, list[str], dict[str, Any]]] = [
            ("homeric-agents", ["hi.agents.>"], {}),
            ("homeric-tasks", ["hi.tasks.>"], {}),
            (
                "homeric-deadletter",
                ["hi.deadletter.>"],
                {"max_age": dead_letter_ttl} if dead_letter_ttl is not None else {},
            ),
        ]

        for name, subjects, extra in stream_configs:
            try:
                await jsm.stream_info(name)
            except NotFoundError:
                await jsm.add_stream(StreamConfig(name=name, subjects=subjects, **extra))
                logger.info("Created JetStream stream: %s (%s)", name, subjects)
            if name not in self._stream_names:
                self._stream_names.append(name)

    async def disconnect(self) -> None:
        """Stop the reconnect loop, drain, and close the NATS connection."""
        self._stop_event.set()
        if self._reconnect_task is not None:
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except (asyncio.CancelledError, Exception):
                pass
            self._reconnect_task = None
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
    def active_subjects_max(self) -> int:
        return self._max_subjects

    @property
    def stream_names(self) -> list[str]:
        return list(self._stream_names)

    @property
    def dead_letters(self) -> list[dict[str, Any]]:
        return list(self._dead_letters)

    @property
    def dead_letter_count(self) -> int:
        return len(self._dead_letters)

    def drain_dead_letters(self) -> int:
        """Clear the dead-letter queue and return the number of drained items."""
        count = len(self._dead_letters)
        self._dead_letters.clear()
        DEAD_LETTER_QUEUE_DEPTH.set(0)
        return count

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
                "schema_version": 1,
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
                depth = len(self._dead_letters)
                DEAD_LETTER_QUEUE_DEPTH.set(depth)
                capacity = self._dead_letters.maxlen or 0
                if capacity > 0 and depth >= self._dead_letter_alert_threshold * capacity:
                    logger.warning(
                        "Dead-letter queue depth %d/%d exceeds %.0f%% threshold",
                        depth,
                        capacity,
                        self._dead_letter_alert_threshold * 100,
                    )
                    DEAD_LETTER_QUEUE_ALERTS.inc()
                logger.warning(
                    "No subject mapping for event type %r; dead-lettered to %s",
                    payload.event,
                    dead_subject,
                )
            else:
                raise UnknownEventTypeError(payload.event)
            return

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
            except _RETRYABLE_PUBLISH_ERRORS as exc:
                last_exc = exc
                if attempt < retries - 1:
                    delay = min(base_delay * (2**attempt), 2.0) * random.uniform(0.5, 1.5)
                    logger.warning(
                        "Transient NATS error on attempt %d/%d for %s; retrying in %.3fs: %s",
                        attempt + 1,
                        retries,
                        subject,
                        delay,
                        exc,
                    )
                    await asyncio.sleep(delay)

        raise last_exc or RuntimeError("unreachable")

    def _track_subject(self, subject: str) -> None:
        """Add subject to the LRU OrderedDict, evicting the oldest if at capacity."""
        if subject in self._active_subjects:
            self._active_subjects.move_to_end(subject)
        else:
            if len(self._active_subjects) >= self._max_subjects:
                evicted, _ = self._active_subjects.popitem(last=False)
                logger.warning(
                    "active_subjects LRU cap (%d) reached; evicted %s", self._max_subjects, evicted
                )
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
        raw_host = data.get("hostId") or data.get("host")
        raw_name = data.get("name")
        if not raw_host:
            logger.warning(
                "agent event missing 'host' field; using 'unknown'", extra={"event": event}
            )
        if not raw_name:
            logger.warning(
                "agent event missing 'name' field; using 'unknown'", extra={"event": event}
            )
        host = _slug(raw_host or "unknown") or "unknown"
        name = _slug(raw_name or "unknown") or "unknown"
        # Strip the "agent." prefix to get the bare verb (created/updated/deleted)
        verb = event.split(".", 1)[-1] if "." in event else event
        return f"hi.agents.{host}.{name}.{verb}"

    def _parse_task_subject(self, data: dict[str, Any], event: str) -> str:
        """Build ``hi.tasks.{team_id}.{task_id}.{event}`` from task event data."""
        raw_team_id = data.get("teamId") or data.get("team_id")
        raw_task_id = data.get("id") or data.get("task_id")
        if not raw_team_id:
            logger.warning(
                "task event missing 'team_id' field; using 'unknown'", extra={"event": event}
            )
        if not raw_task_id:
            logger.warning(
                "task event missing 'task_id' field; using 'unknown'", extra={"event": event}
            )
        team_id = _slug(raw_team_id or "unknown") or "unknown"
        task_id = _slug(raw_task_id or "unknown") or "unknown"
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
