"""Tests for Settings configuration."""

from __future__ import annotations

import pytest
from pydantic import ValidationError


class TestHermesHostDefault:
    def test_default_host_is_localhost(self) -> None:
        """HERMES_HOST must default to 127.0.0.1, not 0.0.0.0."""
        from hermes.config import Settings

        s = Settings()
        assert s.hermes_host == "127.0.0.1"

    def test_host_reads_from_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """HERMES_HOST env var overrides the default."""
        monkeypatch.setenv("HERMES_HOST", "0.0.0.0")
        from hermes.config import Settings

        s = Settings()
        assert s.hermes_host == "0.0.0.0"

    def test_host_env_var_is_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Settings are case-insensitive per model_config."""
        monkeypatch.setenv("hermes_host", "192.168.1.1")
        from hermes.config import Settings

        s = Settings()
        assert s.hermes_host == "192.168.1.1"

    def test_default_host_is_not_all_interfaces(self) -> None:
        """Sanity check: the insecure 0.0.0.0 must not be the default."""
        from hermes.config import Settings

        s = Settings()
        assert s.hermes_host != "0.0.0.0"


class TestHermesHostValidation:
    def test_rejects_invalid_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HERMES_HOST", "not@valid!")
        from hermes.config import Settings

        with pytest.raises(ValidationError):
            Settings()

    def test_accepts_ipv4_address(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HERMES_HOST", "192.168.1.1")
        from hermes.config import Settings

        s = Settings()
        assert s.hermes_host == "192.168.1.1"

    def test_accepts_localhost(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HERMES_HOST", "localhost")
        from hermes.config import Settings

        s = Settings()
        assert s.hermes_host == "localhost"

    def test_accepts_all_interfaces(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HERMES_HOST", "0.0.0.0")
        from hermes.config import Settings

        s = Settings()
        assert s.hermes_host == "0.0.0.0"

    def test_accepts_fqdn(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HERMES_HOST", "my-host.example.com")
        from hermes.config import Settings

        s = Settings()
        assert s.hermes_host == "my-host.example.com"

    def test_accepts_ipv6_address(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HERMES_HOST", "::1")
        from hermes.config import Settings

        s = Settings()
        assert s.hermes_host == "::1"


class TestGetSettingsEnvOverride:
    """Issue #136 — get_settings() must reflect HERMES_HOST env-var override."""

    def test_get_settings_reflects_hermes_host_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """monkeypatch HERMES_HOST and verify get_settings() picks it up."""
        monkeypatch.setenv("HERMES_HOST", "127.0.0.1")
        from hermes.config import get_settings

        assert get_settings().hermes_host == "127.0.0.1"


class TestHermesPublicUrl:
    def test_public_url_defaults_to_localhost_port(self) -> None:
        from hermes.config import Settings

        s = Settings(hermes_port=8080, _env_file=None)
        assert s.hermes_public_url == "http://localhost:8080"

    def test_public_url_respects_custom_port(self) -> None:
        from hermes.config import Settings

        s = Settings(hermes_port=9000, _env_file=None)
        assert s.hermes_public_url == "http://localhost:9000"

    def test_public_url_explicit_override(self) -> None:
        from hermes.config import Settings

        s = Settings(hermes_public_url="https://hermes.example.com", _env_file=None)
        assert s.hermes_public_url == "https://hermes.example.com"

    def test_public_url_explicit_override_ignores_port(self) -> None:
        from hermes.config import Settings

        s = Settings(
            hermes_port=9090, hermes_public_url="https://hermes.example.com", _env_file=None
        )
        assert s.hermes_public_url == "https://hermes.example.com"

    def test_public_url_strips_trailing_slash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HERMES_PUBLIC_URL", "http://example.com/")
        from hermes.config import Settings

        s = Settings()
        assert not s.hermes_public_url.endswith("/")
        assert s.hermes_public_url == "http://example.com"

    def test_public_url_rejects_invalid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HERMES_PUBLIC_URL", "not-a-url")
        from hermes.config import Settings
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            Settings()

    def test_public_url_accepts_with_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HERMES_PUBLIC_URL", "https://example.com/webhooks")
        from hermes.config import Settings

        s = Settings()
        assert s.hermes_public_url == "https://example.com/webhooks"


