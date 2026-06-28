"""Tests for src/hermes/__main__.py entry point."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from hermes.__main__ import _parse_args, main  # noqa: E402


class TestParseArgs:
    def test_defaults(self) -> None:
        args = _parse_args([])
        assert args.host == "0.0.0.0"
        assert args.log_level == "info"
        assert args.reload is False

    def test_reload_flag(self) -> None:
        args = _parse_args(["--reload"])
        assert args.reload is True

    def test_custom_host(self) -> None:
        args = _parse_args(["--host", "127.0.0.1"])
        assert args.host == "127.0.0.1"

    def test_custom_port(self) -> None:
        args = _parse_args(["--port", "9000"])
        assert args.port == 9000

    def test_custom_log_level(self) -> None:
        args = _parse_args(["--log-level", "debug"])
        assert args.log_level == "debug"

    @pytest.mark.parametrize("level", ["debug", "info", "warning", "error", "critical"])
    def test_all_log_levels_accepted(self, level: str) -> None:
        args = _parse_args(["--log-level", level])
        assert args.log_level == level

    def test_invalid_log_level_raises(self) -> None:
        with pytest.raises(SystemExit):
            _parse_args(["--log-level", "verbose"])

    def test_port_default_matches_settings(self) -> None:
        from hermes.config import get_settings

        args = _parse_args([])
        assert args.port == get_settings().hermes_port


class TestMain:
    def test_main_calls_uvicorn_run(self) -> None:
        mock_uvicorn = MagicMock()
        with patch.dict("sys.modules", {"uvicorn": mock_uvicorn}):
            main(["--port", "9090", "--host", "127.0.0.1", "--log-level", "warning"])

        mock_uvicorn.run.assert_called_once_with(
            "hermes.server:app",
            host="127.0.0.1",
            port=9090,
            log_level="warning",
            reload=False,
        )

    def test_main_default_args(self) -> None:
        from hermes.config import get_settings

        mock_uvicorn = MagicMock()
        with patch.dict("sys.modules", {"uvicorn": mock_uvicorn}):
            main([])

        mock_uvicorn.run.assert_called_once_with(
            "hermes.server:app",
            host="0.0.0.0",
            port=get_settings().hermes_port,
            log_level="info",
            reload=False,
        )

    def test_main_reload_flag(self) -> None:
        mock_uvicorn = MagicMock()
        with patch.dict("sys.modules", {"uvicorn": mock_uvicorn}):
            main(["--reload"])

        mock_uvicorn.run.assert_called_once_with(
            "hermes.server:app",
            host="0.0.0.0",
            port=mock_uvicorn.run.call_args[1]["port"],
            log_level="info",
            reload=True,
        )

    def test_main_no_reload_flag(self) -> None:
        mock_uvicorn = MagicMock()
        with patch.dict("sys.modules", {"uvicorn": mock_uvicorn}):
            main([])

        _, kwargs = mock_uvicorn.run.call_args
        assert kwargs.get("reload") is False

    def test_main_calls_setup_logging(self) -> None:
        mock_uvicorn = MagicMock()
        with (
            patch.dict("sys.modules", {"uvicorn": mock_uvicorn}),
            patch("hermes.__main__.setup_logging") as mock_setup,
        ):
            main([])

        mock_setup.assert_called_once_with(level=logging.INFO, json_format=False)

    def test_main_log_json_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LOG_JSON", "true")
        mock_uvicorn = MagicMock()
        with (
            patch.dict("sys.modules", {"uvicorn": mock_uvicorn}),
            patch("hermes.__main__.setup_logging") as mock_setup,
            patch("hermes.__main__.get_settings") as mock_get_settings,
        ):
            mock_settings = MagicMock()
            mock_settings.log_json = True
            mock_settings.hermes_port = 8080
            mock_get_settings.return_value = mock_settings
            main([])

        mock_setup.assert_called_once_with(level=logging.INFO, json_format=True)

    def test_main_log_level_forwarded(self) -> None:
        mock_uvicorn = MagicMock()
        with (
            patch.dict("sys.modules", {"uvicorn": mock_uvicorn}),
            patch("hermes.__main__.setup_logging") as mock_setup,
        ):
            main(["--log-level", "debug"])

        mock_setup.assert_called_once_with(level=logging.DEBUG, json_format=False)

    def test_main_basicconfig_not_called(self) -> None:
        mock_uvicorn = MagicMock()
        with (
            patch.dict("sys.modules", {"uvicorn": mock_uvicorn}),
            patch("hermes.__main__.setup_logging"),
            patch("logging.basicConfig") as mock_basicconfig,
        ):
            main([])

        mock_basicconfig.assert_not_called()
