from __future__ import annotations

from pathlib import Path

import pytest

from orch.gate import GateSpec, run_gate

from .conftest import git

GLOBS = ("tests/**", "**/test_*.py")


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, str]:
    r = tmp_path / "r"
    (r / "tests").mkdir(parents=True)
    git(r, "init", "-q", "-b", "main")
    (r / "tests" / "test_a.py").write_text(
        "def test_one():\n    pass\n\n\ndef test_two():\n    pass\n"
    )
    (r / "QA.md").write_text("oracles\n")
    git(r, "add", "-A")
    git(r, "commit", "-qm", "base")
    return r, git(r, "rev-parse", "HEAD")


def _commit(r: Path) -> None:
    git(r, "add", "-A")
    git(r, "commit", "-qm", "change")


def _violations(r: Path, base: str) -> list[str]:
    return run_gate(r, GateSpec("", GLOBS, ("QA.md",)), base).violations


def test_clean_change_passes(repo: tuple[Path, str]) -> None:
    r, base = repo
    (r / "tests" / "test_b.py").write_text("def test_three():\n    pass\n")
    _commit(r)
    res = run_gate(r, GateSpec("true", GLOBS, ("QA.md",)), base)
    assert (res.ok, res.violations) == (True, [])


def test_new_skip_is_not_evidence(repo: tuple[Path, str]) -> None:
    r, base = repo
    (r / "tests" / "test_b.py").write_text(
        "import pytest\n\n@pytest.mark.skip\ndef test_three():\n    pass\n"
    )
    _commit(r)
    assert _violations(r, base) == [
        "new skip/xfail in tests/test_b.py: @pytest.mark.skip"
    ]


def test_deleted_test_function_and_file(repo: tuple[Path, str]) -> None:
    r, base = repo
    (r / "tests" / "test_a.py").unlink()
    _commit(r)
    assert _violations(r, base) == [
        "deleted test file tests/test_a.py",
        "deleted test test_one",
        "deleted test test_two",
    ]


def test_moved_test_is_not_deleted(repo: tuple[Path, str]) -> None:
    r, base = repo
    (r / "tests" / "test_a.py").write_text("def test_one():\n    pass\n")
    (r / "tests" / "test_c.py").write_text("def test_two():\n    assert 1\n")
    _commit(r)
    assert _violations(r, base) == []


def test_qa_md_edit_and_dirty_tree(repo: tuple[Path, str]) -> None:
    r, base = repo
    (r / "QA.md").write_text("weaker oracles\n")
    _commit(r)
    (r / "scratch.txt").write_text("x")
    assert _violations(r, base) == [
        "edited protected file QA.md (only humans change it)",
        "uncommitted changes: scratch.txt",
    ]


def test_failing_command_fails_gate(repo: tuple[Path, str]) -> None:
    r, base = repo
    res = run_gate(r, GateSpec("echo nope; exit 3", GLOBS, ()), base)
    assert (res.ok, res.returncode, res.output.strip()) == (False, 3, "nope")
    assert res.summary() == "`echo nope; exit 3` exited 3"
