"""Tests for the FastAPI webhook endpoints."""

from __future__ import annotations

import hashlib
import hmac as hmac_mod
import json
import sys
import os
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# Fixed secret used across all webhook tests
_TEST_SECRET = "test-webhook-secret-padding-xxxxx"


def _sign(body: bytes) -> str:
    """Compute HMAC-SHA256 hex digest for the given body using _TEST_SECRET."""
    return hmac_mod.new(_TEST_SECRET.encode(), body, hashlib.sha256).hexdigest()


def _build_client() -> TestClient:
    """Build a TestClient with a mocked Publisher and a known webhook secret."""
    from hermes.config import settings
    from hermes.publisher import Publisher
    from hermes.server import app

    mock_publisher = MagicMock(spec=Publisher)
    mock_publisher.is_connected = True
    mock_publisher.active_subjects = []
    mock_publisher.publish = AsyncMock()

    # Inject the mock before the test client starts
    app.state.publisher = mock_publisher
    # Set a known secret so tests can compute valid signatures
    settings.webhook_secret = _TEST_SECRET
    return TestClient(app, raise_server_exceptions=True)


def _build_client_disconnected() -> TestClient:
    """Build a TestClient where the Publisher reports NATS as disconnected."""
    from hermes.server import app
    from hermes.publisher import Publisher

    mock_publisher = MagicMock(spec=Publisher)
    mock_publisher.is_connected = False
    mock_publisher.active_subjects = []
    mock_publisher.publish = AsyncMock()

    app.state.publisher = mock_publisher
    return TestClient(app, raise_server_exceptions=True)


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
    def test_valid_payload_returns_202(self) -> None:
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

    def test_webhook_invalid_payload_returns_422(self) -> None:
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

    def test_webhook_missing_body_returns_422(self) -> None:
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

    def test_webhook_returns_event_name(self) -> None:
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

    def test_webhook_bad_signature_returns_401(self) -> None:
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

    def test_invalid_signature_increments_failed_counter(self) -> None:
        from prometheus_client import REGISTRY

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


class TestSettings:
    def test_hermes_host_defaults_to_localhost(self) -> None:
        from hermes.config import Settings

        s = Settings()
        assert s.hermes_host == "127.0.0.1"


class TestSubjectsEndpoint:
    def test_subjects_returns_list(self) -> None:
        client = _build_client()
        body = client.get("/subjects").json()
        assert "subjects" in body
        assert isinstance(body["subjects"], list)
