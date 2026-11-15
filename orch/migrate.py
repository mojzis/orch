"""`orch migrate`: move run files an older orch left in the repo's .orch/ to
where this orch keeps them (orch's home, or .orch/runs/<id>/ with home = "repo")."""

from __future__ import annotations

import shutil
from pathlib import Path

from orch import state as S
from orch.errors import ConfigError
from orch.repo import Repo

# what an older orch kept at the top of .orch/ for the current run
RUN_FILES = (
    "state.json",
    "PROGRESS.md",
    "plan.md",
    "plan",
    "replaced",
    "report.md",
    "full-qa.json",
    "push-failures.log",
)
REPO_FILES = ("backlog.md", "process-learnings.md")


def planned_moves(repo: Repo) -> list[tuple[Path, Path]]:
    """(from, to) for every legacy run file; empty when there is nothing to do."""
    old = repo.orch
    moves: list[tuple[Path, Path]] = []
    if (old / "state.json").is_file():
        try:
            st = S.load(old / "state.json")
        except (ValueError, TypeError, KeyError) as e:
            raise ConfigError(f"{old / 'state.json'} is unreadable: {e}") from None
        dest = repo.runs / st.run_id
        moves += [(old / n, dest / n) for n in RUN_FILES if (old / n).exists()]
        moves += [
            (old / "phases" / p.dirname, dest / "phases" / p.dirname)
            for p in st.phases
            if (old / "phases" / p.dirname).is_dir()
        ]
    if (old / "runs").is_dir() and old / "runs" != repo.runs:
        moves += [(d, repo.runs / d.name) for d in sorted((old / "runs").iterdir())]
    if old != repo.home:
        moves += [(old / n, repo.home / n) for n in REPO_FILES if (old / n).is_file()]
    return moves


def require_migrated(repo: Repo) -> None:
    if planned_moves(repo):
        raise ConfigError(
            f"{repo.orch} holds run files from an older orch",
            hint="run `orch migrate` to move them",
        )


def migrate(repo: Repo) -> list[tuple[Path, Path]]:
    """Move the legacy files. A learnings or backlog file that exists at both
    ends is appended; any other clash stops before anything moves."""
    moves = planned_moves(repo)
    clashes = [dst for src, dst in moves if dst.exists() and src.name not in REPO_FILES]
    if clashes:
        raise ConfigError(
            f"already there: {', '.join(map(str, clashes))}",
            hint="move or remove them by hand, then `orch migrate` again",
        )
    for src, dst in moves:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():  # backlog.md / process-learnings.md
            with dst.open("a", encoding="utf-8") as fh:
                fh.write("\n" + src.read_text(encoding="utf-8"))
            src.unlink()
        else:
            shutil.move(src, dst)
    if not repo.in_repo:
        shutil.rmtree(repo.orch / ".cache", ignore_errors=True)  # rebuilt each run
        gi = repo.orch / ".gitignore"
        if gi.is_file() and gi.read_text(encoding="utf-8").strip() == ".cache/":
            gi.unlink()
    for name in ("phases", "runs"):
        d = repo.orch / name
        if d.is_dir() and d != repo.runs and not any(d.iterdir()):
            d.rmdir()
    return moves
