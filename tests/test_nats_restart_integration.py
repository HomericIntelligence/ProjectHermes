"""Integration test: Hermes /health recovers after a real NATS server restart (issue #527).

Unlike tests/test_publisher_reconnect.py (which mocks nats.connect), this starts a
REAL nats-server subprocess, hard-kills it, restarts it on the same port + store_dir,
and asserts GET /health returns 200 via the Publisher's external reconnect loop.

The fast reconnect loop is obtained by overriding NATS_RECONNECT_INTERVAL (read by
connect() at publisher.py:130) — NOT by monkeypatching the publisher's private task
state. The autouse reset_settings fixture (conftest.py:46) clears the get_settings
cache so the override takes effect.

Skipped when the nats-server binary is absent or does not accept the JetStream flags.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
import socket
import subprocess
import time
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from hermes.publisher import Publisher

pytestmark = pytest.mark.integration

_NATS_BIN = shutil.which("nats-server")

# Fast reconnect so recovery is quick and the wall-clock guard stays tight.
_RECONNECT_INTERVAL = 0.2
# SINGLE source of truth for the recovery deadline: reconnect interval + rebind/poll
# slack. ~12s with the interval above — generous for CI store-lock/port-rebind. See #184.
_RECOVERY_BUDGET = _RECONNECT_INTERVAL * 10 + 10.0
# How long /health may take to flip to 503 after a hard kill (loop must observe is_closed).
_DEGRADE_BUDGET = _RECONNECT_INTERVAL * 10 + 5.0


def _free_port() -> int:
    # NOTE: classic TOCTOU window — the port can be taken between close() and the
    # subprocess re-binding it. _NatsServer.start() compensates with a bounded retry.
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _port_listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.25)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _wait(predicate: object, *, timeout: float, msg: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():  # type: ignore[operator]
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out after {timeout}s waiting for: {msg}")


def _flags_supported() -> bool:
    """Return True only if nats-server accepts the JetStream/store/port flags we use."""
    if _NATS_BIN is None:
        return False
    try:
        out = subprocess.run([_NATS_BIN, "--help"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    help_text = (out.stdout or "") + (out.stderr or "")
    return all(tok in help_text for tok in ("-js", "-sd", "-p", "-a"))


class _NatsServer:
    """A killable/restartable nats-server bound to a fixed port + store_dir."""

    def __init__(self, port: int, store_dir: str) -> None:
        self.port = port
        self.store_dir = store_dir
        self.url = f"nats://127.0.0.1:{port}"
        self.proc: subprocess.Popen[bytes] | None = None

    def _spawn(self) -> subprocess.Popen[bytes]:
        assert _NATS_BIN is not None
        return subprocess.Popen(
            [_NATS_BIN, "-js", "-sd", self.store_dir, "-a", "127.0.0.1", "-p", str(self.port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def start(self, *, attempts: int = 5) -> None:
        """Spawn on the fixed port + store_dir, retrying past port-rebind / store-lock races."""
        # Ensure the OS has released the TCP port from any prior run first.
        _wait(lambda: not _port_listening(self.port), timeout=5.0, msg="old port freed")
        last_err: Exception | None = None
        for _ in range(attempts):
            proc = self._spawn()
            try:
                _wait(lambda: _port_listening(self.port), timeout=4.0, msg="nats listening")
                self.proc = proc
                return
            except AssertionError as exc:
                # Likely a store-lock not yet released or a port-rebind miss — kill this
                # attempt, wait for the port to clear, and retry rather than fail.
                last_err = exc
                with contextlib.suppress(Exception):
                    proc.kill()
                    proc.wait(timeout=5)
                with contextlib.suppress(AssertionError):
                    _wait(lambda: not _port_listening(self.port), timeout=3.0, msg="port clear")
        raise AssertionError(f"nats-server failed to start after {attempts} attempts: {last_err}")

    def kill(self) -> None:
        if self.proc is not None:
            self.proc.kill()
            self.proc.wait(timeout=10)
            self.proc = None


@pytest_asyncio.fixture()
async def nats_server(tmp_path: pytest.TempPathFactory) -> AsyncGenerator[_NatsServer, None]:
    if _NATS_BIN is None:
        pytest.skip("nats-server binary not on PATH")
    if not _flags_supported():
        pytest.skip("nats-server does not accept the JetStream/store/port flags this test uses")
    store_dir = str(tmp_path / "jsstore")
    server = _NatsServer(_free_port(), store_dir)
    server.start()
    try:
        yield server
    finally:
        with contextlib.suppress(Exception):
            server.kill()


@pytest_asyncio.fixture()
async def fast_reconnect(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make connect() launch a fast reconnect loop via the public env override.

    reset_settings (conftest.py:46) clears the get_settings cache per test, so this
    env var is honored by connect()'s own get_settings() call (publisher.py:107).
    """
    monkeypatch.setenv("NATS_RECONNECT_INTERVAL", str(_RECONNECT_INTERVAL))
    monkeypatch.setenv("NATS_RECONNECT_MAX_INTERVAL", str(_RECONNECT_INTERVAL))
    monkeypatch.setenv("NATS_RECONNECT_HARD_TIMEOUT", "3.0")
    monkeypatch.setenv("NATS_CONNECT_TIMEOUT", "3.0")


async def _get_health_status(pub: Publisher) -> int:
    from hermes.server import app

    app.state.publisher = pub
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return (await client.get("/health")).status_code


@pytest.mark.integration
class TestHealthRecoversAfterRealNatsRestart:
    async def test_health_recovers_after_real_nats_restart(
        self, nats_server: _NatsServer, fast_reconnect: None
    ) -> None:
        """Kill a real NATS server, restart it, and /health returns 200 within budget."""
        logging.getLogger("nats").setLevel(logging.CRITICAL)
        pub = Publisher()
        await pub.connect(nats_server.url)
        try:
            assert await _get_health_status(pub) == 200

            # Hard-kill the server: the publisher's loop observes nc.is_closed.
            nats_server.kill()
            deadline = time.monotonic() + _DEGRADE_BUDGET
            degraded = False
            while time.monotonic() < deadline:
                if await _get_health_status(pub) == 503:
                    degraded = True
                    break
                await asyncio.sleep(0.1)
            assert degraded, "/health never degraded to 503 after NATS was killed"

            # Restart the SAME server (same port + store_dir) and poll for recovery.
            nats_server.start()
            deadline = time.monotonic() + _RECOVERY_BUDGET
            recovered = False
            while time.monotonic() < deadline:
                if await _get_health_status(pub) == 200:
                    recovered = True
                    break
                await asyncio.sleep(0.1)
            assert recovered, (
                f"/health did not return 200 within {_RECOVERY_BUDGET}s of NATS restart"
            )
            assert pub.reconnect_count >= 1
        finally:
            await pub.disconnect()

    async def test_health_stays_degraded_without_restart(
        self, nats_server: _NatsServer, fast_reconnect: None
    ) -> None:
        """RED-proof: if the server is NOT restarted, /health never returns 200."""
        logging.getLogger("nats").setLevel(logging.CRITICAL)
        pub = Publisher()
        await pub.connect(nats_server.url)
        try:
            assert await _get_health_status(pub) == 200
            nats_server.kill()  # killed and intentionally NOT restarted
            deadline = time.monotonic() + _RECOVERY_BUDGET
            while time.monotonic() < deadline:
                assert await _get_health_status(pub) != 200, (
                    "/health returned 200 with no server running — recovery is not real"
                )
                await asyncio.sleep(0.2)
        finally:
            await pub.disconnect()
