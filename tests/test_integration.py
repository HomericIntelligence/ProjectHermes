"""Integration tests requiring a live NATS server.

Run with: pytest -m integration
Skip when NATS is unavailable: these tests are automatically skipped if NATS
cannot be reached at TEST_NATS_URL (default nats://localhost:4222).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac as hmac_mod
import json
import os

import nats
import pytest
from httpx import ASGITransport, AsyncClient

from hermes.models import WebhookPayload
from hermes.publisher import Publisher

pytestmark = pytest.mark.integration

_NATS_URL = os.environ.get("TEST_NATS_URL", "nats://localhost:4222")
_TEST_SECRET = "integration-test-secret"


def _sign(body: bytes) -> str:
    return hmac_mod.new(_TEST_SECRET.encode(), body, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# Session-scoped skip if NATS is not available
# ---------------------------------------------------------------------------


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Skip all integration tests if NATS is unreachable."""
    # Evaluated lazily at collection time via a custom marker skip mechanism
    pass


@pytest.fixture(scope="module", autouse=True)
def require_nats() -> None:  # type: ignore[return]
    """Skip the entire module when NATS is not reachable."""
    import asyncio as _asyncio

    async def _check() -> bool:
        try:
            nc = await nats.connect(_NATS_URL, connect_timeout=1)
            await nc.drain()
            return True
        except Exception:
            return False

    reachable = _asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        _check()
    )
    if not reachable:
        pytest.skip("NATS server not available at " + _NATS_URL, allow_module_level=True)


# ---------------------------------------------------------------------------
# Publisher integration tests
# ---------------------------------------------------------------------------


class TestPublisherIntegration:
    async def test_connect_creates_streams(self, nats_url: str) -> None:
        """connect() creates the homeric-agents and homeric-tasks JetStream streams."""
        pub = Publisher()
        await pub.connect(nats_url)
        try:
            jsm = pub._nc.jsm()
            agents_stream = await jsm.find_stream("hi.agents.>")
            tasks_stream = await jsm.find_stream("hi.tasks.>")
            assert agents_stream is not None
            assert tasks_stream is not None
        finally:
            await pub.disconnect()

    async def test_publish_agent_event_delivers_message(
        self, publisher: Publisher, nats_client: nats.aio.client.Client
    ) -> None:
        """Publishing an agent event delivers a message on the expected NATS subject."""
        received: list[nats.aio.msg.Msg] = []
        sub = await nats_client.subscribe("hi.agents.>", cb=lambda m: received.append(m))

        payload = WebhookPayload(
            event="agent.created",
            data={"host": "prod-host", "name": "my-agent"},
            timestamp="2026-04-22T00:00:00Z",
        )
        await publisher.publish(payload)
        await asyncio.sleep(0.1)

        await sub.unsubscribe()
        assert len(received) == 1
        assert received[0].subject == "hi.agents.prod-host.my-agent.created"
        body = json.loads(received[0].data)
        assert body["event"] == "agent.created"

    async def test_publish_task_event_delivers_message(
        self, publisher: Publisher, nats_client: nats.aio.client.Client
    ) -> None:
        """Publishing a task event delivers a message on the expected NATS subject."""
        received: list[nats.aio.msg.Msg] = []
        sub = await nats_client.subscribe("hi.tasks.>", cb=lambda m: received.append(m))

        payload = WebhookPayload(
            event="task.updated",
            data={"teamId": "team-42", "task_id": "t-007"},
            timestamp="2026-04-22T00:00:00Z",
        )
        await publisher.publish(payload)
        await asyncio.sleep(0.1)

        await sub.unsubscribe()
        assert len(received) == 1
        assert received[0].subject == "hi.tasks.team-42.t-007.updated"

    async def test_publish_tracks_active_subjects(
        self, publisher: Publisher
    ) -> None:
        """active_subjects is updated after each successful publish."""
        payload = WebhookPayload(
            event="agent.deleted",
            data={"host": "h1", "name": "bot"},
            timestamp="2026-04-22T00:00:00Z",
        )
        assert "hi.agents.h1.bot.deleted" not in publisher.active_subjects
        await publisher.publish(payload)
        assert "hi.agents.h1.bot.deleted" in publisher.active_subjects

    async def test_disconnect_marks_not_connected(self, nats_url: str) -> None:
        """is_connected is False after disconnect()."""
        pub = Publisher()
        await pub.connect(nats_url)
        assert pub.is_connected
        await pub.disconnect()
        assert not pub.is_connected

    async def test_publish_without_connect_raises(self) -> None:
        """publish() raises RuntimeError if the publisher has never connected."""
        pub = Publisher()
        payload = WebhookPayload(
            event="agent.created",
            data={"host": "h", "name": "n"},
            timestamp="2026-04-22T00:00:00Z",
        )
        with pytest.raises(RuntimeError, match="not connected"):
            await pub.publish(payload)


# ---------------------------------------------------------------------------
# Full webhook → NATS end-to-end tests
# ---------------------------------------------------------------------------


