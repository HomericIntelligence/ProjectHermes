"""Shared pytest fixtures for ProjectHermes tests."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncGenerator, Callable, Generator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import nats
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

import hermes.server as _server
from hermes.config import get_settings
from hermes.models import WebhookPayload
from hermes.publisher import Publisher
from tests.helpers import FIXED_TS as _FIXED_TS, TEST_SECRET as _TEST_SECRET


@pytest.fixture(autouse=True)
def reset_server_state() -> Generator[None, None, None]:
    from hermes.server import app

    _server._shutdown_event = asyncio.Event()
    _server._inflight = 0
    app.state.inflight_lock = asyncio.Lock()
    yield
    _server._shutdown_event = asyncio.Event()
    _server._inflight = 0
    app.state.inflight_lock = asyncio.Lock()


def _nats_url() -> str:
    return os.environ.get("TEST_NATS_URL", "nats://localhost:4222")


async def _nats_reachable() -> bool:
    try:
        nc = await nats.connect(_nats_url(), connect_timeout=1)
        await nc.drain()
        return True
    except Exception:
        return False


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "integration: requires a running NATS server")


@pytest.fixture(autouse=True)
def reset_settings() -> Generator[None, None, None]:
    """Clear the get_settings LRU cache and dependency overrides before/after each test.

    Settings is ``frozen=True`` (see ``hermes.config.Settings.model_config``), so
    direct field mutation raises ``ValidationError``. Tests must override config via
    ``app.dependency_overrides[get_settings] = ...`` or by setting env vars and
    constructing a fresh ``Settings()``.
    """
    from hermes.server import app

    get_settings.cache_clear()
    app.dependency_overrides.clear()
    yield
    get_settings.cache_clear()
    app.dependency_overrides.clear()


@pytest.fixture(scope="session")
def nats_url() -> str:
    return _nats_url()


@pytest_asyncio.fixture()
async def nats_client(nats_url: str) -> AsyncGenerator[nats.aio.client.Client, None]:
    """Raw NATS client for subscribing in integration tests."""
    nc = await nats.connect(nats_url)
    yield nc
    if not nc.is_closed:
        await nc.drain()


@pytest_asyncio.fixture()
async def publisher(nats_url: str) -> AsyncGenerator[Publisher, None]:
    """Connected Publisher, disconnected after the test."""
    pub = Publisher()
    await pub.connect(nats_url)
    yield pub
    if pub.is_connected:
        await pub.disconnect()


@pytest.fixture()
def agent_payload() -> WebhookPayload:
    return WebhookPayload(
        event="agent.created",
        data={"host": "test-host", "name": "test-agent"},
        timestamp=_FIXED_TS,
    )


@pytest.fixture()
def task_payload() -> WebhookPayload:
    return WebhookPayload(
        event="task.updated",
        data={"teamId": "team-1", "task_id": "task-abc"},
        timestamp=_FIXED_TS,
    )


@pytest.fixture()
def make_test_client() -> Callable[..., TestClient]:
    """Factory returning a TestClient wired to a mock Publisher and overridden Settings.

    Replaces duplicate _build_client helpers in test_webhook.py, test_request_id_context.py,
    and test_shutdown.py. Pass ``webhook_secret=None`` to skip the Settings override entirely
    (for tests that mutate ``get_settings().webhook_secret`` directly).
    """
    from hermes.config import Settings
    from hermes.rate_limit import limiter
    from hermes.server import app

    def _factory(
        *,
        publisher: MagicMock | None = None,
        connected: bool = True,
        webhook_secret: str | None = _TEST_SECRET,
        publish_side_effect: Any = None,
        raise_server_exceptions: bool = True,
        reset_rate_limiter: bool = True,
        reconnect_count: int = 0,
        last_error: str = "",
        last_reconnect_attempt_at: object = None,
        consecutive_reconnect_failures: int = 0,
        reconnect_loop_active: bool = False,
    ) -> TestClient:
        if publisher is None:
            publisher = MagicMock(spec=Publisher)
            publisher.is_connected = connected
            publisher.active_subjects = []
            publisher.active_subjects_max = 1000
            publisher.dead_letter_count = 0
            publisher.publish = AsyncMock(side_effect=publish_side_effect)
            publisher.disconnect = AsyncMock()
            publisher.reconnect_count = reconnect_count
            publisher.last_error = last_error
            publisher.last_reconnect_attempt_at = last_reconnect_attempt_at
            publisher.consecutive_reconnect_failures = consecutive_reconnect_failures
            publisher.reconnect_loop_active = reconnect_loop_active

        app.state.publisher = publisher
        if webhook_secret is not None:
            test_settings = Settings(webhook_secret=webhook_secret)
            app.dependency_overrides[get_settings] = lambda: test_settings
        if reset_rate_limiter:
            limiter._storage.reset()  # type: ignore[attr-defined]
        return TestClient(app, raise_server_exceptions=raise_server_exceptions)

    return _factory
