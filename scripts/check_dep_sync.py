#!/usr/bin/env python3
"""CI guardrail: verify dependency version ranges are consistent.

Two checks are performed:

1. Every entry in pyproject.toml ``[project.dependencies]`` has a ``<`` upper
   bound. Without an upper bound, ``pip install .`` may pull a breaking major
   release.

2. Every production dependency declared in pixi.toml ``[pypi-dependencies]``
   appears in pyproject.toml ``[project.dependencies]`` with the **same**
   version range. This prevents drift where one file is updated but not the
   other (see HomericIntelligence/ProjectHermes#594).

The script exits non-zero on any failure, printing the offending entries with
the file each one came from so reviewers can pinpoint the drift.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent.parent
PYPROJECT = ROOT / "pyproject.toml"
PIXI = ROOT / "pixi.toml"

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore[import-not-found,no-redef]
        except ImportError:
            print("ERROR: tomli is required on Python < 3.11 (pip install tomli)", file=sys.stderr)
            sys.exit(1)


# Project package itself in pixi (path-installed); has no version range to compare.
SELF_PACKAGE = "hermes"

# Strip any PEP 508 extras and environment markers from a pyproject entry.
# Examples handled:
#   "uvicorn[standard]>=0.46.0,<1"     -> ("uvicorn", ">=0.46.0,<1")
#   "fastapi>=0.115,<1"                -> ("fastapi", ">=0.115,<1")
#   "httpx>=0.27,<1 ; python_version<'4'" -> ("httpx", ">=0.27,<1")
_NAME_RE = re.compile(r"^\s*([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?\s*(.*?)\s*$")


def _canon(name: str) -> str:
    """PEP 503 canonical name (lowercase, runs of [-_.] collapsed to '-')."""
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_pyproject(deps: list[str]) -> dict[str, str]:
    """Return {canonical_name: version_spec} from PEP 508 entries."""
    out: dict[str, str] = {}
    for entry in deps:
        # Drop environment marker, if any.
        head = entry.split(";", 1)[0]
        m = _NAME_RE.match(head)
        if not m:
            raise SystemExit(f"pyproject.toml: cannot parse dependency entry {entry!r}")
        name, spec = m.group(1), m.group(2).strip()
        out[_canon(name)] = spec
    return out


def parse_pixi(table: dict[str, Any]) -> dict[str, str]:
    """Return {canonical_name: version_spec} from a pixi pypi-dependencies table.

    Pixi entries can be either bare strings (``"fastapi" = ">=0.115,<1"``) or
    inline tables (``{ version = ">=0.46.0,<1", extras = ["standard"] }``).
    The self-install entry (``{ path = ".", editable = true }``) is skipped
    since it carries no version to compare.
    """
    out: dict[str, str] = {}
    for name, value in table.items():
        cname = _canon(name)
        if cname == SELF_PACKAGE:
            continue
        if isinstance(value, str):
            out[cname] = value.strip()
        elif isinstance(value, dict):
            if "path" in value or "git" in value or "url" in value:
                # Local / VCS install — no PyPI version range to compare.
                continue
            ver = value.get("version")
            if not isinstance(ver, str):
                raise SystemExit(
                    f"pixi.toml: entry {name!r} has no 'version' string under [pypi-dependencies]"
                )
            out[cname] = ver.strip()
        else:
            raise SystemExit(f"pixi.toml: unexpected value type for {name!r}: {type(value).__name__}")
    return out


def _load(path: Path) -> dict[str, Any]:
    with path.open("rb") as fh:
        data: dict[str, Any] = tomllib.load(fh)
    return data


def check_upper_bounds(deps: list[str]) -> list[str]:
    return [f"  {entry!r} — missing '<' upper bound" for entry in deps if "<" not in entry]


def check_parity(py: dict[str, str], pixi: dict[str, str]) -> list[str]:
    """Return human-readable failure lines for any drift between the two maps."""
    failures: list[str] = []
    for name, pixi_spec in sorted(pixi.items()):
        py_spec = py.get(name)
        if py_spec is None:
            failures.append(
                f"  {name}: declared in pixi.toml ({pixi_spec!r}) but missing from "
                f"pyproject.toml [project.dependencies]"
            )
            continue
        if py_spec != pixi_spec:
            failures.append(
                f"  {name}: pyproject.toml has {py_spec!r}, pixi.toml has {pixi_spec!r}"
            )
    return failures


def main() -> int:
    py_data = _load(PYPROJECT)
    pixi_data = _load(PIXI)

    py_deps_raw: list[str] | None = py_data.get("project", {}).get("dependencies")
    if not py_deps_raw:
        print(
            "FAIL: [project.dependencies] is missing or empty in pyproject.toml.\n"
            "      Add version-bounded runtime dependencies so 'pip install .' is reproducible.",
            file=sys.stderr,
        )
        return 1

    bound_failures = check_upper_bounds(py_deps_raw)
    if bound_failures:
        print(
            "FAIL: The following [project.dependencies] entries have no upper bound (<).\n"
            "      Without an upper bound, 'pip install .' may pull breaking major versions.\n",
            file=sys.stderr,
        )
        for line in bound_failures:
            print(line, file=sys.stderr)
        print('\nFix by adding an upper bound, e.g.:\n  "some-package>=1.2,<2"', file=sys.stderr)
        return 1

    py_deps = parse_pyproject(py_deps_raw)
    pixi_table = pixi_data.get("pypi-dependencies", {})
    pixi_deps = parse_pixi(pixi_table)

    parity_failures = check_parity(py_deps, pixi_deps)
    if parity_failures:
        print(
            "FAIL: pixi.toml [pypi-dependencies] and pyproject.toml [project.dependencies] "
            "have drifted.\n"
            "      Production dependency version ranges must be identical in both files.\n",
            file=sys.stderr,
        )
        for line in parity_failures:
            print(line, file=sys.stderr)
        print(
            "\nFix by updating one file so both declare the same version range.",
            file=sys.stderr,
        )
        return 1

    print(
        f"OK: {len(py_deps_raw)} dependencies in [project.dependencies], all upper-bounded "
        f"and consistent with pixi.toml [pypi-dependencies] ({len(pixi_deps)} prod entries)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
