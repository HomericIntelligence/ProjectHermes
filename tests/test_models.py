"""Tests for HermesEventBase, AgentEvent, and TaskEvent models."""

from __future__ import annotations

import sys
import os
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from hermes.models import AgentEvent, TaskEvent, WebhookPayload


def _make_agent_event(**overrides) -> AgentEvent:
    defaults = dict(host="myhost", name="bot", event="agent.created", agent_id="a-1")
    defaults.update(overrides)
    return AgentEvent(**defaults)


def _make_task_event(**overrides) -> TaskEvent:
    defaults = dict(team_id="team-1", task_id="task-1", event="task.updated", status="running")
    defaults.update(overrides)
    return TaskEvent(**defaults)


class TestSchemaVersion:
    def test_default_schema_version_is_1(self) -> None:
        ev = _make_agent_event()
        assert ev.schema_version == 1

    def test_schema_version_zero_raises(self) -> None:
        with pytest.raises(ValidationError):
            _make_agent_event(schema_version=0)

    def test_schema_version_negative_raises(self) -> None:
        with pytest.raises(ValidationError):
            _make_agent_event(schema_version=-1)

    def test_schema_version_custom_value_accepted(self) -> None:
        ev = _make_agent_event(schema_version=2)
        assert ev.schema_version == 2


class TestTimestamp:
    def test_timestamp_defaults_to_utc_datetime(self) -> None:
        ev = _make_agent_event()
        assert isinstance(ev.timestamp, datetime)

    def test_timestamp_accepts_iso_string_coercion(self) -> None:
        ev = _make_agent_event(timestamp="2026-01-01T00:00:00Z")
        assert isinstance(ev.timestamp, datetime)
        assert ev.timestamp.year == 2026

    def test_timestamp_accepts_datetime_object(self) -> None:
        ts = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)
        ev = _make_agent_event(timestamp=ts)
        assert ev.timestamp == ts


class TestModelDumpJson:
    def test_agent_event_dump_contains_schema_version(self) -> None:
        import json
        ev = _make_agent_event()
        dumped = json.loads(ev.model_dump_json())
        assert dumped["schema_version"] == 1

    def test_agent_event_dump_timestamp_is_string(self) -> None:
        import json
        ev = _make_agent_event()
        dumped = json.loads(ev.model_dump_json())
        assert isinstance(dumped["timestamp"], str)

    def test_task_event_dump_contains_schema_version(self) -> None:
        import json
        ev = _make_task_event()
        dumped = json.loads(ev.model_dump_json())
        assert dumped["schema_version"] == 1


class TestFrozenModels:
    def test_agent_event_is_frozen(self) -> None:
        ev = _make_agent_event()
        with pytest.raises((ValidationError, TypeError)):
            ev.host = "changed"  # type: ignore[misc]

    def test_task_event_is_frozen(self) -> None:
        ev = _make_task_event()
        with pytest.raises((ValidationError, TypeError)):
            ev.team_id = "changed"  # type: ignore[misc]


class TestAgentEventValidation:
    def test_missing_host_raises(self) -> None:
        with pytest.raises((ValidationError, TypeError)):
            AgentEvent(name="bot", event="agent.created", agent_id="a-1")  # type: ignore[call-arg]

    def test_missing_name_raises(self) -> None:
        with pytest.raises((ValidationError, TypeError)):
            AgentEvent(host="h", event="agent.created", agent_id="a-1")  # type: ignore[call-arg]

    def test_metadata_defaults_to_empty_dict(self) -> None:
        ev = _make_agent_event()
        assert ev.metadata == {}


class TestTaskEventValidation:
    def test_missing_team_id_raises(self) -> None:
        with pytest.raises((ValidationError, TypeError)):
            TaskEvent(task_id="t-1", event="task.updated", status="running")  # type: ignore[call-arg]

    def test_missing_task_id_raises(self) -> None:
        with pytest.raises((ValidationError, TypeError)):
            TaskEvent(team_id="team-1", event="task.updated", status="running")  # type: ignore[call-arg]

    def test_metadata_defaults_to_empty_dict(self) -> None:
        ev = _make_task_event()
        assert ev.metadata == {}


class TestWebhookPayloadTimestamp:
    def test_timestamp_coerced_from_iso_string(self) -> None:
        p = WebhookPayload(event="x", data={}, timestamp="2026-03-15T00:00:00Z")
        assert isinstance(p.timestamp, datetime)

    def test_timestamp_accepts_datetime_object(self) -> None:
        ts = datetime(2026, 3, 15, tzinfo=timezone.utc)
        p = WebhookPayload(event="x", data={}, timestamp=ts)
        assert p.timestamp == ts


class TestPackageExports:
    def test_hermes_exports_agent_event(self) -> None:
        import hermes
        assert hasattr(hermes, "AgentEvent")

    def test_hermes_exports_task_event(self) -> None:
        import hermes
        assert hasattr(hermes, "TaskEvent")

    def test_hermes_exports_hermes_event_base(self) -> None:
        import hermes
        assert hasattr(hermes, "HermesEventBase")
