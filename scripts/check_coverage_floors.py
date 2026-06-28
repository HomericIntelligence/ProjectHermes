#!/usr/bin/env python3
"""CI advisory: warn when measured per-module coverage exceeds its floor.

Parses per-module floors from the inline ``--fail-under=`` lines in
``.github/workflows/_required.yml`` (the existing single source of truth for
floor enforcement) and measured coverage from ``coverage.xml`` (produced by
``pytest --cov-report=xml`` per pyproject.toml:65). For each module whose
measured coverage exceeds its floor by more than ``COVERAGE_FLOOR_MARGIN``
percentage points (default 15.0), emits a GitHub ``::warning::`` annotation
and a row in ``$GITHUB_STEP_SUMMARY`` suggesting the floor be ratcheted up.

Advisory only: **main() always returns 0**. The outermost try/except in
main() is the backstop that preserves this contract even if coverage.xml is
malformed, pyproject.toml is missing, or the workflow file is unreadable.
This bare-Exception catch is intentional and confined to this script —
the advisory step must never block CI. See
HomericIntelligence/ProjectHermes#479 (follow-up from #332).
"""

from __future__ import annotations

import os
import re
import sys
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "_required.yml"
COVERAGE_XML = ROOT / "coverage.xml"
PYPROJECT = ROOT / "pyproject.toml"
DEFAULT_MARGIN = 15.0

# Matches: pixi run coverage report --include="src/hermes/foo.py" --fail-under=90
FLOOR_RE = re.compile(
    r'coverage report\s+--include="(?P<path>[^"]+)"\s+--fail-under=(?P<n>\d+(?:\.\d+)?)'
)


def load_floors(workflow_path: Path) -> dict[str, float]:
    """Parse {module_path: floor_percent} from inline --fail-under= lines."""
    try:
        text = workflow_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    return {m["path"]: float(m["n"]) for m in FLOOR_RE.finditer(text)}


def coverage_source_prefix(pyproject_path: Path) -> str:
    """Return the first entry of [tool.coverage.run].source, e.g. 'hermes'.

    Returns "" on any failure (missing file, parse error, missing keys,
    non-string entries). Callers must treat "" as 'no normalisation needed'.
    """
    try:
        with pyproject_path.open("rb") as fh:
            data = tomllib.load(fh)
        sources = data.get("tool", {}).get("coverage", {}).get("run", {}).get("source")
        if not isinstance(sources, list) or not sources:
            return ""
        first = sources[0]
        if not isinstance(first, str):
            return ""
        return first.strip("/")
    except (OSError, tomllib.TOMLDecodeError):
        return ""


def _normalise_filename(filename: str, prefix: str) -> str:
    """Bridge coverage.xml's '<prefix>/foo.py' to _required.yml's 'src/<prefix>/foo.py'.

    Explicit case table (no nested negations):
      1. filename already starts with 'src/'           -> leave unchanged
      2. prefix is empty                               -> leave unchanged
      3. filename starts with f'{prefix}/'             -> prepend 'src/'
      4. otherwise                                     -> leave unchanged
    """
    if filename.startswith("src/"):
        return filename
    if not prefix:
        return filename
    if filename.startswith(f"{prefix}/"):
        return f"src/{filename}"
    return filename


def parse_coverage_xml(path: Path, prefix: str) -> dict[str, float]:
    """Return {workflow-style path: measured-percent} for every <class>.

    Returns {} on any parse error.
    """
    try:
        tree = ET.parse(path)
    except (OSError, ET.ParseError):
        return {}
    root = tree.getroot()
    out: dict[str, float] = {}
    for cls in root.iter("class"):
        filename = cls.attrib.get("filename")
        rate = cls.attrib.get("line-rate")
        if not filename or rate is None:
            continue
        try:
            value = float(rate) * 100.0
        except (TypeError, ValueError):
            continue
        out[_normalise_filename(filename, prefix)] = value
    return out


def github_warning(msg: str) -> None:
    """Emit a GitHub warning annotation."""
    # File/line context is omitted intentionally: floor changes apply to whole
    # modules and live in _required.yml, not the source file under review.
    print(f"::warning::{msg}")


def append_summary(lines: list[str]) -> None:
    """Append lines to $GITHUB_STEP_SUMMARY, if running in GitHub Actions."""
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary:
        return
    try:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except OSError:
        return  # Advisory: never fail.


def _run() -> int:
    """Core logic; all I/O guards and logic paths here."""
    if not WORKFLOW.exists():
        print(f"NOTE: {WORKFLOW} not found — skipping floor-margin check.")
        return 0
    if not COVERAGE_XML.exists():
        print(f"NOTE: {COVERAGE_XML} not found — skipping floor-margin check.")
        return 0

    try:
        margin = float(os.environ.get("COVERAGE_FLOOR_MARGIN", DEFAULT_MARGIN))
    except (TypeError, ValueError):
        margin = DEFAULT_MARGIN

    floors = load_floors(WORKFLOW)
    if not floors:
        print(f"NOTE: no --fail-under= lines found in {WORKFLOW} — nothing to check.")
        return 0

    prefix = coverage_source_prefix(PYPROJECT) if PYPROJECT.exists() else ""
    measured = parse_coverage_xml(COVERAGE_XML, prefix)

    rows: list[tuple[str, float, float, float]] = []
    for module, floor in sorted(floors.items()):
        if not 0.0 <= floor <= 100.0:
            github_warning(f"Ignoring out-of-range floor for {module}: {floor}%")
            continue
        measured_val = measured.get(module)
        if measured_val is None:
            github_warning(f"Ignoring orphaned floor for {module}: no measured coverage found")
            continue
        gap = measured_val - floor
        if gap > margin:
            github_warning(
                f"Coverage for {module} is {measured_val:.2f}%, but floor is {floor:.2f}% "
                f"(gap: {gap:.2f}pp, threshold: {margin:.2f}pp). Consider raising the floor."
            )
            rows.append((module, floor, measured_val, gap))

    if rows:
        summary_lines = ["## Coverage Floor Advisory", ""]
        summary_lines.append("| Module | Floor | Measured | Gap |")
        summary_lines.append("|--------|-------|----------|-----|")
        for module, floor, measured_val, gap in rows:
            summary_lines.append(
                f"| {module} | {floor:.2f}% | {measured_val:.2f}% | {gap:.2f}pp |"
            )
        append_summary(summary_lines)

    return 0


def main() -> int:
    """Entry point; always returns 0 (advisory contract)."""
    try:
        return _run()
    except Exception as exc:  # noqa: BLE001 — see module docstring.
        print(f"NOTE: coverage-floor advisory aborted ({type(exc).__name__}: {exc}).")
        return 0


if __name__ == "__main__":
    sys.exit(main())
