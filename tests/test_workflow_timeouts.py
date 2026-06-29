"""Regression tests for scripts/check-workflow-timeouts.sh.

Guards issue #420: every job in .github/workflows/*.yml MUST declare
timeout-minutes. The JSON-Schema validator cannot enforce this semantic
rule, so we ship a custom guard and these tests pin its behaviour.
"""

from __future__ import annotations

import pathlib
import subprocess
import textwrap

import yaml

_REPO = pathlib.Path(__file__).parent.parent
_WORKFLOWS = _REPO / ".github" / "workflows"
_SCRIPT = _REPO / "scripts" / "check-workflow-timeouts.sh"


def test_every_job_has_timeout_minutes() -> None:
    """Each job in every shipped workflow declares a positive timeout-minutes."""
    missing: list[str] = []
    for wf in sorted(_WORKFLOWS.glob("*.yml")):
        doc = yaml.safe_load(wf.read_text())
        for job_id, job in (doc.get("jobs") or {}).items():
            if isinstance(job, dict) and "uses" in job:
                continue  # reusable-workflow caller — field not permitted
            tm = job.get("timeout-minutes") if isinstance(job, dict) else None
            if not isinstance(tm, int) or tm <= 0:
                missing.append(f"{wf.name}:{job_id}")
    assert not missing, (
        "Jobs missing a positive integer `timeout-minutes`: "
        + ", ".join(missing)
    )


def test_script_flags_missing_timeout(tmp_path: pathlib.Path) -> None:
    """The guard rejects a synthetic workflow that omits timeout-minutes."""
    repo = tmp_path / "repo"
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / "scripts").mkdir()
    (repo / "scripts" / "check-workflow-timeouts.sh").write_bytes(
        _SCRIPT.read_bytes()
    )
    (repo / "scripts" / "check-workflow-timeouts.sh").chmod(0o755)
    (repo / ".github" / "workflows" / "bad.yml").write_text(
        textwrap.dedent(
            """\
            name: bad
            on: [push]
            jobs:
              no-timeout:
                runs-on: ubuntu-latest
                steps:
                  - run: echo hi
            """
        )
    )
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-q", "-m", "init"],
        cwd=repo, check=True,
    )
    result = subprocess.run(
        ["bash", "scripts/check-workflow-timeouts.sh"],
        cwd=repo, capture_output=True, text=True,
    )
    assert result.returncode == 1, (
        f"expected exit 1, got {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "no-timeout" in result.stdout
    assert "missing required `timeout-minutes`" in result.stdout


def test_script_accepts_clean_workflow(tmp_path: pathlib.Path) -> None:
    """The guard passes (exit 0) when every job has a positive timeout-minutes."""
    repo = tmp_path / "repo"
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / "scripts").mkdir()
    (repo / "scripts" / "check-workflow-timeouts.sh").write_bytes(
        _SCRIPT.read_bytes()
    )
    (repo / "scripts" / "check-workflow-timeouts.sh").chmod(0o755)
    (repo / ".github" / "workflows" / "ok.yml").write_text(
        textwrap.dedent(
            """\
            name: ok
            on: [push]
            jobs:
              fine:
                runs-on: ubuntu-latest
                timeout-minutes: 5
                steps:
                  - run: echo hi
            """
        )
    )
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-q", "-m", "init"],
        cwd=repo, check=True,
    )
    result = subprocess.run(
        ["bash", "scripts/check-workflow-timeouts.sh"],
        cwd=repo, capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        f"expected exit 0, got {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "OK" in result.stdout
