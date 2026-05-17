"""Tests for the lifespan handler's NATS connection retry behavior."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture()
def mock_publisher():
    from hermes.publisher import Publisher

    pub = MagicMock(spec=Publisher)
    pub.connect = AsyncMock()
    pub.disconnect = AsyncMock()
    pub.is_connected = True
    return pub


@pytest.mark.anyio
async def test_lifespan_success(mock_publisher: MagicMock) -> None:
    """Successful connect: connect() called once, publisher set on app state."""
    from hermes.server import lifespan, app

    with patch("hermes.server.Publisher", return_value=mock_publisher):
        async with lifespan(app):
            assert app.state.publisher is mock_publisher
            mock_publisher.connect.assert_awaited_once()


@pytest.mark.anyio
async def test_lifespan_retries_then_succeeds(mock_publisher: MagicMock) -> None:
    """connect() fails twice then succeeds; no exception propagated; error logged twice."""
    from hermes.server import lifespan, app

    side_effects = [RuntimeError("timeout")] * 2 + [None]
    mock_publisher.connect.side_effect = side_effects

    with (
        patch("hermes.server.Publisher", return_value=mock_publisher),
        patch("hermes.server.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        patch("hermes.server.logger") as mock_logger,
    ):
        async with lifespan(app):
            assert app.state.publisher is mock_publisher

        assert mock_publisher.connect.await_count == 3
        assert mock_sleep.await_count == 2
        assert mock_logger.error.call_count == 2
        mock_logger.critical.assert_not_called()


@pytest.mark.anyio
async def test_lifespan_all_retries_exhausted_starts_degraded(mock_publisher: MagicMock) -> None:
    """connect() always fails; lifespan starts in degraded mode (no raise); critical logged once."""
    from hermes.server import lifespan, app

    err = ConnectionRefusedError("NATS unreachable")
    mock_publisher.connect.side_effect = err
    mock_publisher.is_connected = False

    with (
        patch("hermes.server.Publisher", return_value=mock_publisher),
        patch("hermes.server.asyncio.sleep", new_callable=AsyncMock),
        patch("hermes.server.logger") as mock_logger,
    ):
        async with lifespan(app):
            # App starts in degraded mode — publisher is set on state
            assert app.state.publisher is mock_publisher

    mock_logger.critical.assert_called_once()
    assert mock_publisher.connect.await_count == 3


def test_lifespan_degraded_health_returns_503(mock_publisher: MagicMock) -> None:
    """When NATS fails to connect, /health returns 503 with degraded status."""
    from fastapi.testclient import TestClient
    from hermes.server import app

    err = OSError("NATS down")
    mock_publisher.connect.side_effect = err
    mock_publisher.is_connected = False
    mock_publisher.dead_letter_count = 0
    mock_publisher.reconnect_count = 0
    mock_publisher.last_error = ""
    mock_publisher.last_reconnect_attempt_at = None
    mock_publisher.consecutive_reconnect_failures = 0
    mock_publisher.reconnect_loop_running = False

    with (
        patch("hermes.server.Publisher", return_value=mock_publisher),
        patch("hermes.server.asyncio.sleep", new_callable=AsyncMock),
    ):
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/health")
            assert resp.status_code == 503
            body = resp.json()
            assert body["status"] == "degraded"
            assert body["nats_connected"] is False


@pytest.mark.anyio
async def test_lifespan_disconnect_called_on_shutdown(mock_publisher: MagicMock) -> None:
    """disconnect() is called when the lifespan context exits normally."""
    from hermes.server import lifespan, app

    with patch("hermes.server.Publisher", return_value=mock_publisher):
        async with lifespan(app):
            pass

    mock_publisher.disconnect.assert_awaited_once()


@pytest.mark.anyio
async def test_lifespan_error_log_includes_attempt_info(mock_publisher: MagicMock) -> None:
    """Each error log call contains attempt number and retry count."""
    from hermes.server import lifespan, app
    from hermes.config import get_settings

    mock_publisher.connect.side_effect = RuntimeError("boom")
    mock_publisher.is_connected = False

    with (
        patch("hermes.server.Publisher", return_value=mock_publisher),
        patch("hermes.server.asyncio.sleep", new_callable=AsyncMock),
        patch("hermes.server.logger") as mock_logger,
    ):
        async with lifespan(app):
            pass

    retry_attempts = get_settings().nats_retry_attempts
    for i, logged_call in enumerate(mock_logger.error.call_args_list, start=1):
        args = logged_call.args
        # First positional arg after the format string: attempt number
        assert args[1] == i
        assert args[2] == retry_attempts


@pytest.mark.anyio
async def test_lifespan_no_sleep_after_last_retry(mock_publisher: MagicMock) -> None:
    """Sleep is called only between attempts, not after the final failed attempt."""
    from hermes.config import get_settings
    from hermes.server import app, lifespan

    mock_publisher.connect.side_effect = RuntimeError("boom")

    with (
        patch("hermes.server.Publisher", return_value=mock_publisher),
        patch("hermes.server.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        patch("hermes.server.logger"),
    ):
        async with lifespan(app):
            pass

    assert mock_sleep.await_count == get_settings().nats_retry_attempts - 1


@pytest.mark.anyio
async def test_lifespan_last_error_log_omits_retry_message(mock_publisher: MagicMock) -> None:
    """The final error log must not claim a retry is coming."""
    from hermes.server import lifespan, app

    mock_publisher.connect.side_effect = RuntimeError("boom")

    with (
        patch("hermes.server.Publisher", return_value=mock_publisher),
        patch("hermes.server.asyncio.sleep", new_callable=AsyncMock),
        patch("hermes.server.logger") as mock_logger,
    ):
        async with lifespan(app):
            pass

    last_format_string: str = mock_logger.error.call_args_list[-1].args[0]
    # The final attempt must say "giving up", not "retrying"
    assert "retrying" not in last_format_string.lower()
    assert "giving up" in last_format_string.lower()


@pytest.mark.anyio
async def test_lifespan_warns_on_all_interfaces_bind(mock_publisher: MagicMock) -> None:
    """A WARNING is logged when hermes_host is 0.0.0.0."""
    from hermes.config import Settings
    from hermes.server import lifespan, app

    settings = Settings(hermes_host="0.0.0.0", _env_file=None)

    with (
        patch("hermes.server.Publisher", return_value=mock_publisher),
        patch("hermes.server.get_settings", return_value=settings),
        patch("hermes.server.logger") as mock_logger,
    ):
        async with lifespan(app):
            pass

    warning_messages = [call.args[0] for call in mock_logger.warning.call_args_list]
    assert any("0.0.0.0" in msg for msg in warning_messages)


@pytest.mark.anyio
async def test_lifespan_no_warn_on_loopback_bind(mock_publisher: MagicMock) -> None:
    """No 0.0.0.0 WARNING is logged when hermes_host is 127.0.0.1."""
    from hermes.config import Settings
    from hermes.server import lifespan, app

    settings = Settings(hermes_host="127.0.0.1", _env_file=None)

    with (
        patch("hermes.server.Publisher", return_value=mock_publisher),
        patch("hermes.server.get_settings", return_value=settings),
        patch("hermes.server.logger") as mock_logger,
    ):
        async with lifespan(app):
            pass

    warning_messages = [call.args[0] for call in mock_logger.warning.call_args_list]
    assert not any("0.0.0.0" in msg for msg in warning_messages)


@pytest.mark.anyio
async def test_last_retry_logs_giving_up_not_retrying(mock_publisher: MagicMock) -> None:
    """The final attempt logs 'giving up', not 'retrying in Xs'."""
    from hermes.server import lifespan, app

    mock_publisher.connect.side_effect = RuntimeError("boom")

    with (
        patch("hermes.server.Publisher", return_value=mock_publisher),
        patch("hermes.server.asyncio.sleep", new_callable=AsyncMock),
        patch("hermes.server.logger") as mock_logger,
    ):
        async with lifespan(app):
            pass

    last_error_call = mock_logger.error.call_args_list[-1]
    last_format_string: str = last_error_call.args[0]
    assert "retrying" not in last_format_string.lower()
    assert "giving up" in last_format_string.lower()


@pytest.mark.anyio
async def test_lifespan_warns_when_webhook_secret_unset(
    mock_publisher: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When WEBHOOK_SECRET is unset, lifespan must log the HMAC-disabled warning (#516)."""
    monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("WEBHOOK_SECRET", "")
    from hermes.config import get_settings
    from hermes.server import lifespan, app

    get_settings.cache_clear()  # type: ignore[attr-defined]

    with (
        patch("hermes.server.Publisher", return_value=mock_publisher),
        patch("hermes.server.logger") as mock_logger,
    ):
        async with lifespan(app):
            pass

    warning_messages = [c.args[0] for c in mock_logger.warning.call_args_list if c.args]
    assert any(
        "HMAC webhook validation is DISABLED" in msg for msg in warning_messages
    ), f"expected HMAC-disabled warning, got: {warning_messages!r}"

    get_settings.cache_clear()  # type: ignore[attr-defined]
