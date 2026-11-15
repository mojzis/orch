from __future__ import annotations

import json
from pathlib import Path

import pytest

from orch import loop
from orch import state as S
from orch.repo import Repo

from .conftest import FakeClaude, git
from .helpers import LEARN, checker, dev, finding, make_project


def _run(repo: Path, scenario: str | None = "small") -> S.RunState:
    return loop.execute(loop.start_or_resume(Repo(repo), scenario=scenario))


@pytest.fixture
def project(tmp_path: Path) -> Path:
    return make_project(tmp_path)


@pytest.fixture
def happy(fake_claude: FakeClaude) -> FakeClaude:
    fake_claude.on("orch-dev", dev("work.txt"))
    fake_claude.on("orch-combined", {"structured": checker("ship")})
    fake_claude.on("orch-learnings", {"structured": LEARN})
    return fake_claude


def test_three_phases_finish_and_push(project: Path, happy: FakeClaude) -> None:
    st = _run(project)
    assert (st.status, st.stop_reason) == (S.DONE, "")
    remote_state = json.loads(
        git(project, "show", f"origin/{st.branch}:.orch/state.json")
    )
    assert [p["status"] for p in remote_state["phases"]] == ["done"] * 3


def test_phase_files_are_written(project: Path, happy: FakeClaude) -> None:
    _run(project)
    pdir = project / ".orch" / "phases" / "02-p2"
    assert (pdir / "handoff.md").read_text() == "module x exists now\n"
    assert "## dev (dev)" in (pdir / "log.md").read_text()
    lessons = (project / ".orch" / "process-learnings.md").read_text()
    assert lessons.count("keep briefs short") == 3


def test_run_happens_on_a_branch_and_main_is_untouched(
    project: Path, happy: FakeClaude
) -> None:
    main_before = git(project, "rev-parse", "main")
    st = _run(project)
    assert git(project, "rev-parse", "--abbrev-ref", "HEAD") == st.branch
    assert git(project, "rev-parse", "main") == main_before


def test_each_dev_session_is_fresh_and_skill_dir_is_added(
    project: Path, happy: FakeClaude
) -> None:
    _run(project)
    devs = happy.calls("orch-dev")
    assert len({c["session"] for c in devs}) == 3
    assert not any(c["resume"] for c in devs)
    argv = devs[0]["argv"]
    assert argv[argv.index("--add-dir") + 1].endswith(".orch/.cache/skills")


def test_gate_failing_past_cap_ends_blocked(project: Path, happy: FakeClaude) -> None:
    happy.on("orch-dev", dev("work.txt", {"write": "BROKEN", "content": "1"}))
    happy.on("orch-fix", dev("fix.txt"))  # never removes BROKEN
    st = _run(project)
    assert (st.status, st.phases[0].status) == (S.BLOCKED, S.BLOCKED)
    assert len(happy.calls("orch-fix")) == 2  # max_fix_rounds repairs
    assert "gate still failing" in st.stop_reason


def test_gate_repair_session_gets_gate_output(project: Path, happy: FakeClaude) -> None:
    happy.on("orch-dev", dev("work.txt", {"write": "BROKEN", "content": "1"}))
    happy.on("orch-fix", dev("fix.txt", {"rm": "BROKEN"}))
    st = _run(project)
    assert st.status == S.DONE
    brief = project / ".orch" / "phases" / "01-p1" / "gate-fix-dev-1.md"
    assert "`test ! -f BROKEN` exited 1" in brief.read_text()


def test_combined_fix_verdict_runs_fix_then_gate(
    project: Path, happy: FakeClaude
) -> None:
    happy.on(
        "orch-combined",
        {"structured": checker("fix", finding("C1"))},
        {"structured": checker("ship")},
    )
    happy.on("orch-fix", dev("fix.txt"))
    st = _run(project)
    assert st.status == S.DONE
    fb = (project / ".orch" / "phases" / "01-p1" / "fix-brief-1.md").read_text()
    assert "### C1 (medium, from combined): broken" in fb
    assert "fix#1" in st.phases[0].done_steps


def test_shipped_lows_go_to_backlog(project: Path, happy: FakeClaude) -> None:
    happy.on(
        "orch-combined", {"structured": checker("ship", finding("C1", "low", "nit"))}
    )
    _run(project)
    assert "**C1**: nit [low]" in (project / ".orch" / "backlog.md").read_text()


def test_bad_output_is_retried_once_then_failed(
    project: Path, happy: FakeClaude
) -> None:
    happy.on("orch-dev", {"result": "no structured output"})
    st = _run(project)
    assert (st.status, len(happy.calls("orch-dev"))) == (S.FAILED, 2)


def test_resume_from_fresh_clone_of_pushed_branch(
    project: Path, happy: FakeClaude, tmp_path: Path
) -> None:
    # phase 2's dev crashes twice: the run fails after phase 1 was pushed
    happy.on(
        "orch-dev",
        dev("work.txt"),
        {"exit": 1, "stdout": "", "stderr": "boom"},
        {"exit": 1, "stdout": "", "stderr": "boom"},
        dev("work.txt"),
    )
    st = _run(project)
    assert (st.status, st.phases[0].status) == (S.FAILED, "done")
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", str(tmp_path / "remote.git"), str(clone))
    git(clone, "switch", "-q", st.branch)
    st2 = _run(clone, scenario=None)
    assert (st2.status, [p.status for p in st2.phases]) == (S.DONE, ["done"] * 3)
    assert (clone / "work.txt").read_text().count("x") == 3


def test_resume_after_blocked_reruns_gate_on_current_code(
    project: Path, happy: FakeClaude
) -> None:
    happy.on("orch-dev", dev("work.txt", {"write": "BROKEN", "content": "1"}))
    happy.on("orch-fix", dev("fix.txt"))
    assert _run(project).status == S.BLOCKED
    (project / "BROKEN").unlink()  # the user fixes it by hand
    git(project, "commit", "-qam", "manual fix")
    happy.on("orch-dev", dev("work.txt"))
    st = _run(project, scenario=None)
    assert st.status == S.DONE


def test_no_phases_is_a_config_error(tmp_path: Path, fake_claude: FakeClaude) -> None:
    repo = make_project(tmp_path, phases=0)
    with pytest.raises(loop.ConfigError, match="no phases"):
        loop.start_or_resume(Repo(repo))


def test_dirty_tree_refused(project: Path, happy: FakeClaude) -> None:
    (project / "README.md").write_text("changed\n")
    with pytest.raises(loop.ConfigError, match="uncommitted"):
        loop.start_or_resume(Repo(project))
