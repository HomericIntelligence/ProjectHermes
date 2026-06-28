"""Tests for the startup banner logging."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestMaskSecret:
    def test_empty_string_returns_not_set(self) -> None:
        from hermes.server import _mask_secret

        assert _mask_secret("") == "(not set)"

    def test_short_value_fully_masked(self) -> None:
        from hermes.server import _mask_secret

        assert _mask_secret("abc") == "****"

    def test_exact_show_chars_fully_masked(self) -> None:
        from hermes.server import _mask_secret

        assert _mask_secret("abcd") == "****"

    def test_longer_value_shows_prefix(self) -> None:
        from hermes.server import _mask_secret

        assert _mask_secret("abcdefgh") == "abcd****"

    def test_zero_show_chars_fully_masks(self) -> None:
        from hermes.server import _mask_secret

        assert _mask_secret("supersecret", show_chars=0) == "****"

    def test_custom_show_chars(self) -> None:
        from hermes.server import _mask_secret

        assert _mask_secret("abcdefgh", show_chars=2) == "ab****"


class TestLogStartupBanner:
    def _make_publisher(
        self,
        is_connected: bool = True,
        stream_names: list[str] | None = None,
    ) -> MagicMock:
        from hermes.publisher import Publisher

        mock = MagicMock(spec=Publisher)
        mock.is_connected = is_connected
        mock.stream_names = (
            stream_names if stream_names is not None else ["homeric-agents", "homeric-tasks"]
        )
        return mock

    def test_banner_logs_version(self) -> None:
        from hermes import __version__
        from hermes.server import _log_startup_banner

        publisher = self._make_publisher()
        with patch("hermes.server.logger") as mock_logger:
            _log_startup_banner(publisher)

        all_calls = mock_logger.info.call_args_list
        first_call = all_calls[0]
        assert "version=%s" in first_call.args[0]
        assert __version__ in first_call.args[1:]

    def test_banner_logs_nats_url(self) -> None:
        from hermes.server import _log_startup_banner
        from hermes.config import get_settings

        settings = get_settings()
        publisher = self._make_publisher()
        with patch("hermes.server.logger") as mock_logger:
            _log_startup_banner(publisher, settings)

        all_info_args = [str(c) for c in mock_logger.info.call_args_list]
        assert any(settings.nats_url in a for a in all_info_args)

    def test_banner_logs_port(self) -> None:
        from hermes.server import _log_startup_banner
        from hermes.config import get_settings

        settings = get_settings()
        publisher = self._make_publisher()
        with patch("hermes.server.logger") as mock_logger:
            _log_startup_banner(publisher, settings)

        all_info_args = [str(c) for c in mock_logger.info.call_args_list]
        assert any(str(settings.hermes_port) in a for a in all_info_args)

    def test_banner_masks_webhook_secret(self) -> None:
        from hermes.server import _log_startup_banner
        from hermes.config import Settings

        secret = "abcdefgh" + "x" * 24  # pad to 32 chars to pass validation
        settings = Settings(webhook_secret=secret)
        publisher = self._make_publisher()
        with patch("hermes.server.logger") as mock_logger:
            _log_startup_banner(publisher, settings)

        all_info_args = [str(c) for c in mock_logger.info.call_args_list]
        assert any("abcd****" in a for a in all_info_args)
        assert not any(secret in a for a in all_info_args)

    def test_banner_shows_not_set_for_empty_webhook_secret(self) -> None:
        from hermes.server import _log_startup_banner
        from hermes.config import Settings

        settings = Settings(webhook_secret="")
        publisher = self._make_publisher()
        with patch("hermes.server.logger") as mock_logger:
            _log_startup_banner(publisher, settings)

        all_info_args = [str(c) for c in mock_logger.info.call_args_list]
        assert any("(not set)" in a for a in all_info_args)

    def test_banner_shows_hmac_enabled(self) -> None:
        from hermes.server import _log_startup_banner
        from hermes.config import Settings

        settings = Settings(webhook_secret="mysecret" + "x" * 24)  # pad to 32 chars
        publisher = self._make_publisher()
        with patch("hermes.server.logger") as mock_logger:
            _log_startup_banner(publisher, settings)

        all_info_args = [str(c) for c in mock_logger.info.call_args_list]
        assert any("enabled" in a for a in all_info_args)

    def test_banner_shows_hmac_disabled(self) -> None:
        from hermes.server import _log_startup_banner
        from hermes.config import Settings

        settings = Settings(webhook_secret="")
        publisher = self._make_publisher()
        with patch("hermes.server.logger") as mock_logger:
            _log_startup_banner(publisher, settings)

        all_info_args = [str(c) for c in mock_logger.info.call_args_list]
        assert any("disabled" in a for a in all_info_args)

    def test_banner_masks_dead_letter_api_key(self) -> None:
        from hermes.server import _log_startup_banner
        from hermes.config import Settings

        key = "wxyz1234" + "k" * 24  # >= 32 chars to pass validation
        settings = Settings(dead_letter_api_key=key)
        publisher = self._make_publisher()
        with patch("hermes.server.logger") as mock_logger:
            _log_startup_banner(publisher, settings)

        all_info_args = [str(c) for c in mock_logger.info.call_args_list]
        assert any("wxyz****" in a for a in all_info_args)
        assert not any(key in a for a in all_info_args)

    def test_banner_shows_not_set_for_empty_dead_letter_api_key(self) -> None:
        from hermes.server import _log_startup_banner
        from hermes.config import Settings

        settings = Settings(dead_letter_api_key="")
        publisher = self._make_publisher()
        with patch("hermes.server.logger") as mock_logger:
            _log_startup_banner(publisher, settings)

        all_info_args = [str(c) for c in mock_logger.info.call_args_list]
        assert any("(not set)" in a for a in all_info_args)

    def test_banner_shows_dead_letter_auth_enabled(self) -> None:
        from hermes.server import _log_startup_banner
        from hermes.config import Settings

        settings = Settings(dead_letter_api_key="k" * 32)
        publisher = self._make_publisher()
        with patch("hermes.server.logger") as mock_logger:
            _log_startup_banner(publisher, settings)

        all_info_args = [str(c) for c in mock_logger.info.call_args_list]
        assert any("dead_letter_auth" in a and "enabled" in a for a in all_info_args)

    def test_banner_shows_dead_letter_auth_disabled(self) -> None:
        from hermes.server import _log_startup_banner
        from hermes.config import Settings

        settings = Settings(dead_letter_api_key="")
        publisher = self._make_publisher()
        with patch("hermes.server.logger") as mock_logger:
            _log_startup_banner(publisher, settings)

        all_info_args = [str(c) for c in mock_logger.info.call_args_list]
        assert any("dead_letter_auth" in a and "disabled" in a for a in all_info_args)

    def test_banner_logs_nats_connected_true(self) -> None:
        from hermes.server import _log_startup_banner

        publisher = self._make_publisher(is_connected=True)
        with patch("hermes.server.logger") as mock_logger:
            _log_startup_banner(publisher)

        all_info_args = [str(c) for c in mock_logger.info.call_args_list]
        assert any("True" in a for a in all_info_args)

    def test_banner_logs_nats_connected_false(self) -> None:
        from hermes.server import _log_startup_banner

        publisher = self._make_publisher(is_connected=False)
        with patch("hermes.server.logger") as mock_logger:
            _log_startup_banner(publisher)

        all_info_args = [str(c) for c in mock_logger.info.call_args_list]
        assert any("False" in a for a in all_info_args)

    def test_banner_logs_stream_names(self) -> None:
        from hermes.server import _log_startup_banner

        streams = ["homeric-agents", "homeric-tasks"]
        publisher = self._make_publisher(stream_names=streams)
        with patch("hermes.server.logger") as mock_logger:
            _log_startup_banner(publisher)

        all_info_args = [str(c) for c in mock_logger.info.call_args_list]
        assert any("homeric-agents" in a for a in all_info_args)
        assert any("homeric-tasks" in a for a in all_info_args)

    def test_banner_logs_empty_streams_when_not_connected(self) -> None:
        from hermes.server import _log_startup_banner

        publisher = self._make_publisher(is_connected=False, stream_names=[])
        with patch("hermes.server.logger") as mock_logger:
            _log_startup_banner(publisher)

        # Should still log the nats line — just with empty list
        assert mock_logger.info.call_count >= 4
