# SPDX-License-Identifier: MIT
"""Tests for dead-letter queue limits, TTL config, gauge, and alerting (issue #347)."""

from __future__ import annotations

from collections import deque
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from hermes.config import Settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_client(items: list[dict]) -> TestClient:
    from hermes.publisher import Publisher
    from hermes.server import app

    mock_publisher = MagicMock(spec=Publisher)
    mock_publisher.is_connected = True
    mock_publisher.active_subjects = []
    mock_publisher.dead_letters = items
    app.state.publisher = mock_publisher
    return TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------


class TestDeadLetterConfigDefaults:
    """Verify the new config fields have sane defaults."""

    def test_dead_letter_max_size_default(self) -> None:
        s = Settings()
        assert s.dead_letter_max_size == 1000

    def test_dead_letter_ttl_seconds_default(self) -> None:
        s = Settings()
        assert s.dead_letter_ttl_seconds == 86400

    def test_dead_letter_alert_threshold_default(self) -> None:
        s = Settings()
        assert s.dead_letter_alert_threshold == 0.8

    def test_dead_letter_page_size_default_default(self) -> None:
        s = Settings()
        assert s.dead_letter_page_size_default == 100

    def test_dead_letter_page_size_max_default(self) -> None:
        s = Settings()
        assert s.dead_letter_page_size_max == 500


# ---------------------------------------------------------------------------
# Publisher: config-driven deque size
# ---------------------------------------------------------------------------


class TestPublisherDequeSize:
    """Deque capacity is driven by dead_letter_max_size config."""

    def test_deque_maxlen_reflects_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from hermes.config import get_settings
        from hermes.publisher import Publisher

        monkeypatch.setenv("DEAD_LETTER_MAX_SIZE", "42")
        get_settings.cache_clear()
        pub = Publisher()
        assert pub._dead_letters.maxlen == 42

    def test_deque_default_maxlen_is_1000(self) -> None:
        from hermes.publisher import Publisher

        pub = Publisher()
        assert pub._dead_letters.maxlen == 1000


# ---------------------------------------------------------------------------
# Publisher: queue-depth gauge and alert counter
# ---------------------------------------------------------------------------


