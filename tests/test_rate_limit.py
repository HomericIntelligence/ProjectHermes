"""Tests for rate limiting on the /webhook endpoint."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Generator
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from tests.helpers import TEST_SECRET, sign_body

_VALID_PAYLOAD = {
    "event": "agent.created",
    "data": {"host": "localhost", "name": "bot"},
    "timestamp": "2026-03-15T00:00:00Z",
}



@contextmanager
def _rate_limit_client(
    rate_limit: str = "5/minute",
) -> Generator[TestClient, None, None]:
    """Context manager providing a TestClient with mocked Publisher and configurable rate limit."""
    from hermes.server import app
    from hermes.publisher import Publisher
    from hermes.config import get_settings
    from hermes.rate_limit import limiter

    mock_publisher = MagicMock(spec=Publisher)
    mock_publisher.is_connected = True
    mock_publisher.active_subjects = []
    mock_publisher.publish = AsyncMock()

    app.state.publisher = mock_publisher
    limiter._storage.reset()  # type: ignore[attr-defined]

    env_overrides = {
        "WEBHOOK_SECRET": TEST_SECRET,
        "WEBHOOK_RATE_LIMIT": rate_limit,
    }
    old_env = {k: os.environ.get(k) for k in env_overrides}
    os.environ.update(env_overrides)
    get_settings.cache_clear()
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        get_settings.cache_clear()
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _post_webhook(client: TestClient, payload: dict | None = None) -> object:
    body = json.dumps(payload or _VALID_PAYLOAD).encode()
    return client.post(
        "/webhook",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Webhook-Signature": sign_body(body, TEST_SECRET),
        },
    )


class TestRateLimitEnforcement:
    def test_requests_within_limit_return_202(self) -> None:
        with _rate_limit_client(rate_limit="5/minute") as client:
            for _ in range(5):
                response = _post_webhook(client)
                assert response.status_code == 202

    def test_request_exceeding_limit_returns_429(self) -> None:
        with _rate_limit_client(rate_limit="3/minute") as client:
            for _ in range(3):
                _post_webhook(client)
            response = _post_webhook(client)
        assert response.status_code == 429

    def test_429_response_has_retry_after_header(self) -> None:
        with _rate_limit_client(rate_limit="2/minute") as client:
            _post_webhook(client)
            _post_webhook(client)
            response = _post_webhook(client)
        assert response.status_code == 429
        assert "retry-after" in response.headers

    def test_429_response_body_contains_detail(self) -> None:
        with _rate_limit_client(rate_limit="1/minute") as client:
            _post_webhook(client)
            response = _post_webhook(client)
        assert response.status_code == 429
        body = response.json()
        assert "detail" in body

    def test_retry_after_header_is_numeric(self) -> None:
        with _rate_limit_client(rate_limit="1/minute") as client:
            _post_webhook(client)
            response = _post_webhook(client)
        assert response.status_code == 429
        retry_after = response.headers["retry-after"]
        assert retry_after.isdigit() or retry_after.lstrip("-").isdigit()


class TestNatsPublishTimeout:
    def test_slow_publish_returns_503(self) -> None:
        from hermes.server import app
        from hermes.publisher import Publisher
        from hermes.config import get_settings
        from hermes.rate_limit import limiter

        mock_publisher = MagicMock(spec=Publisher)
        mock_publisher.is_connected = True
        mock_publisher.active_subjects = []
        mock_publisher.publish = AsyncMock(side_effect=asyncio.TimeoutError)

        app.state.publisher = mock_publisher
        limiter._storage.reset()  # type: ignore[attr-defined]

        env_overrides = {
            "WEBHOOK_SECRET": TEST_SECRET,
            "WEBHOOK_RATE_LIMIT": "100/minute",
        }
        old_env = {k: os.environ.get(k) for k in env_overrides}
        os.environ.update(env_overrides)
        get_settings.cache_clear()
        try:
            client = TestClient(app, raise_server_exceptions=False)
            response = _post_webhook(client)
        finally:
            get_settings.cache_clear()
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

        assert response.status_code == 503
        assert "timed out" in response.json()["detail"]
