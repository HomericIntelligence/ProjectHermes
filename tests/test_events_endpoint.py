"""Tests for the GET /events endpoint and the canonical event constants."""

from __future__ import annotations

import sys
import os
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _build_client() -> TestClient:
    from hermes.server import app
    from hermes.publisher import Publisher

    mock_publisher = MagicMock(spec=Publisher)
    mock_publisher.is_connected = True
    mock_publisher.active_subjects = []
    mock_publisher.publish = AsyncMock()

    app.state.publisher = mock_publisher
    return TestClient(app, raise_server_exceptions=True)


class TestEventsEndpoint:
    def test_events_returns_200(self) -> None:
        client = _build_client()
        response = client.get("/events")
        assert response.status_code == 200

    def test_events_has_agent_events_key(self) -> None:
        client = _build_client()
        body = client.get("/events").json()
        assert "agent_events" in body
        assert isinstance(body["agent_events"], list)

    def test_events_has_task_events_key(self) -> None:
        client = _build_client()
        body = client.get("/events").json()
        assert "task_events" in body
        assert isinstance(body["task_events"], list)

    def test_events_has_all_events_key(self) -> None:
        client = _build_client()
        body = client.get("/events").json()
        assert "all_events" in body
        assert isinstance(body["all_events"], list)

    def test_agent_events_content(self) -> None:
        client = _build_client()
        body = client.get("/events").json()
        assert set(body["agent_events"]) == {"agent.created", "agent.updated", "agent.deleted"}

    def test_task_events_content(self) -> None:
        client = _build_client()
        body = client.get("/events").json()
        assert set(body["task_events"]) == {"task.updated", "task.completed", "task.failed"}

    def test_all_events_is_union(self) -> None:
        client = _build_client()
        body = client.get("/events").json()
        expected = {"agent.created", "agent.updated", "agent.deleted",
                    "task.updated", "task.completed", "task.failed"}
        assert set(body["all_events"]) == expected

    def test_all_events_count(self) -> None:
        client = _build_client()
        body = client.get("/events").json()
        assert len(body["all_events"]) == 6
