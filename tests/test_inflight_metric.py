# SPDX-License-Identifier: MIT
"""Tests for the hermes_inflight_requests Prometheus gauge."""

from __future__ import annotations

import pytest

from hermes.metrics import INFLIGHT_REQUESTS
from hermes.server import _inflight_context


class TestInflightRequestsGauge:
    @pytest.mark.asyncio
    async def test_gauge_increments_inside_context(self) -> None:
        INFLIGHT_REQUESTS.set(0)
        async with _inflight_context():
            assert INFLIGHT_REQUESTS._value.get() == 1.0
        assert INFLIGHT_REQUESTS._value.get() == 0.0

    @pytest.mark.asyncio
    async def test_gauge_decrements_on_exception(self) -> None:
        INFLIGHT_REQUESTS.set(0)
        with pytest.raises(RuntimeError):
            async with _inflight_context():
                assert INFLIGHT_REQUESTS._value.get() == 1.0
                raise RuntimeError("boom")
        assert INFLIGHT_REQUESTS._value.get() == 0.0

    @pytest.mark.asyncio
    async def test_gauge_tracks_concurrent_requests(self) -> None:
        INFLIGHT_REQUESTS.set(0)
        async with _inflight_context():
            async with _inflight_context():
                assert INFLIGHT_REQUESTS._value.get() == 2.0
            assert INFLIGHT_REQUESTS._value.get() == 1.0
        assert INFLIGHT_REQUESTS._value.get() == 0.0
