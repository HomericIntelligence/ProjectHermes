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
    from hermes.server import app
    from hermes.publisher import Publisher
    from hermes.config import settings

    mock_publisher = MagicMock(spec=Publisher)
    mock_publisher.is_connected = True
    mock_publisher.active_subjects = []
    mock_publisher.publish = AsyncMock()

    # Inject the mock before the test client starts
    app.state.publisher = mock_publisher
    # Set a known secret so tests can compute valid signatures
    settings.webhook_secret = _TEST_SECRET
    return TestClient(app, raise_server_exceptions=True)


class TestHealthEndpoint:
    def test_health_returns_200(self) -> None:
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


class TestHealthHmacField:
    def test_health_shows_hmac_enabled(self) -> None:
        client = _build_client()  # _build_client sets webhook_secret = _TEST_SECRET
        body = client.get("/health").json()
        assert body["hmac_validation_enabled"] is True

    def test_health_shows_hmac_disabled(self) -> None:
        from hermes.server import app
        from hermes.publisher import Publisher
        from hermes.config import settings

        mock_publisher = MagicMock(spec=Publisher)
        mock_publisher.is_connected = True
        mock_publisher.active_subjects = []
        mock_publisher.publish = AsyncMock()

        app.state.publisher = mock_publisher
        settings.webhook_secret = ""
        client = TestClient(app, raise_server_exceptions=True)
        body = client.get("/health").json()
        assert body["hmac_validation_enabled"] is False


class TestHmacStartupWarning:
    """Tests that lifespan emits (or suppresses) the HMAC-disabled warning."""

    async def _run_lifespan(self) -> None:
        """Run lifespan startup with a mocked Publisher, then shut down."""
        from unittest.mock import patch
        from hermes.server import lifespan, app

        mock_publisher_instance = MagicMock()
        mock_publisher_instance.connect = AsyncMock()
        mock_publisher_instance.disconnect = AsyncMock()

        with patch("hermes.server.Publisher", return_value=mock_publisher_instance):
            async with lifespan(app):
                pass

    async def test_startup_warns_when_hmac_disabled(self, caplog: object) -> None:
        import logging
        from hermes.config import settings

        settings.webhook_secret = ""
        with caplog.at_level(logging.WARNING, logger="hermes.server"):  # type: ignore[attr-defined]
            await self._run_lifespan()
        assert any(
            "HMAC webhook validation is DISABLED" in r.message
            for r in caplog.records  # type: ignore[attr-defined]
        )

    async def test_no_warning_when_secret_set(self, caplog: object) -> None:
        import logging
        from hermes.config import settings

        settings.webhook_secret = "some-secret"
        with caplog.at_level(logging.WARNING, logger="hermes.server"):  # type: ignore[attr-defined]
            await self._run_lifespan()
        assert not any(
            "HMAC webhook validation is DISABLED" in r.message
            for r in caplog.records  # type: ignore[attr-defined]
        )


class TestSubjectsEndpoint:
    def test_subjects_returns_list(self) -> None:
        client = _build_client()
        body = client.get("/subjects").json()
        assert "subjects" in body
        assert isinstance(body["subjects"], list)
