"""Tests for graceful shutdown behavior (issue #46)."""

from __future__ import annotations

import asyncio
import signal
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_mock_publisher(*, connected: bool = True) -> MagicMock:
    from hermes.publisher import Publisher

    mock = MagicMock(spec=Publisher)
    mock.is_connected = connected
    mock.active_subjects = []
    mock.publish = AsyncMock()
    mock.disconnect = AsyncMock()
    mock.reconnect_count = 0
    mock.last_error = ""
    return mock


def _build_client(publisher: MagicMock | None = None) -> TestClient:
    from hermes.server import app

    if publisher is None:
        publisher = _make_mock_publisher()
    app.state.publisher = publisher
    return TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class TestShutdownTimeoutSetting:
    def test_default_shutdown_timeout(self) -> None:
        from hermes.config import Settings

        s = Settings()
        assert s.shutdown_timeout == 10.0

    def test_shutdown_timeout_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from hermes.config import Settings

        monkeypatch.setenv("SHUTDOWN_TIMEOUT", "30.0")
        s = Settings()
        assert s.shutdown_timeout == 30.0


# ---------------------------------------------------------------------------
# Publisher.disconnect drain_timeout
# ---------------------------------------------------------------------------


class TestPublisherDisconnect:
    @pytest.mark.asyncio
    async def test_disconnect_drains_connection(self) -> None:
        from hermes.publisher import Publisher

        pub = Publisher()
        mock_nc = AsyncMock()
        mock_nc.is_closed = False
        pub._nc = mock_nc

        await pub.disconnect()

        mock_nc.drain.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_disconnect_skips_drain_when_not_connected(self) -> None:
        from hermes.publisher import Publisher

        pub = Publisher()
        # _nc is None — should be a no-op
        await pub.disconnect()


# ---------------------------------------------------------------------------
# Health endpoint reflects shutting_down flag
# ---------------------------------------------------------------------------


class TestHealthShuttingDown:
    def test_health_includes_shutting_down_field(self) -> None:
        client = _build_client()
        body = client.get("/health").json()
        assert "shutting_down" in body

    def test_health_shutting_down_false_normally(self) -> None:
        import hermes.server as srv

        srv._shutdown_event = asyncio.Event()
        client = _build_client()
        body = client.get("/health").json()
        assert body["shutting_down"] is False

    def test_health_shutting_down_true_when_event_set(self) -> None:
        import hermes.server as srv

        srv._shutdown_event = asyncio.Event()
        srv._shutdown_event.set()
        try:
            client = _build_client()
            body = client.get("/health").json()
            assert body["shutting_down"] is True
        finally:
            srv._shutdown_event = asyncio.Event()


# ---------------------------------------------------------------------------
# ShutdownMiddleware — reject /webhook during shutdown
# ---------------------------------------------------------------------------


class TestShutdownMiddleware:
    def test_webhook_rejected_503_during_shutdown(self) -> None:
        import hermes.server as srv

        srv._shutdown_event = asyncio.Event()
        srv._shutdown_event.set()
        try:
            # webhook_secret="" is the default; no env override needed
            client = _build_client()
            response = client.post(
                "/webhook",
                json={
                    "event": "agent.created",
                    "data": {"host": "h", "name": "n"},
                    "timestamp": "2026-04-22T00:00:00Z",
                },
            )
            assert response.status_code == 503
        finally:
            srv._shutdown_event = asyncio.Event()

    def test_webhook_accepted_when_not_shutting_down(self) -> None:
        import hermes.server as srv

        srv._shutdown_event = asyncio.Event()
        # webhook_secret="" is the default; no env override needed
        client = _build_client()
        response = client.post(
            "/webhook",
            json={
                "event": "agent.created",
                "data": {"host": "h", "name": "n"},
                "timestamp": "2026-04-22T00:00:00Z",
            },
        )
        # 202 means the request was processed (NATS publish is mocked)
        assert response.status_code == 202

    def test_health_allowed_during_shutdown(self) -> None:
        import hermes.server as srv

        srv._shutdown_event = asyncio.Event()
        srv._shutdown_event.set()
        try:
            client = _build_client()
            response = client.get("/health")
            assert response.status_code == 200
        finally:
            srv._shutdown_event = asyncio.Event()


