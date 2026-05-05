"""Pre-commit hook: verify the [Unreleased] section of CHANGELOG.md is populated.

Exits non-zero if the [Unreleased] block contains fewer than MIN_ENTRIES non-empty,
non-heading lines, which would indicate placeholder-only content.
"""

from __future__ import annotations

import sys
from pathlib import Path

CHANGELOG = Path("CHANGELOG.md")
MIN_ENTRIES = 3


def count_unreleased_entries(text: str) -> int:
    """Return the number of substantive lines in the [Unreleased] block."""
    in_unreleased = False
    count = 0
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "## [Unreleased]":
            in_unreleased = True
            continue
        if in_unreleased:
            # Next versioned section ends the block
            if stripped.startswith("## [") and stripped != "## [Unreleased]":
                break
            # Skip blanks and category headings (### Added, etc.)
            if not stripped or stripped.startswith("#"):
                continue
            count += 1
    return count


def main() -> int:
    if not CHANGELOG.exists():
        print(f"error: {CHANGELOG} not found", file=sys.stderr)
        return 1

    text = CHANGELOG.read_text(encoding="utf-8")
    count = count_unreleased_entries(text)
    if count < MIN_ENTRIES:
        print(
            f"error: CHANGELOG.md [Unreleased] section has only {count} "
            f"entr{'y' if count == 1 else 'ies'} (minimum {MIN_ENTRIES}).\n"
            "Please document your changes before committing.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
