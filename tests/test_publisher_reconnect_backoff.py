# SPDX-License-Identifier: MIT
"""Tests for exponential backoff in Publisher._reconnect_loop (issue #525).

The reconnect loop must space out *failed* reconnect attempts using bounded
exponential backoff with optional jitter, so that a long NATS outage does
not produce a steady stream of fixed-interval reconnect attempts (and a
linearly-growing ``NATS_RECONNECTS{result='failed'}`` counter).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hermes.publisher import Publisher


def _make_mock_nc(*, closed: bool = False) -> MagicMock:
    nc = MagicMock()
    nc.is_closed = closed
    nc.jetstream.return_value = MagicMock()
    nc.jsm.return_value = AsyncMock()
    nc.drain = AsyncMock()
    return nc


class TestReconnectBackoffDelaySequence:
    """The sleep before each failed retry should grow exponentially, bounded by ``max_interval``.

    These tests patch ``asyncio.wait_for`` in the publisher module so they capture
    the requested ``timeout`` (the throttle) and return *immediately* without
    actually waiting — keeping the suite fast and deterministic.  A
    ``TimeoutError`` is raised so the loop falls through to the reconnect attempt
    just as it would after a real timeout expiry.
    """

    @pytest.mark.asyncio
    async def test_delay_sequence_is_increasing_and_bounded(self) -> None:
        pub = Publisher()
        # Always-closed connection so every iteration attempts (and fails) a reconnect.
        pub._nc = _make_mock_nc(closed=True)

        observed_delays: list[float] = []
        max_iterations = 6
        base, cap = 1.0, 8.0  # expected sequence (jitter=0): 1, 2, 4, 8, 8, 8

        async def fast_wait_for(awaitable, timeout):  # type: ignore[no-untyped-def]
            coro_name = getattr(awaitable, "__qualname__", "") or repr(awaitable)
            if "Event.wait" in coro_name:
                observed_delays.append(timeout)
                # Close the awaitable so it doesn't dangle.
                if hasattr(awaitable, "close"):
                    awaitable.close()
                if len(observed_delays) >= max_iterations:
                    pub._stop_event.set()
                    # Returning normally lets the loop exit cleanly via the
                    # ``return  # stop_event fired during sleep`` branch.
                    return None
                raise asyncio.TimeoutError
            # Other wait_for calls (hard_timeout wrapping _connect_internal) —
            # invoke the awaitable directly with no timeout enforcement so the
            # failing_connect side_effect raises promptly.
            return await awaitable

        async def failing_connect(url: str, **_: object) -> MagicMock:
            raise OSError("connection refused")

        pub._stop_event = asyncio.Event()
        with (
            patch("asyncio.wait_for", side_effect=fast_wait_for),
            patch("nats.connect", side_effect=failing_connect),
        ):
            await asyncio.wait_for(
                pub._reconnect_loop(
                    "nats://localhost:4222",
                    connect_timeout=0.01,
                    reconnect_interval=base,
                    hard_timeout=0.01,
                    max_interval=cap,
                    jitter=0.0,
                ),
                timeout=5.0,
            )

        assert len(observed_delays) >= 4, f"too few iterations: {observed_delays}"

        # Every delay must be <= cap.
        for d in observed_delays:
            assert d <= cap + 1e-9, f"delay {d} exceeds cap {cap}"

        # Non-decreasing (monotone backoff) all the way through.
        for prev, curr in zip(observed_delays, observed_delays[1:]):
            assert curr + 1e-9 >= prev, f"delay decreased: {prev} -> {curr}"

        # Strictly grows at least once (proves it isn't flat).
        assert observed_delays[-1] > observed_delays[0], f"backoff never grew: {observed_delays}"

        # And the first delay equals the base (jitter=0).
        assert observed_delays[0] == pytest.approx(base)

    @pytest.mark.asyncio
    async def test_delay_resets_after_successful_reconnect(self) -> None:
        """After a successful reconnect, the backoff exponent must reset to 0."""
        pub = Publisher()
        pub._nc = _make_mock_nc(closed=True)

        observed_delays: list[float] = []
        # Fail twice, then succeed.  The post-success delay must equal the base
        # interval (jitter=0), proving the exponent reset.
        call_count = 0
        target_observations = 4

        async def fake_connect(url: str, **_: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count in (1, 2):
                raise OSError("refused")
            # Success — return a healthy mock; the next loop iteration will see
            # ``is_closed=False`` and reset the backoff.
            return _make_mock_nc(closed=False)

        async def fast_wait_for(awaitable, timeout):  # type: ignore[no-untyped-def]
            coro_name = getattr(awaitable, "__qualname__", "") or repr(awaitable)
            if "Event.wait" in coro_name:
                observed_delays.append(timeout)
                if hasattr(awaitable, "close"):
                    awaitable.close()
                # After the successful reconnect (call_count == 3) the next
                # iteration's wait_for will be observed; re-arm closed state so
                # the iteration *after that* attempts another reconnect — that
                # second post-reset delay confirms the exponent is back at 0.
                if call_count == 3 and pub._nc is not None and not pub._nc.is_closed:
                    pub._nc = _make_mock_nc(closed=True)
                if len(observed_delays) >= target_observations:
                    pub._stop_event.set()
                    return None
                raise asyncio.TimeoutError
            return await awaitable

        pub._stop_event = asyncio.Event()
        with (
            patch("asyncio.wait_for", side_effect=fast_wait_for),
            patch("nats.connect", side_effect=fake_connect),
        ):
            await asyncio.wait_for(
                pub._reconnect_loop(
                    "nats://localhost:4222",
                    connect_timeout=0.01,
                    reconnect_interval=1.0,
                    hard_timeout=0.01,
                    max_interval=16.0,
                    jitter=0.0,
                ),
                timeout=5.0,
            )

        # Expected (base=1, cap=16, jitter=0):
        #   iter 1 (pre fail #1):   1
        #   iter 2 (pre fail #2):   2
        #   iter 3 (pre success):   4
        #   iter 4 (post-success, sees healthy nc -> reset): 1
        assert len(observed_delays) >= 4, f"too few iterations: {observed_delays}"
        # After the success the exponent must reset, so a later delay must
        # equal (or be smaller than) the first delay.
        assert min(observed_delays[3:]) <= observed_delays[0] + 1e-9, (
            f"backoff did not reset after success: {observed_delays}"
        )

    @pytest.mark.asyncio
    async def test_jitter_keeps_delay_within_bounds(self) -> None:
        """With jitter=0.1, delays must stay within [0.9 * base, 1.1 * cap]."""
        pub = Publisher()
        pub._nc = _make_mock_nc(closed=True)

        observed_delays: list[float] = []
        base, cap, jitter = 2.0, 4.0, 0.1

        async def fast_wait_for(awaitable, timeout):  # type: ignore[no-untyped-def]
            coro_name = getattr(awaitable, "__qualname__", "") or repr(awaitable)
            if "Event.wait" in coro_name:
                observed_delays.append(timeout)
                if hasattr(awaitable, "close"):
                    awaitable.close()
                if len(observed_delays) >= 5:
                    pub._stop_event.set()
                    return None
                raise asyncio.TimeoutError
            return await awaitable

        async def failing_connect(url: str, **_: object) -> MagicMock:
            raise OSError("refused")

        pub._stop_event = asyncio.Event()
        with (
            patch("asyncio.wait_for", side_effect=fast_wait_for),
            patch("nats.connect", side_effect=failing_connect),
        ):
            await asyncio.wait_for(
                pub._reconnect_loop(
                    "nats://localhost:4222",
                    connect_timeout=0.01,
                    reconnect_interval=base,
                    hard_timeout=0.01,
                    max_interval=cap,
                    jitter=jitter,
                ),
                timeout=5.0,
            )

        # Every delay must lie in [base*(1-jitter), cap*(1+jitter)].
        lo, hi = base * (1.0 - jitter), cap * (1.0 + jitter)
        for d in observed_delays:
            assert lo - 1e-9 <= d <= hi + 1e-9, f"delay {d} outside [{lo}, {hi}]"
