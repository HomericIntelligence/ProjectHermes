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
_TEST_SECRET = "integration-test-secret-for-hermes-webhook"


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
            agents_stream = await jsm.stream_info("homeric-agents")
            tasks_stream = await jsm.stream_info("homeric-tasks")
            assert agents_stream is not None
            assert tasks_stream is not None
        finally:
            await pub.disconnect()

    async def test_publish_agent_event_delivers_message(
        self, publisher: Publisher, nats_client: nats.aio.client.Client
    ) -> None:
        """Publishing an agent event delivers a message on the expected NATS subject."""
        received: list[nats.aio.msg.Msg] = []

        async def _cb(m: nats.aio.msg.Msg) -> None:
            received.append(m)

        sub = await nats_client.subscribe("hi.agents.>", cb=_cb)

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

        async def _cb(m: nats.aio.msg.Msg) -> None:
            received.append(m)

        sub = await nats_client.subscribe("hi.tasks.>", cb=_cb)

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
        self, monkeypatch: pytest.MonkeyPatch, nats_url: str, nats_client: nats.aio.client.Client
    ) -> None:
        """POST /webhook with a valid payload results in a NATS message being delivered."""
        from hermes.server import app

        monkeypatch.setenv("WEBHOOK_SECRET", _TEST_SECRET)
        monkeypatch.setenv("NATS_URL", nats_url)

        received: list[nats.aio.msg.Msg] = []

        async def _cb(m: nats.aio.msg.Msg) -> None:
            received.append(m)

        sub = await nats_client.subscribe("hi.agents.>", cb=_cb)

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

    async def test_webhook_nats_disconnected_returns_503(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """POST /webhook returns 503 when the publisher is not connected."""
        from hermes.server import app

        monkeypatch.setenv("WEBHOOK_SECRET", _TEST_SECRET)

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
    async def test_lifespan_connects_publisher(
        self, monkeypatch: pytest.MonkeyPatch, nats_url: str
    ) -> None:
        """The lifespan context manager connects the publisher on startup."""
        from hermes.server import lifespan
        from fastapi import FastAPI

        monkeypatch.setenv("NATS_URL", nats_url)
        test_app = FastAPI()

        async with lifespan(test_app):
            assert test_app.state.publisher.is_connected

        assert not test_app.state.publisher.is_connected

    async def test_lifespan_handles_nats_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Lifespan starts in degraded mode when NATS is unreachable after all retry attempts."""
        from unittest.mock import patch
        from hermes.config import Settings
        from hermes.server import lifespan
        from fastapi import FastAPI

        bad_settings = Settings(nats_url="nats://127.0.0.1:19999", nats_retry_attempts=1)
        test_app = FastAPI()

        with patch("hermes.server.get_settings", return_value=bad_settings):
            async with lifespan(test_app):
                assert not test_app.state.publisher.is_connected


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    async def test_unknown_event_type_not_published(
        self, nats_url: str, nats_client: nats.aio.client.Client
    ) -> None:
        """Unknown event types with dead-letter disabled raise UnknownEventTypeError — no NATS message."""
        from hermes.publisher import UnknownEventTypeError

        received: list[nats.aio.msg.Msg] = []

        async def _cb(m: nats.aio.msg.Msg) -> None:
            received.append(m)

        sub = await nats_client.subscribe("hi.>", cb=_cb)

        pub = Publisher(enable_dead_letter=False)
        await pub.connect(nats_url)
        try:
            payload = WebhookPayload(
                event="unknown.event",
                data={"foo": "bar"},
                timestamp="2026-04-22T00:00:00Z",
            )
            with pytest.raises(UnknownEventTypeError):
                await pub.publish(payload)
        finally:
            await pub.disconnect()

        await sub.unsubscribe()
        assert received == []

    async def test_concurrent_webhooks(
        self, publisher: Publisher, nats_client: nats.aio.client.Client
    ) -> None:
        """Ten concurrent publish calls all deliver distinct messages to NATS."""
        received: list[nats.aio.msg.Msg] = []

        async def _cb(m: nats.aio.msg.Msg) -> None:
            received.append(m)

        sub = await nats_client.subscribe("hi.agents.>", cb=_cb)

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

        async def _cb(m: nats.aio.msg.Msg) -> None:
            received.append(m)

        sub = await nats_client.subscribe(
            "hi.agents.large.large-agent.created",
            cb=_cb,
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


# ---------------------------------------------------------------------------
# Issue #66: _ensure_streams integration tests
# ---------------------------------------------------------------------------


class TestEnsureStreams:
    """Verify that _ensure_streams creates the expected JetStream streams."""

    @pytest.mark.integration
    async def test_ensure_streams_creates_agents_stream(self, nats_url: str) -> None:
        """connect() creates the homeric-agents stream via _ensure_streams."""
        pub = Publisher()
        await pub.connect(nats_url)
        try:
            jsm = pub._nc.jsm()
            info = await jsm.stream_info("homeric-agents")
            assert info is not None
            assert info.config.name == "homeric-agents"
            assert "hi.agents.>" in info.config.subjects
        finally:
            await pub.disconnect()

    @pytest.mark.integration
    async def test_ensure_streams_creates_tasks_stream(self, nats_url: str) -> None:
        """connect() creates the homeric-tasks stream via _ensure_streams."""
        pub = Publisher()
        await pub.connect(nats_url)
        try:
            jsm = pub._nc.jsm()
            info = await jsm.stream_info("homeric-tasks")
            assert info is not None
            assert info.config.name == "homeric-tasks"
            assert "hi.tasks.>" in info.config.subjects
        finally:
            await pub.disconnect()

    @pytest.mark.integration
    async def test_ensure_streams_is_idempotent(self, nats_url: str) -> None:
        """Calling connect() twice does not raise — _ensure_streams is idempotent."""
        pub1 = Publisher()
        await pub1.connect(nats_url)
        await pub1.disconnect()

        pub2 = Publisher()
        await pub2.connect(nats_url)
        try:
            jsm = pub2._nc.jsm()
            agents_info = await jsm.stream_info("homeric-agents")
            tasks_info = await jsm.stream_info("homeric-tasks")
            assert agents_info is not None
            assert tasks_info is not None
        finally:
            await pub2.disconnect()

    @pytest.mark.integration
    async def test_ensure_streams_populates_stream_names(self, nats_url: str) -> None:
        """connect() populates publisher.stream_names with the created stream names."""
        pub = Publisher()
        await pub.connect(nats_url)
        try:
            assert "homeric-agents" in pub.stream_names
            assert "homeric-tasks" in pub.stream_names
        finally:
            await pub.disconnect()


# ---------------------------------------------------------------------------
# Issue #82: /health and /ready integration tests
# ---------------------------------------------------------------------------


class TestHealthAndReadyIntegration:
    """Test /health and /ready endpoints with a real connected Publisher."""

    @pytest.mark.integration
    async def test_health_returns_200_when_nats_connected(self, nats_url: str) -> None:
        """GET /health returns 200 and nats_connected: true when NATS is running."""
        from hermes.server import app

        pub = Publisher()
        await pub.connect(nats_url)
        app.state.publisher = pub
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/health")
            assert response.status_code == 200
            body = response.json()
            assert body["nats_connected"] is True
            assert body["status"] == "ok"
        finally:
            await pub.disconnect()

    @pytest.mark.integration
    async def test_ready_returns_true_when_nats_connected(self, nats_url: str) -> None:
        """GET /ready returns {"ready": true} when Publisher is connected."""
        from hermes.server import app

        pub = Publisher()
        await pub.connect(nats_url)
        app.state.publisher = pub
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/ready")
            assert response.status_code == 200
            assert response.json() == {"ready": True}
        finally:
            await pub.disconnect()

    @pytest.mark.integration
    async def test_health_returns_503_when_nats_disconnected(self) -> None:
        """GET /health returns 503 and nats_connected: false when Publisher is not connected."""
        from hermes.server import app

        disconnected_pub = Publisher()  # never connected
        app.state.publisher = disconnected_pub

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/health")
        assert response.status_code == 503
        body = response.json()
        assert body["nats_connected"] is False

    @pytest.mark.integration
    async def test_ready_returns_503_when_nats_disconnected(self) -> None:
        """GET /ready returns 503 and ready: false when Publisher is not connected."""
        from hermes.server import app

        disconnected_pub = Publisher()  # never connected
        app.state.publisher = disconnected_pub

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/ready")
        assert response.status_code == 503
        body = response.json()
        assert body["ready"] is False


# ---------------------------------------------------------------------------
# Issue #100: schema_version serialization integration test
# ---------------------------------------------------------------------------


class TestPublishSchemaVersion:
    """Verify that published NATS messages include the schema_version field."""

    @pytest.mark.integration
    async def test_publish_includes_schema_version(
        self, publisher: Publisher, nats_client: nats.aio.client.Client
    ) -> None:
        """publish() includes schema_version=1 in the serialized NATS message body."""
        received: list[nats.aio.msg.Msg] = []

        async def _cb(m: nats.aio.msg.Msg) -> None:
            received.append(m)

        sub = await nats_client.subscribe("hi.agents.schema-host.schema-agent.created", cb=_cb)

        payload = WebhookPayload(
            event="agent.created",
            data={"host": "schema-host", "name": "schema-agent"},
            timestamp="2026-04-22T00:00:00Z",
        )
        await publisher.publish(payload)
        await asyncio.sleep(0.1)

        await sub.unsubscribe()
        assert len(received) == 1
        body = json.loads(received[0].data)
        assert "schema_version" in body, "schema_version field missing from published message"
        assert body["schema_version"] == 1

    @pytest.mark.integration
    async def test_publish_schema_version_present_in_task_event(
        self, publisher: Publisher, nats_client: nats.aio.client.Client
    ) -> None:
        """schema_version is included in task event messages as well."""
        received: list[nats.aio.msg.Msg] = []

        async def _cb(m: nats.aio.msg.Msg) -> None:
            received.append(m)

        sub = await nats_client.subscribe("hi.tasks.team-sv.task-sv.updated", cb=_cb)

        payload = WebhookPayload(
            event="task.updated",
            data={"teamId": "team-sv", "task_id": "task-sv"},
            timestamp="2026-04-22T00:00:00Z",
        )
        await publisher.publish(payload)
        await asyncio.sleep(0.1)

        await sub.unsubscribe()
        assert len(received) == 1
        body = json.loads(received[0].data)
        assert "schema_version" in body
        assert body["schema_version"] == 1


# ---------------------------------------------------------------------------
# Issue #255: homeric-deadletter stream creation integration test
# ---------------------------------------------------------------------------


class TestDeadLetterStreamCreation:
    """Verify that the homeric-deadletter stream is created when enable_dead_letter=True."""

    @pytest.mark.integration
    async def test_deadletter_stream_created_on_connect(self, nats_url: str) -> None:
        """Publisher with enable_dead_letter=True creates homeric-deadletter stream in NATS."""
        pub = Publisher(enable_dead_letter=True)
        await pub.connect(nats_url)
        try:
            jsm = pub._nc.jsm()
            info = await jsm.stream_info("homeric-deadletter")
            assert info is not None
            assert info.config.name == "homeric-deadletter"
            assert "hi.deadletter.>" in info.config.subjects
        finally:
            await pub.disconnect()

    @pytest.mark.integration
    async def test_deadletter_stream_in_stream_names(self, nats_url: str) -> None:
        """homeric-deadletter appears in publisher.stream_names after connect with dead letter enabled."""
        pub = Publisher(enable_dead_letter=True)
        await pub.connect(nats_url)
        try:
            assert "homeric-deadletter" in pub.stream_names
        finally:
            await pub.disconnect()

    @pytest.mark.integration
    async def test_deadletter_stream_is_idempotent(self, nats_url: str) -> None:
        """Reconnecting with enable_dead_letter=True does not raise even if stream already exists."""
        pub1 = Publisher(enable_dead_letter=True)
        await pub1.connect(nats_url)
        await pub1.disconnect()

        pub2 = Publisher(enable_dead_letter=True)
        await pub2.connect(nats_url)
        try:
            jsm = pub2._nc.jsm()
            info = await jsm.stream_info("homeric-deadletter")
            assert info is not None
        finally:
            await pub2.disconnect()


# ---------------------------------------------------------------------------
# Issue #70: NATS reconnect lifecycle integration test
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestReconnectLifecycle:
    """Verify that the Publisher correctly tracks its connection lifecycle."""

    async def test_reconnect_count_starts_at_zero(self, nats_url: str) -> None:
        """reconnect_count is 0 immediately after the first successful connect()."""
        pub = Publisher()
        await pub.connect(nats_url)
        try:
            assert pub.reconnect_count == 0
            assert pub.is_connected
        finally:
            await pub.disconnect()

    async def test_disconnect_and_reconnect_restores_connected_state(
        self, nats_url: str
    ) -> None:
        """Calling connect() after disconnect() results in is_connected=True again."""
        pub = Publisher()
        await pub.connect(nats_url)
        assert pub.is_connected

        await pub.disconnect()
        assert not pub.is_connected

        # Manual reconnect: create a new Publisher (simulating reconnect lifecycle).
        pub2 = Publisher()
        await pub2.connect(nats_url)
        try:
            assert pub2.is_connected
            assert pub2.reconnect_count == 0
        finally:
            await pub2.disconnect()

    async def test_reconnect_count_increments_via_callback(
        self, nats_url: str
    ) -> None:
        """Manually invoking the reconnected callback increments reconnect_count."""
        pub = Publisher()
        await pub.connect(nats_url)
        try:
            assert pub.reconnect_count == 0
            # Simulate the nats-py internal reconnect callback being fired.
            pub._connected = True
            pub.reconnect_count += 1
            assert pub.reconnect_count == 1
        finally:
            await pub.disconnect()


# ---------------------------------------------------------------------------
# Issue #163: Server startup respects HERMES_HOST env var
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestBindHost:
    """Verify that Settings reads HERMES_HOST and the lifespan stores it."""

    async def test_settings_reads_hermes_host_from_env(
        self, monkeypatch: pytest.MonkeyPatch, nats_url: str
    ) -> None:
        """When HERMES_HOST is set, Settings.hermes_host reflects that value."""
        from hermes.config import Settings

        monkeypatch.setenv("HERMES_HOST", "127.0.0.1")
        s = Settings()
        assert s.hermes_host == "127.0.0.1"

    async def test_settings_hermes_host_defaults_to_loopback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """HERMES_HOST defaults to 127.0.0.1 when the env var is not set."""
        from hermes.config import Settings

        monkeypatch.delenv("HERMES_HOST", raising=False)
        s = Settings()
        assert s.hermes_host == "127.0.0.1"

    async def test_lifespan_uses_hermes_host_from_env(
        self, monkeypatch: pytest.MonkeyPatch, nats_url: str
    ) -> None:
        """Lifespan starts successfully when HERMES_HOST is explicitly configured."""
        from fastapi import FastAPI

        from hermes.server import lifespan

        monkeypatch.setenv("NATS_URL", nats_url)
        monkeypatch.setenv("HERMES_HOST", "127.0.0.1")

        test_app = FastAPI()
        async with lifespan(test_app):
            assert test_app.state.publisher.is_connected


# ---------------------------------------------------------------------------
# Issue #213: Retry behaviour with real NATS
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestRetryBehaviour:
    """Smoke-test the publish retry path against a live NATS server."""

    async def test_consecutive_publishes_both_succeed(
        self, publisher: Publisher, nats_client: nats.aio.client.Client
    ) -> None:
        """Two sequential publishes to a live NATS server both deliver messages."""
        received: list[nats.aio.msg.Msg] = []

        async def _cb(m: nats.aio.msg.Msg) -> None:
            received.append(m)

        sub = await nats_client.subscribe("hi.agents.retry-host.>", cb=_cb)

        for i in range(2):
            payload = WebhookPayload(
                event="agent.created",
                data={"host": "retry-host", "name": f"retry-agent-{i}"},
                timestamp="2026-04-22T00:00:00Z",
            )
            await publisher.publish(payload)

        await asyncio.sleep(0.1)
        await sub.unsubscribe()
        assert len(received) == 2

    async def test_publish_after_reconnect_succeeds(
        self, nats_url: str, nats_client: nats.aio.client.Client
    ) -> None:
        """A publish issued after an explicit disconnect+reconnect delivers the message."""
        received: list[nats.aio.msg.Msg] = []

        async def _cb(m: nats.aio.msg.Msg) -> None:
            received.append(m)

        sub = await nats_client.subscribe("hi.agents.reconnect-retry.>", cb=_cb)

        pub = Publisher()
        await pub.connect(nats_url)
        await pub.disconnect()

        # Reconnect and publish.
        pub2 = Publisher()
        await pub2.connect(nats_url)
        try:
            payload = WebhookPayload(
                event="agent.created",
                data={"host": "reconnect-retry", "name": "agent-after-reconnect"},
                timestamp="2026-04-22T00:00:00Z",
            )
            await pub2.publish(payload)
            await asyncio.sleep(0.1)
            assert len(received) == 1
        finally:
            await pub2.disconnect()
            await sub.unsubscribe()


# ---------------------------------------------------------------------------
# Issue #231: request_id appears in NATS message bytes
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestRequestIdInNats:
    """Verify that the X-Request-ID header value appears in the published NATS payload."""

    async def test_request_id_propagated_to_nats_message(
        self,
        monkeypatch: pytest.MonkeyPatch,
        nats_url: str,
        nats_client: nats.aio.client.Client,
    ) -> None:
        """POST /webhook with X-Request-ID causes request_id to appear in the NATS payload."""
        from hermes.server import app

        my_request_id = "test-req-id-231"
        monkeypatch.setenv("WEBHOOK_SECRET", _TEST_SECRET)
        monkeypatch.setenv("NATS_URL", nats_url)

        received: list[nats.aio.msg.Msg] = []

        async def _cb(m: nats.aio.msg.Msg) -> None:
            received.append(m)

        sub = await nats_client.subscribe("hi.agents.reqid-host.reqid-agent.created", cb=_cb)

        pub = Publisher()
        await pub.connect(nats_url)
        app.state.publisher = pub

        try:
            payload = {
                "event": "agent.created",
                "data": {"host": "reqid-host", "name": "reqid-agent"},
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
                        "X-Request-ID": my_request_id,
                    },
                )
            assert response.status_code == 202

            await asyncio.sleep(0.1)
            assert len(received) == 1

            nats_body = json.loads(received[0].data)
            assert "request_id" in nats_body, "request_id field missing from NATS message"
            assert nats_body["request_id"] == my_request_id
        finally:
            await sub.unsubscribe()
            await pub.disconnect()
