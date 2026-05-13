"""Tests for the FastAPI webhook endpoints."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from tests.helpers import TEST_SECRET, sign_body

_TEST_SECRET = TEST_SECRET


def _sign(body: bytes) -> str:
    return sign_body(body, _TEST_SECRET)


def _build_client(
    *,
    connected: bool = True,
    reconnect_count: int = 0,
    last_error: str = "",
) -> TestClient:
    """Build a TestClient with a mocked Publisher and a known webhook secret."""
    from hermes.config import Settings, get_settings
    from hermes.publisher import Publisher
    from hermes.rate_limit import limiter
    from hermes.server import app

    mock_publisher = MagicMock(spec=Publisher)
    mock_publisher.is_connected = connected
    mock_publisher.active_subjects = []
    mock_publisher.dead_letter_count = 0
    mock_publisher.active_subjects_max = 1000
    mock_publisher.publish = AsyncMock()
    mock_publisher.reconnect_count = reconnect_count
    mock_publisher.last_error = last_error

    # Inject the mock before the test client starts
    app.state.publisher = mock_publisher
    # Override settings via FastAPI dependency injection
    test_settings = Settings(webhook_secret=_TEST_SECRET)
    app.dependency_overrides[get_settings] = lambda: test_settings
    # Reset rate limiter so each test starts with a clean slate
    limiter._storage.reset()  # type: ignore[attr-defined]
    return TestClient(app, raise_server_exceptions=True)


def _build_client_disconnected() -> TestClient:
    """Build a TestClient where the Publisher reports NATS as disconnected."""
    return _build_client(connected=False)


class TestHealthEndpoint:
    """Tests for the GET /health liveness endpoint."""

    def test_health_returns_200_when_connected(self) -> None:
        client = _build_client()
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_returns_ok_status(self) -> None:
        client = _build_client()
        body = client.get("/health").json()
        assert body["status"] == "ok"

    def test_health_includes_nats_connected(self) -> None:
        client = _build_client()
        body = client.get("/health").json()
        assert "nats_connected" in body

    def test_health_includes_hermes_public_url(self) -> None:
        client = _build_client()
        body = client.get("/health").json()
        assert "hermes_public_url" in body

    def test_health_returns_503_when_nats_disconnected(self) -> None:
        client = _build_client_disconnected()
        response = client.get("/health")
        assert response.status_code == 503

    def test_health_returns_degraded_status_when_nats_disconnected(self) -> None:
        client = _build_client_disconnected()
        body = client.get("/health").json()
        assert body["status"] == "degraded"

    def test_health_returns_nats_connected_false_when_disconnected(self) -> None:
        client = _build_client_disconnected()
        body = client.get("/health").json()
        assert body["nats_connected"] is False

    def test_health_includes_inflight_requests(self) -> None:
        client = _build_client()
        body = client.get("/health").json()
        assert "inflight_requests" in body
        assert isinstance(body["inflight_requests"], int)
        assert body["inflight_requests"] >= 0

    def test_health_includes_timeout_fields(self) -> None:
        client = _build_client()
        body = client.get("/health").json()
        assert "timeouts" in body
        assert body["timeouts"]["nats_connect"] > 0
        assert body["timeouts"]["nats_publish"] > 0

    def test_health_includes_dead_letter_count(self) -> None:
        client = _build_client()
        body = client.get("/health").json()
        assert "dead_letter_count" in body
        assert isinstance(body["dead_letter_count"], int)

    def test_health_dead_letter_count_reflects_publisher(self) -> None:
        from hermes.publisher import Publisher
        from hermes.server import app

        mock_publisher = MagicMock(spec=Publisher)
        mock_publisher.is_connected = True
        mock_publisher.active_subjects = []
        mock_publisher.dead_letter_count = 7
        mock_publisher.reconnect_count = 0
        mock_publisher.last_error = ""
        mock_publisher.publish = AsyncMock()
        app.state.publisher = mock_publisher

        client = TestClient(app, raise_server_exceptions=True)
        body = client.get("/health").json()
        assert body["dead_letter_count"] == 7

    def test_health_includes_nats_reconnect_count(self) -> None:
        client = _build_client(reconnect_count=3)
        body = client.get("/health").json()
        assert "nats_reconnect_count" in body
        assert body["nats_reconnect_count"] == 3

    def test_health_reconnect_count_defaults_to_zero(self) -> None:
        client = _build_client()
        body = client.get("/health").json()
        assert body["nats_reconnect_count"] == 0

    def test_health_includes_nats_last_error(self) -> None:
        client = _build_client(last_error="NATS disconnected")
        body = client.get("/health").json()
        assert "nats_last_error" in body
        assert body["nats_last_error"] == "NATS disconnected"

    def test_health_last_error_defaults_to_empty_string(self) -> None:
        client = _build_client()
        body = client.get("/health").json()
        assert body["nats_last_error"] == ""

    def test_health_includes_nats_retry_attempts(self) -> None:
        client = _build_client()
        body = client.get("/health").json()
        assert "nats_retry_attempts" in body
        assert isinstance(body["nats_retry_attempts"], int)
        assert body["nats_retry_attempts"] == 3

    def test_health_includes_nats_retry_interval(self) -> None:
        client = _build_client()
        body = client.get("/health").json()
        assert "nats_retry_interval" in body
        assert isinstance(body["nats_retry_interval"], float)
        assert body["nats_retry_interval"] == 5.0

    def test_health_includes_dead_letter_queue_depth_gauge(self) -> None:
        """Issue #531: surface dead_letter_queue_depth gauge in /health."""
        from hermes.publisher import Publisher
        from hermes.server import app

        mock_publisher = MagicMock(spec=Publisher)
        mock_publisher.is_connected = True
        mock_publisher.active_subjects = []
        mock_publisher.dead_letter_count = 42
        mock_publisher.reconnect_count = 0
        mock_publisher.last_error = ""
        mock_publisher.publish = AsyncMock()
        app.state.publisher = mock_publisher

        client = TestClient(app, raise_server_exceptions=True)
        body = client.get("/health").json()
        assert "dead_letter_queue_depth" in body
        assert body["dead_letter_queue_depth"] == 42
        assert "dead_letter_queue_capacity" in body
        # default DEAD_LETTER_MAX_SIZE = 1000
        assert body["dead_letter_queue_capacity"] == 1000
        assert "dead_letter_queue_alert_threshold_pct" in body
        # 42 / 1000 * 100 == 4.2
        assert body["dead_letter_queue_alert_threshold_pct"] == pytest.approx(4.2)

    def test_health_dead_letter_threshold_pct_zero_when_empty(self) -> None:
        """alert_threshold_pct is 0.0 when no dead-letters are queued."""
        client = _build_client()
        body = client.get("/health").json()
        assert body["dead_letter_queue_depth"] == 0
        assert body["dead_letter_queue_alert_threshold_pct"] == 0.0


