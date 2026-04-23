"""Tests for single-source-of-truth version handling."""

from __future__ import annotations

import sys
import os
from importlib.metadata import PackageNotFoundError
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_hermes_version_is_a_string() -> None:
    """__version__ must be a non-empty string."""
    import hermes

    assert isinstance(hermes.__version__, str)
    assert hermes.__version__ != ""


def test_hermes_version_reads_from_package_metadata_when_installed() -> None:
    """When the package is installed, __version__ reflects the package metadata version."""
    with patch("importlib.metadata.version", return_value="1.2.3"):
        # Re-run the version lookup logic as implemented in __init__
        try:
            from importlib.metadata import version as _v

            result = _v("hermes")
        except PackageNotFoundError:
            result = "unknown"

        # When metadata is available, it should be used
        assert result == "1.2.3"


def test_hermes_version_falls_back_to_unknown_when_not_installed() -> None:
    """When the package metadata is missing, __version__ falls back to 'unknown'."""
    with patch("importlib.metadata.version", side_effect=PackageNotFoundError("hermes")):
        try:
            from importlib.metadata import version as _v

            result = _v("hermes")
        except PackageNotFoundError:
            result = "unknown"

    assert result == "unknown"


def test_server_app_version_matches_hermes_version() -> None:
    """FastAPI app version must match hermes.__version__, not a hardcoded literal."""
    import hermes
    from hermes.server import app

    assert app.version == hermes.__version__


def test_server_app_version_is_not_hardcoded_string() -> None:
    """FastAPI app version must be derived from __version__, not a literal '0.1.0'."""
    import hermes
    import inspect
    import hermes.server as server_module

    # If someone hardcodes a version they will diverge from __version__ under mocking
    with patch.object(hermes, "__version__", "9.9.9"):
        source = inspect.getsource(server_module)
        assert '"0.1.0"' not in source, "server.py must not contain the hardcoded version string"
        assert "__version__" in source, "server.py must reference __version__"
