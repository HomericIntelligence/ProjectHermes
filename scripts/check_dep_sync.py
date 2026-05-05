#!/usr/bin/env python3
"""CI guardrail: verify [project.dependencies] in pyproject.toml has upper bounds.

Exits non-zero if any runtime dependency is missing a < upper bound or if the
[project.dependencies] section is absent.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
PYPROJECT = ROOT / "pyproject.toml"

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ImportError:
            print("ERROR: tomli is required on Python < 3.11 (pip install tomli)", file=sys.stderr)
            sys.exit(1)


def main() -> int:
    with PYPROJECT.open("rb") as f:
        data = tomllib.load(f)

    deps: list[str] | None = data.get("project", {}).get("dependencies")

    if not deps:
        print(
            "FAIL: [project.dependencies] is missing or empty in pyproject.toml.\n"
            "      Add version-bounded runtime dependencies so 'pip install .' is reproducible.",
            file=sys.stderr,
        )
        return 1

    failures: list[str] = []
    for entry in deps:
        if "<" not in entry:
            failures.append(f"  {entry!r} — missing < upper bound")

    if failures:
        print(
            "FAIL: The following [project.dependencies] entries have no upper bound (<).\n"
            "      Without an upper bound, 'pip install .' may pull breaking major versions.\n",
            file=sys.stderr,
        )
        for line in failures:
            print(line, file=sys.stderr)
        print(
            "\nFix by adding an upper bound, e.g.:\n"
            "  \"some-package>=1.2,<2\"",
            file=sys.stderr,
        )
        return 1

    print(f"OK: {len(deps)} dependencies in [project.dependencies], all have upper bounds.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