class TestReadyEndpoint:
    def test_ready_returns_200_when_connected(self) -> None:
        client = _build_client()
        response = client.get("/ready")
        assert response.status_code == 200

    def test_ready_returns_ready_true_when_connected(self) -> None:
        client = _build_client()
        body = client.get("/ready").json()
        assert body["ready"] is True

    def test_ready_returns_503_when_nats_disconnected(self) -> None:
        client = _build_client_disconnected()
        response = client.get("/ready")
        assert response.status_code == 503

    def test_ready_returns_ready_false_when_disconnected(self) -> None:
        client = _build_client_disconnected()
        body = client.get("/ready").json()
        assert body["ready"] is False

    def test_ready_includes_reason_when_disconnected(self) -> None:
        client = _build_client_disconnected()
        body = client.get("/ready").json()
        assert "reason" in body


class TestWebhookEndpoint:
    def test_valid_payload_returns_202(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WEBHOOK_SECRET", _TEST_SECRET)
        client = _build_client()
        payload = {
            "event": "agent.created",
            "data": {"host": "localhost", "name": "bot"},
            "timestamp": "2026-03-15T00:00:00Z",
        }
        body_bytes = json.dumps(payload).encode()
        response = client.post(
            "/webhook",
            content=body_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Signature": _sign(body_bytes),
            },
        )
        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "accepted"
        assert body["event"] == "agent.created"
        assert "request_id" in body
        assert isinstance(body["request_id"], str)
        assert len(body["request_id"]) > 0

    def test_webhook_invalid_payload_returns_422(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WEBHOOK_SECRET", _TEST_SECRET)
        client = _build_client()
        body_bytes = json.dumps({"bad": "payload"}).encode()
        response = client.post(
            "/webhook",
            content=body_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Signature": _sign(body_bytes),
            },
        )
        assert response.status_code == 422

    def test_webhook_missing_body_returns_422(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WEBHOOK_SECRET", _TEST_SECRET)
        client = _build_client()
        body_bytes = b"not json"
        response = client.post(
            "/webhook",
            content=body_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Signature": _sign(body_bytes),
            },
        )
        assert response.status_code == 422

    def test_webhook_returns_event_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WEBHOOK_SECRET", _TEST_SECRET)
        client = _build_client()
        payload = {
            "event": "task.updated",
            "data": {"team_id": "t1", "task_id": "task-1"},
            "timestamp": "2026-03-15T00:00:00Z",
        }
        body_bytes = json.dumps(payload).encode()
        response = client.post(
            "/webhook",
            content=body_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Signature": _sign(body_bytes),
            },
        )
        body = response.json()
        assert body["event"] == "task.updated"

    def test_webhook_bad_signature_returns_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WEBHOOK_SECRET", _TEST_SECRET)
        client = _build_client()
        payload = {
            "event": "agent.created",
            "data": {"host": "localhost", "name": "bot"},
            "timestamp": "2026-03-15T00:00:00Z",
        }
        body_bytes = json.dumps(payload).encode()
        response = client.post(
            "/webhook",
            content=body_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Signature": "bad-signature",
            },
        )
        assert response.status_code == 401

    def test_invalid_signature_increments_failed_counter(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from prometheus_client import REGISTRY

        monkeypatch.setenv("WEBHOOK_SECRET", _TEST_SECRET)
        client = _build_client()
        labels = {"reason": "invalid_signature"}
        before = REGISTRY.get_sample_value("hermes_webhooks_failed_total", labels) or 0.0
        payload = {
            "event": "agent.created",
            "data": {"host": "localhost", "name": "bot"},
            "timestamp": "2026-01-01T00:00:00Z",
        }
        body_bytes = json.dumps(payload).encode()
        response = client.post(
            "/webhook",
            content=body_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Signature": "sha256=wrong",
            },
        )
        assert response.status_code == 401
        after = REGISTRY.get_sample_value("hermes_webhooks_failed_total", labels) or 0.0
        assert after > before

    def test_webhook_publish_timeout_returns_503(self) -> None:
        from hermes.server import app
        from hermes.publisher import Publisher

        mock_publisher = MagicMock(spec=Publisher)
        mock_publisher.is_connected = True
        mock_publisher.active_subjects = []
        mock_publisher.publish = AsyncMock(side_effect=asyncio.TimeoutError())
        app.state.publisher = mock_publisher

        from hermes.config import Settings, get_settings

        app.dependency_overrides[get_settings] = lambda: Settings(webhook_secret=_TEST_SECRET)

        client = TestClient(app, raise_server_exceptions=False)
        payload = {
            "event": "agent.created",
            "data": {"host": "h", "name": "n"},
            "timestamp": "2026-01-01T00:00:00Z",
        }
        body_bytes = json.dumps(payload).encode()
        response = client.post(
            "/webhook",
            content=body_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Signature": _sign(body_bytes),
            },
        )
        assert response.status_code == 503
        assert "timed out" in response.json()["detail"].lower()


