"""Regression test: Gitleaks must use --exit-code 1 to enforce secret blocking."""

import pathlib
import re
import tomllib

_WORKFLOW = pathlib.Path(__file__).parent.parent / ".github" / "workflows" / "_required.yml"

_GITLEAKS_CONFIG = pathlib.Path(__file__).parent.parent / ".gitleaks.toml"
_GITLEAKS_TOML = _GITLEAKS_CONFIG


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


def test_gitleaks_config_extends_default_rules() -> None:
    """'.gitleaks.toml' must declare 'useDefault = true' under the '[extend]' section.

    Without this stanza, a config file with only an [allowlist] section causes
    Gitleaks to load zero built-in rules — the gate passes silently even when
    real secrets are committed.  This was the root-cause bug found during the
    issue-#507 E2E verification.

    The assertion is scoped so that 'useDefault = true' must appear inside the
    '[extend]' table specifically (i.e. before the next TOML section header).
    A 'useDefault = true' line under a different section (e.g. '[allowlist]')
    would NOT enable the default ruleset and must not satisfy this test.
    """
    content = _GITLEAKS_CONFIG.read_text()
    assert re.search(
        r"^\[extend\][^\[]*?^useDefault\s*=\s*true",
        content,
        re.MULTILINE | re.DOTALL,
    ), (
        ".gitleaks.toml must set 'useDefault = true' directly under the [extend] "
        "section so that Gitleaks loads its default ruleset.  Without this, the "
        "config replaces all built-in rules and the gate never fires.  A "
        "'useDefault = true' line under any other TOML section does not count."
    )


def test_gitleaks_upload_sarif_uses_if_always() -> None:
    """Upload Gitleaks SARIF step must use 'if: always()' so findings reach the artifact
    even when the scan exits non-zero (i.e. when secrets ARE found).

    Without 'if: always()', the upload step is skipped exactly when there are findings,
    making the SARIF artifact absent precisely when reviewers need it most.
    """
    content = _WORKFLOW.read_text()
    match = re.search(
        r"- name: Upload Gitleaks SARIF\b.*?(?=\n      - name:|\n  \w|\Z)",
        content,
        re.DOTALL,
    )
    assert match is not None, "Could not find 'Upload Gitleaks SARIF' step in workflow."
    upload_block = match.group(0)
    assert "if: always()" in upload_block, (
        "The 'Upload Gitleaks SARIF' step must use 'if: always()' — "
        "without it, the SARIF artifact is skipped exactly when findings exist "
        "(the scan step fails the job, skipping all later steps)."
    )


def test_gitleaks_checkout_uses_full_history() -> None:
    """Checkout must use fetch-depth: 0 so gitleaks scans the full git history.

    A shallow clone (fetch-depth: 1) would miss secrets committed in earlier history.
    """
    content = _WORKFLOW.read_text()
    secrets_scan_job = re.search(
        r"security-secrets-scan:.*?(?=\n  \w|\Z)",
        content,
        re.DOTALL,
    )
    assert secrets_scan_job is not None, "Could not find 'security-secrets-scan' job in workflow."
    job_block = secrets_scan_job.group(0)
    assert "fetch-depth: 0" in job_block, (
        "The secrets-scan checkout must set fetch-depth: 0 — "
        "a shallow clone misses secrets in earlier commits."
    )


def test_gitleaks_sarif_artifact_name() -> None:
    """The SARIF upload artifact must be named 'gitleaks-report'.

    The smoke test procedure (issue #487) downloads this artifact by name to verify
    the finding is attributed to the planted file; changing the name silently breaks
    the evidence-gathering step.
    """
    content = _WORKFLOW.read_text()
    match = re.search(
        r"- name: Upload Gitleaks SARIF\b.*?(?=\n      - name:|\n  \w|\Z)",
        content,
        re.DOTALL,
    )
    assert match is not None, "Could not find 'Upload Gitleaks SARIF' step in workflow."
    upload_block = match.group(0)
    assert "name: gitleaks-report" in upload_block, (
        "The Gitleaks SARIF artifact must be named 'gitleaks-report' — "
        "the smoke test downloads it by this name to verify CI failure attribution."
    )


def test_gitleaks_allowlist_does_not_cover_repo_root() -> None:
    """The .gitleaks.toml allowlist must not match files at the repo root.

    The smoke test plants a dummy secret at the repo root (gitleaks-smoke.txt).
    If the allowlist is misconfigured to cover the root, gitleaks would silently
    pass, producing a false green CI result that invalidates the smoke test.
    """
    data = tomllib.loads(_GITLEAKS_TOML.read_text())
    paths: list[str] = data.get("allowlist", {}).get("paths", [])

    # Gitleaks evaluates allowlist.paths as unanchored regex searches
    # (Go regexp.MatchString), so mirror that with re.search rather than
    # re.fullmatch to actually guard the precondition this test protects.
    root_patterns = [p for p in paths if re.search(p, "gitleaks-smoke.txt") is not None]
    assert root_patterns == [], (
        f"The allowlist patterns {root_patterns!r} match 'gitleaks-smoke.txt' at the "
        "repo root — a secret planted there would be silently ignored, making the "
        "smoke test produce a false green result."
    )
