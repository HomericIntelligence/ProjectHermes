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

from hermes.models import HealthResponse, SubjectsResponse, WebhookAcceptedResponse

# Fixed secret used across all webhook tests
_TEST_SECRET = "test-webhook-secret"


def _sign(body: bytes) -> str:
    """Compute HMAC-SHA256 hex digest for the given body using _TEST_SECRET."""
    return hmac_mod.new(_TEST_SECRET.encode(), body, hashlib.sha256).hexdigest()


def _build_client() -> TestClient:
    """Build a TestClient with a mocked Publisher and a known webhook secret."""
    from hermes.server import app
    from hermes.publisher import Publisher
    from hermes.config import Settings, get_settings

    mock_publisher = MagicMock(spec=Publisher)
    mock_publisher.is_connected = True
    mock_publisher.active_subjects = []
    mock_publisher.publish = AsyncMock()

    # Inject the mock before the test client starts
    app.state.publisher = mock_publisher

    # Override the settings dependency so tests never touch shared state
    def override_get_settings() -> Settings:
        return Settings(webhook_secret=_TEST_SECRET)

    app.dependency_overrides[get_settings] = override_get_settings
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

    def test_health_response_matches_model(self) -> None:
        client = _build_client()
        body = client.get("/health").json()
        model = HealthResponse(**body)
        assert model.status == "ok"
        assert isinstance(model.nats_connected, bool)


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

    def test_webhook_response_matches_model(self) -> None:
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
        model = WebhookAcceptedResponse(**response.json())
        assert model.status == "accepted"
        assert model.event == "agent.created"

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


class TestSubjectsEndpoint:
    def test_subjects_returns_list(self) -> None:
        client = _build_client()
        body = client.get("/subjects").json()
        assert "subjects" in body
        assert isinstance(body["subjects"], list)

    def test_subjects_response_matches_model(self) -> None:
        client = _build_client()
        body = client.get("/subjects").json()
        model = SubjectsResponse(**body)
        assert isinstance(model.subjects, list)


class TestOpenAPISchema:
    def test_openapi_includes_health_response_schema(self) -> None:
        client = _build_client()
        schema = client.get("/openapi.json").json()
        components = schema.get("components", {}).get("schemas", {})
        assert "HealthResponse" in components

    def test_openapi_includes_webhook_accepted_response_schema(self) -> None:
        client = _build_client()
        schema = client.get("/openapi.json").json()
        components = schema.get("components", {}).get("schemas", {})
        assert "WebhookAcceptedResponse" in components

    def test_openapi_includes_subjects_response_schema(self) -> None:
        client = _build_client()
        schema = client.get("/openapi.json").json()
        components = schema.get("components", {}).get("schemas", {})
        assert "SubjectsResponse" in components

    def test_openapi_includes_error_response_schema(self) -> None:
        client = _build_client()
        schema = client.get("/openapi.json").json()
        components = schema.get("components", {}).get("schemas", {})
        assert "ErrorResponse" in components

    def test_openapi_webhook_documents_error_responses(self) -> None:
        client = _build_client()
        schema = client.get("/openapi.json").json()
        webhook_path = schema["paths"]["/webhook"]["post"]
        response_codes = set(webhook_path["responses"].keys())
        assert "401" in response_codes
        assert "503" in response_codes
