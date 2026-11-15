"""Regressions found by the phase 5-6 verification (written by the verifier,
adjusted where the fix changed the intended behaviour)."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
from pathlib import Path

import pytest
from typer.testing import CliRunner

from orch import loop, planning
from orch import state as S
from orch.cli import app
from orch.repo import Repo

from . import test_planning as tp
from .conftest import FakeClaude, git
from .helpers import LEARN, checker, dev, make_project
from .test_planning import BRIEF, GOOD, QUESTION, row
from .test_stops import USAGE

project = tp.project  # the same fixtures as the planning tests
fc = tp.fc

# ---------------------------------------------------------------- planning stops


def test_usage_limit_during_outline_exits_75(
    project: Path, fc: FakeClaude, monkeypatch: pytest.MonkeyPatch
) -> None:
    fc.on("orch-outline", USAGE)
    monkeypatch.chdir(project)
    res = CliRunner().invoke(app, ["run"])
    assert res.exit_code == 75, res.output


def test_wait_covers_usage_limit_during_outline(
    project: Path, fc: FakeClaude, monkeypatch: pytest.MonkeyPatch
) -> None:
    slept: list[float] = []
    monkeypatch.setattr(planning, "_sleep", slept.append)
    fc.on("orch-outline", USAGE, GOOD)
    st = planning.run(Repo(project), wait=True)
    assert st.status == S.DONE, (st.status, st.stop_reason)


def test_rerun_after_paused_outline_does_not_finish_an_empty_run(
    project: Path, fc: FakeClaude
) -> None:
    fc.on("orch-outline", USAGE, GOOD)
    st = planning.run(Repo(project))
    assert st.status == S.PAUSED
    st = planning.run(Repo(project))
    # BUG: state.json exists with no phases -> execute() runs nothing -> DONE
    assert (st.status, len(st.phases)) == (S.DONE, 2), (st.status, st.phases)


def test_rerun_after_outline_needs_replan_does_not_finish(
    project: Path, fc: FakeClaude
) -> None:
    fc.on("orch-outline", {"structured": {"phases": [row("Add", ["R1"])]}})
    assert planning.run(Repo(project)).status == S.NEEDS_REPLAN
    st = planning.run(Repo(project))
    assert st.status != S.DONE, "a run with zero phases was reported done"


def test_plan_sessions_honour_the_pitch_settings(project: Path, fc: FakeClaude) -> None:
    pitch = project / "docs/pitch.md"
    pitch.write_text(
        pitch.read_text().replace(
            "title: toy", "title: toy\nset: [outline.model=haiku]"
        )
    )
    git(project, "commit", "-qam", "pitch settings")
    planning.plan(Repo(project))
    argv = fc.calls("orch-outline")[0]["argv"]
    assert argv[argv.index("--model") + 1] == "haiku", argv


# ---------------------------------------------------------------- questions


def test_auto_decide_after_questions_were_asked(project: Path, fc: FakeClaude) -> None:
    fc.on("orch-brief", QUESTION, BRIEF)
    assert planning.run(Repo(project)).status == S.NEEDS_USER
    # the questions file promises that --auto-decide takes the recommended options
    st = planning.run(Repo(project), auto_decide=True)
    assert st.status == S.DONE, st.stop_reason


def test_auto_decide_does_not_outlive_its_invocation(
    project: Path, fc: FakeClaude
) -> None:
    fc.on("orch-brief", USAGE, QUESTION, BRIEF)
    assert planning.run(Repo(project), auto_decide=True).status == S.PAUSED
    # resumed without --auto-decide: the question is asked, not auto-answered
    assert planning.run(Repo(project)).status == S.NEEDS_USER


def test_answer_on_the_next_line_is_read(project: Path, fc: FakeClaude) -> None:
    fc.on("orch-brief", QUESTION, BRIEF)
    planning.run(Repo(project))
    q = project / ".orch/phases/01-add/questions.md"
    q.write_text(q.read_text().replace("answer: ", "answer:\nsqlite, because x\n"))
    st = planning.run(Repo(project))
    assert st.status == S.DONE, st.stop_reason


def test_multi_line_answer_is_kept_whole(project: Path, fc: FakeClaude) -> None:
    fc.on("orch-brief", QUESTION, BRIEF)
    planning.run(Repo(project))
    q = project / ".orch/phases/01-add/questions.md"
    q.write_text(
        q.read_text().replace("answer: ", "answer: sqlite\nbut only for the cache\n")
    )
    planning.run(Repo(project))
    answered = (project / ".orch/phases/01-add/inputs/brief-answered.md").read_text()
    assert "only for the cache" in answered


def test_extra_heading_in_questions_md(project: Path, fc: FakeClaude) -> None:
    fc.on("orch-brief", QUESTION, BRIEF)
    planning.run(Repo(project))
    q = project / ".orch/phases/01-add/questions.md"
    q.write_text(
        q.read_text().replace("answer: ", "answer: sqlite") + "\n## My notes\n\nhm\n"
    )
    st = planning.run(Repo(project))
    assert st.status == S.DONE, st.stop_reason


def test_lead_reasking_after_answers_is_not_a_permanent_failure(
    project: Path, fc: FakeClaude
) -> None:
    fc.on("orch-brief", QUESTION, QUESTION, BRIEF)
    planning.run(Repo(project))
    q = project / ".orch/phases/01-add/questions.md"
    q.write_text(q.read_text().replace("answer: ", "answer: sqlite"))
    st = planning.run(Repo(project))
    assert st.status == S.NEEDS_USER  # the lead asked again: a second round
    q2 = project / ".orch/phases/01-add/questions-2.md"
    q2.write_text(q2.read_text().replace("answer: ", "answer: files"))
    st = planning.run(Repo(project))
    assert st.status == S.DONE, (st.status, st.stop_reason)


# ---------------------------------------------------------------- missing-file FP


@pytest.mark.parametrize(
    "brief",
    [
        "## New files\n\n- `calc/core.py`\n- `tests/test_core.py`\n",
        "## Tests to write\n\n- `tests/test_core.py`: summing two ints\n",
        "## Goal\n\nWrite `calc/core.py` with a sum function.\n",
        "## Functional QA\n\n- `python -m calc` writes `out/result.csv` (confirmed)\n",
    ],
)
def test_missing_file_heuristic_false_positives(tmp_path: Path, brief: str) -> None:
    p = tmp_path / "brief.md"
    p.write_text(brief)

    class R:
        root = tmp_path

    out = loop.plan_review_blockers(R(), {"findings": [], "qa_dry_run": []}, p)  # ty: ignore[invalid-argument-type]
    assert out == [], out


# ---------------------------------------------------------------- probe


PROBE_BAD = {
    "structured": {
        "ok": False,
        "ran": "x",
        "observations": "",
        "problems": ["credential rejected: 403"],
    }
}
PROBE_OK = {
    "structured": {"ok": True, "ran": "x", "observations": "fine", "problems": []}
}


def _stops_fc(fc: FakeClaude) -> FakeClaude:
    fc.on("orch-dev", dev("work.txt"))
    fc.on("orch-add-checks", {"structured": {"checks": []}})
    fc.on("orch-review", {"structured": checker()})
    fc.on("orch-qa", {"structured": checker()})
    fc.on("orch-learnings", {"structured": LEARN})
    return fc


def test_probe_credential_then_user_fixes_it_and_resumes(
    tmp_path: Path, fake_claude: FakeClaude
) -> None:
    repo = make_project(tmp_path, phases=1)
    _stops_fc(fake_claude).on("orch-probe", PROBE_BAD, PROBE_OK)
    st = planning.run(Repo(repo), scenario="real-data")
    assert st.status == S.NEEDS_USER
    st = planning.run(Repo(repo))  # credential fixed by the user
    assert st.status == S.DONE, (st.status, st.stop_reason)


# ---------------------------------------------------------------- report


def test_report_says_done(tmp_path: Path, fake_claude: FakeClaude) -> None:
    repo = make_project(tmp_path, phases=1)
    _stops_fc(fake_claude).on("orch-combined", {"structured": checker()})
    planning.run(Repo(repo), scenario="small")
    report = (repo / ".orch" / "report.md").read_text()
    assert "status: **done**" in report, report[:300]


def test_report_counts_cost_of_replanned_phase(project: Path, fc: FakeClaude) -> None:
    fc.on("orch-dev", dev("work.txt"), {"exit": 1, "stdout": "", "stderr": "x"})
    fc.on("orch-brief", {**BRIEF, "cost": 3.0})
    st = planning.run(Repo(project))
    spent = st.cost()
    fc.on("orch-outline", {"structured": {"phases": [row("Cli two", ["R2"])]}})
    st = planning.plan(Repo(project), from_n=2)
    # the failed phase 2's sessions were paid for
    assert st.cost() >= spent - 1e-9, (st.cost(), spent)


# ---------------------------------------------------------------- replan + pending


def test_replan_does_not_resume_a_stale_session(project: Path, fc: FakeClaude) -> None:
    fc.on("orch-dev", dev("work.txt"), USAGE, dev("w2.txt"))
    st = planning.run(Repo(project))
    assert (st.status, list(st.pending)) == (S.PAUSED, ["2:dev"])
    old = st.pending["2:dev"]
    fc.on("orch-outline", {"structured": {"phases": [row("Other", ["R2"])]}})
    planning.plan(Repo(project), from_n=2)
    planning.run(Repo(project))
    last = fc.calls("orch-dev")[-1]
    assert (last["resume"], last["session"]) != (True, old), "resumed old phase's dev"


# ---------------------------------------------------------------- push failure


def test_push_failure_does_not_lose_next_phase_base(
    tmp_path: Path, fake_claude: FakeClaude
) -> None:
    repo = make_project(tmp_path, phases=2)
    _stops_fc(fake_claude).on("orch-combined", {"structured": checker()})
    hook = repo / ".git" / "hooks" / "pre-push"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    st = planning.run(Repo(repo), scenario="small")
    assert (st.status, [p.base is not None for p in st.phases]) == (
        S.DONE,
        [True, True],
    ), "a failed push must not fail the run or skip a phase's base"
    assert (repo / ".orch" / "push-failures.log").is_file()


# ---------------------------------------------------------------- SIGINT


def test_sigint_between_sessions_does_not_start_another(
    tmp_path: Path, fake_claude: FakeClaude
) -> None:
    repo = make_project(tmp_path, phases=1, config="")
    (repo / ".orch" / "config.toml").write_text(
        'gate_cmd = "kill -INT $PPID; sleep 0.3; true"\nmax_fix_rounds = 2\n'
    )
    git(repo, "commit", "-qam", "gate")
    _stops_fc(fake_claude).on("orch-combined", {"structured": checker()})
    st = planning.run(Repo(repo), scenario="small")
    assert st.status == S.PAUSED
    # Ctrl+C landed during the gate; the combined session should not have started
    calls = fake_claude.calls("orch-combined")
    st = planning.run(Repo(repo))
    calls = fake_claude.calls("orch-combined")
    assert len(calls) <= 1, "finished combined session was discarded and resumed"


def test_interrupted_lead_cost_is_not_double_counted(
    tmp_path: Path, fake_claude: FakeClaude
) -> None:
    repo = make_project(tmp_path, phases=1)
    _stops_fc(fake_claude).on(
        "orch-learnings",
        {"sleep": 4, "structured": LEARN, "cost": 1.0},
        {"structured": LEARN, "cost": 1.0},
    )
    fake_claude.on("orch-add-checks", {"structured": {"checks": []}, "cost": 5.0})
    timer = threading.Timer(2.5, os.kill, (os.getpid(), signal.SIGINT))
    timer.start()
    st = planning.run(Repo(repo), scenario="full")
    timer.join()
    assert st.status == S.PAUSED, st.stop_reason
    st = planning.run(Repo(repo))
    lead = [s for s in st.phases[0].sessions if s.role in ("add_checks", "learnings")]
    recorded = sum(s.cost for s in lead)
    counters = json.loads(fake_claude.script.with_suffix(".counters.json").read_text())
    true_total = counters["_costs"][st.phases[0].lead_session]
    assert recorded == pytest.approx(true_total), (recorded, true_total, lead)


def test_sigint_during_planning_does_not_orphan_claude(
    project: Path, fc: FakeClaude
) -> None:
    fc.on("orch-outline", {"sleep": 6, **GOOD})
    timer = threading.Timer(1.5, os.kill, (os.getpid(), signal.SIGINT))
    timer.start()
    st = planning.run(Repo(project))
    timer.join()
    assert (st.status, st.stop_kind) == (S.PAUSED, "interrupted")
    ps = subprocess.run(
        ["pgrep", "-f", "fake_claude.py"], check=False, capture_output=True, text=True
    )
    left = ps.stdout.split()
    for pid in left:  # zorilla: ignore[ZR001] -- clean up leaked processes
        os.kill(int(pid), signal.SIGKILL)
    assert left == [], "fake claude still running after Ctrl+C in planning"


# ---------------------------------------------------------------- -s precedence


def test_cli_scenario_override_is_not_permanent(project: Path, fc: FakeClaude) -> None:
    fc.on(
        "orch-outline",
        {
            "structured": {
                "phases": [
                    row("Add", ["R1"]),
                    row("Cli", ["R2"], scenario="full", reason="risky"),
                ]
            }
        },
    )
    planning.run(Repo(project), pause=True)
    fc.on("orch-dev", dev("work.txt"), USAGE, dev("w.txt"))
    st = planning.run(Repo(project), scenario="small")  # this run only
    assert st.status == S.PAUSED
    st = S.load(Repo(project).state_path)
    assert st.phases[1].scenario == "full", "planner's choice lost after one -s run"