class TestWebhookIntegration:
    async def test_webhook_to_nats_end_to_end(
        self, nats_url: str, nats_client: nats.aio.client.Client
    ) -> None:
        """POST /webhook with a valid payload results in a NATS message being delivered."""
        from hermes.config import settings
        from hermes.server import app

        settings.webhook_secret = _TEST_SECRET
        settings.nats_url = nats_url

        received: list[nats.aio.msg.Msg] = []
        sub = await nats_client.subscribe("hi.agents.>", cb=lambda m: received.append(m))

        # Ensure the app has a live publisher
        pub = Publisher()
        await pub.connect(nats_url)
        app.state.publisher = pub

        try:
            payload = {
                "event": "agent.created",
                "data": {"host": "e2e-host", "name": "e2e-agent"},
                "timestamp": "2026-04-22T00:00:00Z",
            }
            body_bytes = json.dumps(payload).encode()

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/webhook",
                    content=body_bytes,
                    headers={
                        "Content-Type": "application/json",
                        "X-Webhook-Signature": _sign(body_bytes),
                    },
                )
            assert response.status_code == 202

            await asyncio.sleep(0.1)
            assert len(received) >= 1
            assert received[0].subject == "hi.agents.e2e-host.e2e-agent.created"
        finally:
            await sub.unsubscribe()
            await pub.disconnect()

    async def test_webhook_nats_disconnected_returns_503(self) -> None:
        """POST /webhook returns 503 when the publisher is not connected."""
        from hermes.config import settings
        from hermes.server import app

        settings.webhook_secret = _TEST_SECRET

        disconnected = Publisher()  # never connected
        app.state.publisher = disconnected

        payload = {
            "event": "agent.created",
            "data": {"host": "h", "name": "n"},
            "timestamp": "2026-04-22T00:00:00Z",
        }
        body_bytes = json.dumps(payload).encode()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/webhook",
                content=body_bytes,
                headers={
                    "Content-Type": "application/json",
                    "X-Webhook-Signature": _sign(body_bytes),
                },
            )
        assert response.status_code == 503


# ---------------------------------------------------------------------------
# Lifespan tests
# ---------------------------------------------------------------------------


class TestLifespan:
    async def test_lifespan_connects_publisher(self, nats_url: str) -> None:
        """The lifespan context manager connects the publisher on startup."""
        from hermes.config import settings
        from hermes.server import lifespan
        from fastapi import FastAPI

        settings.nats_url = nats_url
        test_app = FastAPI()

        async with lifespan(test_app):
            assert test_app.state.publisher.is_connected

        assert not test_app.state.publisher.is_connected

    async def test_lifespan_handles_nats_unavailable(self) -> None:
        """Lifespan does not raise even when NATS is unreachable — it logs a warning."""
        from hermes.config import settings
        from hermes.server import lifespan
        from fastapi import FastAPI

        settings.nats_url = "nats://127.0.0.1:19999"  # nothing listening here
        test_app = FastAPI()

        # Should not raise
        async with lifespan(test_app):
            assert not test_app.state.publisher.is_connected


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    async def test_unknown_event_type_not_published(
        self, publisher: Publisher, nats_client: nats.aio.client.Client
    ) -> None:
        """Unknown event types are silently dropped — no NATS message, no error."""
        received: list[nats.aio.msg.Msg] = []
        sub = await nats_client.subscribe(
            "hi.>", cb=lambda m: received.append(m)
        )

        payload = WebhookPayload(
            event="unknown.event",
            data={"foo": "bar"},
            timestamp="2026-04-22T00:00:00Z",
        )
        await publisher.publish(payload)
        await asyncio.sleep(0.1)

        await sub.unsubscribe()
        assert received == []

    async def test_concurrent_webhooks(
        self, publisher: Publisher, nats_client: nats.aio.client.Client
    ) -> None:
        """Ten concurrent publish calls all deliver distinct messages to NATS."""
        received: list[nats.aio.msg.Msg] = []
        sub = await nats_client.subscribe("hi.agents.>", cb=lambda m: received.append(m))

        payloads = [
            WebhookPayload(
                event="agent.created",
                data={"host": "concurrent-host", "name": f"agent-{i}"},
                timestamp="2026-04-22T00:00:00Z",
            )
            for i in range(10)
        ]
        await asyncio.gather(*(publisher.publish(p) for p in payloads))
        await asyncio.sleep(0.2)

        await sub.unsubscribe()
        assert len(received) == 10

    async def test_large_payload(
        self, publisher: Publisher, nats_client: nats.aio.client.Client
    ) -> None:
        """A payload with a large data dict (~100 KB) is published and received intact."""
        received: list[nats.aio.msg.Msg] = []
        sub = await nats_client.subscribe(
            "hi.agents.large.large-agent.created",
            cb=lambda m: received.append(m),
        )

        large_data: dict[str, object] = {
            "host": "large",
            "name": "large-agent",
            "blob": "x" * 100_000,
        }
        payload = WebhookPayload(
            event="agent.created",
            data=large_data,
            timestamp="2026-04-22T00:00:00Z",
        )
        await publisher.publish(payload)
        await asyncio.sleep(0.2)

        await sub.unsubscribe()
        assert len(received) == 1
        body = json.loads(received[0].data)
        assert body["data"]["blob"] == "x" * 100_000
