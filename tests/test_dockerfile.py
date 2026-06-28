"""Regression tests ensuring the Dockerfile uses pinned/versioned pip dependencies and signal forwarding."""

from __future__ import annotations

import pathlib
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).parent.parent
DOCKERFILE = ROOT / "Dockerfile"
PYPROJECT = ROOT / "pyproject.toml"
_DOCKERFILE = pathlib.Path(__file__).parent.parent / "Dockerfile"

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
    copy_idx = next((i for i, ln in enumerate(lines) if "COPY pyproject.toml" in ln), None)
    pip_idx = next((i for i, ln in enumerate(lines) if _PIP_INSTALL_RE.search(ln)), None)
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


_SHELL_SEPARATORS = {"&&", "||", ";", "|", "&"}
_CMD_WORDS = {"pip", "install", "run", "RUN", "python3", "python", "\\"}
_FILE_FLAGS = {
    "-r", "-c", "--requirement", "--constraint", "-t", "--target", "--prefix",
    "--root", "--cache-dir", "--index-url", "--extra-index-url", "--find-links", "-f",
}


def _assert_pip_line_versioned(line: str) -> None:
    """Assert every package token on a single pip install line carries a version spec.

    Skip flags, command words, shell separators, and the value following pip's
    file/constraint flags (-r/-c/--requirement/--constraint), which are filenames.
    """
    skip_next = False
    for token in line.split():
        if skip_next:
            skip_next = False
            continue
        if token in _FILE_FLAGS:
            skip_next = True
            continue
        if (
            token.startswith(("--requirement=", "--constraint=", "-"))
            or token in _CMD_WORDS
            or token in _SHELL_SEPARATORS
        ):
            continue
        # A bare package name with no specifier is a violation.
        base = re.sub(r"\[.*?\]", "", token)
        assert _VERSION_SPEC_RE.search(base), (
            f"Dockerfile has unversioned package '{token}' in pip install line: {line!r}"
        )


def test_dockerfile_no_bare_unversioned_pip_packages() -> None:
    """No pip install line should list bare package names without version specifiers."""
    text = DOCKERFILE.read_text()
    # Check that every pip install invocation either uses tomllib or only versioned specs.
    for line in text.splitlines():
        # Skip comment lines: prose may mention "pip install" without being an
        # actual install invocation (e.g. the --no-deps explanatory note).
        if line.lstrip().startswith("#"):
            continue
        if not _PIP_INSTALL_RE.search(line):
            continue
        # tomllib substitution is acceptable — deps come from pyproject.toml.
        if "tomllib" in line or "$(" in line:
            continue
        _assert_pip_line_versioned(line)


class TestDockerfileTini:
    def test_tini_installed(self) -> None:
        lines = _dockerfile_lines()
        assert any("tini" in line for line in lines), (
            "Dockerfile must install tini for PID-1 signal forwarding"
        )

    def test_entrypoint_uses_tini(self) -> None:
        lines = _dockerfile_lines()
        entrypoint_lines = [ln for ln in lines if ln.strip().startswith("ENTRYPOINT")]
        assert entrypoint_lines, "Dockerfile must have an ENTRYPOINT directive"
        assert "tini" in entrypoint_lines[-1], (
            'ENTRYPOINT must use tini (e.g. ENTRYPOINT ["tini", "--"])'
        )

    def test_cmd_preserved(self) -> None:
        lines = _dockerfile_lines()
        cmd_lines = [ln for ln in lines if ln.startswith("CMD")]
        assert cmd_lines, "Dockerfile must retain a CMD directive"
        assert "hermes.server" in cmd_lines[-1], "CMD must still launch the hermes server"

    def test_entrypoint_before_cmd(self) -> None:
        lines = _dockerfile_lines()
        entrypoint_idx = next((i for i, ln in enumerate(lines) if ln.startswith("ENTRYPOINT")), -1)
        cmd_idx = next((i for i, ln in enumerate(lines) if ln.startswith("CMD")), -1)
        assert entrypoint_idx != -1, "ENTRYPOINT not found"
        assert cmd_idx != -1, "CMD not found"
        assert entrypoint_idx < cmd_idx, "ENTRYPOINT must appear before CMD"
