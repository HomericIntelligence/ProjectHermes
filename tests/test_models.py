"""Tests for HermesEventBase and WebhookPayload models."""

from __future__ import annotations

import sys
import os
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from hermes.models import WebhookAcceptedResponse, WebhookPayload


class TestWebhookPayloadTimestamp:
    def test_timestamp_coerced_from_iso_string(self) -> None:
        p = WebhookPayload(event="x", data={}, timestamp="2026-03-15T00:00:00Z")
        assert isinstance(p.timestamp, datetime)

    def test_timestamp_accepts_datetime_object(self) -> None:
        ts = datetime(2026, 3, 15, tzinfo=timezone.utc)
        p = WebhookPayload(event="x", data={}, timestamp=ts)
        assert p.timestamp == ts


class TestWebhookAcceptedResponse:
    def test_requires_request_id(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            WebhookAcceptedResponse(status="accepted", event="agent.created")

    def test_model_dump_includes_request_id(self) -> None:
        r = WebhookAcceptedResponse(
            status="accepted", event="agent.created", request_id="test-id-123"
        )
        dumped = r.model_dump()
        assert dumped["request_id"] == "test-id-123"
        assert dumped["status"] == "accepted"
        assert dumped["event"] == "agent.created"


class TestPackageExports:
    def test_hermes_exports_hermes_event_base(self) -> None:
        import hermes

        assert hasattr(hermes, "HermesEventBase")

    def test_hermes_does_not_export_agent_event(self) -> None:
        import hermes

        assert not hasattr(hermes, "AgentEvent")

    def test_hermes_does_not_export_task_event(self) -> None:
        import hermes

        assert not hasattr(hermes, "TaskEvent")
