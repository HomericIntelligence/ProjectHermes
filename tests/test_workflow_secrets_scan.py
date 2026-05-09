"""Regression test: Gitleaks must use --exit-code 1 to enforce secret blocking."""

import pathlib
import re

_WORKFLOW = (
    pathlib.Path(__file__).parent.parent
    / ".github"
    / "workflows"
    / "_required.yml"
)


def test_gitleaks_advisory_exit_code_absent() -> None:
    """--exit-code 0 must not appear in the secrets-scan step."""
    content = _WORKFLOW.read_text()
    assert not re.search(r"--exit-code[= ]0", content), (
        "Gitleaks must not use --exit-code 0 (advisory-only); use --exit-code 1 "
        "so detected secrets block the PR from merging."
    )


def test_gitleaks_enforcing_exit_code_present() -> None:
    """--exit-code 1 must be present in the secrets-scan step."""
    content = _WORKFLOW.read_text()
    assert re.search(r"--exit-code[= ]1", content), (
        "Gitleaks must use --exit-code 1 to block PRs when secrets are detected."
    )


def test_gitleaks_step_has_no_continue_on_error() -> None:
    """The Run Gitleaks step must not have continue-on-error: true."""
    content = _WORKFLOW.read_text()
    # Find the Run Gitleaks block and ensure continue-on-error: true is absent
    # within it (stops at next step header or end of job).
    match = re.search(
        r"- name: Run Gitleaks\b.*?(?=\n      - name:|\Z)",
        content,
        re.DOTALL,
    )
    assert match is not None, "Could not find 'Run Gitleaks' step in workflow."
    step_block = match.group(0)
    assert "continue-on-error: true" not in step_block, (
        "The 'Run Gitleaks' step must not set continue-on-error: true — "
        "that would suppress the non-zero exit code and make enforcement useless."
    )
