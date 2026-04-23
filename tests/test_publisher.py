"""Tests for NATS subject routing logic in hermes.publisher."""

from __future__ import annotations

import sys
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

# Ensure src is on the path when running directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from nats.js.errors import NotFoundError

from hermes.publisher import Publisher, _slug


def _make_publisher() -> Publisher:
    return Publisher()


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


class TestEnsureStreams:
    """_ensure_streams stream creation logic."""

    def _make_connected_publisher(self) -> Publisher:
        pub = Publisher()
        pub._nc = MagicMock()
        return pub

    @pytest.mark.asyncio
    async def test_stream_exists_no_create(self) -> None:
        pub = self._make_connected_publisher()
        jsm = AsyncMock()
        jsm.stream_info = AsyncMock(return_value=MagicMock())
        jsm.add_stream = AsyncMock()
        pub._nc.jsm.return_value = jsm

        await pub._ensure_streams()

        assert jsm.add_stream.call_count == 0

    @pytest.mark.asyncio
    async def test_stream_not_found_creates_stream(self) -> None:
        pub = self._make_connected_publisher()
        jsm = AsyncMock()
        jsm.stream_info = AsyncMock(side_effect=NotFoundError)
        jsm.add_stream = AsyncMock()
        pub._nc.jsm.return_value = jsm

        await pub._ensure_streams()

        assert jsm.add_stream.call_count == 3  # agents + tasks + deadletter
        names = {call.args[0].name for call in jsm.add_stream.call_args_list}
        assert names == {"homeric-agents", "homeric-tasks", "homeric-deadletter"}

    @pytest.mark.asyncio
    async def test_non_notfounderror_propagates(self) -> None:
        pub = self._make_connected_publisher()
        jsm = AsyncMock()
        jsm.stream_info = AsyncMock(side_effect=OSError("connection refused"))
        jsm.add_stream = AsyncMock()
        pub._nc.jsm.return_value = jsm

        with pytest.raises(OSError, match="connection refused"):
            await pub._ensure_streams()

        assert jsm.add_stream.call_count == 0


class TestSlugSanitisation:
    """Unit tests for the _slug() helper covering wildcard sanitisation."""

    def test_wildcard_star_is_removed(self) -> None:
        assert "*" not in _slug("test*")

    def test_wildcard_gt_is_removed(self) -> None:
        assert ">" not in _slug("all>")

    def test_wildcards_mixed_with_spaces(self) -> None:
        result = _slug("my *agent >")
        assert "*" not in result
        assert ">" not in result
        assert result == "my-agent-"

    def test_wildcard_star_full_subject(self) -> None:
        pub = _make_publisher()
        subject = pub._parse_agent_subject(
            {"host": "myhost", "name": "test*"},
            "agent.created",
        )
        assert "*" not in subject
        assert subject == "hi.agents.myhost.test.created"

    def test_wildcard_gt_full_subject(self) -> None:
        pub = _make_publisher()
        subject = pub._parse_agent_subject(
            {"host": "myhost", "name": "all>"},
            "agent.created",
        )
        assert ">" not in subject
        assert subject == "hi.agents.myhost.all.created"


class TestActiveSubjectsBound:
    """_active_subjects LRU bound behaviour."""

    def _add_subject(self, pub: Publisher, subject: str) -> None:
        """Directly insert a subject as if it had been published."""
        pub._track_subject(subject)

    def test_subjects_below_cap_are_all_retained(self) -> None:
        pub = Publisher(max_subjects=5)
        for i in range(5):
            self._add_subject(pub, f"hi.tasks.team.task-{i}.updated")
        assert len(pub.active_subjects) == 5

    def test_oldest_evicted_when_cap_exceeded(self) -> None:
        pub = Publisher(max_subjects=3)
        for i in range(4):
            self._add_subject(pub, f"hi.tasks.team.task-{i}.updated")
        subjects = pub.active_subjects
        assert len(subjects) == 3
        assert "hi.tasks.team.task-0.updated" not in subjects

    def test_republishing_existing_subject_does_not_evict(self) -> None:
        pub = Publisher(max_subjects=3)
        subjects_added = [f"hi.tasks.team.task-{i}.updated" for i in range(3)]
        for s in subjects_added:
            self._add_subject(pub, s)
        self._add_subject(pub, subjects_added[0])
        assert len(pub.active_subjects) == 3
        assert subjects_added[0] in pub.active_subjects

    def test_lru_promotes_on_repeat_and_evicts_next_oldest(self) -> None:
        pub = Publisher(max_subjects=3)
        s0, s1, s2 = "a", "b", "c"
        for s in (s0, s1, s2):
            self._add_subject(pub, s)
        self._add_subject(pub, s0)
        self._add_subject(pub, "d")
        subjects = pub.active_subjects
        assert s1 not in subjects
        assert s0 in subjects

    def test_active_subjects_returns_sorted_list(self) -> None:
        pub = Publisher(max_subjects=10)
        for s in ("z.subject", "a.subject", "m.subject"):
            self._add_subject(pub, s)
        assert pub.active_subjects == sorted(["z.subject", "a.subject", "m.subject"])

    def test_custom_max_subjects_respected(self) -> None:
        pub = Publisher(max_subjects=2)
        assert pub._max_subjects == 2
        for i in range(5):
            self._add_subject(pub, f"subj-{i}")
        assert len(pub.active_subjects) == 2
