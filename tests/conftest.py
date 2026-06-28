"""Shared pytest fixtures for ProjectHermes tests."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncGenerator, Generator

import nats
import pytest
import pytest_asyncio

import hermes.server as _server
from hermes.config import get_settings
from hermes.models import WebhookPayload
from hermes.publisher import Publisher
from tests.helpers import FIXED_TS as _FIXED_TS


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
