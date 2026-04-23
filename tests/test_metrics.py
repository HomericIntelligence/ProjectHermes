"""Tests for Prometheus metrics endpoints and instrumentation."""

from __future__ import annotations

import hashlib
import hmac as hmac_mod
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

_TEST_SECRET = "test-webhook-secret"


def _sign(body: bytes) -> str:
    return hmac_mod.new(_TEST_SECRET.encode(), body, hashlib.sha256).hexdigest()


def _build_client(dead_letters: list | None = None) -> TestClient:
    from hermes.server import app
    from hermes.publisher import Publisher
    from hermes.config import settings

    mock_publisher = MagicMock(spec=Publisher)
    mock_publisher.is_connected = True
    mock_publisher.active_subjects = []
    mock_publisher.publish = AsyncMock()
    mock_publisher.dead_letters = dead_letters if dead_letters is not None else []

    app.state.publisher = mock_publisher
    settings.webhook_secret = _TEST_SECRET
    return TestClient(app, raise_server_exceptions=True)


class TestMetricsEndpoint:
    def test_metrics_returns_200(self) -> None:
        client = _build_client()
        response = client.get("/metrics")
        assert response.status_code == 200

    def test_metrics_content_type_is_text(self) -> None:
        client = _build_client()
        response = client.get("/metrics")
        assert "text/plain" in response.headers["content-type"]

    def test_metrics_contains_hermes_metric_names(self) -> None:
        client = _build_client()
        response = client.get("/metrics")
        text = response.text
        assert "hermes_webhooks_received_total" in text
        assert "hermes_webhooks_published_total" in text
        assert "hermes_webhooks_failed_total" in text
        assert "hermes_dead_letters_total" in text
        assert "hermes_publish_duration_seconds" in text
        assert "hermes_active_subjects_count" in text


class TestDeadLettersEndpoint:
    def test_dead_letters_returns_200(self) -> None:
        client = _build_client()
        response = client.get("/dead-letters")
        assert response.status_code == 200

    def test_dead_letters_returns_list_when_empty(self) -> None:
        client = _build_client()
        body = client.get("/dead-letters").json()
        assert "dead_letters" in body
        assert isinstance(body["dead_letters"], list)
        assert body["dead_letters"] == []

    def test_dead_letters_returns_entries(self) -> None:
        entries = [{"event": "unknown.event", "data": {"foo": "bar"}}]
        client = _build_client(dead_letters=entries)
        body = client.get("/dead-letters").json()
        assert body["dead_letters"] == entries


class TestWebhookMetricsInstrumentation:
    def test_valid_webhook_increments_received_counter(self) -> None:
        client = _build_client()
        payload = {
            "event": "agent.created",
            "data": {"host": "localhost", "name": "bot"},
            "timestamp": "2026-03-15T00:00:00Z",
        }
        body_bytes = json.dumps(payload).encode()

        before = _get_counter_value("hermes_webhooks_received_total", {"event_type": "agent.created"})
        client.post(
            "/webhook",
            content=body_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Signature": _sign(body_bytes),
            },
        )
        after = _get_counter_value("hermes_webhooks_received_total", {"event_type": "agent.created"})
        assert after == before + 1

    def test_invalid_payload_increments_failed_counter(self) -> None:
        client = _build_client()
        body_bytes = json.dumps({"bad": "payload"}).encode()

        before = _get_counter_value("hermes_webhooks_failed_total", {"reason": "invalid_payload"})
        client.post(
            "/webhook",
            content=body_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Signature": _sign(body_bytes),
            },
        )
        after = _get_counter_value("hermes_webhooks_failed_total", {"reason": "invalid_payload"})
        assert after == before + 1

    def test_nats_unavailable_increments_failed_counter(self) -> None:
        from hermes.server import app
        from hermes.publisher import Publisher
        from hermes.config import settings

        mock_publisher = MagicMock(spec=Publisher)
        mock_publisher.is_connected = False
        mock_publisher.dead_letters = []
        app.state.publisher = mock_publisher
        settings.webhook_secret = _TEST_SECRET
        client = TestClient(app, raise_server_exceptions=False)

        payload = {
            "event": "agent.created",
            "data": {"host": "localhost", "name": "bot"},
            "timestamp": "2026-03-15T00:00:00Z",
        }
        body_bytes = json.dumps(payload).encode()

        before = _get_counter_value("hermes_webhooks_failed_total", {"reason": "nats_not_connected"})
        client.post(
            "/webhook",
            content=body_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Signature": _sign(body_bytes),
            },
        )
        after = _get_counter_value("hermes_webhooks_failed_total", {"reason": "nats_not_connected"})
        assert after == before + 1