class TestSignatureValidation:
    """Tests for _verify_signature behaviour (issue #156)."""

    def test_missing_signature_header_returns_401_when_secret_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("WEBHOOK_SECRET", _TEST_SECRET)
        client = _build_client()
        payload = {
            "event": "agent.created",
            "data": {"host": "localhost", "name": "bot"},
            "timestamp": "2026-03-15T00:00:00Z",
        }
        response = client.post("/webhook", json=payload)
        assert response.status_code == 401

    def test_empty_signature_header_returns_401_when_secret_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("WEBHOOK_SECRET", _TEST_SECRET)
        client = _build_client()
        payload = {
            "event": "agent.created",
            "data": {"host": "localhost", "name": "bot"},
            "timestamp": "2026-03-15T00:00:00Z",
        }
        body_bytes = json.dumps(payload).encode()
        response = client.post(
            "/webhook",
            content=body_bytes,
            headers={"Content-Type": "application/json", "X-Webhook-Signature": ""},
        )
        assert response.status_code == 401


class TestRequestIDMiddleware:
    """Tests for X-Request-ID header sanitization (issue #229)."""

    def test_valid_uuid_request_id_is_passed_through(self) -> None:
        import uuid as uuid_mod

        client = _build_client()
        valid_id = str(uuid_mod.uuid4())
        payload = {
            "event": "agent.created",
            "data": {"host": "localhost", "name": "bot"},
            "timestamp": "2026-03-15T00:00:00Z",
        }
        body_bytes = json.dumps(payload).encode()
        response = client.post(
            "/webhook",
            content=body_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Signature": _sign(body_bytes),
                "X-Request-ID": valid_id,
            },
        )
        assert response.headers.get("X-Request-ID") == valid_id
        assert response.json()["request_id"] == valid_id

    def test_invalid_chars_in_request_id_are_replaced_with_uuid(self) -> None:
        import uuid as uuid_mod

        client = _build_client()
        invalid_id = "bad<>id\ninjection"
        payload = {
            "event": "agent.created",
            "data": {"host": "localhost", "name": "bot"},
            "timestamp": "2026-03-15T00:00:00Z",
        }
        body_bytes = json.dumps(payload).encode()
        response = client.post(
            "/webhook",
            content=body_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Signature": _sign(body_bytes),
                "X-Request-ID": invalid_id,
            },
        )
        returned_id = response.headers.get("X-Request-ID")
        assert returned_id != invalid_id
        uuid_mod.UUID(returned_id)  # raises ValueError if not a valid UUID

    def test_absent_request_id_header_generates_uuid(self) -> None:
        import uuid as uuid_mod

        client = _build_client()
        payload = {
            "event": "agent.created",
            "data": {"host": "localhost", "name": "bot"},
            "timestamp": "2026-03-15T00:00:00Z",
        }
        body_bytes = json.dumps(payload).encode()
        response = client.post(
            "/webhook",
            content=body_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Signature": _sign(body_bytes),
            },
        )
        returned_id = response.headers.get("X-Request-ID")
        assert returned_id is not None
        uuid_mod.UUID(returned_id)  # raises ValueError if not a valid UUID

    def test_oversized_request_id_is_replaced_with_uuid(self) -> None:
        import uuid as uuid_mod

        client = _build_client()
        oversized_id = "a" * 129
        payload = {
            "event": "agent.created",
            "data": {"host": "localhost", "name": "bot"},
            "timestamp": "2026-03-15T00:00:00Z",
        }
        body_bytes = json.dumps(payload).encode()
        response = client.post(
            "/webhook",
            content=body_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Signature": _sign(body_bytes),
                "X-Request-ID": oversized_id,
            },
        )
        returned_id = response.headers.get("X-Request-ID")
        assert returned_id != oversized_id
        uuid_mod.UUID(returned_id)  # raises ValueError if not a valid UUID


class TestSettings:
    def test_hermes_host_defaults_to_localhost(self) -> None:
        from hermes.config import Settings

        s = Settings()
        assert s.hermes_host == "127.0.0.1"


