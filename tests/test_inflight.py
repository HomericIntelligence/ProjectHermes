"""Tests for the _inflight counter — increment/decrement, drain loop, health accuracy."""

from __future__ import annotations

import asyncio
import hashlib
import hmac as hmac_mod
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

_TEST_SECRET = "test-webhook-secret-padding-xxxxx"

_AGENT_PAYLOAD = {
    "event": "agent.created",
    "data": {"host": "h", "name": "n"},
    "timestamp": "2026-04-22T00:00:00Z",
}


def _sign(body: bytes) -> str:
    return hmac_mod.new(_TEST_SECRET.encode(), body, hashlib.sha256).hexdigest()


def _make_mock_publisher(*, connected: bool = True) -> MagicMock:
    from hermes.publisher import Publisher

    mock = MagicMock(spec=Publisher)
    mock.is_connected = connected
    mock.active_subjects = []
    mock.dead_letter_count = 0
    mock.active_subjects_max = 1000
    mock.publish = AsyncMock()
    mock.disconnect = AsyncMock()
    mock.reconnect_count = 0
    mock.last_error = ""
    return mock


def _build_client(publisher: MagicMock | None = None) -> TestClient:
    from hermes.config import get_settings
    from hermes.rate_limit import limiter
    from hermes.server import app

    if publisher is None:
        publisher = _make_mock_publisher()
    app.state.publisher = publisher
    get_settings().webhook_secret = _TEST_SECRET
    limiter._storage.reset()  # type: ignore[attr-defined]
    return TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# Increment / decrement on successful request
# ---------------------------------------------------------------------------


class TestInflightIncrementDecrement:
    def test_inflight_is_zero_before_request(self) -> None:
        import hermes.server as srv

        assert srv._inflight == 0

    def test_inflight_returns_to_zero_after_successful_request(self) -> None:
        import hermes.server as srv
        import json

        client = _build_client()
        body = json.dumps(_AGENT_PAYLOAD).encode()
        client.post(
            "/webhook",
            content=body,
            headers={"X-Webhook-Signature": _sign(body), "Content-Type": "application/json"},
        )
        assert srv._inflight == 0

    def test_inflight_increments_during_publish(self) -> None:
        """Capture _inflight value mid-request inside the publish mock."""
        import hermes.server as srv
        import json

        inflight_during: list[int] = []
        publisher = _make_mock_publisher()

        async def _capture_and_return(*args: object, **kwargs: object) -> None:
            async with srv._inflight_lock:
                inflight_during.append(srv._inflight)

        publisher.publish = _capture_and_return

        client = _build_client(publisher)
        body = json.dumps(_AGENT_PAYLOAD).encode()
        resp = client.post(
            "/webhook",
            content=body,
            headers={"X-Webhook-Signature": _sign(body), "Content-Type": "application/json"},
        )

        assert resp.status_code == 202
        assert inflight_during == [1], f"Expected [1], got {inflight_during}"

    def test_inflight_returns_to_zero_after_publish(self) -> None:
        import hermes.server as srv
        import json

        client = _build_client()
        body = json.dumps(_AGENT_PAYLOAD).encode()
        resp = client.post(
            "/webhook",
            content=body,
            headers={"X-Webhook-Signature": _sign(body), "Content-Type": "application/json"},
        )
        assert resp.status_code == 202
        assert srv._inflight == 0


# ---------------------------------------------------------------------------
# Decrement on error paths
# ---------------------------------------------------------------------------


