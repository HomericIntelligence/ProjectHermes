"""Tests for Publisher connection-state callbacks and _connected flag."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hermes.publisher import Publisher


class TestPublisherInitialState:
    def test_not_connected_before_connect(self) -> None:
        pub = Publisher()
        assert pub.is_connected is False


class TestPublisherConnectionCallbacks:
    @pytest.mark.asyncio
    async def test_connected_flag_set_on_connect(self) -> None:
        pub = Publisher()
        mock_nc = MagicMock()
        mock_nc.is_closed = False
        mock_nc.jetstream.return_value = MagicMock()
        mock_nc.jsm.return_value = AsyncMock()
        jsm = AsyncMock()
        jsm.find_stream = AsyncMock()
        mock_nc.jsm.return_value = jsm

        with patch("nats.connect", return_value=mock_nc) as mock_connect:
            await pub.connect("nats://localhost:4222")
            assert pub.is_connected is True
            mock_connect.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnected_cb_clears_connected_flag(self) -> None:
        pub = Publisher()
        mock_nc = MagicMock()
        mock_nc.is_closed = False
        mock_nc.jetstream.return_value = MagicMock()
        jsm = AsyncMock()
        jsm.find_stream = AsyncMock()
        mock_nc.jsm.return_value = jsm

        captured_callbacks: dict[str, object] = {}

        async def fake_connect(url: str, **kwargs: object) -> MagicMock:
            captured_callbacks.update(kwargs)
            return mock_nc

        with patch("nats.connect", side_effect=fake_connect):
            await pub.connect("nats://localhost:4222")

        assert pub.is_connected is True
        disconnected_cb = captured_callbacks["disconnected_cb"]
        await disconnected_cb()  # type: ignore[operator]
        assert pub.is_connected is False

    @pytest.mark.asyncio
    async def test_reconnected_cb_restores_connected_flag(self) -> None:
        pub = Publisher()
        mock_nc = MagicMock()
        mock_nc.is_closed = False
        mock_nc.jetstream.return_value = MagicMock()
        jsm = AsyncMock()
        jsm.find_stream = AsyncMock()
        mock_nc.jsm.return_value = jsm

        captured_callbacks: dict[str, object] = {}

        async def fake_connect(url: str, **kwargs: object) -> MagicMock:
            captured_callbacks.update(kwargs)
            return mock_nc

        with patch("nats.connect", side_effect=fake_connect):
            await pub.connect("nats://localhost:4222")

        # Simulate disconnect then reconnect
        await captured_callbacks["disconnected_cb"]()  # type: ignore[operator]
        assert pub.is_connected is False
        await captured_callbacks["reconnected_cb"]()  # type: ignore[operator]
        assert pub.is_connected is True

    @pytest.mark.asyncio
    async def test_reconnected_cb_does_not_increment_reconnect_count(self) -> None:
        """Regression for issue #526.

        The nats-py ``reconnected_cb`` must NOT increment ``reconnect_count``.
        The ``_reconnect_loop`` success path is the sole incrementer; otherwise
        a successful reconnect would be counted twice if nats-py ever fires the
        callback (e.g. if ``allow_reconnect`` is changed in the future).
        """
        pub = Publisher()
        mock_nc = MagicMock()
        mock_nc.is_closed = False
        mock_nc.jetstream.return_value = MagicMock()
        jsm = AsyncMock()
        jsm.find_stream = AsyncMock()
        mock_nc.jsm.return_value = jsm

        captured_callbacks: dict[str, object] = {}

        async def fake_connect(url: str, **kwargs: object) -> MagicMock:
            captured_callbacks.update(kwargs)
            return mock_nc

        with patch("nats.connect", side_effect=fake_connect):
            await pub.connect("nats://localhost:4222")

        assert pub.reconnect_count == 0
        await captured_callbacks["reconnected_cb"]()  # type: ignore[operator]
        assert pub.reconnect_count == 0
        await captured_callbacks["reconnected_cb"]()  # type: ignore[operator]
        assert pub.reconnect_count == 0
        # The callback still restores the connected flag.
        assert pub.is_connected is True

    @pytest.mark.asyncio
    async def test_disconnected_cb_sets_last_error(self) -> None:
        pub = Publisher()
        mock_nc = MagicMock()
        mock_nc.is_closed = False
        mock_nc.jetstream.return_value = MagicMock()
        jsm = AsyncMock()
        jsm.find_stream = AsyncMock()
        mock_nc.jsm.return_value = jsm

        captured_callbacks: dict[str, object] = {}

        async def fake_connect(url: str, **kwargs: object) -> MagicMock:
            captured_callbacks.update(kwargs)
            return mock_nc

        with patch("nats.connect", side_effect=fake_connect):
            await pub.connect("nats://localhost:4222")

        assert pub.last_error == ""
        await captured_callbacks["disconnected_cb"]()  # type: ignore[operator]
        assert pub.last_error == "NATS disconnected"

    @pytest.mark.asyncio
    async def test_disconnect_method_clears_connected_flag(self) -> None:
        pub = Publisher()
        mock_nc = MagicMock()
        mock_nc.is_closed = False
        mock_nc.jetstream.return_value = MagicMock()
        mock_nc.drain = AsyncMock()
        jsm = AsyncMock()
        jsm.find_stream = AsyncMock()
        mock_nc.jsm.return_value = jsm

        with patch("nats.connect", return_value=mock_nc):
            await pub.connect("nats://localhost:4222")

        assert pub.is_connected is True
        await pub.disconnect()
        assert pub.is_connected is False
