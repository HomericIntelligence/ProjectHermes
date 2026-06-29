# SPDX-License-Identifier: MIT
"""Tests for request_id in log context (issue #228) and error responses (issue #230)."""

from __future__ import annotations

import json
import logging
import uuid

import pytest

from tests.helpers import TEST_SECRET, sign_body


# ---------------------------------------------------------------------------
# Issue #228 — request_id in structured log extra
# ---------------------------------------------------------------------------


class TestRequestIdInLogContext:
    """Verify that request_id appears in extra fields of log records."""

    def test_invalid_payload_log_contains_request_id(
        self, caplog: pytest.LogCaptureFixture, make_test_client
    ) -> None:
        """A 422 from invalid payload should emit a log record with request_id in extra."""
        client = make_test_client(raise_server_exceptions=False)
        body_bytes = json.dumps({"bad": "payload"}).encode()
        fixed_id = str(uuid.uuid4())

        with caplog.at_level(logging.WARNING, logger="hermes.server"):
            client.post(
                "/webhook",
                content=body_bytes,
                headers={
                    "Content-Type": "application/json",
                    "X-Webhook-Signature": sign_body(body_bytes, TEST_SECRET),
                    "X-Request-ID": fixed_id,
                },
            )

        records_with_id = [r for r in caplog.records if getattr(r, "request_id", None) == fixed_id]
        assert records_with_id, (
            f"Expected at least one log record with request_id={fixed_id!r}; "
            f"got records: {[vars(r) for r in caplog.records]}"
        )

    def test_nats_not_connected_log_contains_request_id(
        self, caplog: pytest.LogCaptureFixture, make_test_client
    ) -> None:
        """A 503 from NATS disconnected should emit a log record with request_id in extra."""
        client = make_test_client(connected=False, raise_server_exceptions=False)
        payload = {
            "event": "agent.created",
            "data": {"host": "h", "name": "n"},
            "timestamp": "2026-01-01T00:00:00Z",
        }
        body_bytes = json.dumps(payload).encode()
        fixed_id = str(uuid.uuid4())

        with caplog.at_level(logging.ERROR, logger="hermes.server"):
            client.post(
                "/webhook",
                content=body_bytes,
                headers={
                    "Content-Type": "application/json",
                    "X-Webhook-Signature": sign_body(body_bytes, TEST_SECRET),
                    "X-Request-ID": fixed_id,
                },
            )

        records_with_id = [r for r in caplog.records if getattr(r, "request_id", None) == fixed_id]
        assert records_with_id, (
            f"Expected at least one log record with request_id={fixed_id!r}; "
            f"got records: {[vars(r) for r in caplog.records]}"
        )

    def test_publish_timeout_log_contains_request_id(
        self, caplog: pytest.LogCaptureFixture, make_test_client
    ) -> None:
        """A 503 from publish timeout should emit a log record with request_id in extra."""
        import asyncio

        client = make_test_client(
            publish_side_effect=asyncio.TimeoutError(), raise_server_exceptions=False
        )
        payload = {
            "event": "agent.created",
            "data": {"host": "h", "name": "n"},
            "timestamp": "2026-01-01T00:00:00Z",
        }
        body_bytes = json.dumps(payload).encode()
        fixed_id = str(uuid.uuid4())

        with caplog.at_level(logging.ERROR, logger="hermes.server"):
            client.post(
                "/webhook",
                content=body_bytes,
                headers={
                    "Content-Type": "application/json",
                    "X-Webhook-Signature": sign_body(body_bytes, TEST_SECRET),
                    "X-Request-ID": fixed_id,
                },
            )

        records_with_id = [r for r in caplog.records if getattr(r, "request_id", None) == fixed_id]
        assert records_with_id, f"Expected at least one log record with request_id={fixed_id!r}"


# ---------------------------------------------------------------------------
# Issue #230 — request_id in error response bodies
# ---------------------------------------------------------------------------


