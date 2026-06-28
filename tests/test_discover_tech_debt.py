"""Regression tests for scripts/discover-tech-debt.sh exit-code + sentinel contract.

The script must signal clean/dirty via EXIT CODE (so the CI workflow can gate
its #544 comment), and its CLEAN output must contain no bare marker tokens.
"""

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "discover-tech-debt.sh"
MARKER_TOKENS = ("FIXME", "TODO", "DEPRECATED", "HACK", "XXX")


def _run(cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


def test_script_exists() -> None:
    assert SCRIPT.is_file(), f"{SCRIPT} missing"


def test_clean_tree_exits_zero_with_token_free_sentinel(tmp_path: Path) -> None:
    (tmp_path / "ok.py").write_text("x = 1\n")
    res = _run(tmp_path)
    assert res.returncode == 0, res.stderr
    assert "clean" in res.stdout.lower()
    for tok in MARKER_TOKENS:
        assert tok not in res.stdout, f"clean output leaked marker token {tok!r}"


def test_dirty_tree_exits_nonzero_and_lists_marker(tmp_path: Path) -> None:
    (tmp_path / "bad.py").write_text("x = 1  # FIXME real debt\n")
    res = _run(tmp_path)
    assert res.returncode == 1, res.stderr
    assert "FIXME" in res.stdout
    assert "bad.py" in res.stdout


def test_excludes_build_and_claude_worktrees(tmp_path: Path) -> None:
    (tmp_path / "clean.py").write_text("x = 1\n")
    wt = tmp_path / "build" / ".worktrees" / "issue-1"
    wt.mkdir(parents=True)
    (wt / "copy.py").write_text("# TODO from a worktree copy\n")
    res = _run(tmp_path)
    assert res.returncode == 0, f"worktree marker should be excluded: {res.stdout}"
