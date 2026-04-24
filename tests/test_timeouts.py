"""Tests verifying timeout configuration is respected."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from hermes.config import Settings
from hermes.models import WebhookPayload
from hermes.publisher import Publisher


class TestTimeoutSettings:
    """Settings expose configurable timeouts with correct defaults."""

    def test_nats_connect_timeout_default(self) -> None:
        """nats_connect_timeout defaults to 5.0 seconds."""
        s = Settings()
        assert s.nats_connect_timeout == 5.0

    def test_nats_publish_timeout_default(self) -> None:
        """nats_publish_timeout defaults to 5.0 seconds."""
        s = Settings()
        assert s.nats_publish_timeout == 5.0

    def test_agamemnon_timeout_default(self) -> None:
        """agamemnon_timeout defaults to 10.0 seconds."""
        s = Settings()
        assert s.agamemnon_timeout == 10.0

    def test_nats_connect_timeout_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """nats_connect_timeout can be overridden via environment variable."""
        monkeypatch.setenv("NATS_CONNECT_TIMEOUT", "15.0")
        s = Settings()
        assert s.nats_connect_timeout == 15.0

    def test_nats_publish_timeout_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """nats_publish_timeout can be overridden via environment variable."""
        monkeypatch.setenv("NATS_PUBLISH_TIMEOUT", "3.0")
        s = Settings()
        assert s.nats_publish_timeout == 3.0

    def test_agamemnon_timeout_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """agamemnon_timeout can be overridden via environment variable."""
        monkeypatch.setenv("AGAMEMNON_TIMEOUT", "20.0")
        s = Settings()
        assert s.agamemnon_timeout == 20.0


    def test_nats_retry_attempts_default(self) -> None:
        """nats_retry_attempts defaults to 3."""
        s = Settings()
        assert s.nats_retry_attempts == 3

    def test_nats_retry_interval_default(self) -> None:
        """nats_retry_interval defaults to 5.0 seconds."""
        s = Settings()
        assert s.nats_retry_interval == 5.0

    def test_nats_retry_attempts_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """nats_retry_attempts can be overridden via NATS_RETRY_ATTEMPTS."""
        monkeypatch.setenv("NATS_RETRY_ATTEMPTS", "5")
        s = Settings()
        assert s.nats_retry_attempts == 5

    def test_nats_retry_interval_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """nats_retry_interval can be overridden via NATS_RETRY_INTERVAL."""
        monkeypatch.setenv("NATS_RETRY_INTERVAL", "10.0")
        s = Settings()
        assert s.nats_retry_interval == 10.0

    def test_nats_retry_attempts_minimum_is_one(self) -> None:
        """nats_retry_attempts rejects values below 1."""
        import pytest as _pytest
        with _pytest.raises(Exception):
            Settings(nats_retry_attempts=0)

    def test_nats_retry_interval_must_be_positive(self) -> None:
        """nats_retry_interval rejects non-positive values."""
        import pytest as _pytest
        with _pytest.raises(Exception):
            Settings(nats_retry_interval=0.0)


class TestPublisherConnectTimeout:
    """Publisher.connect() passes connect_timeout to nats.connect()."""

    @pytest.mark.asyncio
    async def test_connect_passes_timeout(self) -> None:
        """connect_timeout kwarg is forwarded to nats.connect."""
        pub = Publisher()
        mock_nc = MagicMock()
        mock_nc.jetstream.return_value = MagicMock()
        mock_jsm = AsyncMock()
        mock_jsm.find_stream = AsyncMock(return_value=MagicMock())
        mock_nc.jsm.return_value = mock_jsm

        with patch("nats.connect", new_callable=AsyncMock, return_value=mock_nc) as mock_connect:
            await pub.connect("nats://localhost:4222", connect_timeout=7.5)
            _, kwargs = mock_connect.call_args
            assert kwargs["connect_timeout"] == 7.5

    @pytest.mark.asyncio
    async def test_connect_default_timeout(self) -> None:
        """connect_timeout defaults to 5.0 when not specified."""
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
        """Return a minimal WebhookPayload for use in publish tests."""
        return WebhookPayload(
            event="agent.created",
            data={"host": "myhost", "name": "myagent"},
            timestamp="2026-01-01T00:00:00Z",
        )

    @pytest.mark.asyncio
    async def test_publish_passes_timeout(self) -> None:
        """publish_timeout kwarg is forwarded to JetStream publish."""
        pub = Publisher()
        mock_js = AsyncMock()
        pub._js = mock_js

        await pub.publish(self._make_payload(), publish_timeout=2.5)
        mock_js.publish.assert_awaited_once()
        _, kwargs = mock_js.publish.call_args
        assert kwargs["timeout"] == 2.5

    @pytest.mark.asyncio
    async def test_publish_default_timeout(self) -> None:
        """publish_timeout defaults to 5.0 when not specified."""
        pub = Publisher()
        mock_js = AsyncMock()
        pub._js = mock_js

        await pub.publish(self._make_payload())
        _, kwargs = mock_js.publish.call_args
        assert kwargs["timeout"] == 5.0

    @pytest.mark.asyncio
    async def test_publish_raises_when_not_connected(self) -> None:
        """Publish raises RuntimeError when the publisher is not connected."""
        pub = Publisher()
        with pytest.raises(RuntimeError, match="not connected"):
            await pub.publish(self._make_payload())


class TestTimeoutValidation:
    def test_nats_connect_timeout_rejects_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NATS_CONNECT_TIMEOUT", "0")
        with pytest.raises(ValidationError):
            Settings()

    def test_nats_connect_timeout_rejects_negative(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NATS_CONNECT_TIMEOUT", "-1")
        with pytest.raises(ValidationError):
            Settings()

    def test_nats_publish_timeout_rejects_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NATS_PUBLISH_TIMEOUT", "0")
        with pytest.raises(ValidationError):
            Settings()

    def test_nats_publish_timeout_rejects_negative(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NATS_PUBLISH_TIMEOUT", "-5")
        with pytest.raises(ValidationError):
            Settings()

    def test_agamemnon_timeout_rejects_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AGAMEMNON_TIMEOUT", "0")
        with pytest.raises(ValidationError):
            Settings()

    def test_agamemnon_timeout_rejects_negative(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AGAMEMNON_TIMEOUT", "-0.1")
        with pytest.raises(ValidationError):
            Settings()
