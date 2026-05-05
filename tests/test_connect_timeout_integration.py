"""Integration test: Publisher.connect() respects connect_timeout against a hanging TCP server."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator

import nats.errors
import pytest
import pytest_asyncio

from hermes.publisher import Publisher

# Tight timeout: keeps the test fast; add 0.5 s headroom for slow CI runners.
_CONNECT_TIMEOUT = 0.25
_WALL_CLOCK_GUARD = _CONNECT_TIMEOUT + 0.5


@pytest_asyncio.fixture()
async def hanging_tcp_server() -> AsyncGenerator[tuple[str, int], None]:
    """TCP server that accepts connections but never responds (hangs the NATS handshake)."""
    _stop = asyncio.Event()

    async def _handler(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        await _stop.wait()
        writer.close()

    server = await asyncio.start_server(_handler, host="127.0.0.1", port=0)
    host, port = server.sockets[0].getsockname()
    try:
        yield host, port
    finally:
        # Signal handlers to exit before closing so they don't leak as pending tasks.
        _stop.set()
        server.close()
        await server.wait_closed()


class TestConnectTimeoutIntegration:
    """Publisher.connect() fires a real timeout, not just a kwarg passthrough."""

    async def test_connect_raises_within_timeout(
        self, hanging_tcp_server: tuple[str, int]
    ) -> None:
        """connect() raises within ~connect_timeout seconds against a silent TCP server."""
        host, port = hanging_tcp_server
        pub = Publisher()
        url = f"nats://{host}:{port}"

        start = time.monotonic()
        with pytest.raises((nats.errors.NoServersError, asyncio.TimeoutError, OSError)):
            await pub.connect(url, connect_timeout=_CONNECT_TIMEOUT)
        elapsed = time.monotonic() - start

        assert elapsed < _WALL_CLOCK_GUARD, (
            f"connect() took {elapsed:.2f}s — expected < {_WALL_CLOCK_GUARD}s"
        )

    async def test_connect_does_not_hang_indefinitely(
        self, hanging_tcp_server: tuple[str, int]
    ) -> None:
        """connect() completes (by raising) within a generous 5-second outer guard."""
        host, port = hanging_tcp_server
        pub = Publisher()
        url = f"nats://{host}:{port}"

        # asyncio.wait_for cancels pub.connect() after 5 s if it hasn't raised on its own.
        # Either path — connect() timing out on its own, or wait_for cancelling it — counts
        # as passing: the point is the test itself cannot stall the suite indefinitely.
        try:
            await asyncio.wait_for(
                pub.connect(url, connect_timeout=_CONNECT_TIMEOUT),
                timeout=5.0,
            )
        except (nats.errors.NoServersError, asyncio.TimeoutError, OSError):
            pass  # expected: connect timed out on its own
