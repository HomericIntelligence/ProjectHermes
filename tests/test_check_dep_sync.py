"""Regression tests for scripts/check_dep_sync.py (issue #497 follow-up).

The script enforces two invariants in CI:
  1. Every [project.dependencies] entry has a '<' upper bound.
  2. Every package appearing in pixi.toml [pypi-dependencies] OR in
     pyproject.toml [project.dependencies] is present in BOTH files with
     the same version range (bidirectional parity).

These tests pin both behaviours plus the edge cases the script's docstring
promises to handle.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).parent.parent
SCRIPT = ROOT / "scripts" / "check_dep_sync.py"


@pytest.fixture(scope="module")
def cds() -> ModuleType:
    """Load check_dep_sync.py fresh, then pop it from sys.modules on teardown.

    Module scope means we pay the load cost once per test file but still
    leave sys.modules clean for the rest of the suite (and for any
    pytest-xdist worker that runs the file in parallel).
    """
    spec = importlib.util.spec_from_file_location("check_dep_sync", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_dep_sync"] = mod
    spec.loader.exec_module(mod)
    yield mod
    sys.modules.pop("check_dep_sync", None)


# ---- parse_pyproject -------------------------------------------------------


def test_parse_pyproject_strips_extras(cds: ModuleType) -> None:
    assert cds.parse_pyproject(["uvicorn[standard]>=0.46.0,<1"]) == {"uvicorn": ">=0.46.0,<1"}


def test_parse_pyproject_strips_env_markers(cds: ModuleType) -> None:
    assert cds.parse_pyproject(['httpx>=0.27,<1 ; python_version<"4"']) == {"httpx": ">=0.27,<1"}


def test_parse_pyproject_canonicalises_names(cds: ModuleType) -> None:
    # PEP 503: "My.Pkg_Name" -> "my-pkg-name"
    assert cds.parse_pyproject(["My.Pkg_Name>=1,<2"]) == {"my-pkg-name": ">=1,<2"}


def test_parse_pyproject_rejects_garbage(cds: ModuleType) -> None:
    with pytest.raises(SystemExit):
        cds.parse_pyproject(["!!!not a dep!!!"])


# ---- parse_pixi ------------------------------------------------------------


def test_parse_pixi_bare_string(cds: ModuleType) -> None:
    assert cds.parse_pixi({"fastapi": ">=0.115,<1"}) == {"fastapi": ">=0.115,<1"}


def test_parse_pixi_inline_table_with_version_and_extras(cds: ModuleType) -> None:
    table = {"uvicorn": {"version": ">=0.46.0,<1", "extras": ["standard"]}}
    assert cds.parse_pixi(table) == {"uvicorn": ">=0.46.0,<1"}


def test_parse_pixi_skips_self_package(cds: ModuleType) -> None:
    assert cds.parse_pixi({cds.SELF_PACKAGE: {"path": ".", "editable": True}}) == {}


def test_parse_pixi_skips_path_git_url_installs(cds: ModuleType) -> None:
    table = {
        "local-thing": {"path": "../local"},
        "git-thing": {"git": "https://example.com/x.git"},
        "url-thing": {"url": "https://example.com/x.whl"},
    }
    assert cds.parse_pixi(table) == {}


def test_parse_pixi_inline_table_missing_version_errors(cds: ModuleType) -> None:
    with pytest.raises(SystemExit):
        cds.parse_pixi({"broken": {"extras": ["standard"]}})


# ---- check_upper_bounds ----------------------------------------------------


def test_upper_bound_present_passes(cds: ModuleType) -> None:
    assert cds.check_upper_bounds(["fastapi>=0.115,<1"]) == []


def test_missing_upper_bound_flagged(cds: ModuleType) -> None:
    failures = cds.check_upper_bounds(["fastapi>=0.115"])
    assert failures == ["  'fastapi>=0.115' — missing '<' upper bound"]


# ---- check_parity: pixi -> pyproject (existing direction) ------------------


def test_parity_match_returns_no_failures(cds: ModuleType) -> None:
    py = {"fastapi": ">=0.115,<1"}
    pixi = {"fastapi": ">=0.115,<1"}
    assert cds.check_parity(py, pixi) == []


def test_parity_missing_in_pyproject_flagged(cds: ModuleType) -> None:
    failures = cds.check_parity(py={}, pixi={"fastapi": ">=0.115,<1"})
    assert failures == [
        "  fastapi: declared in pixi.toml ('>=0.115,<1') but missing from "
        "pyproject.toml [project.dependencies]"
    ]


def test_parity_range_mismatch_flagged(cds: ModuleType) -> None:
    failures = cds.check_parity(
        py={"fastapi": ">=0.115,<1"},
        pixi={"fastapi": ">=0.120,<1"},
    )
    assert failures == ["  fastapi: pyproject.toml has '>=0.115,<1', pixi.toml has '>=0.120,<1'"]


# ---- check_parity: pyproject -> pixi (NEW direction, this PR) --------------


def test_parity_missing_in_pixi_flagged(cds: ModuleType) -> None:
    """A dep declared in pyproject but absent from pixi must fail the gate."""
    failures = cds.check_parity(
        py={"orphan": ">=1,<2"},
        pixi={},
    )
    assert failures == [
        "  orphan: declared in pyproject.toml ('>=1,<2') but missing from "
        "pixi.toml [pypi-dependencies]"
    ]


def test_parity_no_duplicate_when_both_sides_have_dep(cds: ModuleType) -> None:
    """A dep in both files with matching ranges produces zero failures (no
    duplicate from the new reverse-direction loop)."""
    py = {"fastapi": ">=0.115,<1", "httpx": ">=0.27,<1"}
    pixi = {"fastapi": ">=0.115,<1", "httpx": ">=0.27,<1"}
    assert cds.check_parity(py, pixi) == []


def test_parity_mismatch_reports_once_not_twice(cds: ModuleType) -> None:
    """A range mismatch present in both files reports exactly once (not once
    per direction)."""
    failures = cds.check_parity(
        py={"fastapi": ">=0.115,<1"},
        pixi={"fastapi": ">=0.120,<1"},
    )
    assert len(failures) == 1


# ---- integration: main() against fixture files -----------------------------


def _write_pair(tmp_path: Path, pyproject: str, pixi: str) -> tuple[Path, Path]:
    py = tmp_path / "pyproject.toml"
    px = tmp_path / "pixi.toml"
    py.write_text(pyproject)
    px.write_text(pixi)
    return py, px


def test_main_passes_on_synced_files(
    cds: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    py, px = _write_pair(
        tmp_path,
        pyproject='[project]\nname="x"\nversion="0"\ndependencies = ["fastapi>=0.115,<1"]\n',
        pixi='[pypi-dependencies]\nfastapi = ">=0.115,<1"\n',
    )
    monkeypatch.setattr(cds, "PYPROJECT", py)
    monkeypatch.setattr(cds, "PIXI", px)
    assert cds.main() == 0
    assert "OK:" in capsys.readouterr().out


def test_main_fails_on_drift(
    cds: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    py, px = _write_pair(
        tmp_path,
        pyproject='[project]\nname="x"\nversion="0"\ndependencies = ["fastapi>=0.115,<1"]\n',
        pixi='[pypi-dependencies]\nfastapi = ">=0.120,<1"\n',
    )
    monkeypatch.setattr(cds, "PYPROJECT", py)
    monkeypatch.setattr(cds, "PIXI", px)
    assert cds.main() == 1
    err = capsys.readouterr().err
    assert "have drifted" in err and "fastapi" in err


def test_main_fails_when_pyproject_missing_upper_bound(
    cds: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    py, px = _write_pair(
        tmp_path,
        pyproject='[project]\nname="x"\nversion="0"\ndependencies = ["fastapi>=0.115"]\n',
        pixi='[pypi-dependencies]\nfastapi = ">=0.115"\n',
    )
    monkeypatch.setattr(cds, "PYPROJECT", py)
    monkeypatch.setattr(cds, "PIXI", px)
    assert cds.main() == 1
    assert "no upper bound (<)" in capsys.readouterr().err


def test_main_fails_when_pixi_has_dep_missing_from_pyproject(
    cds: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    py, px = _write_pair(
        tmp_path,
        pyproject='[project]\nname="x"\nversion="0"\ndependencies = ["fastapi>=0.115,<1"]\n',
        pixi=('[pypi-dependencies]\nfastapi = ">=0.115,<1"\nhttpx = ">=0.27,<1"\n'),
    )
    monkeypatch.setattr(cds, "PYPROJECT", py)
    monkeypatch.setattr(cds, "PIXI", px)
    assert cds.main() == 1
    err = capsys.readouterr().err
    assert "httpx" in err and "missing from pyproject.toml" in err


def test_main_fails_when_pyproject_has_dep_missing_from_pixi(
    cds: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The reverse-direction drift case — pyproject has orphaned dep."""
    py, px = _write_pair(
        tmp_path,
        pyproject=(
            '[project]\nname="x"\nversion="0"\n'
            'dependencies = ["fastapi>=0.115,<1", "orphan>=1,<2"]\n'
        ),
        pixi='[pypi-dependencies]\nfastapi = ">=0.115,<1"\n',
    )
    monkeypatch.setattr(cds, "PYPROJECT", py)
    monkeypatch.setattr(cds, "PIXI", px)
    assert cds.main() == 1
    err = capsys.readouterr().err
    assert "orphan" in err and "missing from pixi.toml" in err


