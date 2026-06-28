"""Regression test: publish workflow must emit SLSA provenance + SBOM attestations.

Every assertion uses yaml.safe_load and navigates the parsed object so a flag
in the wrong YAML context (e.g. workflow-level, or a different step) cannot
satisfy the test. PyYAML is declared in pixi.toml under feature.dev so this
test is not relying on a transitive dependency.
"""

import pathlib

import yaml

_WORKFLOW_PATH = (
    pathlib.Path(__file__).parent.parent
    / ".github"
    / "workflows"
    / "publish.yml"
)


def _workflow() -> dict:
    return yaml.safe_load(_WORKFLOW_PATH.read_text())


def _publish_job() -> dict:
    return _workflow()["jobs"]["publish"]


def _build_push_step() -> dict:
    for step in _publish_job()["steps"]:
        if step.get("name") == "Build and push":
            return step
    raise AssertionError("Could not locate the 'Build and push' step in publish.yml")


def test_publish_job_has_id_token_write() -> None:
    perms = _publish_job().get("permissions", {})
    assert perms.get("id-token") == "write", (
        "publish job must declare 'id-token: write' so docker/build-push-action "
        "can sign provenance via GitHub OIDC. See ADR-004 for the security tradeoff."
    )


def test_publish_job_has_attestations_write() -> None:
    perms = _publish_job().get("permissions", {})
    assert perms.get("attestations") == "write", (
        "publish job must declare 'attestations: write' so the build attestation "
        "can be uploaded to the GitHub attestation store."
    )


def test_publish_job_keeps_packages_write() -> None:
    perms = _publish_job().get("permissions", {})
    assert perms.get("packages") == "write", (
        "Existing GHCR push permission must not be regressed."
    )


def test_build_push_step_enables_max_provenance() -> None:
    """The provenance flag must be on the Build and push step's `with:` block,
    not anywhere else in the file. mode=max emits SLSA Level 2 with materials."""
    with_block = _build_push_step().get("with", {})
    assert with_block.get("provenance") == "mode=max", (
        "Build and push step must set with.provenance: mode=max (SLSA L2 with "
        "materials). Got: %r" % with_block.get("provenance")
    )


def test_build_push_step_enables_sbom() -> None:
    with_block = _build_push_step().get("with", {})
    assert with_block.get("sbom") is True, (
        "Build and push step must set with.sbom: true to attach an SPDX SBOM "
        "attestation. Got: %r" % with_block.get("sbom")
    )


def test_build_push_step_still_pushes() -> None:
    """Don't regress: attestation flags must NOT have displaced push: true."""
    with_block = _build_push_step().get("with", {})
    assert with_block.get("push") is True, "Build step must still push to GHCR."
