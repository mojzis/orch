"""Builders for loop tests: a project repo with a remote, canned reports."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .conftest import git

DEV_OK: dict[str, Any] = {
    "summary": "did it",
    "commits": ["abc did it"],
    "tests": {"before": 1, "after": 2, "explained": "added one"},
    "design": "simple",
    "real_run": "none",
    "deviations": [],
    "unresolved_issues": [],
}
LEARN: dict[str, Any] = {
    "phase_learnings": "went fine",
    "process_lessons": ["keep briefs short"],
    "handoff": "module x exists now",
}


def checker(verdict: str = "ship", *findings: dict[str, Any]) -> dict[str, Any]:
    return {
        "verdict": verdict,
        "findings": list(findings),
        "checks_run": [{"check": "c", "result": "pass", "evidence": "ok"}],
        "preexisting": [],
        "proposed_oracles": [],
    }


def finding(
    fid: str, severity: str = "medium", title: str = "broken"
) -> dict[str, Any]:
    return {
        "id": fid,
        "severity": severity,
        "title": title,
        "detail": "d",
        "repro": "r",
        "files": [],
    }


def dev(name: str, *extra: dict[str, Any], report: dict | None = None) -> dict:
    """A dev/fix response that appends to `name` and commits."""
    return {
        "structured": report or DEV_OK,
        "actions": [
            {"append": name, "content": "x\n"},
            *extra,
            {"commit": f"work on {name}"},
        ],
    }


def make_project(tmp_path: Path, phases: int = 3, config: str = "") -> Path:
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    repo = tmp_path / "project"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("project\n")
    (repo / ".orch").mkdir()
    (repo / ".orch" / "config.toml").write_text(
        'gate_cmd = "test ! -f BROKEN"\nmax_fix_rounds = 2\n' + config
    )
    for n in range(1, phases + 1):
        d = repo / ".orch" / "phases" / f"{n:02d}-p{n}"
        d.mkdir(parents=True)
        (d / "brief.md").write_text(f"# Phase {n}\n\n## Goal\n\nstep {n}\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-q", "-u", "origin", "main")
    return repo


def run_dir(project: Path) -> Path:
    """The current run's files, in the test's orch home."""
    from orch.loop import select_run  # noqa: PLC0415 -- conftest pins env first
    from orch.repo import Repo  # noqa: PLC0415

    return select_run(Repo(project)).run_dir


def repo_home(project: Path) -> Path:
    """The repo's files across runs (backlog, process learnings)."""
    from orch.repo import Repo  # noqa: PLC0415

    return Repo(project).home