class TestRequestIdInErrorResponses:
    """Verify that request_id is included in HTTP error response bodies."""

    def test_422_error_body_contains_request_id(self, make_test_client) -> None:
        """Invalid payload 422 response body must include request_id."""
        client = make_test_client(raise_server_exceptions=False)
        body_bytes = json.dumps({"bad": "payload"}).encode()
        fixed_id = str(uuid.uuid4())

        response = client.post(
            "/webhook",
            content=body_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Signature": sign_body(body_bytes, TEST_SECRET),
                "X-Request-ID": fixed_id,
            },
        )
        assert response.status_code == 422
        data = response.json()
        assert "request_id" in data
        assert data["request_id"] == fixed_id

    def test_401_error_body_contains_request_id(self, make_test_client) -> None:
        """Bad signature 401 response body must include request_id."""
        client = make_test_client(raise_server_exceptions=False)
        payload = {
            "event": "agent.created",
            "data": {"host": "h", "name": "n"},
            "timestamp": "2026-01-01T00:00:00Z",
        }
        body_bytes = json.dumps(payload).encode()
        fixed_id = str(uuid.uuid4())

        response = client.post(
            "/webhook",
            content=body_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Signature": "bad-sig",
                "X-Request-ID": fixed_id,
            },
        )
        assert response.status_code == 401
        data = response.json()
        assert "request_id" in data
        assert data["request_id"] == fixed_id

    def test_503_nats_disconnected_body_contains_request_id(self, make_test_client) -> None:
        """NATS disconnected 503 response body must include request_id."""
        client = make_test_client(connected=False, raise_server_exceptions=False)
        payload = {
            "event": "agent.created",
            "data": {"host": "h", "name": "n"},
            "timestamp": "2026-01-01T00:00:00Z",
        }
        body_bytes = json.dumps(payload).encode()
        fixed_id = str(uuid.uuid4())

        response = client.post(
            "/webhook",
            content=body_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Signature": sign_body(body_bytes, TEST_SECRET),
                "X-Request-ID": fixed_id,
            },
        )
        assert response.status_code == 503
        data = response.json()
        assert "request_id" in data
        assert data["request_id"] == fixed_id

    def test_503_publish_timeout_body_contains_request_id(self, make_test_client) -> None:
        """Publish timeout 503 response body must include request_id."""
        import asyncio

        client = make_test_client(
            publish_side_effect=asyncio.TimeoutError(), raise_server_exceptions=False
        )
        payload = {
            "event": "agent.created",
            "data": {"host": "h", "name": "n"},
            "timestamp": "2026-01-01T00:00:00Z",
        }
        body_bytes = json.dumps(payload).encode()
        fixed_id = str(uuid.uuid4())

        response = client.post(
            "/webhook",
            content=body_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Signature": sign_body(body_bytes, TEST_SECRET),
                "X-Request-ID": fixed_id,
            },
        )
        assert response.status_code == 503
        data = response.json()
        assert "request_id" in data
        assert data["request_id"] == fixed_id

    def test_error_body_contains_detail(self, make_test_client) -> None:
        """Error bodies must still contain the original detail field."""
        client = make_test_client(raise_server_exceptions=False)
        body_bytes = json.dumps({"bad": "payload"}).encode()

        response = client.post(
            "/webhook",
            content=body_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Signature": sign_body(body_bytes, TEST_SECRET),
            },
        )
        assert response.status_code == 422
        data = response.json()
        assert "detail" in data
        assert isinstance(data["detail"], str)

    def test_generated_request_id_appears_in_error_body(self, make_test_client) -> None:
        """When no X-Request-ID is sent, a generated UUID should appear in error body."""
        client = make_test_client(raise_server_exceptions=False)
        body_bytes = json.dumps({"bad": "payload"}).encode()

        response = client.post(
            "/webhook",
            content=body_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Signature": sign_body(body_bytes, TEST_SECRET),
            },
        )
        assert response.status_code == 422
        data = response.json()
        assert "request_id" in data
        # Should be a valid UUID (auto-generated)
        returned_id = data["request_id"]
        assert returned_id is not None
        uuid.UUID(returned_id)  # raises ValueError if not a valid UUID
        # Should match the X-Request-ID response header
        assert response.headers.get("X-Request-ID") == returned_id
