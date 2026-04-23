"""Tests for Settings configuration, focusing on HERMES_HOST."""

from __future__ import annotations

import os
import sys

import pytest

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
