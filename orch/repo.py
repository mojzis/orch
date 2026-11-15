"""Locating the project repo, its config (.orch/ in the repo) and its run files
(in orch's home, see orch.home)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, replace
from functools import cached_property
from pathlib import Path
from typing import Any

from orch import home as H
from orch.errors import ConfigError

DEFAULT_CONFIG: dict[str, Any] = {
    "gate_cmd": "",
    "setup_cmd": "",
    "max_fix_rounds": 2,
    "remote": "origin",
    "push": True,
    "open_pr": False,
    "test_globs": [
        "tests/**",
        "test/**",
        "test_*.py",
        "*_test.py",
        "**/test_*.py",
        "**/*_test.py",
    ],
    # untracked build and tool caches the clean-tree check ignores
    "dirty_ignore": [
        "*__pycache__/*",
        "*.pyc",
        ".pytest_cache/*",
        ".ruff_cache/*",
        ".mypy_cache/*",
        ".coverage",
        "node_modules/*",
    ],
    # files in the repo a session must not change: oracles and orch's own config
    # (briefs live in the run dir and are checked by hash, see loop._step)
    "protected": [
        "QA.md",
        ".orch/config.toml",
        ".orch/skills/*",
        ".orch/scenarios/*",
        ".orch/phases/*/brief.md",
    ],
}


@dataclass(frozen=True)
class Repo:
    root: Path
    run_id: str | None = None  # the run the paths below are for

    @property
    def orch(self) -> Path:
        """The repo's own .orch/: config, scenarios, skills, hand-written briefs."""
        return self.root / ".orch"

    @cached_property
    def base(self) -> Path | None:
        """orch's home, or None when the run files live in the repo."""
        return H.orch_home()

    @property
    def in_repo(self) -> bool:
        return self.base is None

    @cached_property
    def home(self) -> Path:
        """This repo's files across runs: backlog, process learnings, runs/."""
        if self.base is None:
            return self.orch
        return self.base / "repos" / H.repo_key(self.root, str(self.config()["remote"]))

    @property
    def sessions_log(self) -> Path:
        return (self.base or self.orch) / "sessions.jsonl"

    @property
    def runs(self) -> Path:
        return self.home / "runs"

    @property
    def cache(self) -> Path:
        return self.home / (".cache" if self.in_repo else "cache")

    def with_run(self, run_id: str) -> Repo:
        return replace(self, run_id=run_id)

    @property
    def run_dir(self) -> Path:
        if self.run_id is None:
            raise ConfigError("no run selected")  # a bug: callers pick a run first
        return self.runs / self.run_id

    @property
    def state_path(self) -> Path:
        return self.run_dir / "state.json"

    def phase_dir(self, dirname: str) -> Path:
        return self.run_dir / "phases" / dirname

    def config(self) -> dict[str, Any]:
        p = self.orch / "config.toml"
        data: dict[str, Any] = {}
        if p.is_file():
            try:
                data = tomllib.loads(p.read_text(encoding="utf-8"))
            except tomllib.TOMLDecodeError as e:
                raise ConfigError(f"{p}: invalid TOML: {e}") from None
        return DEFAULT_CONFIG | {k: v for k, v in data.items() if k != "role"}


def find_repo(start: Path | None = None) -> Repo:
    here = (start or Path.cwd()).resolve()
    for d in (here, *here.parents):
        if (d / ".git").exists():
            return Repo(d)
    raise ConfigError(
        f"{here} is not inside a git repository",
        hint="run orch from the project repo",
    )


CONFIG_TEMPLATE = """\
# orch repo config. Layers and roles: `orch guide tune`.

# The gate: one command that must pass after every code change (tests + typecheck).
gate_cmd = ""
# Run once in each fresh review/QA worktree, e.g. "uv sync".
setup_cmd = ""
max_fix_rounds = 2
remote = "origin"
push = true
# Open a PR for the run branch when the run is done (needs gh); orch never merges.
open_pr = false

# Per-role overrides, e.g.:
# [role.dev]
# model = "sonnet"
# effort = "high"
"""


def init_repo(repo: Repo) -> Path:
    path = repo.orch / "config.toml"
    if path.exists():
        raise ConfigError(f"{path} already exists", hint="edit it in place")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(CONFIG_TEMPLATE, encoding="utf-8")
    return path