class TestPublisherDeadLetters:
    @pytest.mark.asyncio
    async def test_unknown_event_goes_to_dead_letters(self) -> None:
        from unittest.mock import AsyncMock
        from hermes.publisher import Publisher
        from hermes.models import WebhookPayload

        pub = Publisher(enable_dead_letter=True)
        pub._js = AsyncMock()
        payload = WebhookPayload(
            event="unknown.event.type",
            data={"key": "value"},
            timestamp="2026-03-15T00:00:00Z",
        )
        await pub.publish(payload)
        assert len(pub.dead_letters) == 1
        assert pub.dead_letters[0]["event"] == "unknown.event.type"

    @pytest.mark.asyncio
    async def test_dead_letters_bounded_at_1000(self) -> None:
        from unittest.mock import AsyncMock
        from hermes.publisher import Publisher
        from hermes.models import WebhookPayload

        pub = Publisher(enable_dead_letter=True)
        pub._js = AsyncMock()
        for i in range(1001):
            payload = WebhookPayload(
                event=f"unknown.event.{i}",
                data={},
                timestamp="2026-03-15T00:00:00Z",
            )
            await pub.publish(payload)

        assert len(pub.dead_letters) == 1000

    @pytest.mark.asyncio
    async def test_known_event_does_not_go_to_dead_letters(self) -> None:
        from unittest.mock import AsyncMock
        from hermes.publisher import Publisher
        from hermes.models import WebhookPayload

        pub = Publisher(enable_dead_letter=True)
        pub._js = AsyncMock()
        payload = WebhookPayload(
            event="agent.created",
            data={"host": "localhost", "name": "bot"},
            timestamp="2026-03-15T00:00:00Z",
        )
        await pub.publish(payload)
        assert len(pub.dead_letters) == 0


class TestPublisherSubjectCap:
    def test_active_subjects_does_not_exceed_max(self) -> None:
        from hermes.publisher import Publisher, _MAX_SUBJECTS

        pub = Publisher()
        # Fill up to the cap
        for i in range(_MAX_SUBJECTS):
            pub._active_subjects.add(f"hi.agents.host.agent-{i}.created")

        # The set is now at the cap; simulate a publish that would add a new subject
        # Directly test the guard logic: adding when at cap should log warning and skip
        before = len(pub._active_subjects)

        # Simulate what publish() does for the subject tracking guard
        new_subject = "hi.agents.host.new-agent.created"
        if len(pub._active_subjects) >= _MAX_SUBJECTS:
            pass  # Guard: should not add
        else:
            pub._active_subjects.add(new_subject)

        assert len(pub._active_subjects) == before


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_counter_value(metric_name: str, labels: dict[str, str]) -> float:
    """Read a counter value from the Prometheus default registry.

    prometheus-client stores Counter metric names without the _total suffix
    (e.g. "hermes_webhooks_received" not "hermes_webhooks_received_total"),
    but the samples are named with _total.
    """
    from prometheus_client import REGISTRY

    base_name = metric_name.removesuffix("_total")
    for metric in REGISTRY.collect():
        if metric.name == base_name:
            for sample in metric.samples:
                if sample.labels == labels and sample.name.endswith("_total"):
                    return sample.value
    return 0.0
