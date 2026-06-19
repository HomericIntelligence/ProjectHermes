"""Tests for Prometheus metrics endpoints and instrumentation."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from tests.helpers import TEST_SECRET, sign_body




def _build_client(dead_letters: list | None = None) -> TestClient:
    from hermes.server import app
    from hermes.publisher import Publisher

    mock_publisher = MagicMock(spec=Publisher)
    mock_publisher.is_connected = True
    mock_publisher.active_subjects = []
    mock_publisher.publish = AsyncMock()
    mock_publisher.dead_letters = dead_letters if dead_letters is not None else []

    app.state.publisher = mock_publisher
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
        assert "hermes_inflight_requests" in text


class TestDeadLettersEndpoint:
    def test_dead_letters_returns_200(self) -> None:
        client = _build_client()
        response = client.get("/dead-letters")
        assert response.status_code == 200

    def test_dead_letters_returns_list_when_empty(self) -> None:
        client = _build_client()
        body = client.get("/dead-letters").json()
        assert "items" in body
        assert isinstance(body["items"], list)
        assert body["items"] == []

    def test_dead_letters_returns_entries(self) -> None:
        entries = [{"event": "unknown.event", "data": {"foo": "bar"}}]
        client = _build_client(dead_letters=entries)
        body = client.get("/dead-letters").json()
        assert body["items"] == entries


class TestWebhookMetricsInstrumentation:
    def test_valid_webhook_increments_received_counter(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("WEBHOOK_SECRET", TEST_SECRET)
        client = _build_client()
        payload = {
            "event": "agent.created",
            "data": {"host": "localhost", "name": "bot"},
            "timestamp": "2026-03-15T00:00:00Z",
        }
        body_bytes = json.dumps(payload).encode()

        before = _get_counter_value(
            "hermes_webhooks_received_total", {"event_type": "agent.created"}
        )
        client.post(
            "/webhook",
            content=body_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Signature": sign_body(body_bytes, TEST_SECRET),
            },
        )
        after = _get_counter_value(
            "hermes_webhooks_received_total", {"event_type": "agent.created"}
        )
        assert after == before + 1

    def test_invalid_payload_increments_failed_counter(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("WEBHOOK_SECRET", TEST_SECRET)
        client = _build_client()
        body_bytes = json.dumps({"bad": "payload"}).encode()

        before = _get_counter_value("hermes_webhooks_failed_total", {"reason": "invalid_payload"})
        client.post(
            "/webhook",
            content=body_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Signature": sign_body(body_bytes, TEST_SECRET),
            },
        )
        after = _get_counter_value("hermes_webhooks_failed_total", {"reason": "invalid_payload"})
        assert after == before + 1

    def test_nats_unavailable_increments_failed_counter(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("WEBHOOK_SECRET", TEST_SECRET)
        from hermes.server import app
        from hermes.publisher import Publisher

        mock_publisher = MagicMock(spec=Publisher)
        mock_publisher.is_connected = False
        mock_publisher.dead_letters = []
        app.state.publisher = mock_publisher
        client = TestClient(app, raise_server_exceptions=False)

        payload = {
            "event": "agent.created",
            "data": {"host": "localhost", "name": "bot"},
            "timestamp": "2026-03-15T00:00:00Z",
        }
        body_bytes = json.dumps(payload).encode()

        before = _get_counter_value(
            "hermes_webhooks_failed_total", {"reason": "nats_not_connected"}
        )
        client.post(
            "/webhook",
            content=body_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Signature": sign_body(body_bytes, TEST_SECRET),
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
        from hermes.publisher import Publisher

        cap = 10
        pub = Publisher(max_subjects=cap)
        for i in range(cap + 5):
            pub._track_subject(f"hi.agents.host.agent-{i}.created")

        assert len(pub._active_subjects) == cap


class TestRequireAdminKeyIsolated:
    """Isolated unit tests for ``_require_dead_letter_key`` (issue #580).

    These tests call the dependency function directly with a freshly-constructed
    ``Settings`` instance per case, so they do not rely on (or mutate) the
    ``get_settings()`` LRU-cached singleton.  This avoids test-order coupling
    flagged in #580 / #360.

    Covered paths:
      * ``dead_letter_api_key`` set + correct key  -> returns ``None``
      * ``dead_letter_api_key`` set + wrong key    -> raises ``HTTPException(401)``
      * ``dead_letter_api_key`` set + missing key  -> raises ``HTTPException(401)``
      * ``dead_letter_api_key`` unset              -> auth bypassed (returns ``None``)
    """

    _KEY = "isolated-test-dead-letter-key-xxxxx"  # 37 chars, satisfies >=32 validator

    @staticmethod
    def _call(
        key_configured: str, header_value: str
    ) -> None:
        """Invoke ``_require_dead_letter_key`` with an isolated Settings instance."""
        import asyncio

        from hermes.config import Settings
        from hermes.server import _require_dead_letter_key

        settings = Settings(dead_letter_api_key=key_configured)
        asyncio.run(_require_dead_letter_key(settings=settings, x_dead_letter_key=header_value))

    def test_correct_key_passes(self) -> None:
        # Should not raise.
        self._call(self._KEY, self._KEY)

    def test_wrong_key_raises_401(self) -> None:
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as excinfo:
            self._call(self._KEY, "definitely-not-the-right-key")
        assert excinfo.value.status_code == 401
        assert "WWW-Authenticate" in excinfo.value.headers
        assert excinfo.value.headers["WWW-Authenticate"].startswith("Bearer")

    def test_missing_key_raises_401(self) -> None:
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as excinfo:
            self._call(self._KEY, "")
        assert excinfo.value.status_code == 401
        assert "WWW-Authenticate" in excinfo.value.headers

    def test_unset_key_bypasses_auth(self) -> None:
        """When ``dead_letter_api_key`` is unset the check is bypassed (documented contract)."""
        # Should not raise even though header is empty.
        self._call("", "")

    def test_unset_key_bypasses_auth_even_with_header(self) -> None:
        """An unconfigured server ignores any header value (allow-all path)."""
        # Should not raise even though a header is presented.
        self._call("", "some-random-attacker-supplied-value")

    def test_does_not_mutate_get_settings_singleton(self) -> None:
        """Regression: isolated tests must not touch the cached ``get_settings()``.

        Guards the test-isolation issue from #580: if these tests ever start
        mutating the singleton, the next test in the file (or any later test)
        could see a stale ``dead_letter_api_key`` and fail intermittently.
        """
        from fastapi import HTTPException

        from hermes.config import get_settings

        before = get_settings().dead_letter_api_key
        self._call(self._KEY, self._KEY)
        with pytest.raises(HTTPException):
            self._call(self._KEY, "wrong")
        after = get_settings().dead_letter_api_key
        assert before == after, (
            "Isolated _require_dead_letter_key tests must not mutate the "
            "get_settings() singleton (see #580)."
        )


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
