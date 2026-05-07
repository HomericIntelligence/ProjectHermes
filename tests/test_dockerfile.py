"""Regression tests ensuring the Dockerfile uses pinned/versioned pip dependencies."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).parent.parent
DOCKERFILE = ROOT / "Dockerfile"
PYPROJECT = ROOT / "pyproject.toml"

_VERSION_SPEC_RE = re.compile(r"[><=!~]")
_PIP_INSTALL_RE = re.compile(r"pip install\b")


def _dockerfile_lines() -> list[str]:
    return DOCKERFILE.read_text().splitlines()


def _pyproject_deps() -> list[str]:
    with PYPROJECT.open("rb") as f:
        data = tomllib.load(f)
    return data["project"]["dependencies"]


def test_dockerfile_exists() -> None:
    assert DOCKERFILE.exists(), "Dockerfile not found at repo root"


def test_pyproject_has_dependencies_table() -> None:
    """pyproject.toml must declare [project.dependencies] for the Docker build."""
    deps = _pyproject_deps()
    assert deps, "[project.dependencies] in pyproject.toml must not be empty"


def test_pyproject_dependencies_all_versioned() -> None:
    """Every entry in [project.dependencies] must include a version specifier."""
    for dep in _pyproject_deps():
        # Strip extras like [standard] before checking for version spec
        base = re.sub(r"\[.*?\]", "", dep)
        assert _VERSION_SPEC_RE.search(base), (
            f"pyproject.toml dependency '{dep}' has no version specifier"
        )


def test_dockerfile_copies_pyproject_before_pip_install() -> None:
    """COPY pyproject.toml must appear before the RUN pip install line in the builder stage."""
    lines = _dockerfile_lines()
    copy_idx = next(
        (i for i, ln in enumerate(lines) if "COPY pyproject.toml" in ln), None
    )
    pip_idx = next(
        (i for i, ln in enumerate(lines) if _PIP_INSTALL_RE.search(ln)), None
    )
    assert copy_idx is not None, "Dockerfile has no 'COPY pyproject.toml' line"
    assert pip_idx is not None, "Dockerfile has no 'pip install' line"
    assert copy_idx < pip_idx, (
        "COPY pyproject.toml must appear before RUN pip install in the Dockerfile"
    )


def test_dockerfile_pip_install_uses_tomllib_extraction() -> None:
    """The pip install command must extract deps from pyproject.toml via tomllib, not hardcode them."""
    text = DOCKERFILE.read_text()
    assert "tomllib" in text, (
        "Dockerfile pip install must use tomllib to extract deps from pyproject.toml"
    )
    assert "project" in text and "dependencies" in text, (
        "Dockerfile tomllib extraction must reference project.dependencies"
    )


def test_dockerfile_no_bare_unversioned_pip_packages() -> None:
    """No pip install line should list bare package names without version specifiers."""
    text = DOCKERFILE.read_text()
    # Find any pip install ... line that contains a plain package token (no version spec)
    # and is NOT using tomllib (i.e. a hardcoded bare name).
    # We check that every pip install invocation either uses tomllib or only versioned specs.
    for line in text.splitlines():
        if not _PIP_INSTALL_RE.search(line):
            continue
        # If the line uses tomllib substitution, it's acceptable — deps come from pyproject.toml
        if "tomllib" in line or "$(" in line:
            continue
        # Otherwise, extract package tokens and assert each has a version spec
        tokens = line.split()
        for token in tokens:
            if token.startswith("-") or token in ("pip", "install", "run", "RUN", "python3", "\\"):
                continue
            # A bare package name with no specifier is a violation
            base = re.sub(r"\[.*?\]", "", token)
            assert _VERSION_SPEC_RE.search(base), (
                f"Dockerfile has unversioned package '{token}' in pip install line: {line!r}"
            )
