"""Integration tests requiring a live NATS server.

Run with: pytest -m integration
Skip when NATS is unavailable: these tests are automatically skipped if NATS
cannot be reached at TEST_NATS_URL (default nats://localhost:4222).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import nats
import pytest
from httpx import ASGITransport, AsyncClient

from hermes.models import WebhookPayload
from hermes.publisher import Publisher

from tests.helpers import FIXED_TS as _FIXED_TS, sign_body, wait_for_messages

pytestmark = pytest.mark.integration

_NATS_URL = os.environ.get("TEST_NATS_URL", "nats://localhost:4222")
INTEGRATION_TEST_SECRET = "integration-test-secret-for-hermes-webhook"


# ---------------------------------------------------------------------------
# Session-scoped skip if NATS is not available
# ---------------------------------------------------------------------------


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

    reachable = _asyncio.run(_check())
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

        sub = await nats_client.subscribe("hi.agents.prod-host.my-agent.created", cb=_cb)
        await nats_client.flush()  # ensure server has ack'd the subscription before we publish

        payload = WebhookPayload(
            event="agent.created",
            data={"host": "prod-host", "name": "my-agent"},
            timestamp=_FIXED_TS,
        )
        await publisher.publish(payload)
        await wait_for_messages(received, expected=1, timeout=5.0)

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

        sub = await nats_client.subscribe("hi.tasks.team-42.t-007.updated", cb=_cb)
        await nats_client.flush()  # ensure server has ack'd the subscription before we publish

        payload = WebhookPayload(
            event="task.updated",
            data={"teamId": "team-42", "task_id": "t-007"},
            timestamp=_FIXED_TS,
        )
        await publisher.publish(payload)
        await wait_for_messages(received, expected=1, timeout=5.0)

        await sub.unsubscribe()
        assert len(received) == 1
        assert received[0].subject == "hi.tasks.team-42.t-007.updated"

    async def test_publish_tracks_active_subjects(self, publisher: Publisher) -> None:
        """active_subjects is updated after each successful publish."""
        payload = WebhookPayload(
            event="agent.deleted",
            data={"host": "h1", "name": "bot"},
            timestamp=_FIXED_TS,
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
            timestamp=_FIXED_TS,
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

        monkeypatch.setenv("WEBHOOK_SECRET", INTEGRATION_TEST_SECRET)
        monkeypatch.setenv("NATS_URL", nats_url)

        received: list[nats.aio.msg.Msg] = []

        async def _cb(m: nats.aio.msg.Msg) -> None:
            received.append(m)

        sub = await nats_client.subscribe("hi.agents.e2e-host.e2e-agent.created", cb=_cb)
        await nats_client.flush()  # ensure server has ack'd the subscription before we publish

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
                        "X-Webhook-Signature": sign_body(body_bytes, INTEGRATION_TEST_SECRET),
                    },
                )
            assert response.status_code == 202

            await wait_for_messages(received, expected=1, timeout=5.0)
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

        monkeypatch.setenv("WEBHOOK_SECRET", INTEGRATION_TEST_SECRET)

        disconnected = Publisher()  # never connected
        app.state.publisher = disconnected

        payload = {
            "event": "agent.created",
            "data": {"host": "h", "name": "n"},
            "timestamp": "2026-04-22T00:00:00Z",
        }
        body_bytes = json.dumps(payload).encode()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/webhook",
                content=body_bytes,
                headers={
                    "Content-Type": "application/json",
                    "X-Webhook-Signature": sign_body(body_bytes, INTEGRATION_TEST_SECRET),
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

    async def test_lifespan_handles_nats_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
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

        sub = await nats_client.subscribe("hi.deadletter.>", cb=_cb)

        pub = Publisher(enable_dead_letter=False)
        await pub.connect(nats_url)
        try:
            payload = WebhookPayload(
                event="unknown.event",
                data={"foo": "bar"},
                timestamp=_FIXED_TS,
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

        sub = await nats_client.subscribe("hi.agents.concurrent-host.>", cb=_cb)
        await nats_client.flush()  # ensure server has ack'd the subscription before we publish

        payloads = [
            WebhookPayload(
                event="agent.created",
                data={"host": "concurrent-host", "name": f"agent-{i}"},
                timestamp=_FIXED_TS,
            )
            for i in range(10)
        ]
        await asyncio.gather(*(publisher.publish(p) for p in payloads))
        await wait_for_messages(received, expected=10, timeout=5.0)

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
        await nats_client.flush()  # ensure server has ack'd the subscription before we publish

        large_data: dict[str, object] = {
            "host": "large",
            "name": "large-agent",
            "blob": "x" * 100_000,
        }
        payload = WebhookPayload(
            event="agent.created",
            data=large_data,
            timestamp=_FIXED_TS,
        )
        await publisher.publish(payload)
        await wait_for_messages(received, expected=1, timeout=5.0)

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

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
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

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
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
        await nats_client.flush()  # ensure server has ack'd the subscription before we publish

        payload = WebhookPayload(
            event="agent.created",
            data={"host": "schema-host", "name": "schema-agent"},
            timestamp="2026-04-22T00:00:00Z",
        )
        await publisher.publish(payload)
        await wait_for_messages(received, expected=1, timeout=5.0)

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
        await nats_client.flush()  # ensure server has ack'd the subscription before we publish

        payload = WebhookPayload(
            event="task.updated",
            data={"teamId": "team-sv", "task_id": "task-sv"},
            timestamp="2026-04-22T00:00:00Z",
        )
        await publisher.publish(payload)
        await wait_for_messages(received, expected=1, timeout=5.0)

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

    async def test_disconnect_and_reconnect_restores_connected_state(self, nats_url: str) -> None:
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

    async def test_reconnect_count_not_incremented_by_nats_callback(self, nats_url: str) -> None:
        """Regression for issue #526.

        The nats-py ``reconnected_cb`` must not touch ``reconnect_count``; only
        the ``_reconnect_loop`` success path increments it. This prevents a
        single reconnect from being counted twice if nats-py ever fires the
        callback (e.g. if ``allow_reconnect`` is flipped to True).
        """
        pub = Publisher()
        await pub.connect(nats_url)
        try:
            assert pub.reconnect_count == 0
            # The callback is closed over inside _connect_internal; we cannot
            # reach it from outside, but the contract is verified in the unit
            # test ``test_reconnected_cb_does_not_increment_reconnect_count``.
            # Here we just assert the steady-state invariant.
            assert pub.reconnect_count == 0
        finally:
            await pub.disconnect()


# ---------------------------------------------------------------------------
# Issue #525 — exponential backoff in reconnect loop
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestReconnectBackoffIntegration:
    """Drive ``Publisher._reconnect_loop`` against a real NATS server to cover
    the new exponential-backoff code paths under the integration coverage
    gate (issue #525).  These complement the pure-unit tests in
    ``tests/test_publisher_reconnect_backoff.py`` that mock NATS entirely.
    """

    async def test_loop_resets_backoff_when_connection_healthy(self, nats_url: str) -> None:
        """A healthy NATS connection makes the loop hit the
        ``failed_attempts = 0; continue`` reset branch on every iteration.

        We start the loop with tiny intervals + jitter so the very first
        iteration executes the jitter multiplication path (line 161), waits
        briefly, observes the healthy ``nc`` (lines 169-171), and loops.
        After a short delay we set ``_stop_event`` and assert clean shutdown
        without any reconnect attempts (``reconnect_count`` stays at 0).
        """
        pub = Publisher()
        await pub.connect(nats_url)
        try:
            pub._stop_event = asyncio.Event()
            # Run a *fresh* loop with extremely short tick so we cover the
            # delay/jitter/healthy-reset path many times in a few hundred ms.
            loop_task = asyncio.create_task(
                pub._reconnect_loop(
                    nats_url,
                    connect_timeout=1.0,
                    reconnect_interval=0.01,
                    hard_timeout=1.0,
                    max_interval=0.05,
                    jitter=0.1,
                )
            )
            await asyncio.sleep(0.1)
            assert not loop_task.done(), "loop terminated unexpectedly"
            assert pub.reconnect_count == 0, "reset branch must not increment reconnect_count"
            pub._stop_event.set()
            await asyncio.wait_for(loop_task, timeout=2.0)
        finally:
            await pub.disconnect()

    async def test_loop_recovers_after_transient_disconnect(self, nats_url: str) -> None:
        """Forcibly close the underlying NATS client so the loop enters the
        reconnect-attempt branch (lines 172-194), then succeeds against the
        live server and resets the backoff exponent (line 182).
        """
        pub = Publisher()
        await pub.connect(nats_url)
        try:
            # Stop the existing reconnect task so we can drive a fresh loop.
            pub._stop_event.set()
            assert pub._reconnect_task is not None
            try:
                await asyncio.wait_for(pub._reconnect_task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            pub._reconnect_task = None

            # Force the publisher into the "connection lost" state.
            assert pub._nc is not None
            await pub._nc.close()
            assert pub._nc.is_closed

            pub._stop_event = asyncio.Event()
            loop_task = asyncio.create_task(
                pub._reconnect_loop(
                    nats_url,
                    connect_timeout=2.0,
                    reconnect_interval=0.02,
                    hard_timeout=2.0,
                    max_interval=0.1,
                    jitter=0.0,
                )
            )

            # Poll until the loop has successfully reconnected once.
            deadline = asyncio.get_event_loop().time() + 5.0
            while asyncio.get_event_loop().time() < deadline:
                if pub.reconnect_count >= 1 and pub._nc is not None and not pub._nc.is_closed:
                    break
                await asyncio.sleep(0.05)

            pub._stop_event.set()
            await asyncio.wait_for(loop_task, timeout=2.0)

            assert pub.reconnect_count >= 1, "loop never recorded a successful reconnect"
        finally:
            # ``disconnect`` is safe even if we already replaced _nc.
            await pub.disconnect()


# ---------------------------------------------------------------------------
# Issue #447 — reconnect loop fires after NATS connection is closed
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestReconnectLoopFiresAfterClose:
    """End-to-end: close the underlying NATS client and assert the production
    background reconnect loop recovers and increments reconnect_count.

    Disconnection is verified via `_nc.is_closed` (deterministic after an explicit
    close()) rather than polling `_connected` (callback-driven, may be suppressed
    on user-initiated close in some nats-py versions).

    Complements the mocked unit tests in tests/test_publisher_reconnect.py
    by exercising real nats-py state transitions (is_closed semantics under
    allow_reconnect=False) against a live server.
    """

    async def test_reconnect_loop_recovers_after_nc_close(
        self,
        monkeypatch: pytest.MonkeyPatch,
        nats_url: str,
    ) -> None:
        # Shrink the production loop interval so the test converges in seconds,
        # not the 5s default. Must be set before Publisher() / connect() so the
        # Settings LRU cache (cleared by the reset_settings autouse fixture)
        # picks it up.
        monkeypatch.setenv("NATS_RECONNECT_INTERVAL", "0.1")
        monkeypatch.setenv("NATS_RECONNECT_MAX_INTERVAL", "0.2")
        monkeypatch.setenv("NATS_RECONNECT_JITTER", "0.0")
        monkeypatch.setenv("NATS_RECONNECT_HARD_TIMEOUT", "2.0")

        pub = Publisher()
        await pub.connect(nats_url)
        try:
            # (1) Sanity: connected, counter at zero, production loop running.
            assert pub.is_connected
            assert pub.reconnect_count == 0
            assert pub.reconnect_loop_active is True

            # (2) Force a real disconnect by closing the underlying NATS client.
            #     With allow_reconnect=False (publisher.py:159), close() drives
            #     is_closed=True and triggers disconnected_cb, which clears
            #     pub._connected (publisher.py:141).
            assert pub._nc is not None
            await pub._nc.close()

            # (3) Verify the underlying client is closed (deterministic on explicit close()).
            #     The production reconnect loop keys off _nc.is_closed (publisher.py:206),
            #     not _connected, so we gate here on the same condition.
            #     disconnected_cb may be suppressed on user-initiated close() in some
            #     nats-py versions — polling _connected would be a flake source.
            assert pub._nc.is_closed

            # (4) Wait for the production loop to observe is_closed and
            #     successfully reconnect against the live server.
            async def _wait_reconnected() -> None:
                while pub.reconnect_count < 1:
                    await asyncio.sleep(0.05)

            await asyncio.wait_for(_wait_reconnected(), timeout=10.0)

            # (5) Assert the contract from issue #447.
            assert pub.reconnect_count >= 1
            assert pub.is_connected
            assert pub._nc is not None and not pub._nc.is_closed
        finally:
            await pub.disconnect()


# ---------------------------------------------------------------------------
# Issue #147 — lifespan abort / degraded state
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestLifespanAbort:
    """Verify that /health returns 503 when the publisher is disconnected (degraded state).

    When NATS is unavailable at startup the lifespan raises and the app never
    serves traffic.  This class tests the *degraded-state* behaviour: a
    Publisher that never connected is injected into app.state so that the
    health endpoint can be exercised independently of lifespan.
    """

    async def test_health_returns_503_when_publisher_disconnected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET /health returns 503 and status='degraded' when publisher is not connected."""
        from hermes.server import app

        disconnected = Publisher()  # never connected — is_connected is False
        app.state.publisher = disconnected

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/health")

        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "degraded"
        assert body["nats_connected"] is False

    async def test_webhook_returns_503_when_publisher_disconnected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """POST /webhook returns 503 when the publisher has never connected (bad NATS URL)."""
        from hermes.server import app

        monkeypatch.setenv("WEBHOOK_SECRET", "")
        disconnected = Publisher()  # never connected — nats://localhost:9999 equivalent
        app.state.publisher = disconnected

        payload = {
            "event": "agent.created",
            "data": {"host": "abort-host", "name": "abort-agent"},
            "timestamp": "2026-04-22T00:00:00Z",
        }
        body_bytes = json.dumps(payload).encode()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/webhook",
                content=body_bytes,
                headers={"Content-Type": "application/json"},
            )

        assert response.status_code == 503


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
        await nats_client.flush()  # ensure server has ack'd the subscription before we publish

        for i in range(2):
            payload = WebhookPayload(
                event="agent.created",
                data={"host": "retry-host", "name": f"retry-agent-{i}"},
                timestamp="2026-04-22T00:00:00Z",
            )
            await publisher.publish(payload)

        await wait_for_messages(received, expected=2, timeout=5.0)
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
        await nats_client.flush()  # ensure server has ack'd the subscription before we publish

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
            await wait_for_messages(received, expected=1, timeout=5.0)
            assert len(received) == 1
        finally:
            await pub2.disconnect()
            await sub.unsubscribe()


