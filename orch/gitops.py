"""Git operations orch needs: run branch, commits of its own files, push,
detached worktrees, and diff inspection for the gate."""

from __future__ import annotations

import subprocess
from pathlib import Path

from orch.errors import GitError


def git(cwd: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(  # noqa: S603 -- fixed git argv
        ["git", *args],  # noqa: S607 -- git from PATH, like the user's shell
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if check and proc.returncode != 0:
        raise GitError(
            f"git {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}"
        )
    return proc.stdout.rstrip("\n")  # keep leading spaces: porcelain needs them


def head(cwd: Path) -> str:
    return git(cwd, "rev-parse", "HEAD").strip()


def current_branch(cwd: Path) -> str:
    return git(cwd, "rev-parse", "--abbrev-ref", "HEAD")


def is_clean(cwd: Path, exclude_orch: bool = False) -> bool:
    out = git(cwd, "status", "--porcelain", "--untracked-files=all")
    lines = [ln for ln in out.splitlines() if ln.strip()]
    if exclude_orch:
        lines = [ln for ln in lines if not ln[3:].startswith(".orch/")]
    return not lines


def dirty_files(cwd: Path) -> list[str]:
    out = git(cwd, "status", "--porcelain", "--untracked-files=all")
    return [ln[3:] for ln in out.splitlines() if ln.strip()]


def ensure_branch(cwd: Path, branch: str) -> None:
    """Switch to `branch`, creating it from HEAD if it does not exist."""
    if current_branch(cwd) == branch:
        return
    exists = git(cwd, "rev-parse", "--verify", "--quiet", branch, check=False)
    if exists:
        git(cwd, "switch", branch)
    else:
        git(cwd, "switch", "-c", branch)


def commit_paths(cwd: Path, paths: list[str], message: str) -> str | None:
    """Commit only `paths` (orch's own files). Returns the sha, or None when there
    was nothing to commit. Skips hooks: orch's files are not code."""
    git(cwd, "add", "--all", "--", *paths)
    if not git(cwd, "diff", "--cached", "--name-only", "--", *paths):
        return None
    git(cwd, "commit", "--quiet", "--no-verify", "-m", message, "--", *paths)
    return head(cwd)


def has_remote(cwd: Path, remote: str) -> bool:
    return remote in git(cwd, "remote").split()


def push(cwd: Path, remote: str, branch: str) -> None:
    git(cwd, "push", "--quiet", "-u", remote, branch)


def add_worktree(cwd: Path, path: Path, ref: str = "HEAD") -> Path:
    git(cwd, "worktree", "add", "--detach", "--quiet", str(path), ref)
    return path


def remove_worktree(cwd: Path, path: Path) -> None:
    git(cwd, "worktree", "remove", "--force", str(path), check=False)
    git(cwd, "worktree", "prune", check=False)


# the gate judges the project's code, not orch's own records
NOT_ORCH = ("--", ".", ":(exclude).orch")


def name_status(cwd: Path, base: str, ref: str = "HEAD") -> list[tuple[str, str]]:
    """(status letter, path) for every file outside .orch changed between base
    and ref."""
    out = git(cwd, "diff", "--name-status", "--no-renames", f"{base}..{ref}", *NOT_ORCH)
    rows = []
    for line in out.splitlines():
        status, _, path = line.partition("\t")
        rows.append((status[:1], path))
    return rows


def added_lines(cwd: Path, base: str, ref: str = "HEAD") -> list[tuple[str, str]]:
    """(path, line) for every line added between base and ref."""
    out = git(cwd, "diff", "--unified=0", "--no-renames", f"{base}..{ref}", *NOT_ORCH)
    rows: list[tuple[str, str]] = []
    path = ""
    for line in out.splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else ""
        elif line.startswith("+") and not line.startswith("+++"):
            rows.append((path, line[1:]))
    return rows


def removed_lines(cwd: Path, base: str, ref: str = "HEAD") -> list[tuple[str, str]]:
    out = git(cwd, "diff", "--unified=0", "--no-renames", f"{base}..{ref}", *NOT_ORCH)
    rows: list[tuple[str, str]] = []
    path = ""
    for line in out.splitlines():
        if line.startswith("--- "):
            path = line[6:] if line.startswith("--- a/") else ""
        elif line.startswith("-") and not line.startswith("---"):
            rows.append((path, line[1:]))
    return rows


def log_oneline(cwd: Path, base: str, ref: str = "HEAD") -> list[str]:
    out = git(cwd, "log", "--oneline", "--no-decorate", f"{base}..{ref}")
    return [ln for ln in out.splitlines() if ln]


ORCH_COMMIT_PREFIX = "orch: "


def files_in_session_commits(cwd: Path, base: str, ref: str = "HEAD") -> set[str]:
    """Files touched by commits between base and ref that orch did not make."""
    out = git(cwd, "log", "--format=%x00%s", "--name-only", f"{base}..{ref}")
    files: set[str] = set()
    for chunk in out.split("\x00")[1:]:
        lines = chunk.splitlines()
        if not lines or lines[0].startswith(ORCH_COMMIT_PREFIX):
            continue
        files |= {ln for ln in lines[1:] if ln.strip()}
    return files
