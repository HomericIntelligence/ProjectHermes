"""Guard that openapi.json matches the current FastAPI app schema (#434).

Without this test, the Spectral lint added in issue #434 could pass on a
stale openapi.json while the live FastAPI app generates a different spec.
"""
from __future__ import annotations

import json
from pathlib import Path

from hermes.server import app


def test_openapi_json_matches_app_schema() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    committed = json.loads((repo_root / "openapi.json").read_text(encoding="utf-8"))
    assert app.openapi() == committed, (
        "openapi.json is out of date — run `just export-openapi` and commit."
    )
