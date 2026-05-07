# SPDX-License-Identifier: MIT
"""Tests for the external NATS reconnect loop in Publisher."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hermes.publisher import Publisher


def _make_mock_nc(*, closed: bool = False) -> MagicMock:
    """Return a minimal mock NATSClient."""
    nc = MagicMock()
    nc.is_closed = closed
    nc.jetstream.return_value = MagicMock()
    jsm = AsyncMock()
    nc.jsm.return_value = jsm
    nc.drain = AsyncMock()
    return nc


async def _do_connect(pub: Publisher, mock_nc: MagicMock) -> None:
    """Connect pub using a patched nats.connect that returns mock_nc."""
    with patch("nats.connect", return_value=mock_nc):
        await pub.connect("nats://localhost:4222")


class TestReconnectLoopStartsOnConnect:
    @pytest.mark.asyncio
    async def test_reconnect_task_created_after_connect(self) -> None:
        pub = Publisher()
        mock_nc = _make_mock_nc()
        await _do_connect(pub, mock_nc)
        assert pub._reconnect_task is not None
        assert not pub._reconnect_task.done()
        # cleanup
        await pub.disconnect()

    @pytest.mark.asyncio
    async def test_stop_event_clear_after_connect(self) -> None:
        pub = Publisher()
        mock_nc = _make_mock_nc()
        await _do_connect(pub, mock_nc)
        assert not pub._stop_event.is_set()
        await pub.disconnect()


class TestReconnectLoopStopsOnDisconnect:
    @pytest.mark.asyncio
    async def test_reconnect_task_done_after_disconnect(self) -> None:
        pub = Publisher()
        mock_nc = _make_mock_nc()
        await _do_connect(pub, mock_nc)
        task = pub._reconnect_task
        await pub.disconnect()
        assert task is not None
        assert task.done()

    @pytest.mark.asyncio
    async def test_stop_event_set_after_disconnect(self) -> None:
        pub = Publisher()
        mock_nc = _make_mock_nc()
        await _do_connect(pub, mock_nc)
        await pub.disconnect()
        assert pub._stop_event.is_set()

    @pytest.mark.asyncio
    async def test_reconnect_task_none_after_disconnect(self) -> None:
        pub = Publisher()
        mock_nc = _make_mock_nc()
        await _do_connect(pub, mock_nc)
        await pub.disconnect()
        assert pub._reconnect_task is None


class TestReconnectLoopDoesNotReconnectWhenAlive:
    @pytest.mark.asyncio
    async def test_no_reconnect_while_connection_alive(self) -> None:
        pub = Publisher()
        mock_nc = _make_mock_nc(closed=False)

        with patch("nats.connect", return_value=mock_nc) as mock_conn:
            await pub.connect("nats://localhost:4222")
            connect_call_count = mock_conn.call_count  # 1 from initial connect

            # Run one loop iteration: sleep (very short), then check
            with patch("hermes.config.get_settings") as mock_settings:
                settings = MagicMock()
                settings.nats_reconnect_interval = 0.05
                settings.nats_reconnect_hard_timeout = 5.0
                mock_settings.return_value = settings

                await asyncio.sleep(0.12)  # allow 2 iterations

            # nats.connect must NOT have been called again (connection is alive)
            assert mock_conn.call_count == connect_call_count

        await pub.disconnect()


class TestReconnectLoopRetriesOnLostConnection:
    @pytest.mark.asyncio
    async def test_reconnect_called_when_nc_is_closed(self) -> None:
        pub = Publisher()
        connect_calls: list[str] = []

        async def fake_connect(url: str, **kwargs: object) -> MagicMock:
            connect_calls.append(url)
            return _make_mock_nc(closed=False)

        with patch("nats.connect", side_effect=fake_connect):
            await pub.connect("nats://localhost:4222")
            first_call_count = len(connect_calls)  # 1

            # Simulate connection loss while patch is still active
            pub._nc = _make_mock_nc(closed=True)

            pub._stop_event = asyncio.Event()
            loop_task = asyncio.ensure_future(
                pub._reconnect_loop("nats://localhost:4222", 5.0, 0.05, 5.0)
            )
            await asyncio.sleep(0.12)
            pub._stop_event.set()
            await loop_task

        assert len(connect_calls) > first_call_count  # reconnect was attempted

    @pytest.mark.asyncio
    async def test_reconnect_count_increments_on_success(self) -> None:
        pub = Publisher()

        async def fake_connect(url: str, **kwargs: object) -> MagicMock:
            return _make_mock_nc(closed=False)

        with patch("nats.connect", side_effect=fake_connect):
            await pub.connect("nats://localhost:4222")

        initial_count = pub.reconnect_count

        # Simulate connection loss
        pub._nc = _make_mock_nc(closed=True)

        loop_task = asyncio.ensure_future(
            pub._reconnect_loop("nats://localhost:4222", 5.0, 0.05, 5.0)
        )
        await asyncio.sleep(0.12)
        pub._stop_event.set()
        await loop_task

        assert pub.reconnect_count > initial_count

    @pytest.mark.asyncio
    async def test_last_error_set_on_failed_reconnect(self) -> None:
        pub = Publisher()
        call_count = 0

        async def fake_connect(url: str, **kwargs: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_mock_nc(closed=False)
            raise OSError("connection refused")

        with patch("nats.connect", side_effect=fake_connect):
            await pub.connect("nats://localhost:4222")
            assert pub.last_error == ""

            # Simulate connection loss while patch is still active
            pub._nc = _make_mock_nc(closed=True)

            pub._stop_event = asyncio.Event()
            loop_task = asyncio.ensure_future(
                pub._reconnect_loop("nats://localhost:4222", 5.0, 0.05, 5.0)
            )
            await asyncio.sleep(0.12)
            pub._stop_event.set()
            await loop_task

        assert pub.last_error != ""

    @pytest.mark.asyncio
    async def test_nats_reconnects_metric_incremented_on_success(self) -> None:
        from hermes.metrics import NATS_RECONNECTS

        pub = Publisher()

        async def fake_connect(url: str, **kwargs: object) -> MagicMock:
            return _make_mock_nc(closed=False)

        with patch("nats.connect", side_effect=fake_connect):
            await pub.connect("nats://localhost:4222")

        before = NATS_RECONNECTS.labels(result="success")._value.get()

        pub._nc = _make_mock_nc(closed=True)

        loop_task = asyncio.ensure_future(
            pub._reconnect_loop("nats://localhost:4222", 5.0, 0.05, 5.0)
        )
        await asyncio.sleep(0.12)
        pub._stop_event.set()
        await loop_task

        after = NATS_RECONNECTS.labels(result="success")._value.get()
        assert after > before

    @pytest.mark.asyncio
    async def test_nats_reconnects_metric_incremented_on_failure(self) -> None:
        from hermes.metrics import NATS_RECONNECTS

        pub = Publisher()
        call_count = 0

        async def fake_connect(url: str, **kwargs: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_mock_nc(closed=False)
            raise OSError("refused")

        with patch("nats.connect", side_effect=fake_connect):
            await pub.connect("nats://localhost:4222")
            before = NATS_RECONNECTS.labels(result="failed")._value.get()

            # Simulate connection loss while patch is still active
            pub._nc = _make_mock_nc(closed=True)

            pub._stop_event = asyncio.Event()
            loop_task = asyncio.ensure_future(
                pub._reconnect_loop("nats://localhost:4222", 5.0, 0.05, 5.0)
            )
            await asyncio.sleep(0.12)
            pub._stop_event.set()
            await loop_task

        after = NATS_RECONNECTS.labels(result="failed")._value.get()
        assert after > before


class TestReconnectLoopInterruptibleSleep:
    @pytest.mark.asyncio
    async def test_shutdown_during_sleep_returns_promptly(self) -> None:
        """Stopping the loop while it is sleeping should return well before the interval expires."""
        pub = Publisher()
        mock_nc = _make_mock_nc(closed=False)
        await _do_connect(pub, mock_nc)

        # Replace the reconnect task with a fresh loop that has a 60s interval
        pub._stop_event = asyncio.Event()
        loop_task = asyncio.ensure_future(
            pub._reconnect_loop("nats://localhost:4222", 5.0, 60.0, 5.0)
        )

        await asyncio.sleep(0.05)  # let loop enter wait_for sleep
        t0 = asyncio.get_event_loop().time()
        pub._stop_event.set()
        await asyncio.wait_for(loop_task, timeout=2.0)  # must not take 60s
        elapsed = asyncio.get_event_loop().time() - t0
        assert elapsed < 2.0

        await pub.disconnect()


class TestConnectInternalExtracted:
    @pytest.mark.asyncio
    async def test_connect_internal_sets_connected_and_js(self) -> None:
        pub = Publisher()
        mock_nc = _make_mock_nc()

        with patch("nats.connect", return_value=mock_nc):
            await pub._connect_internal("nats://localhost:4222", 5.0)

        assert pub._connected is True
        assert pub._js is not None

    @pytest.mark.asyncio
    async def test_connect_internal_registers_callbacks(self) -> None:
        pub = Publisher()
        mock_nc = _make_mock_nc()
        captured: dict[str, object] = {}

        async def fake_connect(url: str, **kwargs: object) -> MagicMock:
            captured.update(kwargs)
            return mock_nc

        with patch("nats.connect", side_effect=fake_connect):
            await pub._connect_internal("nats://localhost:4222", 5.0)

        assert "disconnected_cb" in captured
        assert "reconnected_cb" in captured

    @pytest.mark.asyncio
    async def test_connect_calls_connect_internal(self) -> None:
        pub = Publisher()
        mock_nc = _make_mock_nc()

        with patch("nats.connect", return_value=mock_nc) as mock_conn:
            await pub.connect("nats://localhost:4222")

        mock_conn.assert_called_once()
        await pub.disconnect()