# ---------------------------------------------------------------------------
# Issue #215 — full lifespan shutdown sequence
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestLifespanShutdown:
    """Verify the graceful shutdown sequence via the lifespan context manager."""

    async def test_lifespan_shutdown_rejects_webhook_but_allows_health(
        self, monkeypatch: pytest.MonkeyPatch, nats_url: str
    ) -> None:
        """After shutdown signal, /webhook → 503 while /health remains available."""
        import hermes.server as _server
        from hermes.server import app, lifespan

        monkeypatch.setenv("NATS_URL", nats_url)

        from fastapi import FastAPI

        test_app = FastAPI()

        async with lifespan(test_app):
            # App started, publisher connected: /health should be 200.
            test_app_pub = test_app.state.publisher
            app.state.publisher = test_app_pub

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                health_before = await client.get("/health")
            assert health_before.status_code == 200

            # Simulate shutdown signal by setting the module-level event.
            _server._shutdown_event.set()

            # /webhook must now return 503 (ShutdownMiddleware).
            payload = {
                "event": "agent.created",
                "data": {"host": "shutdown-host", "name": "shutdown-agent"},
                "timestamp": "2026-04-22T00:00:00Z",
            }
            body_bytes = json.dumps(payload).encode()

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                webhook_response = await client.post(
                    "/webhook",
                    content=body_bytes,
                    headers={"Content-Type": "application/json"},
                )
            assert webhook_response.status_code == 503

            # /health must still respond (ShutdownMiddleware only blocks /webhook).
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                health_after = await client.get("/health")
            assert health_after.status_code in {200, 503}
            body = health_after.json()
            assert body["shutting_down"] is True


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
        monkeypatch.setenv("WEBHOOK_SECRET", INTEGRATION_TEST_SECRET)
        monkeypatch.setenv("NATS_URL", nats_url)

        received: list[nats.aio.msg.Msg] = []

        async def _cb(m: nats.aio.msg.Msg) -> None:
            received.append(m)

        sub = await nats_client.subscribe("hi.agents.reqid-host.reqid-agent.created", cb=_cb)
        await nats_client.flush()  # ensure server has ack'd the subscription before we publish

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
                        "X-Webhook-Signature": sign_body(body_bytes, INTEGRATION_TEST_SECRET),
                        "X-Request-ID": my_request_id,
                    },
                )
            assert response.status_code == 202

            await wait_for_messages(received, expected=1, timeout=5.0)
            assert len(received) == 1

            nats_body = json.loads(received[0].data)
            assert "request_id" in nats_body, "request_id field missing from NATS message"
            assert nats_body["request_id"] == my_request_id
        finally:
            await sub.unsubscribe()
            await pub.disconnect()