class TestPayloadSizeLimit:
    def test_oversized_content_length_returns_413(self) -> None:
        client = _build_client()
        body_bytes = b"x" * 100
        response = client.post(
            "/webhook",
            content=body_bytes,
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Length": str(2_000_000),
                "X-Webhook-Signature": _sign(body_bytes),
            },
        )
        assert response.status_code == 413

    def test_oversized_body_returns_413(self) -> None:
        client = _build_client()
        body_bytes = b"x" * (1_048_576 + 1)
        response = client.post(
            "/webhook",
            content=body_bytes,
            headers={
                "Content-Type": "application/octet-stream",
                "X-Webhook-Signature": _sign(body_bytes),
            },
        )
        assert response.status_code == 413

    def test_exact_limit_body_accepted(self) -> None:
        """A body exactly at the limit must not be rejected with 413."""
        client = _build_client()
        body_bytes = b"x" * 1_048_576
        response = client.post(
            "/webhook",
            content=body_bytes,
            headers={
                "Content-Type": "application/octet-stream",
                "X-Webhook-Signature": _sign(body_bytes),
            },
        )
        assert response.status_code != 413

    def test_custom_limit_respected(self) -> None:
        from hermes.middleware import PayloadSizeLimitMiddleware
        from starlette.testclient import TestClient
        from starlette.requests import Request
        from starlette.responses import Response
        from starlette.applications import Starlette
        from starlette.routing import Route

        async def echo(request: Request) -> Response:
            return Response("ok", status_code=200)

        mini_app = Starlette(routes=[Route("/", echo, methods=["POST"])])
        mini_app.add_middleware(PayloadSizeLimitMiddleware, max_bytes=10)
        tc = TestClient(mini_app)

        assert tc.post("/", content=b"x" * 10).status_code == 200
        assert tc.post("/", content=b"x" * 11).status_code == 413

    def test_oversized_content_length_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging

        client = _build_client()
        with caplog.at_level(logging.WARNING, logger="hermes.middleware"):
            client.post(
                "/webhook",
                content=b"x" * 100,
                headers={
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(2_000_000),
                    "X-Webhook-Signature": _sign(b"x" * 100),
                },
            )
        assert any("2000000" in r.message and "1048576" in r.message for r in caplog.records)

    def test_oversized_body_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging

        client = _build_client()
        body_bytes = b"x" * (1_048_576 + 1)
        with caplog.at_level(logging.WARNING, logger="hermes.middleware"):
            client.post(
                "/webhook",
                content=body_bytes,
                headers={
                    "Content-Type": "application/octet-stream",
                    "X-Webhook-Signature": _sign(body_bytes),
                },
            )
        assert any("1048577" in r.message and "1048576" in r.message for r in caplog.records)


class TestWildcardInjectionSanitization:
    """Verify that wildcard characters in webhook payloads are sanitized before publish (#152)."""

    def test_wildcard_in_host_field_is_sanitized_before_publish(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("WEBHOOK_SECRET", _TEST_SECRET)

        published_subjects: list[str] = []

        from hermes.publisher import Publisher
        from hermes.server import app

        real_publisher = Publisher()

        async def _capture_publish(payload, publish_timeout=5.0, *, request_id=""):
            subject = real_publisher._resolve_subject(payload)
            if subject is not None:
                published_subjects.append(subject)

        mock_publisher = MagicMock(spec=Publisher)
        mock_publisher.is_connected = True
        mock_publisher.active_subjects = []
        mock_publisher.publish = AsyncMock(side_effect=_capture_publish)

        app.state.publisher = mock_publisher

        payload = {
            "event": "agent.created",
            "data": {"host": "evil*host", "name": "bot"},
            "timestamp": "2026-01-01T00:00:00Z",
        }
        body_bytes = json.dumps(payload).encode()
        client = TestClient(app, raise_server_exceptions=True)
        response = client.post(
            "/webhook",
            content=body_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Signature": _sign(body_bytes),
            },
        )

        assert response.status_code == 202
        assert len(published_subjects) == 1
        assert "*" not in published_subjects[0]
        assert ">" not in published_subjects[0]
        assert "evil" in published_subjects[0]

    def test_wildcard_in_name_field_is_sanitized_before_publish(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("WEBHOOK_SECRET", _TEST_SECRET)

        published_subjects: list[str] = []

        from hermes.publisher import Publisher
        from hermes.server import app

        real_publisher = Publisher()

        async def _capture_publish(payload, publish_timeout=5.0, *, request_id=""):
            subject = real_publisher._resolve_subject(payload)
            if subject is not None:
                published_subjects.append(subject)

        mock_publisher = MagicMock(spec=Publisher)
        mock_publisher.is_connected = True
        mock_publisher.active_subjects = []
        mock_publisher.publish = AsyncMock(side_effect=_capture_publish)

        app.state.publisher = mock_publisher

        payload = {
            "event": "agent.created",
            "data": {"host": "myhost", "name": "bad>bot"},
            "timestamp": "2026-01-01T00:00:00Z",
        }
        body_bytes = json.dumps(payload).encode()
        client = TestClient(app, raise_server_exceptions=True)
        response = client.post(
            "/webhook",
            content=body_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Signature": _sign(body_bytes),
            },
        )

        assert response.status_code == 202
        assert len(published_subjects) == 1
        assert "*" not in published_subjects[0]
        assert ">" not in published_subjects[0]