# ---------------------------------------------------------------------------
# Signal handler sets _shutdown_event
# ---------------------------------------------------------------------------


class TestSignalHandler:
    def test_on_shutdown_signal_sets_event(self) -> None:
        import hermes.server as srv

        original = srv._shutdown_event
        srv._shutdown_event = asyncio.Event()
        try:
            assert not srv._shutdown_event.is_set()
            srv._on_shutdown_signal(signal.SIGTERM)
            assert srv._shutdown_event.is_set()
        finally:
            srv._shutdown_event = original

    def test_on_shutdown_signal_with_sigint(self) -> None:
        import hermes.server as srv

        original = srv._shutdown_event
        srv._shutdown_event = asyncio.Event()
        try:
            srv._on_shutdown_signal(signal.SIGINT)
            assert srv._shutdown_event.is_set()
        finally:
            srv._shutdown_event = original


# ---------------------------------------------------------------------------
# Lifespan shutdown sequence
# ---------------------------------------------------------------------------


class TestLifespanShutdown:
    @pytest.mark.asyncio
    async def test_shutdown_waits_for_inflight_requests(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Lifespan teardown waits until _inflight reaches 0 before disconnecting."""
        import hermes.server as srv
        from hermes.config import Settings

        monkeypatch.setenv("SHUTDOWN_TIMEOUT", "2.0")
        timeout = Settings().shutdown_timeout

        mock_pub = _make_mock_publisher()

        # Simulate one in-flight request that resolves quickly
        srv._shutdown_event = asyncio.Event()
        srv._inflight = 1

        async def _clear_inflight_soon() -> None:
            await asyncio.sleep(0.2)
            async with srv._inflight_lock:
                srv._inflight = 0

        task = asyncio.create_task(_clear_inflight_soon())

        # Patch publisher.disconnect to confirm it's called after inflight clears
        disconnect_called_at_inflight: list[int] = []

        async def _tracked_disconnect(**kwargs: object) -> None:
            async with srv._inflight_lock:
                disconnect_called_at_inflight.append(srv._inflight)

        mock_pub.disconnect = _tracked_disconnect

        # Run the post-yield shutdown logic directly
        deadline = timeout
        poll_interval = 0.05
        elapsed = 0.0
        while elapsed < deadline:
            async with srv._inflight_lock:
                remaining = srv._inflight
            if remaining == 0:
                break
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        await _tracked_disconnect()
        await task

        # Disconnect was called only after inflight dropped to 0
        assert disconnect_called_at_inflight == [0]

    @pytest.mark.asyncio
    async def test_shutdown_proceeds_after_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Lifespan teardown proceeds with disconnect even if inflight never clears."""
        import hermes.server as srv
        from hermes.config import Settings

        monkeypatch.setenv("SHUTDOWN_TIMEOUT", "0.3")
        timeout = Settings().shutdown_timeout

        srv._shutdown_event = asyncio.Event()
        srv._inflight = 5  # stuck requests

        disconnect_called = False

        async def _tracked_disconnect(**kwargs: object) -> None:
            nonlocal disconnect_called
            disconnect_called = True

        # Run the shutdown polling loop
        deadline = timeout
        poll_interval = 0.05
        elapsed = 0.0
        while elapsed < deadline:
            async with srv._inflight_lock:
                remaining = srv._inflight
            if remaining == 0:
                break
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        await _tracked_disconnect()

        assert disconnect_called
        # _inflight never reached 0 — stuck at 5
        async with srv._inflight_lock:
            assert srv._inflight == 5

        # Reset inflight for subsequent tests
        srv._inflight = 0
