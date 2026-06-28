"""Tests for docker-compose.yml resource limits."""

from __future__ import annotations

import pathlib
import re

import pytest
import yaml

COMPOSE_PATH = pathlib.Path(__file__).parent.parent / "docker-compose.yml"
SERVICES = ["nats", "hermes"]

_SUBST = re.compile(r"^\$\{[A-Z0-9_]+:-(?P<default>[^}]+)\}$")


def _default_of(value: str) -> str:
    """Extract the default from a Compose ``${VAR:-default}`` substitution."""
    m = _SUBST.match(str(value))
    assert m, f"expected ${{VAR:-default}} substitution, got: {value!r}"
    return m.group("default")


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text())


@pytest.mark.parametrize("service", SERVICES)
def test_deploy_resources_limits_cpu(compose: dict, service: str) -> None:
    """CPU limit must be an overridable substitution with a positive default."""
    limits = compose["services"][service]["deploy"]["resources"]["limits"]
    assert "cpus" in limits, f"{service}: missing cpus limit"
    assert float(_default_of(limits["cpus"])) > 0, f"{service}: cpus default must be positive"


@pytest.mark.parametrize("service", SERVICES)
def test_deploy_resources_limits_memory(compose: dict, service: str) -> None:
    """Memory limit must be an overridable substitution with a non-empty default."""
    limits = compose["services"][service]["deploy"]["resources"]["limits"]
    assert "memory" in limits, f"{service}: missing memory limit"
    assert _default_of(limits["memory"]), f"{service}: memory default must be non-empty"


@pytest.mark.parametrize("service", SERVICES)
def test_deploy_resources_reservations_cpu(compose: dict, service: str) -> None:
    """CPU reservation must be an overridable substitution with a positive default."""
    reservations = compose["services"][service]["deploy"]["resources"]["reservations"]
    assert "cpus" in reservations, f"{service}: missing cpus reservation"
    assert float(_default_of(reservations["cpus"])) > 0, f"{service}: cpus default must be positive"


@pytest.mark.parametrize("service", SERVICES)
def test_deploy_resources_reservations_memory(compose: dict, service: str) -> None:
    """Memory reservation must be an overridable substitution with a non-empty default."""
    reservations = compose["services"][service]["deploy"]["resources"]["reservations"]
    assert "memory" in reservations, f"{service}: missing memory reservation"
    assert _default_of(reservations["memory"]), f"{service}: memory default must be non-empty"


@pytest.mark.parametrize("service", SERVICES)
def test_deploy_resource_defaults_preserve_prior_values(compose: dict, service: str) -> None:
    """Substitution defaults must equal the original #348 hard-coded values."""
    res = compose["services"][service]["deploy"]["resources"]
    assert _default_of(res["limits"]["cpus"]) == "0.50"
    assert _default_of(res["limits"]["memory"]) == "256M"
    assert _default_of(res["reservations"]["cpus"]) == "0.10"
    assert _default_of(res["reservations"]["memory"]) == "64M"


# ---------------------------------------------------------------------------
# Read-only filesystem enforcement (regression guard for issue #570)
# ---------------------------------------------------------------------------


def test_hermes_service_is_read_only(compose: dict) -> None:
    """The hermes service must run with a read-only root filesystem.

    Regression guard for #570 and the read-only FS smoke test
    (`scripts/smoke-readonly-fs.sh`). If this flag is ever removed, the
    smoke test will start failing — but this unit test catches the
    regression at PR time without requiring Docker.
    """
    hermes = compose["services"]["hermes"]
    assert hermes.get("read_only") is True, (
        "hermes service must set `read_only: true` (security hardening, issue #570)"
    )


def test_hermes_service_has_tmpfs_tmp(compose: dict) -> None:
    """The hermes service must expose a writable tmpfs at /tmp.

    Required because `read_only: true` makes the root FS immutable, and
    several Python stdlib paths still need a writable scratch dir.
    """
    hermes = compose["services"]["hermes"]
    tmpfs = hermes.get("tmpfs") or []
    assert any(entry == "/tmp" or entry.startswith("/tmp:") for entry in tmpfs), (
        "hermes service must declare `tmpfs: [/tmp]` to pair with `read_only: true`"
    )


def test_smoke_readonly_fs_script_exists_and_executable() -> None:
    """The read-only FS smoke test script must exist and be executable."""
    script = COMPOSE_PATH.parent / "scripts" / "smoke-readonly-fs.sh"
    assert script.is_file(), f"missing smoke test: {script}"
    import os

    assert os.access(script, os.X_OK), f"smoke test not executable: {script}"
