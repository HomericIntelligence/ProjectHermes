"""Packaging sanity checks for pyproject.toml."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
PYPROJECT = ROOT / "pyproject.toml"

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]


@pytest.fixture(scope="module")
def pyproject() -> dict:
    with PYPROJECT.open("rb") as f:
        return tomllib.load(f)


def test_project_dependencies_section_exists(pyproject: dict) -> None:
    deps = pyproject.get("project", {}).get("dependencies")
    assert deps is not None, "[project.dependencies] is missing from pyproject.toml"
    assert len(deps) > 0, "[project.dependencies] is empty"


def test_project_dependencies_count(pyproject: dict) -> None:
    deps = pyproject["project"]["dependencies"]
    assert len(deps) == 12, (
        f"Expected 12 runtime dependencies, found {len(deps)}: {deps}"
    )


@pytest.mark.parametrize(
    "dep",
    [
        "fastapi",
        "starlette",
        "uvicorn",
        "nats-py",
        "pydantic",
        "pydantic-settings",
        "httpx",
        "slowapi",
        "limits",
        "prometheus-client",
        "urllib3",
        "idna",
    ],
)
def test_dependency_has_upper_bound(dep: str, pyproject: dict) -> None:
    deps: list[str] = pyproject["project"]["dependencies"]
    matched = [d for d in deps if d.lower().startswith(dep.lower())]
    assert matched, f"Dependency '{dep}' not found in [project.dependencies]"
    entry = matched[0]
    assert ">=" in entry, f"'{entry}' is missing a >= lower bound"
    assert "<" in entry, f"'{entry}' is missing a < upper bound (prevents unbounded major pulls)"


def test_no_dev_dependencies_in_project_deps(pyproject: dict) -> None:
    dev_only = {"pytest", "ruff", "mypy", "pre-commit", "pip-audit"}
    deps: list[str] = pyproject["project"]["dependencies"]
    for dep in deps:
        name = dep.split("[")[0].split(">=")[0].split(">")[0].split("==")[0].strip().lower()
        assert name not in dev_only, (
            f"Dev dependency '{name}' should not appear in [project.dependencies]"
        )
