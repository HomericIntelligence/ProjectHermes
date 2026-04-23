"""Tests for rate limiting on the /webhook endpoint."""

from __future__ import annotations

import asyncio
import hashlib
import hmac as hmac_mod
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

_TEST_SECRET = "test-webhook-secret-padding-xxxxx"
_VALID_PAYLOAD = {
    "event": "agent.created",
    "data": {"host": "localhost", "name": "bot"},
    "timestamp": "2026-03-15T00:00:00Z",
}


def _sign(body: bytes) -> str:
    return hmac_mod.new(_TEST_SECRET.encode(), body, hashlib.sha256).hexdigest()


def _build_client(rate_limit: str = "5/minute") -> TestClient:
    """Build a TestClient with mocked Publisher and configurable rate limit."""
    from hermes.server import app
    from hermes.publisher import Publisher
    from hermes.config import settings

    mock_publisher = MagicMock(spec=Publisher)
    mock_publisher.is_connected = True
    mock_publisher.active_subjects = []
    mock_publisher.publish = AsyncMock()

    app.state.publisher = mock_publisher
    settings.webhook_secret = _TEST_SECRET
    settings.webhook_rate_limit = rate_limit

    # Reset the limiter storage between tests so counts don't bleed across
    from hermes.rate_limit import limiter
    limiter._storage.reset()  # type: ignore[attr-defined]

    return TestClient(app, raise_server_exceptions=False)


def _post_webhook(client: TestClient, payload: dict | None = None) -> object:
    body = json.dumps(payload or _VALID_PAYLOAD).encode()
    return client.post(
        "/webhook",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Webhook-Signature": _sign(body),
        },
    )


class TestRateLimitEnforcement:
    def test_requests_within_limit_return_202(self) -> None:
        client = _build_client(rate_limit="5/minute")
        for _ in range(5):
            response = _post_webhook(client)
            assert response.status_code == 202

    def test_request_exceeding_limit_returns_429(self) -> None:
        client = _build_client(rate_limit="3/minute")
        for _ in range(3):
            _post_webhook(client)
        response = _post_webhook(client)
        assert response.status_code == 429

    def test_429_response_has_retry_after_header(self) -> None:
        client = _build_client(rate_limit="2/minute")
        _post_webhook(client)
        _post_webhook(client)
        response = _post_webhook(client)
        assert response.status_code == 429
        assert "retry-after" in response.headers

    def test_429_response_body_contains_detail(self) -> None:
        client = _build_client(rate_limit="1/minute")
        _post_webhook(client)
        response = _post_webhook(client)
        assert response.status_code == 429
        body = response.json()
        assert "detail" in body

    def test_retry_after_header_is_numeric(self) -> None:
        client = _build_client(rate_limit="1/minute")
        _post_webhook(client)
        response = _post_webhook(client)
        assert response.status_code == 429
        retry_after = response.headers["retry-after"]
        assert retry_after.isdigit() or retry_after.lstrip("-").isdigit()


class TestNatsPublishTimeout:
    def test_slow_publish_returns_503(self) -> None:
        from hermes.server import app
        from hermes.publisher import Publisher
        from hermes.config import settings
        from hermes.rate_limit import limiter

        async def _slow_publish(*_args: object, **_kwargs: object) -> None:
            await asyncio.sleep(10)

        mock_publisher = MagicMock(spec=Publisher)
        mock_publisher.is_connected = True
        mock_publisher.active_subjects = []
        mock_publisher.publish = _slow_publish

        app.state.publisher = mock_publisher
        settings.webhook_secret = _TEST_SECRET
        settings.webhook_rate_limit = "100/minute"

        limiter._storage.reset()  # type: ignore[attr-defined]

        with patch("hermes.server.asyncio.wait_for", side_effect=asyncio.TimeoutError):
            client = TestClient(app, raise_server_exceptions=False)
            response = _post_webhook(client)

        assert response.status_code == 503
        assert "timed out" in response.json()["detail"]
