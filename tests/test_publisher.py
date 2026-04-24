"""Tests for NATS subject routing logic in hermes.publisher."""

from __future__ import annotations

import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure src is on the path when running directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from hermes.models import WebhookPayload
from hermes.publisher import Publisher


def _make_publisher(enable_dead_letter: bool = True) -> Publisher:
    return Publisher(enable_dead_letter=enable_dead_letter)


def _make_payload(event: str, data: dict | None = None) -> WebhookPayload:
    return WebhookPayload(
        event=event,
        data=data or {},
        timestamp="2026-04-22T00:00:00Z",
    )


class TestAgentSubjectMapping:
    """Agent event → NATS subject routing."""

    def test_agent_created(self) -> None:
        pub = _make_publisher()
        subject = pub._parse_agent_subject(
            {"host": "docker-desktop", "name": "researcher"},
            "agent.created",
        )
        assert subject == "hi.agents.docker-desktop.researcher.created"

    def test_agent_updated(self) -> None:
        pub = _make_publisher()
        subject = pub._parse_agent_subject(
            {"host": "worker-01", "name": "analyst"},
            "agent.updated",
        )
        assert subject == "hi.agents.worker-01.analyst.updated"

    def test_agent_deleted(self) -> None:
        pub = _make_publisher()
        subject = pub._parse_agent_subject(
            {"host": "worker-01", "name": "scout"},
            "agent.deleted",
        )
        assert subject == "hi.agents.worker-01.scout.deleted"

    def test_missing_host_falls_back_to_unknown(self) -> None:
        pub = _make_publisher()
        subject = pub._parse_agent_subject({"name": "bot"}, "agent.created")
        assert subject == "hi.agents.unknown.bot.created"

    def test_missing_name_falls_back_to_unknown(self) -> None:
        pub = _make_publisher()
        subject = pub._parse_agent_subject({"host": "myhost"}, "agent.created")
        assert subject == "hi.agents.myhost.unknown.created"

    def test_spaces_in_tokens_are_slugified(self) -> None:
        pub = _make_publisher()
        subject = pub._parse_agent_subject(
            {"host": "my host", "name": "my agent"},
            "agent.created",
        )
        assert " " not in subject
        assert subject == "hi.agents.my-host.my-agent.created"


class TestTaskSubjectMapping:
    """Task event → NATS subject routing."""

    def test_task_updated(self) -> None:
        pub = _make_publisher()
        subject = pub._parse_task_subject(
            {"team_id": "team-alpha", "task_id": "task-42"},
            "task.updated",
        )
        assert subject == "hi.tasks.team-alpha.task-42.updated"

    def test_task_completed(self) -> None:
        pub = _make_publisher()
        subject = pub._parse_task_subject(
            {"team_id": "team-beta", "task_id": "t-99"},
            "task.completed",
        )
        assert subject == "hi.tasks.team-beta.t-99.completed"

    def test_missing_team_id_falls_back_to_unknown(self) -> None:
        pub = _make_publisher()
        subject = pub._parse_task_subject({"task_id": "t-1"}, "task.updated")
        assert subject == "hi.tasks.unknown.t-1.updated"

    def test_alternate_id_key(self) -> None:
        """task_id falls back to 'id' if 'task_id' is absent."""
        pub = _make_publisher()
        subject = pub._parse_task_subject(
            {"team_id": "alpha", "id": "xyz"},
            "task.updated",
        )
        assert subject == "hi.tasks.alpha.xyz.updated"