class TestDeadLettersGetEndpoint:
    """Tests for GET /dead-letters with pagination (issues #108)."""

    def _build_client_with_dead_letters(self, items: list[dict]) -> TestClient:
        from hermes.publisher import Publisher
        from hermes.server import app

        mock_publisher = MagicMock(spec=Publisher)
        mock_publisher.is_connected = True
        mock_publisher.active_subjects = []
        mock_publisher.publish = AsyncMock()
        mock_publisher.dead_letters = items
        app.state.publisher = mock_publisher
        return TestClient(app, raise_server_exceptions=True)

    def test_dead_letters_returns_200(self) -> None:
        client = self._build_client_with_dead_letters([])
        resp = client.get("/dead-letters")
        assert resp.status_code == 200

    def test_dead_letters_empty_queue_structure(self) -> None:
        client = self._build_client_with_dead_letters([])
        body = client.get("/dead-letters").json()
        assert body["total"] == 0
        assert body["offset"] == 0
        assert body["limit"] is not None  # default page size is applied
        assert body["items"] == []

    def test_dead_letters_returns_all_by_default(self) -> None:
        items = [{"event": f"evt.{i}", "subject": f"hi.deadletter.evt-{i}"} for i in range(5)]
        client = self._build_client_with_dead_letters(items)
        body = client.get("/dead-letters").json()
        assert body["total"] == 5
        assert len(body["items"]) == 5
        assert body["limit"] is not None  # default page size is applied

    def test_dead_letters_limit_param(self) -> None:
        items = [{"event": f"evt.{i}", "subject": f"hi.deadletter.evt-{i}"} for i in range(10)]
        client = self._build_client_with_dead_letters(items)
        body = client.get("/dead-letters?limit=3").json()
        assert body["total"] == 10
        assert len(body["items"]) == 3
        assert body["limit"] == 3
        assert body["offset"] == 0

    def test_dead_letters_offset_param(self) -> None:
        items = [{"event": f"evt.{i}", "subject": f"hi.deadletter.evt-{i}"} for i in range(10)]
        client = self._build_client_with_dead_letters(items)
        body = client.get("/dead-letters?offset=5").json()
        assert body["total"] == 10
        assert len(body["items"]) == 5
        assert body["offset"] == 5
        assert body["items"][0]["event"] == "evt.5"

    def test_dead_letters_limit_and_offset(self) -> None:
        items = [{"event": f"evt.{i}", "subject": f"hi.deadletter.evt-{i}"} for i in range(20)]
        client = self._build_client_with_dead_letters(items)
        body = client.get("/dead-letters?offset=5&limit=10").json()
        assert body["total"] == 20
        assert body["offset"] == 5
        assert body["limit"] == 10
        assert len(body["items"]) == 10
        assert body["items"][0]["event"] == "evt.5"
        assert body["items"][-1]["event"] == "evt.14"

    def test_dead_letters_offset_beyond_total_returns_empty_items(self) -> None:
        items = [{"event": "evt.0", "subject": "hi.deadletter.evt-0"}]
        client = self._build_client_with_dead_letters(items)
        body = client.get("/dead-letters?offset=100").json()
        assert body["total"] == 1
        assert body["items"] == []

    def test_dead_letters_limit_larger_than_total(self) -> None:
        items = [{"event": "evt.0", "subject": "hi.deadletter.evt-0"}]
        client = self._build_client_with_dead_letters(items)
        body = client.get("/dead-letters?limit=100").json()
        assert body["total"] == 1
        assert len(body["items"]) == 1


class TestDeadLettersDeleteEndpoint:
    """Tests for DELETE /dead-letters (issue #110)."""

    def _build_client_with_drain(self, drained_count: int) -> TestClient:
        from hermes.publisher import Publisher
        from hermes.server import app

        mock_publisher = MagicMock(spec=Publisher)
        mock_publisher.is_connected = True
        mock_publisher.active_subjects = []
        mock_publisher.publish = AsyncMock()
        mock_publisher.drain_dead_letters = MagicMock(return_value=drained_count)
        app.state.publisher = mock_publisher
        return TestClient(app, raise_server_exceptions=True)

    def test_delete_dead_letters_returns_200(self) -> None:
        client = self._build_client_with_drain(0)
        resp = client.delete("/dead-letters")
        assert resp.status_code == 200

    def test_delete_dead_letters_returns_drained_count_zero(self) -> None:
        client = self._build_client_with_drain(0)
        body = client.delete("/dead-letters").json()
        assert body["drained"] == 0

    def test_delete_dead_letters_returns_drained_count_nonzero(self) -> None:
        client = self._build_client_with_drain(42)
        body = client.delete("/dead-letters").json()
        assert body["drained"] == 42

    def test_delete_dead_letters_calls_drain(self) -> None:
        from hermes.publisher import Publisher
        from hermes.server import app

        mock_publisher = MagicMock(spec=Publisher)
        mock_publisher.is_connected = True
        mock_publisher.active_subjects = []
        mock_publisher.publish = AsyncMock()
        mock_publisher.drain_dead_letters = MagicMock(return_value=5)
        app.state.publisher = mock_publisher
        client = TestClient(app, raise_server_exceptions=True)
        client.delete("/dead-letters")
        mock_publisher.drain_dead_letters.assert_called_once()


_DEAD_LETTER_KEY = "dead-letter-api-key-for-testing-xxxxx"


