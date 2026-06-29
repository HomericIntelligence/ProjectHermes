"""Static checks on requirements.lock.txt — runs in CI without a Docker daemon."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
LOCK = ROOT / "requirements.lock.txt"
PYPROJECT = ROOT / "pyproject.toml"

PIN_RE = re.compile(r"^(?P<name>[A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?==(?P<ver>[^ \\]+)", re.M)
HASH_RE = re.compile(r"--hash=sha256:[0-9a-f]{64}")


def _direct_prod_names() -> set[str]:
    data = tomllib.loads(PYPROJECT.read_text())
    out = set()
    for entry in data["project"]["dependencies"]:
        name = re.split(r"[\[<>=! ;]", entry, maxsplit=1)[0]
        out.add(re.sub(r"[-_.]+", "-", name).lower())
    return out


def test_lock_file_exists() -> None:
    assert LOCK.exists(), "requirements.lock.txt missing — run `just lock`"


def test_every_pin_has_a_hash() -> None:
    body = LOCK.read_text()
    pins = list(PIN_RE.finditer(body))
    assert pins, "no name==ver pins found in lock"
    for m in pins:
        block_start = m.start()
        next_pin = PIN_RE.search(body, m.end())
        block_end = next_pin.start() if next_pin else len(body)
        assert HASH_RE.search(body[block_start:block_end]), (
            f"{m['name']}=={m['ver']} has no --hash=sha256: line"
        )


def test_every_direct_prod_dep_is_pinned() -> None:
    pinned = {
        re.sub(r"[-_.]+", "-", m["name"]).lower()
        for m in PIN_RE.finditer(LOCK.read_text())
    }
    missing = _direct_prod_names() - pinned
    assert not missing, f"direct prod deps missing from lock: {sorted(missing)}"


def test_no_dev_only_packages_in_lock() -> None:
    """pip-compile against [project.dependencies] only must not drag in dev tools."""
    body = LOCK.read_text().lower()
    for forbidden in ("pytest==", "ruff==", "mypy==", "pip-audit==", "pre-commit=="):
        assert forbidden not in body, f"{forbidden} leaked into prod lock"
