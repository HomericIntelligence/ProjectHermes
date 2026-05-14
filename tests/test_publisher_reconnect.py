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
        # Fired as soon as the reconnect loop attempts a second nats.connect call
        reconnect_attempted: asyncio.Event = asyncio.Event()

        async def fake_connect(url: str, **kwargs: object) -> MagicMock:
            connect_calls.append(url)
            if len(connect_calls) > 1:
                # This is a reconnect attempt — signal the test and stop the loop
                reconnect_attempted.set()
                pub._stop_event.set()
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
            # Block until the reconnect fires (self-terminates) or 5 s safety timeout
            await asyncio.wait_for(reconnect_attempted.wait(), timeout=5.0)
            pub._stop_event.set()
            await loop_task

        assert len(connect_calls) > first_call_count  # reconnect was attempted

    @pytest.mark.asyncio
    async def test_reconnect_count_increments_on_success(self) -> None:
        pub = Publisher()
        call_count = 0
        # Fired as soon as the reconnect loop successfully reconnects
        reconnected: asyncio.Event = asyncio.Event()

        async def fake_connect(url: str, **kwargs: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                # This is a reconnect attempt — signal the test and stop the loop
                reconnected.set()
                pub._stop_event.set()
            return _make_mock_nc(closed=False)

        with patch("nats.connect", side_effect=fake_connect):
            await pub.connect("nats://localhost:4222")

            initial_count = pub.reconnect_count

            # Simulate connection loss
            pub._nc = _make_mock_nc(closed=True)

            pub._stop_event = asyncio.Event()
            loop_task = asyncio.ensure_future(
                pub._reconnect_loop("nats://localhost:4222", 5.0, 0.05, 5.0)
            )
            # Block until the reconnect fires (self-terminates) or 5 s safety timeout
            await asyncio.wait_for(reconnected.wait(), timeout=5.0)
            pub._stop_event.set()
            await loop_task

        assert pub.reconnect_count == initial_count + 1

    @pytest.mark.asyncio
    async def test_reconnect_count_no_double_increment_if_callback_fires(self) -> None:
        """Regression for issue #526.

        A single successful reconnect must increment ``reconnect_count`` by
        exactly 1, even if nats-py also invokes the ``reconnected_cb`` callback
        (which it would do if ``allow_reconnect`` were ever changed to True).
        """
        pub = Publisher()
        call_count = 0
        reconnected: asyncio.Event = asyncio.Event()
        captured_callbacks: dict[str, object] = {}

        async def fake_connect(url: str, **kwargs: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            captured_callbacks.update(kwargs)
            if call_count > 1:
                reconnected.set()
                pub._stop_event.set()
            return _make_mock_nc(closed=False)

        with patch("nats.connect", side_effect=fake_connect):
            await pub.connect("nats://localhost:4222")
            initial_count = pub.reconnect_count

            # Simulate connection loss
            pub._nc = _make_mock_nc(closed=True)

            pub._stop_event = asyncio.Event()
            loop_task = asyncio.ensure_future(
                pub._reconnect_loop("nats://localhost:4222", 5.0, 0.05, 5.0)
            )
            await asyncio.wait_for(reconnected.wait(), timeout=5.0)
            # Simulate nats-py firing the reconnected_cb in addition to the
            # external reconnect loop succeeding (the double-fire scenario).
            await captured_callbacks["reconnected_cb"]()  # type: ignore[operator]
            pub._stop_event.set()
            await loop_task

        # Exactly one increment despite both the loop AND the callback firing.
        assert pub.reconnect_count == initial_count + 1

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

            pub._stop_event = asyncio.Event()
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


class TestReconnectLoopHealthState:
    """Issue #528: Publisher exposes reconnect-loop state for /health."""

    @pytest.mark.asyncio
    async def test_initial_health_state_defaults(self) -> None:
        pub = Publisher()
        assert pub.last_reconnect_attempt_at is None
        assert pub.consecutive_reconnect_failures == 0
        assert pub.reconnect_loop_running is False

    @pytest.mark.asyncio
    async def test_reconnect_loop_running_true_after_connect(self) -> None:
        pub = Publisher()
        mock_nc = _make_mock_nc()
        await _do_connect(pub, mock_nc)
        try:
            assert pub.reconnect_loop_running is True
        finally:
            await pub.disconnect()
        assert pub.reconnect_loop_running is False

    @pytest.mark.asyncio
    async def test_failed_reconnect_increments_failure_counter(self) -> None:
        from datetime import datetime

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
            assert pub.consecutive_reconnect_failures == 0

            pub._nc = _make_mock_nc(closed=True)
            pub._stop_event = asyncio.Event()
            loop_task = asyncio.ensure_future(
                pub._reconnect_loop("nats://localhost:4222", 5.0, 0.05, 5.0)
            )
            await asyncio.sleep(0.20)
            pub._stop_event.set()
            await loop_task

        assert pub.consecutive_reconnect_failures >= 1
        assert isinstance(pub.last_reconnect_attempt_at, datetime)

    @pytest.mark.asyncio
    async def test_successful_reconnect_resets_failure_counter(self) -> None:
        pub = Publisher()
        call_count = 0
        reconnected: asyncio.Event = asyncio.Event()

        async def fake_connect(url: str, **kwargs: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_mock_nc(closed=False)
            # Simulate a successful reconnect on retry
            reconnected.set()
            pub._stop_event.set()
            return _make_mock_nc(closed=False)

        with patch("nats.connect", side_effect=fake_connect):
            await pub.connect("nats://localhost:4222")
            pub.consecutive_reconnect_failures = 5  # pretend prior failures occurred
            pub._nc = _make_mock_nc(closed=True)

            pub._stop_event = asyncio.Event()
            loop_task = asyncio.ensure_future(
                pub._reconnect_loop("nats://localhost:4222", 5.0, 0.05, 5.0)
            )
            await asyncio.wait_for(reconnected.wait(), timeout=5.0)
            pub._stop_event.set()
            await loop_task

        assert pub.consecutive_reconnect_failures == 0


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


class TestStopEventLifetime:
    """Regression coverage for #524 — _stop_event identity must survive reconnect()."""

    @pytest.mark.asyncio
    async def test_stop_event_identity_preserved_across_reconnect(self) -> None:
        """Calling connect() twice without disconnect() must reuse the same Event.

        Otherwise a stale _reconnect_task spawned by the first connect() holds a
        reference to the old Event and would never observe stop_event.set().
        """
        pub = Publisher()
        original_event = pub._stop_event

        mock_nc1 = _make_mock_nc()
        await _do_connect(pub, mock_nc1)
        assert pub._stop_event is original_event, (
            "connect() must not replace _stop_event"
        )

        # Second connect() without intervening disconnect() (simulates retry
        # after a startup failure described in #524).
        mock_nc2 = _make_mock_nc()
        await _do_connect(pub, mock_nc2)
        assert pub._stop_event is original_event, (
            "second connect() must not replace _stop_event"
        )

        # And disconnect() must still cleanly stop the (latest) loop via the
        # shared Event.
        await pub.disconnect()
        assert pub._stop_event is original_event
        assert pub._stop_event.is_set()

    @pytest.mark.asyncio
    async def test_double_connect_cancels_stale_reconnect_task(self) -> None:
        """A second connect() must cancel the prior _reconnect_task.

        Two concurrent loops sharing _stop_event would race on reconnect attempts
        and double-increment metrics on every flap.
        """
        pub = Publisher()
        mock_nc = _make_mock_nc()
        await _do_connect(pub, mock_nc)
        first_task = pub._reconnect_task
        assert first_task is not None

        await _do_connect(pub, _make_mock_nc())
        second_task = pub._reconnect_task
        assert second_task is not None
        assert second_task is not first_task
        assert first_task.done(), "stale reconnect task must be cancelled"

        await pub.disconnect()

    @pytest.mark.asyncio
    async def test_stop_event_cleared_on_reconnect(self) -> None:
        """connect() after a prior disconnect() must re-arm (clear) the Event.

        Otherwise the new _reconnect_loop sees stop_event already set and exits
        immediately, leaving the publisher with no reconnect coverage.
        """
        pub = Publisher()
        mock_nc = _make_mock_nc()
        await _do_connect(pub, mock_nc)
        await pub.disconnect()
        assert pub._stop_event.is_set()

        # Re-connect: stop_event must be cleared so the new loop can run.
        await _do_connect(pub, _make_mock_nc())
        assert not pub._stop_event.is_set()
        assert pub._reconnect_task is not None
        assert not pub._reconnect_task.done()

        await pub.disconnect()