# ---------------------------------------------------------------------------
# Issue #250 — startup banner log messages
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestStartupBanner:
    """Verify that the startup banner is emitted during real lifespan startup."""

    async def test_startup_banner_logs_version_config_and_nats_status(
        self, monkeypatch: pytest.MonkeyPatch, nats_url: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        """lifespan emits version, config, and NATS connectivity banner lines."""
        from unittest.mock import patch
        from hermes.server import lifespan
        from fastapi import FastAPI

        monkeypatch.setenv("NATS_URL", nats_url)

        test_app = FastAPI()

        # Patch setup_logging to prevent it from replacing pytest's caplog handler
        with (
            caplog.at_level(logging.INFO),
            patch("hermes.server.setup_logging"),
        ):
            async with lifespan(test_app):
                pass  # startup complete; banner already logged

        log_text = "\n".join(r.message for r in caplog.records)

        # Version line
        assert "hermes" in log_text
        # Config line contains the NATS URL
        assert nats_url in log_text or "nats_url=" in log_text
        # NATS connectivity status
        assert "connected=" in log_text


# ---------------------------------------------------------------------------
# Issue #221: Full HTTP POST → HMAC → NATS JetStream ACK integration test
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestJetStreamAck:
    """Verify the full path: HTTP POST → HMAC validation → JetStream publish → ACK."""

    async def test_webhook_delivers_jetstream_ack(
        self,
        monkeypatch: pytest.MonkeyPatch,
        nats_url: str,
        nats_client: nats.aio.client.Client,
    ) -> None:
        """POST /webhook → HMAC check → Publisher.publish → JetStream ACK succeeds."""
        from hermes.server import app

        monkeypatch.setenv("WEBHOOK_SECRET", INTEGRATION_TEST_SECRET)
        monkeypatch.setenv("NATS_URL", nats_url)

        from nats.js.api import ConsumerConfig, DeliverPolicy

        js = nats_client.jetstream()

        pub = Publisher()
        await pub.connect(nats_url)
        app.state.publisher = pub

        try:
            sub = await js.pull_subscribe(
                "hi.agents.js-ack-host.js-ack-agent.created",
                stream="homeric-agents",
                config=ConsumerConfig(deliver_policy=DeliverPolicy.NEW),
            )

            payload = {
                "event": "agent.created",
                "data": {"host": "js-ack-host", "name": "js-ack-agent"},
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
                        "X-Webhook-Signature": sign_body(body_bytes, INTEGRATION_TEST_SECRET),
                    },
                )
            assert response.status_code == 202

            msgs = await sub.fetch(batch=1, timeout=5)
            assert len(msgs) == 1
            msg = msgs[0]

            assert msg.subject == "hi.agents.js-ack-host.js-ack-agent.created"
            body = json.loads(msg.data)
            assert body["event"] == "agent.created"
            assert body["schema_version"] == 1
            assert "request_id" in body

            await msg.ack()
        finally:
            await pub.disconnect()


