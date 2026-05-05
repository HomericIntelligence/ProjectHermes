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
