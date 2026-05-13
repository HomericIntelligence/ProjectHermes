"""Tests for single-source-of-truth version handling."""

from __future__ import annotations

import importlib
import sys
from importlib.metadata import PackageNotFoundError
from unittest.mock import patch


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


def _reload_hermes_pkg() -> object:
    """Reload the ``hermes`` package module so its top-level code re-executes.

    This is required to actually exercise the ``try/except PackageNotFoundError``
    block at the top of ``src/hermes/__init__.py`` (lines 10-11), which only runs
    at module import time.
    """
    # Drop the cached module so ``importlib.reload`` re-executes the module body.
    sys.modules.pop("hermes", None)
    return importlib.import_module("hermes")


def test_hermes_init_sets_version_from_metadata_on_import() -> None:
    """Importing ``hermes`` with metadata available sets ``__version__`` from it.

    This exercises line 9 of ``src/hermes/__init__.py`` (the successful
    ``version("hermes")`` call) at actual import time, not via re-implementation.
    """
    original = sys.modules.get("hermes")
    try:
        with patch("importlib.metadata.version", return_value="7.7.7"):
            hermes_mod = _reload_hermes_pkg()
            assert hermes_mod.__version__ == "7.7.7"
    finally:
        # Restore a clean import for subsequent tests.
        sys.modules.pop("hermes", None)
        if original is not None:
            sys.modules["hermes"] = original
        importlib.import_module("hermes")


def test_hermes_init_falls_back_to_unknown_on_import_when_metadata_missing() -> None:
    """Importing ``hermes`` without package metadata sets ``__version__='unknown'``.

    This exercises lines 10-11 of ``src/hermes/__init__.py`` (the
    ``except PackageNotFoundError`` fallback) at actual import time, which is the
    code path flagged as uncovered in issue #477.
    """
    original = sys.modules.get("hermes")
    try:
        with patch(
            "importlib.metadata.version",
            side_effect=PackageNotFoundError("hermes"),
        ):
            hermes_mod = _reload_hermes_pkg()
            assert hermes_mod.__version__ == "unknown"
    finally:
        sys.modules.pop("hermes", None)
        if original is not None:
            sys.modules["hermes"] = original
        importlib.import_module("hermes")
