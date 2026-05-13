"""Tests for docker-compose.yml resource limits."""

from __future__ import annotations

import pathlib

import pytest
import yaml

COMPOSE_PATH = pathlib.Path(__file__).parent.parent / "docker-compose.yml"
SERVICES = ["nats", "hermes"]


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text())


@pytest.mark.parametrize("service", SERVICES)
def test_deploy_resources_limits_cpu(compose: dict, service: str) -> None:
    """Each service must declare a CPU limit under deploy.resources.limits."""
    limits = compose["services"][service]["deploy"]["resources"]["limits"]
    assert "cpus" in limits, f"{service}: missing cpus limit"
    assert float(limits["cpus"]) > 0, f"{service}: cpus limit must be positive"


@pytest.mark.parametrize("service", SERVICES)
def test_deploy_resources_limits_memory(compose: dict, service: str) -> None:
    """Each service must declare a memory limit under deploy.resources.limits."""
    limits = compose["services"][service]["deploy"]["resources"]["limits"]
    assert "memory" in limits, f"{service}: missing memory limit"
    assert limits["memory"], f"{service}: memory limit must be non-empty"


@pytest.mark.parametrize("service", SERVICES)
def test_deploy_resources_reservations_cpu(compose: dict, service: str) -> None:
    """Each service must declare a CPU reservation under deploy.resources.reservations."""
    reservations = compose["services"][service]["deploy"]["resources"]["reservations"]
    assert "cpus" in reservations, f"{service}: missing cpus reservation"
    assert float(reservations["cpus"]) > 0, f"{service}: cpus reservation must be positive"


@pytest.mark.parametrize("service", SERVICES)
def test_deploy_resources_reservations_memory(compose: dict, service: str) -> None:
    """Each service must declare a memory reservation under deploy.resources.reservations."""
    reservations = compose["services"][service]["deploy"]["resources"]["reservations"]
    assert "memory" in reservations, f"{service}: missing memory reservation"
    assert reservations["memory"], f"{service}: memory reservation must be non-empty"


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
