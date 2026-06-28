# SPDX-License-Identifier: MIT
"""Issue #444: end-to-end wiring + concurrent desynchronisation under jitter."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hermes.publisher import Publisher


def _mock_closed_nc() -> MagicMock:
    nc = MagicMock()
    nc.is_closed = True
    nc.jetstream.return_value = MagicMock()
    nc.jsm.return_value = AsyncMock()
    nc.drain = AsyncMock()
    return nc


@pytest.mark.asyncio
async def test_connect_passes_configured_jitter_and_cap_to_reconnect_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for issue #444: connect() must forward both new settings into _reconnect_loop.

    Guards against a future refactor (or reviewer's claimed duplicate-kwarg bug)
    that would silently drop ``jitter`` or ``max_interval``.
    """
    from hermes import config as cfg_mod

    cfg_mod.get_settings.cache_clear()
    monkeypatch.setenv("NATS_RECONNECT_MAX_INTERVAL", "37.5")
    monkeypatch.setenv("NATS_RECONNECT_JITTER", "0.42")
    monkeypatch.setenv("NATS_RECONNECT_INTERVAL", "2.0")

    captured: dict[str, Any] = {}

    async def fake_reconnect_loop(
        self: Publisher,
        url: str,
        connect_timeout: float,
        reconnect_interval: float,
        hard_timeout: float,
        max_interval: float | None = None,
        jitter: float = 0.0,
    ) -> None:
        captured["reconnect_interval"] = reconnect_interval
        captured["max_interval"] = max_interval
        captured["jitter"] = jitter
        # Exit immediately so connect() returns cleanly.
        return None

    pub = Publisher()

    async def fake_internal(self: Publisher, url: str, timeout: float) -> None:
        self._nc = _mock_closed_nc()
        self._connected = True

    with (
        patch.object(Publisher, "_connect_internal", fake_internal),
        patch.object(Publisher, "_reconnect_loop", fake_reconnect_loop),
    ):
        await pub.connect("nats://x", connect_timeout=0.01)
        if pub._reconnect_task is not None:
            await pub._reconnect_task

    cfg_mod.get_settings.cache_clear()

    assert captured["reconnect_interval"] == pytest.approx(2.0)
    assert captured["max_interval"] == pytest.approx(37.5)
    assert captured["jitter"] == pytest.approx(0.42)


@pytest.mark.asyncio
async def test_two_publishers_desynchronise_under_jitter() -> None:
    """Two concurrent reconnect loops with jitter > 0 must not march in lockstep.

    Each coroutine closes over its OWN publisher and OWN delay bucket — no
    nonlocal sharing across the gather() tasks (review finding #5).
    """
    pub_a, pub_b = Publisher(), Publisher()
    pub_a._nc, pub_b._nc = _mock_closed_nc(), _mock_closed_nc()
    delays_a: list[float] = []
    delays_b: list[float] = []
    max_iters = 8

    def make_driver(pub: Publisher, bucket: list[float]):
        async def fast_wait_for(awaitable, timeout):  # type: ignore[no-untyped-def]
            qname = getattr(awaitable, "__qualname__", "") or repr(awaitable)
            if "Event.wait" in qname:
                bucket.append(timeout)
                if hasattr(awaitable, "close"):
                    awaitable.close()
                if len(bucket) >= max_iters:
                    pub._stop_event.set()
                    return None
                raise asyncio.TimeoutError
            # _connect_internal path: fail it so the loop iterates again.
            if hasattr(awaitable, "close"):
                awaitable.close()
            raise OSError("simulated reconnect failure")

        async def drive() -> None:
            with patch("asyncio.wait_for", side_effect=fast_wait_for):
                await pub._reconnect_loop(
                    "nats://x",
                    connect_timeout=0.01,
                    reconnect_interval=1.0,
                    hard_timeout=0.01,
                    max_interval=8.0,
                    jitter=0.5,  # issue-spec spread [0.5, 1.5]
                )

        return drive

    await asyncio.gather(make_driver(pub_a, delays_a)(), make_driver(pub_b, delays_b)())

    assert len(delays_a) == max_iters, f"a captured {delays_a}"
    assert len(delays_b) == max_iters, f"b captured {delays_b}"
    # Anti-thundering-herd property: at least one paired sample differs.
    diffs = sum(1 for a, b in zip(delays_a, delays_b) if a != b)
    assert diffs >= max_iters - 1, (
        f"jitter=0.5 failed to desynchronise concurrent loops: a={delays_a} b={delays_b}"
    )
