"""Unit tests for the no-bare-crypto-in-tests lint (issue #468)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "check-no-bare-crypto-in-tests.py"


def _run(cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def _make_tmp_repo(tmp_path: Path) -> Path:
    """Initialise a minimal git repo with helpers.py in tests/."""
    (tmp_path / "tests").mkdir()
    return tmp_path


def _commit_all(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        [
            "git",
            "-c", "user.email=t@t",
            "-c", "user.name=t",
            "commit", "-q", "-m", "x",
            "--no-gpg-sign",
        ],
        cwd=repo,
        check=True,
    )


def test_repo_passes_check() -> None:
    """Current tree satisfies the rule — helpers.py is the only allowed importer."""
    repo_root = Path(__file__).parent.parent
    result = _run(repo_root)
    assert result.returncode == 0, result.stderr


def test_offending_file_is_rejected(tmp_path: Path) -> None:
    """A test file with `import hmac` outside the allowlist must fail the check."""
    repo = _make_tmp_repo(tmp_path)
    (repo / "tests" / "helpers.py").write_text("import hmac\n")  # allowlisted
    (repo / "tests" / "test_bad.py").write_text("import hmac\n")  # forbidden
    _commit_all(repo)

    result = _run(repo)
    assert result.returncode == 1
    # test_bad.py must appear as a violation; helpers.py must NOT appear as a violation line
    assert "tests/test_bad.py:1:" in result.stderr
    assert "tests/helpers.py:1:" not in result.stderr


def test_from_import_form_is_rejected(tmp_path: Path) -> None:
    """`from hashlib import sha256` is forbidden — not just `import hashlib`."""
    repo = _make_tmp_repo(tmp_path)
    (repo / "tests" / "helpers.py").write_text("")
    (repo / "tests" / "test_bad.py").write_text("from hashlib import sha256\n")
    _commit_all(repo)

    result = _run(repo)
    assert result.returncode == 1
    assert "from hashlib" in result.stderr


def test_helpers_only_is_clean(tmp_path: Path) -> None:
    """When only helpers.py imports hmac/hashlib and no other test file does, check passes."""
    repo = _make_tmp_repo(tmp_path)
    (repo / "tests" / "helpers.py").write_text("import hmac\nimport hashlib\n")
    (repo / "tests" / "test_clean.py").write_text(
        "from tests.helpers import sign_body, TEST_SECRET\n"
    )
    _commit_all(repo)

    result = _run(repo)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "source",
    [
        "import hmac",
        "import hashlib",
        "from hmac import new",
        "from hashlib import sha256",
        "import hmac as h",
        "from hashlib import sha256, sha512",
    ],
)
def test_all_forbidden_import_forms(tmp_path: Path, source: str) -> None:
    """All import forms referencing hmac or hashlib are rejected in non-helper files."""
    repo = _make_tmp_repo(tmp_path)
    (repo / "tests" / "helpers.py").write_text("")
    (repo / "tests" / "test_offender.py").write_text(f"{source}\n")
    _commit_all(repo)

    result = _run(repo)
    assert result.returncode == 1, f"expected failure for: {source!r}"
