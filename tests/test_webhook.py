"""Tests for the FastAPI webhook endpoints."""

from __future__ import annotations

import hashlib
import hmac as hmac_mod
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

# Fixed secret used across all webhook tests
_TEST_SECRET = "test-webhook-secret-padding-xxxxx"


def _sign(body: bytes) -> str:
    """Compute HMAC-SHA256 hex digest for the given body using _TEST_SECRET."""
    return hmac_mod.new(_TEST_SECRET.encode(), body, hashlib.sha256).hexdigest()


def _build_client(*, connected: bool = True) -> TestClient:
    """Build a TestClient with a mocked Publisher."""
    from hermes.publisher import Publisher
    from hermes.server import app

    mock_publisher = MagicMock(spec=Publisher)
    mock_publisher.is_connected = connected
    mock_publisher.active_subjects = []
    mock_publisher.publish = AsyncMock()

    # Inject the mock before the test client starts
    app.state.publisher = mock_publisher
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

    def test_invalid_signature_increments_failed_counter(self, monkeypatch: pytest.MonkeyPatch) -> None:
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


class TestSignatureValidation:
    """Tests for _verify_signature behaviour (issue #156)."""

    def test_missing_signature_header_returns_401_when_secret_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WEBHOOK_SECRET", _TEST_SECRET)
        client = _build_client()
        payload = {
            "event": "agent.created",
            "data": {"host": "localhost", "name": "bot"},
            "timestamp": "2026-03-15T00:00:00Z",
        }
        response = client.post("/webhook", json=payload)
        assert response.status_code == 401

    def test_empty_signature_header_returns_401_when_secret_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
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


class TestSubjectsEndpoint:
    def test_subjects_returns_list(self) -> None:
        client = _build_client()
        body = client.get("/subjects").json()
        assert "subjects" in body
        assert isinstance(body["subjects"], list)


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
