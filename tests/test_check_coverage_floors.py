"""Tests for scripts/check_coverage_floors.py."""

from __future__ import annotations

import importlib.util
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest


# Load the script as a module dynamically.
def _load_script(script_path: Path) -> Any:
    """Import a .py file as a module."""
    spec = importlib.util.spec_from_file_location("check_coverage_floors", script_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "check_coverage_floors.py"


@pytest.fixture
def mod() -> Any:
    """Load the script module."""
    return _load_script(SCRIPT_PATH)


@pytest.fixture
def tmp_project(tmp_path: Path) -> dict[str, Path]:
    """Create a minimal project layout with .github/workflows and coverage.xml."""
    project = {
        "root": tmp_path,
        "workflow": tmp_path / ".github" / "workflows" / "_required.yml",
        "coverage": tmp_path / "coverage.xml",
        "pyproject": tmp_path / "pyproject.toml",
    }
    project["workflow"].parent.mkdir(parents=True, exist_ok=True)
    # Write a minimal pyproject.toml with coverage source so paths normalize correctly
    _write_pyproject(
        project["pyproject"],
        """
[tool.coverage.run]
source = ["hermes"]
        """,
    )
    return project


def _write_workflow(path: Path, content: str) -> None:
    """Write a workflow file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_pyproject(path: Path, content: str) -> None:
    """Write a pyproject.toml file."""
    path.write_text(content, encoding="utf-8")


def _write_coverage_xml(path: Path, classes: list[tuple[str, float]]) -> None:
    """Write a minimal coverage.xml with given (filename, line-rate) pairs.

    Args:
        path: Path to write coverage.xml to.
        classes: List of (filename, line-rate) tuples, where line-rate is a
            decimal (e.g., 0.85 for 85%).
    """
    xml_lines = [
        '<?xml version="1.0" ?>',
        '<coverage version="5.5" timestamp="1234567890" lines-valid="100" lines-covered="85" '
        'line-rate="0.85" branches-covered="0" branches-valid="0" branch-rate="0" complexity="0">',
    ]
    for filename, line_rate in classes:
        xml_lines.append(
            f'  <class filename="{filename}" line-rate="{line_rate}" branch-rate="0">'
        )
        xml_lines.append("  </class>")
    xml_lines.append("</coverage>")
    path.write_text("\n".join(xml_lines), encoding="utf-8")


class TestLoadFloors:
    """Test load_floors()."""

    def test_parses_valid_floors(self, mod: Any, tmp_project: dict[str, Path]) -> None:
        """Extract floors from valid --fail-under= lines."""
        workflow = tmp_project["workflow"]
        _write_workflow(
            workflow,
            """
            steps:
              - run: pixi run coverage report --include="src/hermes/foo.py" --fail-under=90
              - run: pixi run coverage report --include="src/hermes/bar.py" --fail-under=85.5
            """,
        )
        floors = mod.load_floors(workflow)
        assert floors == {"src/hermes/foo.py": 90.0, "src/hermes/bar.py": 85.5}

    def test_missing_workflow_returns_empty(self, mod: Any, tmp_project: dict[str, Path]) -> None:
        """Return {} when workflow file does not exist."""
        workflow = tmp_project["workflow"]
        floors = mod.load_floors(workflow)
        assert floors == {}

    def test_no_floors_returns_empty(self, mod: Any, tmp_project: dict[str, Path]) -> None:
        """Return {} when no --fail-under= lines found."""
        workflow = tmp_project["workflow"]
        _write_workflow(workflow, "some other yaml content\n")
        floors = mod.load_floors(workflow)
        assert floors == {}


class TestCoverageSourcePrefix:
    """Test coverage_source_prefix()."""

    def test_returns_prefix_from_pyproject(self, mod: Any, tmp_project: dict[str, Path]) -> None:
        """Extract [tool.coverage.run].source[0]."""
        pyproject = tmp_project["pyproject"]
        _write_pyproject(
            pyproject,
            """
[tool.coverage.run]
source = ["hermes", "other"]
            """,
        )
        prefix = mod.coverage_source_prefix(pyproject)
        assert prefix == "hermes"

    def test_strips_trailing_slash(self, mod: Any, tmp_project: dict[str, Path]) -> None:
        """Strip trailing slashes from prefix."""
        pyproject = tmp_project["pyproject"]
        _write_pyproject(
            pyproject,
            """
[tool.coverage.run]
source = ["hermes/"]
            """,
        )
        prefix = mod.coverage_source_prefix(pyproject)
        assert prefix == "hermes"

    def test_missing_file_returns_empty(self, mod: Any, tmp_path: Path) -> None:
        """Return "" when pyproject.toml does not exist."""
        pyproject = tmp_path / "nonexistent.toml"
        prefix = mod.coverage_source_prefix(pyproject)
        assert prefix == ""

    def test_missing_key_returns_empty(self, mod: Any, tmp_project: dict[str, Path]) -> None:
        """Return "" when [tool.coverage.run].source missing."""
        pyproject = tmp_project["pyproject"]
        _write_pyproject(pyproject, "[tool]\n")
        prefix = mod.coverage_source_prefix(pyproject)
        assert prefix == ""

    def test_empty_source_list_returns_empty(self, mod: Any, tmp_project: dict[str, Path]) -> None:
        """Return "" when source is empty list."""
        pyproject = tmp_project["pyproject"]
        _write_pyproject(
            pyproject,
            """
[tool.coverage.run]
source = []
            """,
        )
        prefix = mod.coverage_source_prefix(pyproject)
        assert prefix == ""

    def test_non_string_entry_returns_empty(self, mod: Any, tmp_project: dict[str, Path]) -> None:
        """Return "" when source[0] is not a string."""
        pyproject = tmp_project["pyproject"]
        _write_pyproject(
            pyproject,
            """
[tool.coverage.run]
source = [123, "hermes"]
            """,
        )
        prefix = mod.coverage_source_prefix(pyproject)
        assert prefix == ""

    def test_malformed_toml_returns_empty(self, mod: Any, tmp_project: dict[str, Path]) -> None:
        """Return "" when TOML is malformed."""
        pyproject = tmp_project["pyproject"]
        pyproject.write_text("{ invalid toml }", encoding="utf-8")
        prefix = mod.coverage_source_prefix(pyproject)
        assert prefix == ""


class TestNormaliseFilename:
    """Test _normalise_filename()."""

    def test_already_prefixed_unchanged(self, mod: Any) -> None:
        """Leave unchanged if already starts with 'src/'."""
        result = mod._normalise_filename("src/hermes/foo.py", "hermes")
        assert result == "src/hermes/foo.py"

    def test_empty_prefix_unchanged(self, mod: Any) -> None:
        """Leave unchanged if prefix is empty."""
        result = mod._normalise_filename("hermes/foo.py", "")
        assert result == "hermes/foo.py"

    def test_prepends_src_when_matched(self, mod: Any) -> None:
        """Prepend 'src/' when filename matches prefix."""
        result = mod._normalise_filename("hermes/foo.py", "hermes")
        assert result == "src/hermes/foo.py"

    def test_no_match_unchanged(self, mod: Any) -> None:
        """Leave unchanged when filename does not match prefix."""
        result = mod._normalise_filename("other/foo.py", "hermes")
        assert result == "other/foo.py"


class TestParseCoverageXml:
    """Test parse_coverage_xml()."""

    def test_parses_valid_xml(self, mod: Any, tmp_project: dict[str, Path]) -> None:
        """Extract coverage from valid XML."""
        coverage = tmp_project["coverage"]
        _write_coverage_xml(coverage, [("hermes/foo.py", 0.85), ("hermes/bar.py", 0.95)])
        result = mod.parse_coverage_xml(coverage, "hermes")
        assert result == {
            "src/hermes/foo.py": 85.0,
            "src/hermes/bar.py": 95.0,
        }

    def test_missing_file_returns_empty(self, mod: Any, tmp_project: dict[str, Path]) -> None:
        """Return {} when coverage.xml does not exist."""
        coverage = tmp_project["coverage"]
        result = mod.parse_coverage_xml(coverage, "hermes")
        assert result == {}

    def test_malformed_xml_returns_empty(self, mod: Any, tmp_project: dict[str, Path]) -> None:
        """Return {} when XML is malformed."""
        coverage = tmp_project["coverage"]
        coverage.write_text("<invalid>xml", encoding="utf-8")
        result = mod.parse_coverage_xml(coverage, "hermes")
        assert result == {}

    def test_missing_attributes_skipped(self, mod: Any, tmp_project: dict[str, Path]) -> None:
        """Skip classes with missing filename or line-rate."""
        coverage = tmp_project["coverage"]
        xml = """<?xml version="1.0" ?>
<coverage>
  <class filename="hermes/foo.py" line-rate="0.85"></class>
  <class line-rate="0.90"></class>
  <class filename="hermes/bar.py"></class>
</coverage>
"""
        coverage.write_text(xml, encoding="utf-8")
        result = mod.parse_coverage_xml(coverage, "hermes")
        assert result == {"src/hermes/foo.py": 85.0}


class TestGithubWarning:
    """Test github_warning()."""

    def test_prints_warning_annotation(self, mod: Any, capsys: Any) -> None:
        """Print warning in GitHub Actions format."""
        mod.github_warning("test message")
        captured = capsys.readouterr()
        assert captured.out == "::warning::test message\n"


class TestAppendSummary:
    """Test append_summary()."""

    def test_appends_to_summary_file(self, mod: Any, monkeypatch: Any) -> None:
        """Append lines to $GITHUB_STEP_SUMMARY when set."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            summary_path = f.name
        try:
            monkeypatch.setenv("GITHUB_STEP_SUMMARY", summary_path)
            mod.append_summary(["line1", "line2"])
            content = Path(summary_path).read_text(encoding="utf-8")
            assert content == "line1\nline2\n"
        finally:
            Path(summary_path).unlink()

    def test_skips_when_summary_unset(self, mod: Any, monkeypatch: Any) -> None:
        """Do nothing when $GITHUB_STEP_SUMMARY is not set."""
        monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
        mod.append_summary(["line1"])  # Should not raise.

    def test_handles_write_failure(self, mod: Any, monkeypatch: Any) -> None:
        """Never raise on write failure (advisory contract)."""
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", "/nonexistent/path/summary.txt")
        mod.append_summary(["line1"])  # Should not raise.


class TestRun:
    """Test _run() logic."""

    def test_missing_workflow_returns_zero(self, mod: Any, tmp_project: dict[str, Path]) -> None:
        """Return 0 when workflow missing."""
        with _mock_root(mod, tmp_project["root"]):
            result = mod._run()
            assert result == 0

    def test_missing_coverage_returns_zero(self, mod: Any, tmp_project: dict[str, Path]) -> None:
        """Return 0 when coverage.xml missing."""
        _write_workflow(tmp_project["workflow"], "")
        with _mock_root(mod, tmp_project["root"]):
            result = mod._run()
            assert result == 0

    def test_warning_when_gap_exceeds_margin(
        self, mod: Any, tmp_project: dict[str, Path], capsys: Any
    ) -> None:
        """Warn when measured > floor + margin."""
        _write_workflow(
            tmp_project["workflow"],
            'pixi run coverage report --include="src/hermes/foo.py" --fail-under=70',
        )
        _write_coverage_xml(tmp_project["coverage"], [("hermes/foo.py", 0.88)])
        with _mock_root(mod, tmp_project["root"]):
            result = mod._run()
            assert result == 0
            captured = capsys.readouterr()
            assert "::warning::" in captured.out
            assert "src/hermes/foo.py" in captured.out
            assert "88.00%" in captured.out
            assert "70.00%" in captured.out

    def test_no_warning_when_within_margin(
        self, mod: Any, tmp_project: dict[str, Path], capsys: Any
    ) -> None:
        """No warning when gap <= margin (default 15pp)."""
        _write_workflow(
            tmp_project["workflow"],
            'pixi run coverage report --include="src/hermes/foo.py" --fail-under=75',
        )
        _write_coverage_xml(tmp_project["coverage"], [("hermes/foo.py", 0.85)])
        with _mock_root(mod, tmp_project["root"]):
            result = mod._run()
            assert result == 0
            captured = capsys.readouterr()
            assert "::warning::" not in captured.out

    def test_margin_overridable_via_env(
        self, mod: Any, tmp_project: dict[str, Path], capsys: Any, monkeypatch: Any
    ) -> None:
        """Respect COVERAGE_FLOOR_MARGIN env var."""
        _write_workflow(
            tmp_project["workflow"],
            'pixi run coverage report --include="src/hermes/foo.py" --fail-under=80',
        )
        _write_coverage_xml(tmp_project["coverage"], [("hermes/foo.py", 0.90)])
        monkeypatch.setenv("COVERAGE_FLOOR_MARGIN", "5")
        with _mock_root(mod, tmp_project["root"]):
            result = mod._run()
            assert result == 0
            captured = capsys.readouterr()
            assert "::warning::" in captured.out  # 10pp gap > 5pp margin

    def test_orphaned_floor_warns(
        self, mod: Any, tmp_project: dict[str, Path], capsys: Any
    ) -> None:
        """Warn when floor exists but no measured coverage."""
        _write_workflow(
            tmp_project["workflow"],
            'pixi run coverage report --include="src/hermes/missing.py" --fail-under=80',
        )
        _write_coverage_xml(tmp_project["coverage"], [("hermes/foo.py", 0.85)])
        with _mock_root(mod, tmp_project["root"]):
            result = mod._run()
            assert result == 0
            captured = capsys.readouterr()
            assert "::warning::" in captured.out
            assert "orphaned" in captured.out.lower()
            assert "src/hermes/missing.py" in captured.out

    def test_out_of_range_floor_warns_and_skips(
        self, mod: Any, tmp_project: dict[str, Path], capsys: Any
    ) -> None:
        """Warn and skip when floor is outside [0, 100]."""
        _write_workflow(
            tmp_project["workflow"],
            'pixi run coverage report --include="src/hermes/foo.py" --fail-under=150',
        )
        _write_coverage_xml(tmp_project["coverage"], [("hermes/foo.py", 0.85)])
        with _mock_root(mod, tmp_project["root"]):
            result = mod._run()
            assert result == 0
            captured = capsys.readouterr()
            assert "::warning::" in captured.out
            assert "out-of-range" in captured.out.lower()
            assert "src/hermes/foo.py" in captured.out

    def test_no_floors_returns_zero(
        self, mod: Any, tmp_project: dict[str, Path], capsys: Any
    ) -> None:
        """Return 0 when no --fail-under= lines found."""
        _write_workflow(tmp_project["workflow"], "some yaml content\n")
        _write_coverage_xml(tmp_project["coverage"], [("hermes/foo.py", 0.85)])
        with _mock_root(mod, tmp_project["root"]):
            result = mod._run()
            assert result == 0
            captured = capsys.readouterr()
            assert "nothing to check" in captured.out.lower()

    def test_summary_table_appended_when_warnings(
        self, mod: Any, tmp_project: dict[str, Path], monkeypatch: Any
    ) -> None:
        """Append summary table to $GITHUB_STEP_SUMMARY when warnings issued."""
        _write_workflow(
            tmp_project["workflow"],
            'pixi run coverage report --include="src/hermes/foo.py" --fail-under=70',
        )
        _write_coverage_xml(tmp_project["coverage"], [("hermes/foo.py", 0.88)])
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            summary_path = f.name
        try:
            monkeypatch.setenv("GITHUB_STEP_SUMMARY", summary_path)
            with _mock_root(mod, tmp_project["root"]):
                result = mod._run()
                assert result == 0
            content = Path(summary_path).read_text(encoding="utf-8")
            assert "Coverage Floor Advisory" in content
            assert "src/hermes/foo.py" in content
            assert "70.00" in content
            assert "88.00" in content
        finally:
            Path(summary_path).unlink()


class TestMain:
    """Test main() contract."""

    def test_returns_zero_on_success(self, mod: Any, tmp_project: dict[str, Path]) -> None:
        """main() always returns 0."""
        _write_workflow(tmp_project["workflow"], "")
        with _mock_root(mod, tmp_project["root"]):
            result = mod.main()
            assert result == 0

    def test_top_level_backstop_swallows_unexpected_exception(
        self, mod: Any, monkeypatch: Any
    ) -> None:
        """Unexpected exception in _run() is caught; main() still returns 0."""

        def boom() -> int:
            raise RuntimeError("unexpected error")

        monkeypatch.setattr(mod, "_run", boom)
        result = mod.main()
        assert result == 0


class TestParseRealWorkflowFloors:
    """Regression guard: verify parsing against actual _required.yml."""

    def test_parses_real_workflow_floors_exact_match(self, mod: Any) -> None:
        """Parse exact floors from real _required.yml; fail if any module added/removed."""
        workflow = Path(__file__).parent.parent / ".github" / "workflows" / "_required.yml"
        if not workflow.exists():
            pytest.skip("_required.yml not found")

        floors = mod.load_floors(workflow)

        # Hardcoded set of expected modules at _required.yml:226-235.
        # Update this list if a module is added/removed from the workflow.
        expected = {
            "src/hermes/__init__.py",
            "src/hermes/__main__.py",
            "src/hermes/config.py",
            "src/hermes/logging_config.py",
            "src/hermes/metrics.py",
            "src/hermes/middleware.py",
            "src/hermes/models.py",
            "src/hermes/publisher.py",
            "src/hermes/rate_limit.py",
            "src/hermes/server.py",
        }

        assert set(floors.keys()) == expected, (
            f"Mismatch in module set.\n"
            f"Expected: {expected}\n"
            f"Got: {set(floors.keys())}\n"
            f"Added: {set(floors.keys()) - expected}\n"
            f"Removed: {expected - set(floors.keys())}"
        )


# Helpers


@contextmanager
def _mock_root(mod: Any, root: Path) -> Any:
    """Temporarily replace ROOT, WORKFLOW, COVERAGE_XML, PYPROJECT in the module."""
    old_root = mod.ROOT
    old_workflow = mod.WORKFLOW
    old_coverage = mod.COVERAGE_XML
    old_pyproject = mod.PYPROJECT
    try:
        mod.ROOT = root
        mod.WORKFLOW = root / ".github" / "workflows" / "_required.yml"
        mod.COVERAGE_XML = root / "coverage.xml"
        mod.PYPROJECT = root / "pyproject.toml"
        yield
    finally:
        mod.ROOT = old_root
        mod.WORKFLOW = old_workflow
        mod.COVERAGE_XML = old_coverage
        mod.PYPROJECT = old_pyproject
