"""Tests verifying timeout configuration is respected."""

from __future__ import annotations

import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from hermes.config import Settings
from hermes.publisher import Publisher
from hermes.models import WebhookPayload


class TestTimeoutSettings:
    """Settings expose configurable timeouts with correct defaults."""

    def test_nats_connect_timeout_default(self) -> None:
        s = Settings()
        assert s.nats_connect_timeout == 5.0

    def test_nats_publish_timeout_default(self) -> None:
        s = Settings()
        assert s.nats_publish_timeout == 5.0

    def test_agamemnon_timeout_default(self) -> None:
        s = Settings()
        assert s.agamemnon_timeout == 10.0

    def test_nats_connect_timeout_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NATS_CONNECT_TIMEOUT", "15.0")
        s = Settings()
        assert s.nats_connect_timeout == 15.0

    def test_nats_publish_timeout_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NATS_PUBLISH_TIMEOUT", "3.0")
        s = Settings()
        assert s.nats_publish_timeout == 3.0

    def test_agamemnon_timeout_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AGAMEMNON_TIMEOUT", "20.0")
        s = Settings()
        assert s.agamemnon_timeout == 20.0


class TestPublisherConnectTimeout:
    """Publisher.connect() passes connect_timeout to nats.connect()."""

    @pytest.mark.asyncio
    async def test_connect_passes_timeout(self) -> None:
        pub = Publisher()
        mock_nc = MagicMock()
        mock_nc.jetstream.return_value = MagicMock()
        mock_jsm = AsyncMock()
        mock_jsm.find_stream = AsyncMock(return_value=MagicMock())
        mock_nc.jsm.return_value = mock_jsm

        with patch("nats.connect", new_callable=AsyncMock, return_value=mock_nc) as mock_connect:
            await pub.connect("nats://localhost:4222", connect_timeout=7.5)
            mock_connect.assert_awaited_once_with(
                "nats://localhost:4222", connect_timeout=7.5
            )

    @pytest.mark.asyncio
    async def test_connect_default_timeout(self) -> None:
        pub = Publisher()
        mock_nc = MagicMock()
        mock_nc.jetstream.return_value = MagicMock()
        mock_jsm = AsyncMock()
        mock_jsm.find_stream = AsyncMock(return_value=MagicMock())
        mock_nc.jsm.return_value = mock_jsm

        with patch("nats.connect", new_callable=AsyncMock, return_value=mock_nc) as mock_connect:
            await pub.connect("nats://localhost:4222")
            _, kwargs = mock_connect.call_args
            assert kwargs["connect_timeout"] == 5.0


class TestPublisherPublishTimeout:
    """Publisher.publish() passes timeout to JetStream publish."""

    def _make_payload(self) -> WebhookPayload:
        return WebhookPayload(
            event="agent.created",
            data={"host": "myhost", "name": "myagent"},
            timestamp="2026-01-01T00:00:00Z",
        )

    @pytest.mark.asyncio
    async def test_publish_passes_timeout(self) -> None:
        pub = Publisher()
        mock_js = AsyncMock()
        pub._js = mock_js

        await pub.publish(self._make_payload(), publish_timeout=2.5)
        mock_js.publish.assert_awaited_once()
        _, kwargs = mock_js.publish.call_args
        assert kwargs["timeout"] == 2.5

    @pytest.mark.asyncio
    async def test_publish_default_timeout(self) -> None:
        pub = Publisher()
        mock_js = AsyncMock()
        pub._js = mock_js

        await pub.publish(self._make_payload())
        _, kwargs = mock_js.publish.call_args
        assert kwargs["timeout"] == 5.0

    @pytest.mark.asyncio
    async def test_publish_raises_when_not_connected(self) -> None:
        pub = Publisher()
        with pytest.raises(RuntimeError, match="not connected"):
            await pub.publish(self._make_payload())
