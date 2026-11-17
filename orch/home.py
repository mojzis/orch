"""Where orch keeps its run files: a home directory outside the project repo.

    $ORCH_HOME -> `home` in the user config -> ~/.orch

`home = "repo"` keeps them in the repo's .orch/ instead (and commits them on the
run branch), for machines whose home directory does not last, like Claude Code on
the web or CI.

    <home>/sessions.jsonl                 one line per session, every repo and run
    <home>/live/<session>.json            sessions running now (`orch ps`)
    <home>/repos/<owner>/<repo>/          backlog.md, process-learnings.md, cache/
    <home>/repos/<owner>/<repo>/runs/<id> state.json, plan.md, phases/, report.md
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import tomllib
from pathlib import Path
from typing import Any

from orch import gitops
from orch.errors import ConfigError

IN_REPO = "repo"


def user_config_dir() -> Path:
    return Path(os.environ.get("ORCH_CONFIG_HOME") or Path.home() / ".config" / "orch")


def orch_home() -> Path | None:
    """The home directory, or None for "in the repo"."""
    raw = os.environ.get("ORCH_HOME") or _configured_home()
    if raw == IN_REPO:
        return None
    return Path(raw).expanduser() if raw else Path.home() / ".orch"


def _configured_home() -> str:
    p = user_config_dir() / "config.toml"
    if not p.is_file():
        return ""
    try:
        data = tomllib.loads(p.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{p}: invalid TOML: {e}") from None
    return str(data.get("home") or "")


_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def repo_key(root: Path, remote: str) -> str:
    """`owner/repo` from the remote's URL; the repo's directory name without one.
    Every worktree of a repo gets the same key."""
    url = gitops.git(root, "remote", "get-url", remote, check=False).strip()
    if url:
        path = re.sub(r"^[a-z][a-z0-9+.-]*://[^/]*/", "", url)  # scheme://host/
        path = re.sub(r"^[^/@:]+@[^:/]+:", "", path)  # user@host:
        path = path.removesuffix("/").removesuffix(".git")
        parts = [p for p in path.split("/") if p][-2:]
    else:
        common = gitops.git(
            root, "rev-parse", "--path-format=absolute", "--git-common-dir"
        )
        parts = [Path(common).parent.name]
    return "/".join(_SAFE.sub("-", p) for p in parts) or "repo"


def append_session(log: Path, row: dict[str, Any]) -> None:
    """One JSON line per session. Review and QA end in parallel, and several
    runs may share the file, so lines are written under a lock."""
    log.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row, sort_keys=False) + "\n"
    with log.with_name(log.name + ".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with log.open("a", encoding="utf-8") as fh:
            fh.write(line)
