"""Tests for rate limiting on the /subjects endpoint (mirrors test_rate_limit.py).

Verifies SUBJECTS_RATE_LIMIT is enforced at the HTTP level — not just that the
config field validates correctly. See issue #585 (follow-up from #362).
"""

from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient


@contextmanager
def _subjects_rate_limit_client(
    rate_limit: str = "5/minute",
) -> Generator[TestClient, None, None]:
    """TestClient with mocked Publisher and configurable SUBJECTS_RATE_LIMIT."""
    from hermes.server import app
    from hermes.publisher import Publisher
    from hermes.config import get_settings
    from hermes.rate_limit import limiter

    mock_publisher = MagicMock(spec=Publisher)
    mock_publisher.is_connected = True
    mock_publisher.active_subjects = []
    mock_publisher.active_subjects_max = 1000
    mock_publisher.publish = AsyncMock()

    app.state.publisher = mock_publisher
    limiter._storage.reset()  # type: ignore[attr-defined]

    env_overrides = {
        "SUBJECTS_RATE_LIMIT": rate_limit,
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


def _get_subjects(client: TestClient) -> object:
    return client.get("/subjects")


class TestSubjectsRateLimitEnforcement:
    """Mirrors TestRateLimitEnforcement for /subjects (#585)."""

    def test_requests_within_limit_return_200(self) -> None:
        with _subjects_rate_limit_client(rate_limit="5/minute") as client:
            for _ in range(5):
                response = _get_subjects(client)
                assert response.status_code == 200

    def test_request_exceeding_limit_returns_429(self) -> None:
        with _subjects_rate_limit_client(rate_limit="3/minute") as client:
            for _ in range(3):
                _get_subjects(client)
            response = _get_subjects(client)
        assert response.status_code == 429

    def test_429_response_has_retry_after_header(self) -> None:
        with _subjects_rate_limit_client(rate_limit="2/minute") as client:
            _get_subjects(client)
            _get_subjects(client)
            response = _get_subjects(client)
        assert response.status_code == 429
        assert "retry-after" in response.headers

    def test_429_response_body_contains_detail(self) -> None:
        with _subjects_rate_limit_client(rate_limit="1/minute") as client:
            _get_subjects(client)
            response = _get_subjects(client)
        assert response.status_code == 429
        body = response.json()
        assert "detail" in body

    def test_retry_after_header_is_numeric(self) -> None:
        with _subjects_rate_limit_client(rate_limit="1/minute") as client:
            _get_subjects(client)
            response = _get_subjects(client)
        assert response.status_code == 429
        retry_after = response.headers["retry-after"]
        assert retry_after.isdigit() or retry_after.lstrip("-").isdigit()