class TestDeadLettersGetAuth:
    """Tests for GET /dead-letters API key authentication (issue #344)."""

    def _build_client(self, *, key: str) -> TestClient:
        from hermes.config import get_settings
        from hermes.publisher import Publisher
        from hermes.server import app

        mock_publisher = MagicMock(spec=Publisher)
        mock_publisher.is_connected = True
        mock_publisher.active_subjects = []
        mock_publisher.publish = AsyncMock()
        mock_publisher.dead_letters = []
        app.state.publisher = mock_publisher
        get_settings().dead_letter_api_key = key
        return TestClient(app, raise_server_exceptions=True)

    def teardown_method(self) -> None:
        from hermes.config import get_settings

        get_settings().dead_letter_api_key = ""

    def test_correct_key_returns_200(self) -> None:
        client = self._build_client(key=_DEAD_LETTER_KEY)
        resp = client.get("/dead-letters", headers={"X-Dead-Letter-Key": _DEAD_LETTER_KEY})
        assert resp.status_code == 200

    def test_wrong_key_returns_401(self) -> None:
        client = self._build_client(key=_DEAD_LETTER_KEY)
        resp = client.get("/dead-letters", headers={"X-Dead-Letter-Key": "wrong-key"})
        assert resp.status_code == 401

    def test_missing_key_returns_401(self) -> None:
        client = self._build_client(key=_DEAD_LETTER_KEY)
        resp = client.get("/dead-letters")
        assert resp.status_code == 401

    def test_no_key_configured_bypasses_auth(self) -> None:
        client = self._build_client(key="")
        resp = client.get("/dead-letters")
        assert resp.status_code == 200

    def test_401_has_www_authenticate_header(self) -> None:
        client = self._build_client(key=_DEAD_LETTER_KEY)
        resp = client.get("/dead-letters")
        assert resp.status_code == 401
        assert "WWW-Authenticate" in resp.headers


class TestDeadLettersDeleteAuth:
    """Tests for DELETE /dead-letters API key authentication (issue #344)."""

    def _build_client(self, *, key: str) -> TestClient:
        from hermes.config import get_settings
        from hermes.publisher import Publisher
        from hermes.server import app

        mock_publisher = MagicMock(spec=Publisher)
        mock_publisher.is_connected = True
        mock_publisher.active_subjects = []
        mock_publisher.publish = AsyncMock()
        mock_publisher.drain_dead_letters = MagicMock(return_value=0)
        app.state.publisher = mock_publisher
        get_settings().dead_letter_api_key = key
        return TestClient(app, raise_server_exceptions=True)

    def teardown_method(self) -> None:
        from hermes.config import get_settings

        get_settings().dead_letter_api_key = ""

    def test_correct_key_returns_200(self) -> None:
        client = self._build_client(key=_DEAD_LETTER_KEY)
        resp = client.delete("/dead-letters", headers={"X-Dead-Letter-Key": _DEAD_LETTER_KEY})
        assert resp.status_code == 200

    def test_wrong_key_returns_401(self) -> None:
        client = self._build_client(key=_DEAD_LETTER_KEY)
        resp = client.delete("/dead-letters", headers={"X-Dead-Letter-Key": "wrong-key"})
        assert resp.status_code == 401

    def test_missing_key_returns_401(self) -> None:
        client = self._build_client(key=_DEAD_LETTER_KEY)
        resp = client.delete("/dead-letters")
        assert resp.status_code == 401

    def test_no_key_configured_bypasses_auth(self) -> None:
        client = self._build_client(key="")
        resp = client.delete("/dead-letters")
        assert resp.status_code == 200

    def test_401_has_www_authenticate_header(self) -> None:
        client = self._build_client(key=_DEAD_LETTER_KEY)
        resp = client.delete("/dead-letters")
        assert resp.status_code == 401
        assert "WWW-Authenticate" in resp.headers


class TestDeadLetterApiKeyValidation:
    """Tests for DEAD_LETTER_API_KEY config validation (issue #344)."""

    def test_key_shorter_than_32_chars_raises(self) -> None:
        from pydantic import ValidationError

        from hermes.config import Settings

        with pytest.raises(ValidationError, match="32 characters"):
            Settings(dead_letter_api_key="short")

    def test_key_exactly_32_chars_is_valid(self) -> None:
        from hermes.config import Settings

        s = Settings(dead_letter_api_key="a" * 32)
        assert len(s.dead_letter_api_key) == 32

    def test_empty_key_is_valid(self) -> None:
        from hermes.config import Settings

        s = Settings(dead_letter_api_key="")
        assert s.dead_letter_api_key == ""


class TestPublisherDrainDeadLetters:
    """Unit tests for Publisher.drain_dead_letters."""

    def test_drain_empty_returns_zero(self) -> None:
        from hermes.publisher import Publisher

        pub = Publisher.__new__(Publisher)
        from collections import deque

        pub._dead_letters = deque()
        assert pub.drain_dead_letters() == 0

    def test_drain_clears_queue(self) -> None:
        from hermes.publisher import Publisher
        from collections import deque

        pub = Publisher.__new__(Publisher)
        pub._dead_letters = deque([{"event": "a"}, {"event": "b"}])
        count = pub.drain_dead_letters()
        assert count == 2
        assert len(pub._dead_letters) == 0

    def test_drain_twice_second_returns_zero(self) -> None:
        from hermes.publisher import Publisher
        from collections import deque

        pub = Publisher.__new__(Publisher)
        pub._dead_letters = deque([{"event": "a"}])
        pub.drain_dead_letters()
        assert pub.drain_dead_letters() == 0


