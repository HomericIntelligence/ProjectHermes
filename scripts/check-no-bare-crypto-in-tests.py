#!/usr/bin/env python3
"""Forbid bare `hmac` / `hashlib` imports outside tests/helpers.py.

Regression guard for #468 (follow-up from #329). All test files must obtain
HMAC signing via `from tests.helpers import sign_body, TEST_SECRET` rather
than re-implementing `hmac.new(...).hexdigest()` inline. The only file
permitted to import the stdlib crypto modules is `tests/helpers.py`, which
is the canonical home of `sign_body()`.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

FORBIDDEN = {"hmac", "hashlib"}
ALLOWLIST = {Path("tests/helpers.py")}


def tracked_test_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "tests/"],
        check=True, capture_output=True, text=True,
    ).stdout.splitlines()
    return [Path(p) for p in out if p.endswith(".py")]


def offending_imports(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root in FORBIDDEN:
                    hits.append((node.lineno, f"import {alias.name}"))
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".", 1)[0] in FORBIDDEN:
                hits.append((node.lineno, f"from {node.module} import ..."))
    return hits


def main() -> int:
    failures: list[str] = []
    for path in tracked_test_files():
        if path in ALLOWLIST:
            continue
        for lineno, snippet in offending_imports(path):
            failures.append(f"{path}:{lineno}: forbidden `{snippet}`")
    if failures:
        print("ERROR: bare hmac/hashlib imports found in tests/ (issue #468):",
              file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        print(
            "\nImport `sign_body` and `TEST_SECRET` from `tests.helpers` "
            "instead — see tests/helpers.py.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