class TestDeadLetterQueueGauge:
    """DEAD_LETTER_QUEUE_DEPTH gauge is updated on enqueue and drain."""

    @pytest.mark.asyncio
    async def test_gauge_updated_after_enqueue(self) -> None:
        from datetime import datetime, timezone

        from hermes.metrics import DEAD_LETTER_QUEUE_DEPTH
        from hermes.models import WebhookPayload
        from hermes.publisher import Publisher

        pub = Publisher()
        pub._js = AsyncMock()
        pub._js.publish = AsyncMock()

        payload = WebhookPayload(
            event="unknown.event",
            data={},
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        await pub.publish(payload)
        assert DEAD_LETTER_QUEUE_DEPTH._value.get() == 1.0

    @pytest.mark.asyncio
    async def test_gauge_reset_to_zero_after_drain(self) -> None:
        from hermes.metrics import DEAD_LETTER_QUEUE_DEPTH
        from hermes.publisher import Publisher

        pub = Publisher()
        pub._dead_letters = deque([{"event": "a"}, {"event": "b"}], maxlen=1000)
        pub.drain_dead_letters()
        assert DEAD_LETTER_QUEUE_DEPTH._value.get() == 0.0


class TestDeadLetterAlertCounter:
    """DEAD_LETTER_QUEUE_ALERTS counter increments when threshold is crossed."""

    @pytest.mark.asyncio
    async def test_alert_counter_increments_at_threshold(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from datetime import datetime, timezone

        from hermes.config import get_settings
        from hermes.metrics import DEAD_LETTER_QUEUE_ALERTS
        from hermes.models import WebhookPayload
        from hermes.publisher import Publisher

        # Use a small queue so we hit the threshold quickly.
        monkeypatch.setenv("DEAD_LETTER_MAX_SIZE", "5")
        monkeypatch.setenv("DEAD_LETTER_ALERT_THRESHOLD", "0.8")
        get_settings.cache_clear()

        before = DEAD_LETTER_QUEUE_ALERTS._value.get()

        pub = Publisher()
        pub._js = AsyncMock()
        pub._js.publish = AsyncMock()

        def _make_payload(name: str) -> WebhookPayload:
            return WebhookPayload(
                event=f"unknown.{name}",
                data={},
                timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )

        # Enqueue 4 items — the 4th should cross 80 % of 5.
        for i in range(4):
            await pub.publish(_make_payload(str(i)))

        after = DEAD_LETTER_QUEUE_ALERTS._value.get()
        assert after > before

    @pytest.mark.asyncio
    async def test_no_alert_below_threshold(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from datetime import datetime, timezone

        from hermes.config import get_settings
        from hermes.metrics import DEAD_LETTER_QUEUE_ALERTS
        from hermes.models import WebhookPayload
        from hermes.publisher import Publisher

        monkeypatch.setenv("DEAD_LETTER_MAX_SIZE", "100")
        monkeypatch.setenv("DEAD_LETTER_ALERT_THRESHOLD", "0.8")
        get_settings.cache_clear()

        before = DEAD_LETTER_QUEUE_ALERTS._value.get()

        pub = Publisher()
        pub._js = AsyncMock()
        pub._js.publish = AsyncMock()

        payload = WebhookPayload(
            event="unknown.single",
            data={},
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        await pub.publish(payload)

        after = DEAD_LETTER_QUEUE_ALERTS._value.get()
        assert after == before


# ---------------------------------------------------------------------------
# Publisher: _ensure_streams passes max_age for dead-letter stream
# ---------------------------------------------------------------------------


class TestEnsureStreamsDeadLetterTTL:
    """_ensure_streams passes max_age to the homeric-deadletter stream when TTL is set."""

    @pytest.mark.asyncio
    async def test_max_age_passed_when_ttl_nonzero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from nats.js.errors import NotFoundError

        from hermes.config import get_settings
        from hermes.publisher import Publisher

        monkeypatch.setenv("DEAD_LETTER_TTL_SECONDS", "3600")
        get_settings.cache_clear()

        pub = Publisher()
        mock_nc = MagicMock()
        mock_jsm = AsyncMock()
        mock_jsm.stream_info = AsyncMock(side_effect=NotFoundError)
        mock_jsm.add_stream = AsyncMock()
        mock_nc.jsm = MagicMock(return_value=mock_jsm)
        pub._nc = mock_nc

        await pub._ensure_streams()

        calls = mock_jsm.add_stream.call_args_list
        dead_letter_call = next(c for c in calls if c.args[0].name == "homeric-deadletter")
        cfg = dead_letter_call.args[0]
        assert cfg.max_age == timedelta(seconds=3600).total_seconds()

    @pytest.mark.asyncio
    async def test_no_max_age_when_ttl_is_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from nats.js.errors import NotFoundError

        from hermes.config import get_settings
        from hermes.publisher import Publisher

        monkeypatch.setenv("DEAD_LETTER_TTL_SECONDS", "0")
        get_settings.cache_clear()

        pub = Publisher()
        mock_nc = MagicMock()
        mock_jsm = AsyncMock()
        mock_jsm.stream_info = AsyncMock(side_effect=NotFoundError)
        mock_jsm.add_stream = AsyncMock()
        mock_nc.jsm = MagicMock(return_value=mock_jsm)
        pub._nc = mock_nc

        await pub._ensure_streams()

        calls = mock_jsm.add_stream.call_args_list
        dead_letter_call = next(c for c in calls if c.args[0].name == "homeric-deadletter")
        cfg = dead_letter_call.args[0]
        assert cfg.max_age is None


# ---------------------------------------------------------------------------
# Publisher: _ensure_streams updates existing dead-letter stream TTL (#530)
# ---------------------------------------------------------------------------


class TestEnsureStreamsDeadLetterTTLMigration:
    """_ensure_streams calls update_stream when the existing TTL differs (#530)."""

    @pytest.mark.asyncio
    async def test_update_called_when_existing_max_age_differs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from hermes.config import get_settings
        from hermes.publisher import Publisher

        monkeypatch.setenv("DEAD_LETTER_TTL_SECONDS", "3600")
        get_settings.cache_clear()

        pub = Publisher()
        mock_nc = MagicMock()
        mock_jsm = AsyncMock()

        # Existing stream returns a different max_age (e.g. previously 1800).
        # stream_info must succeed (no NotFoundError) so the update path is taken.
        existing_info = MagicMock()
        existing_info.config = MagicMock(max_age=1800.0)

        async def _stream_info(name: str) -> MagicMock:
            return existing_info

        mock_jsm.stream_info = AsyncMock(side_effect=_stream_info)
        mock_jsm.add_stream = AsyncMock()
        mock_jsm.update_stream = AsyncMock()
        mock_nc.jsm = MagicMock(return_value=mock_jsm)
        pub._nc = mock_nc

        await pub._ensure_streams()

        # add_stream should NOT be called for an existing stream
        assert mock_jsm.add_stream.call_args_list == []

        # update_stream must be called for the homeric-deadletter stream with
        # the desired max_age (3600s).
        update_calls = mock_jsm.update_stream.call_args_list
        deadletter_updates = [c for c in update_calls if c.args[0].name == "homeric-deadletter"]
        assert len(deadletter_updates) == 1
        cfg = deadletter_updates[0].args[0]
        assert cfg.max_age == timedelta(seconds=3600).total_seconds()

    @pytest.mark.asyncio
    async def test_update_skipped_when_existing_max_age_matches(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from hermes.config import get_settings
        from hermes.publisher import Publisher

        monkeypatch.setenv("DEAD_LETTER_TTL_SECONDS", "3600")
        get_settings.cache_clear()

        pub = Publisher()
        mock_nc = MagicMock()
        mock_jsm = AsyncMock()

        # Existing stream already has the desired max_age — no update needed.
        existing_info = MagicMock()
        existing_info.config = MagicMock(max_age=float(timedelta(seconds=3600).total_seconds()))

        # homeric-agents / homeric-tasks have no extra config (max_age == 0/None).
        # Return matching info for those too so update_stream is never called.
        zero_info = MagicMock()
        zero_info.config = MagicMock(max_age=0)

        async def _stream_info(name: str) -> MagicMock:
            return existing_info if name == "homeric-deadletter" else zero_info

        mock_jsm.stream_info = AsyncMock(side_effect=_stream_info)
        mock_jsm.add_stream = AsyncMock()
        mock_jsm.update_stream = AsyncMock()
        mock_nc.jsm = MagicMock(return_value=mock_jsm)
        pub._nc = mock_nc

        await pub._ensure_streams()

        assert mock_jsm.update_stream.call_args_list == []
        assert mock_jsm.add_stream.call_args_list == []


# ---------------------------------------------------------------------------
# Server: GET /dead-letters default and max limit enforcement
# ---------------------------------------------------------------------------


class TestDeadLettersDefaultLimit:
    """GET /dead-letters applies the configured default page size when limit is omitted."""

    def test_default_limit_applied_when_no_limit_param(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from hermes.config import get_settings

        monkeypatch.setenv("DEAD_LETTER_PAGE_SIZE_DEFAULT", "3")
        get_settings.cache_clear()

        items = [{"event": f"evt.{i}", "subject": f"hi.deadletter.evt-{i}"} for i in range(10)]
        client = _build_client(items)
        body = client.get("/dead-letters").json()
        assert len(body["items"]) == 3
        assert body["limit"] == 3
        assert body["total"] == 10

    def test_explicit_limit_overrides_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from hermes.config import get_settings

        monkeypatch.setenv("DEAD_LETTER_PAGE_SIZE_DEFAULT", "3")
        monkeypatch.setenv("DEAD_LETTER_PAGE_SIZE_MAX", "500")
        get_settings.cache_clear()

        items = [{"event": f"evt.{i}", "subject": f"hi.deadletter.evt-{i}"} for i in range(10)]
        client = _build_client(items)
        body = client.get("/dead-letters?limit=5").json()
        assert len(body["items"]) == 5
        assert body["limit"] == 5


class TestDeadLettersMaxLimitEnforcement:
    """GET /dead-letters returns HTTP 400 when limit exceeds the configured maximum."""

    def test_limit_exceeding_max_returns_400(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from hermes.config import get_settings

        monkeypatch.setenv("DEAD_LETTER_PAGE_SIZE_MAX", "50")
        get_settings.cache_clear()

        client = _build_client([])
        resp = client.get("/dead-letters?limit=51")
        assert resp.status_code == 400

    def test_limit_at_max_is_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from hermes.config import get_settings

        monkeypatch.setenv("DEAD_LETTER_PAGE_SIZE_MAX", "50")
        get_settings.cache_clear()

        items = [{"event": f"evt.{i}", "subject": f"hi.deadletter.evt-{i}"} for i in range(10)]
        client = _build_client(items)
        resp = client.get("/dead-letters?limit=50")
        assert resp.status_code == 200

    def test_default_limit_within_max_is_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from hermes.config import get_settings

        monkeypatch.setenv("DEAD_LETTER_PAGE_SIZE_DEFAULT", "100")
        monkeypatch.setenv("DEAD_LETTER_PAGE_SIZE_MAX", "500")
        get_settings.cache_clear()

        client = _build_client([])
        resp = client.get("/dead-letters")
        assert resp.status_code == 200

    def test_error_detail_mentions_max(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from hermes.config import get_settings

        monkeypatch.setenv("DEAD_LETTER_PAGE_SIZE_MAX", "50")
        get_settings.cache_clear()

        client = _build_client([])
        body = client.get("/dead-letters?limit=999").json()
        assert "50" in body["detail"]
        assert "999" in body["detail"]