class TestInflightDecrementOnErrors:
    def test_inflight_decrements_on_hmac_failure(self) -> None:
        import hermes.server as srv
        import json

        client = _build_client()
        body = json.dumps(_AGENT_PAYLOAD).encode()
        resp = client.post(
            "/webhook",
            content=body,
            headers={"X-Webhook-Signature": "badsig", "Content-Type": "application/json"},
        )
        assert resp.status_code == 401
        assert srv._inflight == 0

    def test_inflight_decrements_on_invalid_payload(self) -> None:
        import hermes.server as srv

        malformed = b"not-json"
        client = _build_client()
        resp = client.post(
            "/webhook",
            content=malformed,
            headers={
                "X-Webhook-Signature": _sign(malformed),
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 422
        assert srv._inflight == 0

    def test_inflight_decrements_on_nats_not_connected(self) -> None:
        import hermes.server as srv
        import json

        publisher = _make_mock_publisher(connected=False)
        client = _build_client(publisher)
        body = json.dumps(_AGENT_PAYLOAD).encode()
        resp = client.post(
            "/webhook",
            content=body,
            headers={"X-Webhook-Signature": _sign(body), "Content-Type": "application/json"},
        )
        assert resp.status_code == 503
        assert srv._inflight == 0

    def test_inflight_decrements_on_publish_timeout(self) -> None:
        import hermes.server as srv
        import json

        publisher = _make_mock_publisher()
        publisher.publish = AsyncMock(side_effect=asyncio.TimeoutError)

        client = _build_client(publisher)
        body = json.dumps(_AGENT_PAYLOAD).encode()
        resp = client.post(
            "/webhook",
            content=body,
            headers={"X-Webhook-Signature": _sign(body), "Content-Type": "application/json"},
        )
        assert resp.status_code == 503
        assert srv._inflight == 0

    def test_inflight_decrements_on_publish_exception(self) -> None:
        import hermes.server as srv
        import json

        publisher = _make_mock_publisher()
        publisher.publish = AsyncMock(side_effect=RuntimeError("boom"))

        client = _build_client(publisher)
        body = json.dumps(_AGENT_PAYLOAD).encode()
        with pytest.raises(RuntimeError):
            client.post(
                "/webhook",
                content=body,
                headers={"X-Webhook-Signature": _sign(body), "Content-Type": "application/json"},
            )
        assert srv._inflight == 0


# ---------------------------------------------------------------------------
# Health endpoint reports accurate inflight count
# ---------------------------------------------------------------------------


class TestHealthInflightCount:
    def test_health_reports_zero_inflight_normally(self) -> None:
        client = _build_client()
        body = client.get("/health").json()
        assert body["inflight_requests"] == 0

    def test_health_reports_nonzero_inflight_when_set(self) -> None:
        import hermes.server as srv

        srv._inflight = 3
        try:
            client = _build_client()
            body = client.get("/health").json()
            assert body["inflight_requests"] == 3
        finally:
            srv._inflight = 0


# ---------------------------------------------------------------------------
# Drain loop waits for _inflight to reach 0
# ---------------------------------------------------------------------------


class TestDrainLoopWithRealCounter:
    @pytest.mark.asyncio
    async def test_drain_exits_immediately_when_inflight_zero(self) -> None:
        import hermes.server as srv

        srv._inflight = 0
        deadline = 1.0
        poll_interval = 0.05
        elapsed = 0.0
        iterations = 0
        while elapsed < deadline:
            async with srv._inflight_lock:
                remaining = srv._inflight
            if remaining == 0:
                break
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
            iterations += 1

        assert iterations == 0, "Drain loop should exit without sleeping when inflight is 0"

    @pytest.mark.asyncio
    async def test_drain_waits_until_inflight_clears(self) -> None:
        import hermes.server as srv

        srv._inflight = 1

        async def _clear_soon() -> None:
            await asyncio.sleep(0.15)
            async with srv._inflight_lock:
                srv._inflight = 0

        task = asyncio.create_task(_clear_soon())
        deadline = 2.0
        poll_interval = 0.05
        elapsed = 0.0
        while elapsed < deadline:
            async with srv._inflight_lock:
                remaining = srv._inflight
            if remaining == 0:
                break
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        await task
        assert elapsed < deadline, "Drain loop should have exited before timeout"
        assert srv._inflight == 0