def test_main_fails_when_pixi_pypi_dependencies_table_absent(
    cds: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Empty/absent [pypi-dependencies] with any pyproject deps must fail
    after the bidirectional change (previously passed vacuously)."""
    py, px = _write_pair(
        tmp_path,
        pyproject='[project]\nname="x"\nversion="0"\ndependencies = ["fastapi>=0.115,<1"]\n',
        pixi="# no [pypi-dependencies] table\n",
    )
    monkeypatch.setattr(cds, "PYPROJECT", py)
    monkeypatch.setattr(cds, "PIXI", px)
    assert cds.main() == 1
    assert "fastapi" in capsys.readouterr().err


def test_main_handles_pixi_inline_table_for_extras(
    cds: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    py, px = _write_pair(
        tmp_path,
        pyproject=(
            '[project]\nname="x"\nversion="0"\ndependencies = ["uvicorn[standard]>=0.46.0,<1"]\n'
        ),
        pixi=(
            '[pypi-dependencies]\nuvicorn = { version = ">=0.46.0,<1", extras = ["standard"] }\n'
        ),
    )
    monkeypatch.setattr(cds, "PYPROJECT", py)
    monkeypatch.setattr(cds, "PIXI", px)
    assert cds.main() == 0


def test_main_real_repo_files_pass(cds: ModuleType) -> None:
    """The committed pyproject.toml + pixi.toml must satisfy the gate (no
    regression in the CI guardrail)."""
    # cds.PYPROJECT and cds.PIXI default to the real repo files.
    assert cds.main() == 0