class TestWebhookRateLimit:
    def test_default_rate_limit_is_valid(self) -> None:
        from hermes.config import Settings

        s = Settings(_env_file=None)
        assert s.webhook_rate_limit == "60/minute"

    def test_rejects_invalid_rate_limit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WEBHOOK_RATE_LIMIT", "not-valid")
        from hermes.config import Settings

        with pytest.raises(ValidationError):
            Settings()

    def test_accepts_valid_rate_limit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WEBHOOK_RATE_LIMIT", "50/minute")
        from hermes.config import Settings

        s = Settings()
        assert "50" in s.webhook_rate_limit

    def test_accepts_per_second(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WEBHOOK_RATE_LIMIT", "10/second")
        from hermes.config import Settings

        s = Settings()
        assert s.webhook_rate_limit == "10/second"

    def test_accepts_per_hour(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WEBHOOK_RATE_LIMIT", "1000/hour")
        from hermes.config import Settings

        s = Settings()
        assert s.webhook_rate_limit == "1000/hour"

    def test_rejects_missing_period(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WEBHOOK_RATE_LIMIT", "100")
        from hermes.config import Settings

        with pytest.raises(ValidationError):
            Settings()

    def test_rejects_invalid_period(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WEBHOOK_RATE_LIMIT", "100/week")
        from hermes.config import Settings

        with pytest.raises(ValidationError):
            Settings()


class TestSubjectsRateLimit:
    def test_default_subjects_rate_limit_is_valid(self) -> None:
        from hermes.config import Settings

        s = Settings(_env_file=None)
        assert s.subjects_rate_limit == "60/minute"

    def test_subjects_rate_limit_rejects_invalid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SUBJECTS_RATE_LIMIT", "not-valid")
        from hermes.config import Settings

        with pytest.raises(ValidationError):
            Settings()

    def test_subjects_rate_limit_accepts_valid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SUBJECTS_RATE_LIMIT", "50/minute")
        from hermes.config import Settings

        s = Settings()
        assert s.subjects_rate_limit == "50/minute"

    def test_subjects_rate_limit_accepts_per_second(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SUBJECTS_RATE_LIMIT", "10/second")
        from hermes.config import Settings

        s = Settings()
        assert s.subjects_rate_limit == "10/second"

    def test_subjects_rate_limit_accepts_per_hour(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SUBJECTS_RATE_LIMIT", "1000/hour")
        from hermes.config import Settings

        s = Settings()
        assert s.subjects_rate_limit == "1000/hour"

    def test_subjects_rate_limit_rejects_missing_period(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SUBJECTS_RATE_LIMIT", "100")
        from hermes.config import Settings

        with pytest.raises(ValidationError):
            Settings()

    def test_subjects_rate_limit_rejects_invalid_period(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SUBJECTS_RATE_LIMIT", "100/week")
        from hermes.config import Settings

        with pytest.raises(ValidationError):
            Settings()


class TestWebhookSecretProductionWarning:
    """WEBHOOK_SECRET unset + HERMES_HOST=0.0.0.0 must emit a loud warning."""

    def test_no_warning_when_secret_set_and_host_0000(self) -> None:
        from unittest.mock import patch

        import hermes.config as cfg
        from hermes.config import Settings

        with patch.object(cfg._config_logger, "warning") as mock_warn:
            Settings(
                webhook_secret="a" * 32,
                hermes_host="0.0.0.0",
                _env_file=None,
            )
        for call in mock_warn.call_args_list:
            assert "WEBHOOK_SECRET is NOT SET" not in (call.args[0] if call.args else "")

    def test_no_warning_when_secret_empty_and_host_localhost(self) -> None:
        from unittest.mock import patch

        import hermes.config as cfg
        from hermes.config import Settings

        with patch.object(cfg._config_logger, "warning") as mock_warn:
            Settings(
                webhook_secret="",
                hermes_host="127.0.0.1",
                _env_file=None,
            )
        for call in mock_warn.call_args_list:
            assert "WEBHOOK_SECRET is NOT SET" not in (call.args[0] if call.args else "")

    def test_loud_warning_when_secret_empty_and_host_0000(self) -> None:
        from unittest.mock import patch

        import hermes.config as cfg
        from hermes.config import Settings

        with patch.object(cfg._config_logger, "warning") as mock_warn:
            Settings(
                webhook_secret="",
                hermes_host="0.0.0.0",
                _env_file=None,
            )
        warning_messages = [call.args[0] for call in mock_warn.call_args_list if call.args]
        assert any("WEBHOOK_SECRET is NOT SET" in msg for msg in warning_messages)


class TestDeadLetterKeyProductionWarning:
    """DEAD_LETTER_API_KEY unset + HERMES_HOST=0.0.0.0 must emit a warning; otherwise silent (#519)."""

    def test_no_warning_when_key_set_and_host_0000(self) -> None:
        from unittest.mock import patch

        import hermes.config as cfg
        from hermes.config import Settings

        with patch.object(cfg._config_logger, "warning") as mock_warn:
            Settings(
                dead_letter_api_key="a" * 32,
                hermes_host="0.0.0.0",
                webhook_secret="b" * 32,
                _env_file=None,
            )
        for call in mock_warn.call_args_list:
            assert "DEAD_LETTER_API_KEY is not set" not in (call.args[0] if call.args else "")

    def test_no_warning_when_key_empty_and_host_localhost(self) -> None:
        """Regression guard for #519: no warning when bound to loopback (test/dev default)."""
        from unittest.mock import patch

        import hermes.config as cfg
        from hermes.config import Settings

        with patch.object(cfg._config_logger, "warning") as mock_warn:
            Settings(
                dead_letter_api_key="",
                hermes_host="127.0.0.1",
                _env_file=None,
            )
        for call in mock_warn.call_args_list:
            assert "DEAD_LETTER_API_KEY is not set" not in (call.args[0] if call.args else "")

    def test_loud_warning_when_key_empty_and_host_0000(self) -> None:
        from unittest.mock import patch

        import hermes.config as cfg
        from hermes.config import Settings

        with patch.object(cfg._config_logger, "warning") as mock_warn:
            Settings(
                dead_letter_api_key="",
                hermes_host="0.0.0.0",
                webhook_secret="b" * 32,
                _env_file=None,
            )
        warning_messages = [call.args[0] for call in mock_warn.call_args_list if call.args]
        assert any("DEAD_LETTER_API_KEY is not set" in msg for msg in warning_messages)


class TestAgamemnonFieldsRemoved:
    """Regression guard: AGAMEMNON_URL/API_KEY are gone from Settings (#449)."""

    def test_settings_has_no_agamemnon_url(self) -> None:
        from hermes.config import Settings

        assert not hasattr(Settings(), "agamemnon_url"), (
            "Settings.agamemnon_url was removed; re-introducing it would break "
            "the decoupled-from-Agamemnon contract (see #449)."
        )

    def test_settings_has_no_agamemnon_api_key(self) -> None:
        from hermes.config import Settings

        assert not hasattr(Settings(), "agamemnon_api_key"), (
            "Settings.agamemnon_api_key was removed; re-introducing it would "
            "break the decoupled-from-Agamemnon contract (see #449)."
        )
