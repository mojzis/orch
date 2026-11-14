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
    "test_globs": ["tests/**", "test/**", "**/test_*.py", "**/*_test.py"],
    "protected": ["QA.md"],
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
