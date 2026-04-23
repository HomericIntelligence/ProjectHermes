"""Shared pytest fixtures for ProjectHermes tests."""

from __future__ import annotations

import os
from typing import AsyncGenerator

import nats
import pytest
import pytest_asyncio

from hermes.models import WebhookPayload
from hermes.publisher import Publisher


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
    config.addinivalue_line(
        "markers", "integration: requires a running NATS server"
    )


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
        timestamp="2026-04-22T00:00:00Z",
    )


@pytest.fixture()
def task_payload() -> WebhookPayload:
    return WebhookPayload(
        event="task.updated",
        data={"teamId": "team-1", "task_id": "task-abc"},
        timestamp="2026-04-22T00:00:00Z",
    )
