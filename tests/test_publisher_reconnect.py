"""Tests for Publisher._reconnect_loop background task."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hermes.publisher import Publisher


def _make_mock_nc(*, is_closed: bool = False) -> MagicMock:
    """Return a MagicMock NATS client with configurable is_closed."""
    nc = MagicMock()
    nc.is_closed = is_closed
    nc.jetstream.return_value = MagicMock()
    jsm = AsyncMock()
    nc.jsm.return_value = jsm
    nc.drain = AsyncMock()
    return nc


async def _connect_publisher(pub: Publisher, mock_nc: MagicMock) -> None:
    """Helper: patch nats.connect and call pub.connect()."""
    with patch("nats.connect", return_value=mock_nc):
        await pub.connect("nats://localhost:4222", connect_timeout=5.0)


class TestReconnectLoopStartsOnConnect:
    @pytest.mark.asyncio
    async def test_reconnect_task_created_after_connect(self) -> None:
        pub = Publisher()
        mock_nc = _make_mock_nc()
        await _connect_publisher(pub, mock_nc)

        assert pub._reconnect_task is not None
        assert not pub._reconnect_task.done()

        # Clean up
        pub._stop_event.set()
        await asyncio.sleep(0)
        pub._reconnect_task.cancel()
        try:
            await pub._reconnect_task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_reconnect_task_cancelled_on_disconnect(self) -> None:
        pub = Publisher()
        mock_nc = _make_mock_nc()
        await _connect_publisher(pub, mock_nc)

        task = pub._reconnect_task
        assert task is not None

        await pub.disconnect()

        assert task.done()
        assert pub._reconnect_task is None


class TestReconnectLoopStopEvent:
    @pytest.mark.asyncio
    async def test_loop_exits_when_stop_event_set(self) -> None:
        pub = Publisher()
        # Run the loop with a very long poll interval; stop event fires immediately.
        pub._stop_event.set()
        await asyncio.wait_for(pub._reconnect_loop("nats://localhost:4222", 5.0), timeout=1.0)

    @pytest.mark.asyncio
    async def test_stop_event_set_before_poll_interval_elapses(self) -> None:
        pub = Publisher()

        with patch("hermes.config.get_settings") as mock_settings:
            mock_settings.return_value.nats_retry_interval = 60.0

            loop_task = asyncio.create_task(pub._reconnect_loop("nats://localhost:4222", 5.0))
            await asyncio.sleep(0)  # let the loop start waiting
            pub._stop_event.set()
            await asyncio.wait_for(loop_task, timeout=1.0)  # should exit quickly, not after 60s


class TestReconnectLoopDetectsClosedConnection:
    @pytest.mark.asyncio
    async def test_no_reconnect_attempt_when_connection_open(self) -> None:
        pub = Publisher()
        mock_nc = _make_mock_nc(is_closed=False)
        pub._nc = mock_nc
        pub._connected = True

        reconnect_calls: list[str] = []

        with (
            patch("hermes.config.get_settings") as mock_settings,
            patch("nats.connect", side_effect=lambda *a, **kw: reconnect_calls.append("called")),
        ):
            mock_settings.return_value.nats_retry_interval = 0.05

            loop_task = asyncio.create_task(pub._reconnect_loop("nats://localhost:4222", 5.0))
            await asyncio.sleep(0.12)  # two poll cycles
            pub._stop_event.set()
            await asyncio.wait_for(loop_task, timeout=1.0)

        assert reconnect_calls == [], "nats.connect should not be called while connection is open"

    @pytest.mark.asyncio
    async def test_reconnect_attempted_when_connection_closed(self) -> None:
        pub = Publisher()
        mock_nc = _make_mock_nc(is_closed=True)
        pub._nc = mock_nc

        new_nc = _make_mock_nc(is_closed=False)
        connect_calls: list[tuple] = []

        async def fake_connect(*args: object, **kwargs: object) -> MagicMock:
            connect_calls.append((args, kwargs))
            pub._stop_event.set()  # stop after first successful reconnect
            return new_nc

        with (
            patch("hermes.config.get_settings") as mock_settings,
            patch("nats.connect", side_effect=fake_connect),
        ):
            mock_settings.return_value.nats_retry_interval = 0.05

            await asyncio.wait_for(
                pub._reconnect_loop("nats://localhost:4222", 5.0), timeout=2.0
            )

        assert len(connect_calls) == 1
        assert pub._nc is new_nc


class TestReconnectLoopSuccess:
    @pytest.mark.asyncio
    async def test_successful_reconnect_fires_on_reconnected(self) -> None:
        pub = Publisher()
        mock_nc = _make_mock_nc(is_closed=True)
        pub._nc = mock_nc
        pub._connected = False

        new_nc = _make_mock_nc(is_closed=False)

        async def fake_connect(*args: object, **kwargs: object) -> MagicMock:
            pub._stop_event.set()
            return new_nc

        with (
            patch("hermes.config.get_settings") as mock_settings,
            patch("nats.connect", side_effect=fake_connect),
        ):
            mock_settings.return_value.nats_retry_interval = 0.05

            await asyncio.wait_for(
                pub._reconnect_loop("nats://localhost:4222", 5.0), timeout=2.0
            )

        assert pub._connected is True

    @pytest.mark.asyncio
    async def test_successful_reconnect_increments_reconnect_count(self) -> None:
        pub = Publisher()
        mock_nc = _make_mock_nc(is_closed=True)
        pub._nc = mock_nc

        new_nc = _make_mock_nc(is_closed=False)

        async def fake_connect(*args: object, **kwargs: object) -> MagicMock:
            pub._stop_event.set()
            return new_nc

        with (
            patch("hermes.config.get_settings") as mock_settings,
            patch("nats.connect", side_effect=fake_connect),
        ):
            mock_settings.return_value.nats_retry_interval = 0.05

            await asyncio.wait_for(
                pub._reconnect_loop("nats://localhost:4222", 5.0), timeout=2.0
            )

        assert pub.reconnect_count == 1

    @pytest.mark.asyncio
    async def test_successful_reconnect_updates_nc_and_js(self) -> None:
        pub = Publisher()
        mock_nc = _make_mock_nc(is_closed=True)
        pub._nc = mock_nc

        new_nc = _make_mock_nc(is_closed=False)
        new_js = MagicMock()
        new_nc.jetstream.return_value = new_js

        async def fake_connect(*args: object, **kwargs: object) -> MagicMock:
            pub._stop_event.set()
            return new_nc

        with (
            patch("hermes.config.get_settings") as mock_settings,
            patch("nats.connect", side_effect=fake_connect),
        ):
            mock_settings.return_value.nats_retry_interval = 0.05

            await asyncio.wait_for(
                pub._reconnect_loop("nats://localhost:4222", 5.0), timeout=2.0
            )

        assert pub._nc is new_nc
        assert pub._js is new_js


class TestReconnectLoopFailure:
    @pytest.mark.asyncio
    async def test_failed_reconnect_logs_warning_and_keeps_looping(self) -> None:
        pub = Publisher()
        mock_nc = _make_mock_nc(is_closed=True)
        pub._nc = mock_nc

        call_count = 0

        async def fake_connect(*args: object, **kwargs: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                pub._stop_event.set()
            raise ConnectionRefusedError("NATS unavailable")

        with (
            patch("hermes.config.get_settings") as mock_settings,
            patch("nats.connect", side_effect=fake_connect),
            patch("hermes.publisher.logger") as mock_logger,
        ):
            mock_settings.return_value.nats_retry_interval = 0.05

            await asyncio.wait_for(
                pub._reconnect_loop("nats://localhost:4222", 5.0), timeout=2.0
            )

        assert call_count >= 2
        warning_messages = [c.args[0] for c in mock_logger.warning.call_args_list]
        assert any("reconnect failed" in m for m in warning_messages)

    @pytest.mark.asyncio
    async def test_failed_reconnect_updates_last_error(self) -> None:
        pub = Publisher()
        mock_nc = _make_mock_nc(is_closed=True)
        pub._nc = mock_nc

        async def fake_connect(*args: object, **kwargs: object) -> MagicMock:
            pub._stop_event.set()
            raise ConnectionRefusedError("NATS gone")

        with (
            patch("hermes.config.get_settings") as mock_settings,
            patch("nats.connect", side_effect=fake_connect),
        ):
            mock_settings.return_value.nats_retry_interval = 0.05

            await asyncio.wait_for(
                pub._reconnect_loop("nats://localhost:4222", 5.0), timeout=2.0
            )

        assert "NATS gone" in pub.last_error

    @pytest.mark.asyncio
    async def test_failed_reconnect_does_not_change_nc(self) -> None:
        pub = Publisher()
        original_nc = _make_mock_nc(is_closed=True)
        pub._nc = original_nc

        async def fake_connect(*args: object, **kwargs: object) -> MagicMock:
            pub._stop_event.set()
            raise OSError("connection refused")

        with (
            patch("hermes.config.get_settings") as mock_settings,
            patch("nats.connect", side_effect=fake_connect),
        ):
            mock_settings.return_value.nats_retry_interval = 0.05

            await asyncio.wait_for(
                pub._reconnect_loop("nats://localhost:4222", 5.0), timeout=2.0
            )

        assert pub._nc is original_nc


class TestReconnectLoopConnectTimeout:
    @pytest.mark.asyncio
    async def test_connect_timeout_applied_via_wait_for(self) -> None:
        pub = Publisher()
        mock_nc = _make_mock_nc(is_closed=True)
        pub._nc = mock_nc

        async def slow_connect(*args: object, **kwargs: object) -> MagicMock:
            await asyncio.sleep(100)  # exceeds deadline
            return MagicMock()  # never reached

        with (
            patch("hermes.config.get_settings") as mock_settings,
            patch("nats.connect", side_effect=slow_connect),
            patch("hermes.publisher.logger") as mock_logger,
        ):
            mock_settings.return_value.nats_retry_interval = 0.05

            loop_task = asyncio.create_task(pub._reconnect_loop("nats://localhost:4222", 0.05))
            await asyncio.sleep(0.2)  # give loop time to attempt and time out
            pub._stop_event.set()
            await asyncio.wait_for(loop_task, timeout=1.0)

        warning_messages = [c.args[0] for c in mock_logger.warning.call_args_list]
        assert any("reconnect failed" in m for m in warning_messages)


class TestDisconnectCancelsLoop:
    @pytest.mark.asyncio
    async def test_disconnect_sets_stop_event_and_cancels_task(self) -> None:
        pub = Publisher()
        mock_nc = _make_mock_nc()
        await _connect_publisher(pub, mock_nc)

        task = pub._reconnect_task
        assert task is not None

        await pub.disconnect()

        assert pub._stop_event.is_set()
        assert task.done()
        assert pub._reconnect_task is None

    @pytest.mark.asyncio
    async def test_disconnect_without_prior_connect_does_not_raise(self) -> None:
        pub = Publisher()
        # _reconnect_task is None — disconnect must not raise
        await pub.disconnect()
