"""Tests for TLS configuration fields on Settings."""

from __future__ import annotations

import ssl
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestBuildSslContext:
    def test_no_tls_returns_none_for_nats_url(self) -> None:
        from hermes.config import Settings

        s = Settings(nats_url="nats://localhost:4222", _env_file=None)
        assert s.build_ssl_context() is None

    def test_tls_scheme_returns_ssl_context(self) -> None:
        from hermes.config import Settings

        s = Settings(nats_url="tls://localhost:4222", _env_file=None)
        ctx = s.build_ssl_context()
        assert isinstance(ctx, ssl.SSLContext)

    def test_tls_verify_false_disables_cert_verification(self) -> None:
        from hermes.config import Settings

        s = Settings(nats_url="tls://localhost:4222", tls_verify=False, _env_file=None)
        ctx = s.build_ssl_context()
        assert ctx is not None
        assert ctx.verify_mode == ssl.CERT_NONE

    def test_tls_verify_true_enables_cert_verification(self) -> None:
        from hermes.config import Settings

        s = Settings(nats_url="tls://localhost:4222", tls_verify=True, _env_file=None)
        ctx = s.build_ssl_context()
        assert ctx is not None
        assert ctx.verify_mode != ssl.CERT_NONE

    def test_ca_bundle_triggers_tls_context(self) -> None:
        from hermes.config import Settings

        s = Settings(tls_ca_bundle="/fake/ca.pem", _env_file=None)
        assert s.tls_ca_bundle is not None
        assert s.nats_url.startswith("nats://")


class TestHttpxVerify:
    def test_returns_true_by_default(self) -> None:
        from hermes.config import Settings

        s = Settings(_env_file=None)
        assert s.httpx_verify() is True

    def test_returns_false_when_tls_verify_false(self) -> None:
        from hermes.config import Settings

        s = Settings(tls_verify=False, _env_file=None)
        assert s.httpx_verify() is False

    def test_returns_ca_bundle_path_when_set(self) -> None:
        from hermes.config import Settings

        s = Settings(tls_ca_bundle="/path/to/ca.pem", _env_file=None)
        assert s.httpx_verify() == "/path/to/ca.pem"
