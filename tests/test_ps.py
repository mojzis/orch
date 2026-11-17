"""`orch ps`: markers for running sessions, and what ps reads from them."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from orch import live, planning
from orch import state as S
from orch.cli import app
from orch.repo import Repo

from .conftest import FakeClaude
from .helpers import LEARN, checker, dev, make_project

runner = CliRunner()


@pytest.fixture
def home(tmp_path: Path) -> Path:
    return tmp_path / "orch-home"  # ORCH_HOME, set by conftest


@pytest.fixture
def claude_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "claude-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(d))
    return d


def _marker(home: Path, sid: str, **over: object) -> None:
    row = {
        "session_id": sid,
        "repo": "me/proj",
        "run_id": "20261117-120000",
        "phase": 2,
        "phase_slug": "board",
        "key": "dev",
        "role": "dev",
        "model": "sonnet",
        "root": "/x",
        "pid": os.getpid(),
        "started_at": "2026-11-17T12:00:00+00:00",
        **over,
    }
    d = live.live_dir(home)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sid}.json").write_text(json.dumps(row))


def _transcript(claude_dir: Path, sid: str, *entries: dict) -> Path:
    p = claude_dir / "projects" / "-x" / f"{sid}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(e) + "\n" for e in entries))
    return p


def _assistant(*blocks: dict) -> dict:
    return {"type": "assistant", "message": {"content": list(blocks)}}


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", ""])
    proc.wait()
    return proc.pid


def test_a_running_session_has_a_marker_that_goes_when_it_ends(
    tmp_path: Path, fake_claude: FakeClaude, home: Path
) -> None:
    project = make_project(tmp_path, phases=1)
    seen = tmp_path / "seen"
    copy = {"sh": f"mkdir -p {seen} && cp {live.live_dir(home)}/*.json {seen}/"}
    fake_claude.on("orch-dev", dev("work.txt", copy))
    fake_claude.on("orch-combined", {"structured": checker()})
    fake_claude.on("orch-learnings", {"structured": LEARN})

    st = planning.run(Repo(project), scenario="small")

    assert st.status == S.DONE
    (marker,) = [json.loads(p.read_text()) for p in seen.glob("*.json")]
    got = {k: marker[k] for k in ("phase", "key", "role", "session_id", "pid")}
    assert got == {
        "phase": 1,
        "key": "dev",
        "role": "dev",
        "session_id": fake_claude.calls("orch-dev")[0]["session"],
        "pid": os.getpid(),
    }
    assert not list(live.live_dir(home).glob("*.json"))


def test_ps_shows_step_and_last_tool_call(home: Path, claude_dir: Path) -> None:
    _marker(home, "s1")
    _transcript(
        claude_dir,
        "s1",
        _assistant({"type": "tool_use", "name": "Read", "input": {"file_path": "a"}}),
        {"type": "user", "message": {"content": "tool result"}},
        _assistant(
            {"type": "text", "text": "running tests"},
            {"type": "tool_use", "name": "Bash", "input": {"command": "pytest -q"}},
        ),
    )

    res = runner.invoke(app, ["ps"])

    assert res.exit_code == 0, res.output
    row = next(line for line in res.output.splitlines() if "me/proj" in line)
    assert "2-board/dev" in row
    assert "sonnet" in row
    assert "Bash: pytest -q" in row
    assert "ago" in row


def test_ps_without_transcript_says_so(home: Path, claude_dir: Path) -> None:
    _marker(home, "s1", phase=0, phase_slug="planning", key="outline")
    res = runner.invoke(app, ["ps"])
    row = next(line for line in res.output.splitlines() if "me/proj" in line)
    assert "plan/outline" in row
    assert "no transcript" in row


def test_ps_drops_markers_of_a_dead_orch(home: Path, claude_dir: Path) -> None:
    _marker(home, "gone", pid=_dead_pid())
    res = runner.invoke(app, ["ps"])
    assert res.exit_code == 0
    assert "no orch sessions running" in res.output
    assert "removed 1 stale marker" in res.output
    assert not list(live.live_dir(home).glob("*.json"))


def test_ps_needs_a_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCH_HOME", "repo")
    res = runner.invoke(app, ["ps"])
    assert res.exit_code == 2
    assert "error: orch's home is the repo" in res.output


def test_last_action_skips_a_cut_first_line(tmp_path: Path) -> None:
    p = tmp_path / "t.jsonl"
    p.write_text(
        '"cut": 1}\n'
        + json.dumps(_assistant({"type": "text", "text": "  all\n done  "}))
        + "\n"
    )
    assert live.last_action(p) == "says: all done"


@pytest.mark.parametrize(("secs", "text"), [(5, "5s"), (65, "1m05s"), (3725, "1h02m")])
def test_ago(secs: float, text: str) -> None:
    assert live.ago(secs) == text
