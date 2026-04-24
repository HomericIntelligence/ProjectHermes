"""Tests for Settings configuration."""

from __future__ import annotations

import os
import sys

import pytest
from pydantic import ValidationError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


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

        s = Settings(hermes_port=9090, hermes_public_url="https://hermes.example.com", _env_file=None)
        assert s.hermes_public_url == "https://hermes.example.com"