class TestPublisherLifecycle:
    """Publisher connect / disconnect / publish behavior with mocked NATS."""

    async def test_connect_sets_is_connected(self) -> None:
        mock_nc = MagicMock()
        mock_nc.is_closed = False
        mock_nc.jetstream.return_value = MagicMock()
        mock_jsm = AsyncMock()
        mock_jsm.find_stream = AsyncMock(return_value=MagicMock())
        mock_nc.jsm.return_value = mock_jsm

        with patch("hermes.publisher.nats.connect", AsyncMock(return_value=mock_nc)):
            pub = Publisher()
            await pub.connect("nats://localhost:4222")
            assert pub.is_connected

    async def test_disconnect_sets_not_connected(self) -> None:
        mock_nc = MagicMock()
        mock_nc.is_closed = False
        mock_nc.drain = AsyncMock()
        mock_nc.jetstream.return_value = MagicMock()
        mock_jsm = AsyncMock()
        mock_jsm.find_stream = AsyncMock(return_value=MagicMock())
        mock_nc.jsm.return_value = mock_jsm

        with patch("hermes.publisher.nats.connect", AsyncMock(return_value=mock_nc)):
            pub = Publisher()
            await pub.connect("nats://localhost:4222")
            await pub.disconnect()
            assert not pub.is_connected

    async def test_disconnect_when_never_connected_is_noop(self) -> None:
        pub = Publisher()
        await pub.disconnect()  # must not raise
        assert not pub.is_connected

    async def test_publish_agent_event(self) -> None:
        mock_js = AsyncMock()
        mock_js.publish = AsyncMock()

        pub = Publisher()
        pub._js = mock_js

        payload = WebhookPayload(
            event="agent.created",
            data={"host": "h", "name": "n"},
            timestamp="2026-04-22T00:00:00Z",
        )
        await pub.publish(payload)

        mock_js.publish.assert_awaited_once()
        subject, _ = mock_js.publish.await_args.args
        assert subject == "hi.agents.h.n.created"
        assert "hi.agents.h.n.created" in pub.active_subjects

    async def test_publish_task_event(self) -> None:
        mock_js = AsyncMock()
        mock_js.publish = AsyncMock()

        pub = Publisher()
        pub._js = mock_js

        payload = WebhookPayload(
            event="task.completed",
            data={"teamId": "t1", "task_id": "t-99"},
            timestamp="2026-04-22T00:00:00Z",
        )
        await pub.publish(payload)

        mock_js.publish.assert_awaited_once()
        subject, _ = mock_js.publish.await_args.args
        assert subject == "hi.tasks.t1.t-99.completed"

    async def test_publish_unknown_event_is_dropped(self) -> None:
        mock_js = AsyncMock()
        pub = Publisher(enable_dead_letter=False)
        pub._js = mock_js

        payload = WebhookPayload(
            event="unknown.event",
            data={},
            timestamp="2026-04-22T00:00:00Z",
        )
        await pub.publish(payload)
        mock_js.publish.assert_not_awaited()

    async def test_publish_without_connect_raises(self) -> None:
        pub = Publisher()
        payload = WebhookPayload(
            event="agent.created",
            data={"host": "h", "name": "n"},
            timestamp="2026-04-22T00:00:00Z",
        )
        with pytest.raises(RuntimeError, match="not connected"):
            await pub.publish(payload)

    def test_is_connected_false_when_nc_is_none(self) -> None:
        pub = Publisher()
        assert not pub.is_connected

    def test_active_subjects_initially_empty(self) -> None:
        pub = Publisher()
        assert pub.active_subjects == []

    async def test_ensure_streams_creates_missing_stream(self) -> None:
        """_ensure_streams creates a stream when find_stream raises."""
        from nats.js.errors import NotFoundError

        mock_nc = MagicMock()
        mock_nc.is_closed = False
        mock_nc.jetstream.return_value = MagicMock()
        mock_jsm = AsyncMock()
        mock_jsm.stream_info = AsyncMock(side_effect=NotFoundError)
        mock_jsm.add_stream = AsyncMock()
        mock_nc.jsm.return_value = mock_jsm

        with patch("hermes.publisher.nats.connect", AsyncMock(return_value=mock_nc)):
            pub = Publisher()
            await pub.connect("nats://localhost:4222")
            assert mock_jsm.add_stream.await_count == 3  # agents + tasks + deadletter


class TestDeadLetterRouting:
    """Dead-letter handling for unroutable webhook events."""

    def test_resolve_subject_returns_none_for_unknown_event(self) -> None:
        pub = _make_publisher()
        payload = _make_payload("team.created")
        assert pub._resolve_subject(payload) is None

    @pytest.mark.asyncio
    async def test_publish_dead_letters_unroutable_event(self) -> None:
        pub = _make_publisher(enable_dead_letter=True)
        pub._js = AsyncMock()
        await pub.publish(_make_payload("team.created"))
        pub._js.publish.assert_awaited_once()
        subject = pub._js.publish.call_args[0][0]
        assert subject == "hi.deadletter.team-created"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "event_type,expected_subject",
        [
            ("team.created",   "hi.deadletter.team-created"),
            ("sprint.started", "hi.deadletter.sprint-started"),
            ("unknown",        "hi.deadletter.unknown"),
            ("foo.bar.baz",    "hi.deadletter.foo-bar-baz"),
        ],
    )
    async def test_dead_letter_subject_format(
        self, event_type: str, expected_subject: str
    ) -> None:
        pub = _make_publisher(enable_dead_letter=True)
        pub._js = AsyncMock()
        await pub.publish(_make_payload(event_type))
        subject = pub._js.publish.call_args[0][0]
        assert subject == expected_subject

    @pytest.mark.asyncio
    async def test_dead_letter_disabled_drops_event(self) -> None:
        pub = _make_publisher(enable_dead_letter=False)
        pub._js = AsyncMock()
        await pub.publish(_make_payload("team.created"))
        pub._js.publish.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dead_letter_tracks_active_subject(self) -> None:
        pub = _make_publisher(enable_dead_letter=True)
        pub._js = AsyncMock()
        await pub.publish(_make_payload("team.created"))
        assert "hi.deadletter.team-created" in pub.active_subjects

    @pytest.mark.asyncio
    async def test_dead_letter_disabled_does_not_track_subject(self) -> None:
        pub = _make_publisher(enable_dead_letter=False)
        pub._js = AsyncMock()
        await pub.publish(_make_payload("team.created"))
        assert not any(s.startswith("hi.deadletter.") for s in pub.active_subjects)
