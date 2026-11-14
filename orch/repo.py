"""Locating the project repo and orch's paths inside it."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orch.errors import ConfigError

DEFAULT_CONFIG: dict[str, Any] = {
    "gate_cmd": "",
    "setup_cmd": "",
    "max_fix_rounds": 2,
    "remote": "origin",
    "push": True,
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
    # files a session must not change: oracles, and orch's own config and briefs
    "protected": [
        "QA.md",
        ".orch/config.toml",
        ".orch/skills/*",
        ".orch/scenarios/*",
        ".orch/phases/*/brief.md",
        ".orch/phases/*/fix-brief-*.md",
    ],
}


@dataclass(frozen=True)
class Repo:
    root: Path

    @property
    def orch(self) -> Path:
        return self.root / ".orch"

    @property
    def state_path(self) -> Path:
        return self.orch / "state.json"

    @property
    def cache(self) -> Path:
        return self.orch / ".cache"

    def phase_dir(self, dirname: str) -> Path:
        return self.orch / "phases" / dirname

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
    gi = repo.orch / ".gitignore"
    if not gi.exists():
        gi.write_text(".cache/\n", encoding="utf-8")
    return path
