"""Shared test utilities for ProjectHermes tests."""

from __future__ import annotations

import hashlib
import hmac as hmac_mod

TEST_SECRET = "test-webhook-secret-padding-xxxxx"


def sign_body(body: bytes, secret: str) -> str:
    """Return the HMAC-SHA256 hex digest of *body* signed with *secret*."""
    return hmac_mod.new(secret.encode(), body, hashlib.sha256).hexdigest()