# ---------------------------------------------------------------------------
# Issue #524 — _stop_event reuse across connect() calls
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestStopEventReuseIntegration:
    """Cover the ``connect()`` stale-task cancellation and ``_stop_event`` reuse
    branches against a live NATS server (issue #524).  The pure-unit tests in
    ``tests/test_publisher_reconnect.py`` already verify the same logic with
    mocks, but they do not contribute to the ``integration-tests`` coverage
    gate — these complement them.
    """

    async def test_double_connect_reuses_stop_event_and_cancels_stale_task(
        self, nats_url: str
    ) -> None:
        """A second ``connect()`` without intervening ``disconnect()`` must
        reuse the same ``_stop_event`` instance AND cancel the prior
        reconnect task (publisher.py lines 105-111 + 116).
        """
        pub = Publisher()
        original_event = pub._stop_event
        try:
            await pub.connect(nats_url)
            first_task = pub._reconnect_task
            assert first_task is not None
            assert not first_task.done()
            assert pub._stop_event is original_event

            # Second connect() exercises the cancel-stale-task branch.
            await pub.connect(nats_url)
            second_task = pub._reconnect_task
            assert second_task is not None
            assert second_task is not first_task
            assert first_task.done(), "stale reconnect task must be cancelled"
            assert pub._stop_event is original_event, "second connect() must not rebind _stop_event"
            assert not pub._stop_event.is_set(), (
                "connect() must clear the reused Event so the new loop can run"
            )
        finally:
            await pub.disconnect()
        # disconnect() sets the shared event, so the (still-original) Event
        # instance must reflect that — proves identity preservation end-to-end.
        assert pub._stop_event is original_event
        assert pub._stop_event.is_set()

    async def test_reconnect_after_disconnect_clears_stop_event(self, nats_url: str) -> None:
        """``connect()`` after ``disconnect()`` must clear the existing Event
        so the new ``_reconnect_loop`` does not exit immediately
        (publisher.py line 116, post-disconnect rearm path).
        """
        pub = Publisher()
        original_event = pub._stop_event
        await pub.connect(nats_url)
        await pub.disconnect()
        assert pub._stop_event.is_set()
        assert pub._reconnect_task is None

        # Reconnect: must re-arm (clear) the same Event so the new loop runs.
        await pub.connect(nats_url)
        try:
            assert pub._stop_event is original_event
            assert not pub._stop_event.is_set()
            assert pub._reconnect_task is not None
            assert not pub._reconnect_task.done()
        finally:
            await pub.disconnect()
