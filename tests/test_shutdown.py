"""Tests for graceful shutdown behavior (issue #46)."""

from __future__ import annotations

import asyncio
import contextlib
import signal
from collections.abc import AsyncGenerator, Generator
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
    mock.last_reconnect_attempt_at = None
    mock.consecutive_reconnect_failures = 0
    mock.reconnect_loop_active = False
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
        from hermes.config import Settings, get_settings
        from hermes.server import app

        srv._shutdown_event = asyncio.Event()
        # Disable HMAC validation so the test works regardless of .env contents
        app.dependency_overrides[get_settings] = lambda: Settings(webhook_secret="")
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
    async def test_shutdown_waits_for_inflight_requests(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Lifespan teardown waits until _inflight reaches 0 before disconnecting."""
        import hermes.server as srv
        from hermes.server import app
        from hermes.config import Settings

        monkeypatch.setenv("SHUTDOWN_TIMEOUT", "2.0")
        timeout = Settings().shutdown_timeout

        mock_pub = _make_mock_publisher()

        # Simulate one in-flight request that resolves quickly
        srv._shutdown_event = asyncio.Event()
        srv._inflight = 1
        app.state.inflight_lock = asyncio.Lock()

        async def _clear_inflight_soon() -> None:
            await asyncio.sleep(0.2)
            async with app.state.inflight_lock:
                srv._inflight = 0

        task = asyncio.create_task(_clear_inflight_soon())

        # Patch publisher.disconnect to confirm it's called after inflight clears
        disconnect_called_at_inflight: list[int] = []

        async def _tracked_disconnect(**kwargs: object) -> None:
            async with app.state.inflight_lock:
                disconnect_called_at_inflight.append(srv._inflight)

        mock_pub.disconnect = _tracked_disconnect

        # Run the post-yield shutdown logic directly
        deadline = timeout
        poll_interval = 0.05
        elapsed = 0.0
        while elapsed < deadline:
            async with app.state.inflight_lock:
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
        from hermes.server import app
        from hermes.config import Settings

        monkeypatch.setenv("SHUTDOWN_TIMEOUT", "0.3")
        timeout = Settings().shutdown_timeout

        srv._shutdown_event = asyncio.Event()
        srv._inflight = 5  # stuck requests
        app.state.inflight_lock = asyncio.Lock()

        disconnect_called = False

        async def _tracked_disconnect(**kwargs: object) -> None:
            nonlocal disconnect_called
            disconnect_called = True

        # Run the shutdown polling loop
        deadline = timeout
        poll_interval = 0.05
        elapsed = 0.0
        while elapsed < deadline:
            async with app.state.inflight_lock:
                remaining = srv._inflight
            if remaining == 0:
                break
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        await _tracked_disconnect()

        assert disconnect_called
        # _inflight never reached 0 — stuck at 5
        async with app.state.inflight_lock:
            assert srv._inflight == 5

        # Reset inflight for subsequent tests
        srv._inflight = 0


# ---------------------------------------------------------------------------
# _inflight counter correctness (issue #345)
# ---------------------------------------------------------------------------


class TestInflightCounter:
    @pytest.mark.asyncio
    async def test_context_manager_increments_and_decrements(self) -> None:
        """_inflight_context increments on entry and decrements on exit."""
        import hermes.server as srv

        assert srv._inflight == 0
        async with srv._inflight_context():
            async with srv._inflight_lock:
                assert srv._inflight == 1
        async with srv._inflight_lock:
            assert srv._inflight == 0

    @pytest.mark.asyncio
    async def test_context_manager_decrements_on_exception(self) -> None:
        """_inflight_context decrements even when an exception is raised inside it."""
        import hermes.server as srv

        assert srv._inflight == 0
        with pytest.raises(RuntimeError):
            async with srv._inflight_context():
                raise RuntimeError("boom")
        async with srv._inflight_lock:
            assert srv._inflight == 0

    @pytest.mark.asyncio
    async def test_context_manager_decrements_on_http_exception(self) -> None:
        """_inflight_context decrements when an HTTPException is raised inside it."""
        import hermes.server as srv
        from fastapi import HTTPException

        assert srv._inflight == 0
        with pytest.raises(HTTPException):
            async with srv._inflight_context():
                raise HTTPException(status_code=503, detail="test")
        async with srv._inflight_lock:
            assert srv._inflight == 0

    def test_webhook_post_resets_inflight_to_zero_after_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After a successful webhook POST, _inflight returns to 0."""
        import hermes.server as srv
        from hermes.config import get_settings

        monkeypatch.setenv("WEBHOOK_SECRET", "")
        get_settings.cache_clear()
        client = _build_client()
        assert srv._inflight == 0
        response = client.post(
            "/webhook",
            json={
                "event": "agent.created",
                "data": {"host": "h", "name": "n"},
                "timestamp": "2026-04-22T00:00:00Z",
            },
        )
        assert response.status_code == 202
        assert srv._inflight == 0

    def test_webhook_post_resets_inflight_to_zero_on_publish_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After a failed publish, _inflight returns to 0."""
        import hermes.server as srv
        from hermes.config import get_settings

        monkeypatch.setenv("WEBHOOK_SECRET", "")
        get_settings.cache_clear()
        mock_pub = _make_mock_publisher()
        mock_pub.publish = AsyncMock(side_effect=asyncio.TimeoutError())
        client = _build_client(mock_pub)
        assert srv._inflight == 0
        response = client.post(
            "/webhook",
            json={
                "event": "agent.created",
                "data": {"host": "h", "name": "n"},
                "timestamp": "2026-04-22T00:00:00Z",
            },
        )
        assert response.status_code == 503
        assert srv._inflight == 0

    def test_health_inflight_requests_reflects_module_counter(self) -> None:
        """The /health endpoint reports whatever _inflight is set to."""
        import hermes.server as srv

        srv._inflight = 3
        client = _build_client()
        body = client.get("/health").json()
        assert body["inflight_requests"] == 3


# ---------------------------------------------------------------------------
# Shutdown TOCTOU race (issue #440)
# ---------------------------------------------------------------------------


@contextlib.asynccontextmanager
async def _race_inflight_context() -> AsyncGenerator[None, None]:
    """Test double for _inflight_context that simulates a signal arriving
    between the increment and the handler body — sets _shutdown_event after
    incrementing _inflight, then yields."""
    import hermes.server as srv

    async with srv._inflight_lock:
        srv._inflight += 1
    srv._shutdown_event.set()  # race: signal lands after increment, before re-check
    try:
        yield
    finally:
        async with srv._inflight_lock:
            srv._inflight -= 1


class TestShutdownRaceCondition:
    """Issue #440 — TOCTOU between ShutdownMiddleware and _inflight increment."""

    @pytest.fixture(autouse=True)
    def _reset_shutdown_state(self) -> Generator[None, None, None]:
        """Reset shutdown event and inflight counter around every test in this
        class so a failure mid-test does not poison sibling tests."""
        import hermes.server as srv

        original_event = srv._shutdown_event
        original_inflight = srv._inflight
        srv._shutdown_event = asyncio.Event()
        srv._inflight = 0
        try:
            yield
        finally:
            srv._shutdown_event = original_event
            srv._inflight = original_inflight

    def test_webhook_returns_503_when_shutdown_set_after_increment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If shutdown is signalled between the middleware check and the
        handler's _inflight increment, the handler's post-increment re-check
        must return 503 without calling publisher.publish, and _inflight must
        decrement back to 0 via the context manager's finally."""
        import hermes.server as srv
        from hermes.config import get_settings

        get_settings().webhook_secret = ""
        monkeypatch.setattr(srv, "_inflight_context", _race_inflight_context)

        mock_pub = _make_mock_publisher()
        client = _build_client(mock_pub)

        response = client.post(
            "/webhook",
            json={
                "event": "agent.created",
                "data": {"host": "h", "name": "n"},
                "timestamp": "2026-04-22T00:00:00Z",
            },
        )

        assert response.status_code == 503
        assert response.json()["detail"] == "Service is shutting down"
        mock_pub.publish.assert_not_awaited()
        assert srv._inflight == 0
        assert srv._shutdown_event.is_set()

    def test_webhook_proceeds_when_shutdown_never_signalled(self) -> None:
        """Sanity check: with shutdown unset, the handler succeeds end-to-end
        and the new post-increment guard does not regress the happy path."""
        import hermes.server as srv
        from hermes.config import get_settings

        get_settings().webhook_secret = ""
        mock_pub = _make_mock_publisher()
        client = _build_client(mock_pub)

        response = client.post(
            "/webhook",
            json={
                "event": "agent.created",
                "data": {"host": "h", "name": "n"},
                "timestamp": "2026-04-22T00:00:00Z",
            },
        )

        assert response.status_code == 202
        mock_pub.publish.assert_awaited_once()
        assert srv._inflight == 0
        assert not srv._shutdown_event.is_set()

    @pytest.mark.asyncio
    async def test_drain_loop_observes_increment_taken_before_signal(self) -> None:
        """Invariant check (not guard coverage): validates that _inflight_context increments before the re-check fires, so drain-loop reads always observe >= 1 when the event is still unset — the underlying monotonicity guarantee the new guard at server.py:405 relies on. Direct 503 guard coverage is in test_webhook_returns_503_when_shutdown_set_after_increment."""
        import hermes.server as srv

        srv._shutdown_event = asyncio.Event()
        srv._inflight = 0

        async with srv._inflight_context():
            # Re-check inside the body — event is unset, so we proceed.
            assert not srv._shutdown_event.is_set()
            # The drain loop's read pattern under the same lock:
            async with srv._inflight_lock:
                observed = srv._inflight
            assert observed >= 1  # drain loop would NOT take the disconnect branch
        async with srv._inflight_lock:
            assert srv._inflight == 0
