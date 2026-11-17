"""Sessions running right now, across every repo on this machine (`orch ps`).

While a session runs, orch keeps a marker for it in `<home>/live/<session>.json`
and deletes it when the session ends. `claude -p --output-format json` prints
nothing until it ends, so progress comes from the session's transcript, which
Claude Code writes as it goes: `<claude config dir>/projects/*/<session>.jsonl`.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from collections.abc import Generator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from orch import store

TAIL_BYTES = 256 * 1024  # enough for the last few turns of a transcript
ACTION_WIDTH = 100


def live_dir(home: Path) -> Path:
    return home / "live"


@contextlib.contextmanager
def tracking(
    home: Path | None, session_id: str, info: dict[str, Any]
) -> Generator[None]:
    """Keep a marker for the session while the block runs. No home (run files in
    the repo) means no marker: a file there would end up in the run's commits."""
    if home is None:
        yield
        return
    path = live_dir(home) / f"{session_id}.json"
    store.write_json(
        path,
        {
            "session_id": session_id,
            **info,
            "pid": os.getpid(),
            "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
        },
    )
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


@dataclass(frozen=True)
class Live:
    marker: dict[str, Any]
    transcript: Path | None
    last_write: float | None  # epoch seconds
    last_action: str


def claude_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def find_transcript(session_id: str) -> Path | None:
    return next((claude_dir() / "projects").glob(f"*/{session_id}.jsonl"), None)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def sessions(home: Path) -> tuple[list[Live], int]:
    """Running sessions, oldest first, and how many stale markers were removed
    (left by an orch that died without cleaning up)."""
    found: list[Live] = []
    stale = 0
    for path in sorted(live_dir(home).glob("*.json")):
        try:
            marker = store.read_json(path)
        except (OSError, json.JSONDecodeError):
            continue  # being written or removed right now
        if not _pid_alive(int(marker.get("pid") or 0)):
            path.unlink(missing_ok=True)
            stale += 1
            continue
        t = find_transcript(str(marker.get("session_id")))
        found.append(
            Live(
                marker=marker,
                transcript=t,
                last_write=t.stat().st_mtime if t else None,
                last_action=last_action(t) if t else "",
            )
        )
    found.sort(key=lambda s: str(s.marker.get("started_at")))
    return found, stale


def last_action(transcript: Path) -> str:
    """The latest tool call (or text) in the transcript, on one line."""
    with transcript.open("rb") as fh:
        fh.seek(max(transcript.stat().st_size - TAIL_BYTES, 0))
        lines = fh.read().decode("utf-8", errors="replace").splitlines()
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue  # the first line is usually cut
        if entry.get("type") != "assistant":
            continue
        content = (entry.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for block in reversed(content):
            if text := _describe(block):
                return text[:ACTION_WIDTH]
    return ""


def _describe(block: dict[str, Any]) -> str:
    if block.get("type") == "tool_use":
        inp = block.get("input") or {}
        arg = next(
            (
                inp[k]
                for k in ("command", "file_path", "pattern", "description", "prompt")
                if inp.get(k)
            ),
            "",
        )
        return " ".join(f"{block.get('name')}: {arg}".split()).rstrip(": ")
    if block.get("type") == "text" and str(block.get("text") or "").strip():
        return "says: " + " ".join(str(block["text"]).split())
    return ""


def ago(seconds: float) -> str:
    s = max(round(seconds), 0)
    h, rest = divmod(s, 3600)
    m, s = divmod(rest, 60)
    if h:
        return f"{h}h{m:02d}m"
    return f"{m}m{s:02d}s" if m else f"{s}s"


def since(iso: str, now: float | None = None) -> float:
    started = datetime.fromisoformat(iso).timestamp()
    return (now if now is not None else time.time()) - started