class TestSubjectsEndpoint:
    def test_subjects_returns_list(self) -> None:
        client = _build_client()
        body = client.get("/subjects").json()
        assert "subjects" in body
        assert isinstance(body["subjects"], list)

    def test_subjects_rate_limit_returns_429_when_limit_exceeded(self) -> None:
        from hermes.rate_limit import limiter

        limiter._storage.reset()  # type: ignore[attr-defined]
        client = _build_client()
        for _ in range(60):
            client.get("/subjects")
        resp = client.get("/subjects")
        assert resp.status_code == 429

    def test_subjects_includes_hermes_public_url(self) -> None:
        client = _build_client()
        body = client.get("/subjects").json()
        assert "hermes_public_url" in body
        assert isinstance(body["hermes_public_url"], str)

    def test_subjects_includes_active_subjects_max(self) -> None:
        client = _build_client()
        body = client.get("/subjects").json()
        assert "active_subjects_max" in body
        assert isinstance(body["active_subjects_max"], int)
        assert body["active_subjects_max"] > 0


class TestWWWAuthenticate:
    def test_bad_signature_401_has_www_authenticate_header(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("WEBHOOK_SECRET", _TEST_SECRET)
        client = _build_client()
        payload = {
            "event": "agent.created",
            "data": {"host": "localhost", "name": "bot"},
            "timestamp": "2026-03-15T00:00:00Z",
        }
        body_bytes = json.dumps(payload).encode()
        resp = client.post(
            "/webhook",
            content=body_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Signature": "sha256=bad",
            },
        )
        assert resp.status_code == 401
        headers_lower = {k.lower(): v for k, v in resp.headers.items()}
        assert "www-authenticate" in headers_lower
        assert headers_lower["www-authenticate"] == 'Bearer realm="hermes"'


class TestVersionEndpoint:
    def test_version_returns_200(self) -> None:
        client = _build_client()
        resp = client.get("/version")
        assert resp.status_code == 200

    def test_version_body_has_version_key(self) -> None:
        client = _build_client()
        data = client.get("/version").json()
        assert "version" in data
        assert isinstance(data["version"], str)
        assert len(data["version"]) > 0


class TestEventsEndpoint:
    """Tests for GET /events — validates response against AGENT_EVENTS and TASK_EVENTS (#127)."""

    def test_events_returns_200(self) -> None:
        client = _build_client()
        resp = client.get("/events")
        assert resp.status_code == 200

    def test_events_response_has_agent_events_key(self) -> None:
        client = _build_client()
        body = client.get("/events").json()
        assert "agent_events" in body

    def test_events_response_has_task_events_key(self) -> None:
        client = _build_client()
        body = client.get("/events").json()
        assert "task_events" in body

    def test_events_response_has_all_events_key(self) -> None:
        client = _build_client()
        body = client.get("/events").json()
        assert "all_events" in body

    def test_events_agent_events_matches_publisher_constant(self) -> None:
        from hermes.publisher import AGENT_EVENTS

        client = _build_client()
        body = client.get("/events").json()
        assert set(body["agent_events"]) == AGENT_EVENTS

    def test_events_task_events_matches_publisher_constant(self) -> None:
        from hermes.publisher import TASK_EVENTS

        client = _build_client()
        body = client.get("/events").json()
        assert set(body["task_events"]) == TASK_EVENTS

    def test_events_all_events_is_union_of_agent_and_task(self) -> None:
        from hermes.publisher import AGENT_EVENTS, TASK_EVENTS

        client = _build_client()
        body = client.get("/events").json()
        assert set(body["all_events"]) == AGENT_EVENTS | TASK_EVENTS

    def test_events_lists_are_sorted(self) -> None:
        client = _build_client()
        body = client.get("/events").json()
        assert body["agent_events"] == sorted(body["agent_events"])
        assert body["task_events"] == sorted(body["task_events"])
        assert body["all_events"] == sorted(body["all_events"])


class TestTimestampValidation:
    def test_webhook_naive_timestamp_rejected(self) -> None:
        client = _build_client()
        payload = {
            "event": "agent.created",
            "data": {"host": "h", "name": "n"},
            "timestamp": "2026-01-01T00:00:00",
        }
        body_bytes = json.dumps(payload).encode()
        response = client.post(
            "/webhook",
            content=body_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Signature": _sign(body_bytes),
            },
        )
        assert response.status_code == 422

    def test_webhook_aware_timestamp_accepted(self) -> None:
        client = _build_client()
        payload = {
            "event": "agent.created",
            "data": {"host": "h", "name": "n"},
            "timestamp": "2026-01-01T00:00:00Z",
        }
        body_bytes = json.dumps(payload).encode()
        response = client.post(
            "/webhook",
            content=body_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Signature": _sign(body_bytes),
            },
        )
        assert response.status_code in (202, 500)


