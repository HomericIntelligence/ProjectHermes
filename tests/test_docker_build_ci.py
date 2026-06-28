"""Regression tests: CI must build the Docker image on Dockerfile PRs (issue #562)."""

from __future__ import annotations

import pathlib
import re

import yaml

_WORKFLOW = (
    pathlib.Path(__file__).parent.parent
    / ".github"
    / "workflows"
    / "docker-build.yml"
)


def _workflow_text() -> str:
    return _WORKFLOW.read_text()


def _workflow_doc() -> dict:
    return yaml.safe_load(_workflow_text())


def test_docker_build_workflow_exists() -> None:
    assert _WORKFLOW.exists(), "docker-build.yml workflow not found (issue #562)"


def test_docker_build_job_present() -> None:
    """The workflow must define the docker-build-smoke job."""
    doc = _workflow_doc()
    assert "docker-build-smoke" in doc.get("jobs", {}), (
        "docker-build.yml must define a 'docker-build-smoke' job (issue #562)"
    )


def test_docker_build_invokes_buildx_with_load() -> None:
    """The job must run a docker build of the repo Dockerfile, loaded locally."""
    text = _workflow_text()
    assert re.search(r"docker\s+buildx\s+build", text), (
        "docker-build-smoke must invoke 'docker buildx build'"
    )
    assert "--load" in text, (
        "docker buildx build must use --load (build locally, no --push)"
    )


def test_docker_build_trigger_is_path_scoped_to_dockerfile() -> None:
    """Acceptance criterion: runs on PRs touching the Dockerfile.

    The pull_request trigger must list Dockerfile in its paths filter.
    """
    doc = _workflow_doc()
    # PyYAML parses the bare `on:` key as the boolean True.
    trigger = doc.get("on", doc.get(True, {}))
    paths = trigger.get("pull_request", {}).get("paths", [])
    assert "Dockerfile" in paths, (
        "docker-build.yml pull_request trigger must be path-scoped to 'Dockerfile' "
        "so the build runs on every PR touching the Dockerfile (issue #562)"
    )


def test_docker_build_job_is_blocking() -> None:
    """The job must not soft-fail via continue-on-error."""
    text = _workflow_text()
    assert "continue-on-error: true" not in text, (
        "docker-build-smoke must be blocking — no continue-on-error: true"
    )
