"""Regression test: the tech-debt workflow must only comment on #544 when
markers are actually found — not unconditionally every Monday."""

import re
from pathlib import Path

WORKFLOW = (
    Path(__file__).resolve().parent.parent
    / ".github"
    / "workflows"
    / "tech-debt-discovery.yml"
)


def test_comment_step_is_gated_on_found_output() -> None:
    content = WORKFLOW.read_text()
    assert re.search(
        r"if:\s*steps\.scan\.outputs\.found\s*==\s*'true'", content
    ), "comment step must be gated on steps.scan.outputs.found == 'true'"


def test_scan_step_exports_found_output() -> None:
    content = WORKFLOW.read_text()
    assert "found=" in content and "$GITHUB_OUTPUT" in content, (
        "scan step must export a `found` boolean to $GITHUB_OUTPUT"
    )


def test_workflow_prose_has_no_bare_marker_tokens() -> None:
    content = WORKFLOW.read_text()
    for tok in ("FIXME", "TODO"):
        assert tok not in content, (
            f"workflow prose must not contain bare marker token {tok!r} "
            "(the scanner greps .yml and would self-match)"
        )