class TestUnknownEventType:
    """Tests for #125: unknown event types return 422 when dead-lettering is disabled."""

    def _build_client_with_unknown_event(self, event: str) -> TestClient:
        from hermes.config import Settings, get_settings
        from hermes.publisher import UnknownEventTypeError, Publisher
        from hermes.rate_limit import limiter
        from hermes.server import app

        mock_publisher = MagicMock(spec=Publisher)
        mock_publisher.is_connected = True
        mock_publisher.active_subjects = []
        mock_publisher.publish = AsyncMock(side_effect=UnknownEventTypeError(event))

        app.state.publisher = mock_publisher
        app.dependency_overrides[get_settings] = lambda: Settings(webhook_secret="")
        limiter._storage.reset()  # type: ignore[attr-defined]
        return TestClient(app, raise_server_exceptions=True)

    def test_unknown_event_type_raises_422(self) -> None:
        client = self._build_client_with_unknown_event("foo.bar")
        payload = {"event": "foo.bar", "data": {}, "timestamp": "2026-01-01T00:00:00Z"}
        body_bytes = json.dumps(payload).encode()
        response = client.post(
            "/webhook", content=body_bytes, headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 422

    def test_unknown_event_type_422_detail_contains_event(self) -> None:
        client = self._build_client_with_unknown_event("foo.bar")
        payload = {"event": "foo.bar", "data": {}, "timestamp": "2026-01-01T00:00:00Z"}
        body_bytes = json.dumps(payload).encode()
        response = client.post(
            "/webhook", content=body_bytes, headers={"Content-Type": "application/json"}
        )
        body = response.json()
        assert "foo.bar" in body["detail"]


class TestMissingFieldWarnings:
    """Tests for #98: warnings logged when agent/task data fields are missing."""

    def test_missing_host_field_logs_warning(self) -> None:
        from unittest.mock import patch
        from hermes.publisher import Publisher

        with patch("hermes.publisher.logger") as mock_log:
            pub = Publisher()
            pub._parse_agent_subject({"name": "bot"}, "agent.created")
            warned_messages = [str(call.args) for call in mock_log.warning.call_args_list]
            assert any("host" in msg for msg in warned_messages)

    def test_missing_name_field_logs_warning(self) -> None:
        from unittest.mock import patch
        from hermes.publisher import Publisher

        with patch("hermes.publisher.logger") as mock_log:
            pub = Publisher()
            pub._parse_agent_subject({"host": "myhost"}, "agent.created")
            warned_messages = [str(call.args) for call in mock_log.warning.call_args_list]
            assert any("name" in msg for msg in warned_messages)

    def test_missing_team_id_field_logs_warning(self) -> None:
        from unittest.mock import patch
        from hermes.publisher import Publisher

        with patch("hermes.publisher.logger") as mock_log:
            pub = Publisher()
            pub._parse_task_subject({"task_id": "t-1"}, "task.updated")
            warned_messages = [str(call.args) for call in mock_log.warning.call_args_list]
            assert any("team_id" in msg for msg in warned_messages)

    def test_missing_task_id_field_logs_warning(self) -> None:
        from unittest.mock import patch
        from hermes.publisher import Publisher

        with patch("hermes.publisher.logger") as mock_log:
            pub = Publisher()
            pub._parse_task_subject({"team_id": "alpha"}, "task.updated")
            warned_messages = [str(call.args) for call in mock_log.warning.call_args_list]
            assert any("task_id" in msg for msg in warned_messages)

    def test_present_host_and_name_no_warning(self) -> None:
        from unittest.mock import patch
        from hermes.publisher import Publisher

        with patch("hermes.publisher.logger") as mock_log:
            pub = Publisher()
            pub._parse_agent_subject({"host": "myhost", "name": "bot"}, "agent.created")
            assert not mock_log.warning.called


class TestPublisherRaises:
    """Issue #122 — publisher.publish() raising must return 500, not propagate."""

    def test_publish_runtime_error_returns_500(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When publisher.publish raises RuntimeError the endpoint returns 500."""
        from hermes.publisher import Publisher
        from hermes.server import app

        monkeypatch.setenv("WEBHOOK_SECRET", _TEST_SECRET)

        mock_publisher = MagicMock(spec=Publisher)
        mock_publisher.is_connected = True
        mock_publisher.active_subjects = []
        mock_publisher.publish = AsyncMock(side_effect=RuntimeError("NATS exploded"))

        app.state.publisher = mock_publisher
        client = TestClient(app, raise_server_exceptions=False)

        payload = {
            "event": "agent.created",
            "data": {"host": "localhost", "name": "bot"},
            "timestamp": "2026-03-15T00:00:00Z",
        }
        body_bytes = json.dumps(payload).encode()
        response = client.post(
            "/webhook",
            content=body_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Signature": _sign(body_bytes),
            },
        )
        assert response.status_code == 500


class TestExceptionDetailNotLeaked:
    """Issue #158 — internal exception detail must not appear in response body."""

    def test_invalid_payload_response_has_no_traceback(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """422 response body must only contain 'detail', no stack trace or internal info."""
        import logging

        monkeypatch.setenv("WEBHOOK_SECRET", _TEST_SECRET)
        client = _build_client()
        body_bytes = json.dumps({"bad": "payload"}).encode()

        with caplog.at_level(logging.WARNING):
            response = client.post(
                "/webhook",
                content=body_bytes,
                headers={
                    "Content-Type": "application/json",
                    "X-Webhook-Signature": _sign(body_bytes),
                },
            )

        assert response.status_code == 422
        body_text = response.text
        assert "Traceback" not in body_text
        assert "File " not in body_text
        assert "detail" in response.json()

    def test_invalid_payload_emits_warning_log(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A WARNING-level log must be emitted when the webhook payload is invalid."""
        import logging

        monkeypatch.setenv("WEBHOOK_SECRET", _TEST_SECRET)
        client = _build_client()
        body_bytes = json.dumps({"bad": "payload"}).encode()

        with caplog.at_level(logging.WARNING, logger="hermes"):
            response = client.post(
                "/webhook",
                content=body_bytes,
                headers={
                    "Content-Type": "application/json",
                    "X-Webhook-Signature": _sign(body_bytes),
                },
            )

        assert response.status_code == 422
        warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warning_records) >= 1
