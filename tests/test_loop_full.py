"""The full scenario: lead steps, review ‖ QA in worktrees, triage, fix, verify."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from orch import loop
from orch import state as S
from orch.repo import Repo

from .conftest import FakeClaude, git
from .helpers import (
    DEV_OK,
    LEARN,
    checker,
    dev,
    finding,
    make_project,
    repo_home,
    run_dir,
)


def _run(repo: Path) -> S.RunState:
    return loop.execute(loop.start_or_resume(Repo(repo), scenario="full"))


def triage(decision: str, **lists: list[dict[str, Any]]) -> dict[str, Any]:
    base: dict[str, Any] = {
        "must_fix": [],
        "also_fix": [],
        "do_not_fix": [],
        "defer": [],
    }
    return {"structured": {"decision": decision, **base, **lists}}


def must(*ids: str, title: str = "t") -> dict[str, Any]:
    return {"ids": list(ids), "title": title, "design": "do X", "repro": "run Y"}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    return make_project(tmp_path, phases=1)


@pytest.fixture
def fc(fake_claude: FakeClaude) -> FakeClaude:
    fake_claude.on("orch-dev", dev("work.txt"))
    fake_claude.on("orch-add-checks", {"structured": {"checks": []}})
    fake_claude.on("orch-review", {"structured": checker("ship")})
    fake_claude.on("orch-qa", {"structured": checker("ship")})
    fake_claude.on("orch-learnings", {"structured": LEARN})
    fake_claude.on("orch-fix", dev("fix.txt"))
    fake_claude.on("orch-verify", {"structured": checker("ship") | VERIFY_EXTRA})
    return fake_claude


VERIFY_EXTRA: dict[str, Any] = {"fixes": [], "deviation_verdicts": []}


def test_both_ship_skips_triage_and_fix(project: Path, fc: FakeClaude) -> None:
    st = _run(project)
    assert st.status == S.DONE
    assert (fc.calls("orch-triage"), fc.calls("orch-fix")) == ([], [])
    log = (run_dir(project) / "phases" / "01-p1" / "log.md").read_text()
    assert ("## review (review)" in log, "## qa (qa)" in log) == (True, True)


def test_review_and_qa_run_in_parallel_in_separate_worktrees(
    project: Path, fc: FakeClaude
) -> None:
    probe = {"sh": "date +%s.%N > started; sleep 0.5; python3 -m pytest --version"}
    fc.on("orch-review", {"structured": checker(), "actions": [probe]})
    fc.on("orch-qa", {"structured": checker(), "actions": [probe]})
    _run(project)
    cwds = {
        c["template"]: Path(c["cwd"])
        for c in fc.calls()
        if c["template"] in ("orch-review", "orch-qa")
    }
    assert len(set(cwds.values())) == 2
    assert project not in cwds.values()
    assert git(project, "worktree", "list").count("\n") == 0  # only the main one left


def test_parallel_sessions_overlap_in_time(
    project: Path, fc: FakeClaude, tmp_path: Path
) -> None:
    def timed(name: str) -> dict[str, Any]:
        out = tmp_path / name
        cmd = f"date +%s.%N > {out}.start; sleep 0.4; date +%s.%N > {out}.end"
        return {"structured": checker(), "actions": [{"sh": cmd}]}

    fc.on("orch-review", timed("review"))
    fc.on("orch-qa", timed("qa"))
    _run(project)
    t = {f.name: float(f.read_text()) for f in tmp_path.glob("*.start")}
    t |= {f.name: float(f.read_text()) for f in tmp_path.glob("*.end")}
    # each started before the other ended
    assert t["review.start"] < t["qa.end"]
    assert t["qa.start"] < t["review.end"]


def test_fix_then_ship(project: Path, fc: FakeClaude) -> None:
    fc.on("orch-review", {"structured": checker("fix", finding("R1"), finding("R2"))})
    fc.on("orch-qa", {"structured": checker("fix", finding("Q1"))})
    fc.on(
        "orch-triage",
        triage(
            "fix",
            must_fix=[must("R2", "Q1", title="same bug")],
            do_not_fix=[{"ids": ["R1"], "reason": "false positive"}],
        ),
    )
    st = _run(project)
    assert (st.status, st.phases[0].fix_round) == (S.DONE, 1)
    fb = (run_dir(project) / "phases" / "01-p1" / "fix-brief-1.md").read_text()
    must_part, dnf_part = fb.split("## Do NOT fix")
    assert "### R2 = Q1: same bug" in must_part, "duplicates merged"
    assert "R1" not in must_part, "do-NOT-fix stays out of the fix list"
    assert "- R1: false positive" in dnf_part
    assert len(fc.calls("orch-verify")) == 1, "one verify after one fix round"


def test_fix_rounds_exhausted_blocks(project: Path, fc: FakeClaude) -> None:
    fc.on("orch-review", {"structured": checker("fix", finding("R1"))})
    fc.on("orch-verify", {"structured": checker("fix", finding("V1")) | VERIFY_EXTRA})
    fc.on(
        "orch-triage",
        triage("fix", must_fix=[must("R1")]),
        triage("fix", must_fix=[must("V1")]),
    )
    st = _run(project)
    assert (st.status, len(fc.calls("orch-fix")), len(fc.calls("orch-verify"))) == (
        S.BLOCKED,
        2,
        2,
    )
    assert "must-fix findings left after 2 fix round(s)" in st.stop_reason


def test_malformed_review_output_fails_after_one_retry(
    project: Path, fc: FakeClaude
) -> None:
    fc.on("orch-review", {"result": "I forgot the report"})
    st = _run(project)
    assert (st.status, len(fc.calls("orch-review"))) == (S.FAILED, 2)
    assert "bad_output" in st.stop_reason


def test_deviation_becomes_exactly_one_routed_check(
    project: Path, fc: FakeClaude
) -> None:
    report = DEV_OK | {
        "deviations": ["used sqlite not files"],
        "unresolved_issues": ["slow on big input"],
    }
    fc.on("orch-dev", dev("work.txt", report=report))
    fc.on(
        "orch-add-checks",
        {
            "structured": {
                "checks": [
                    {
                        "source": "used sqlite not files",
                        "route": "review",
                        "check": "read store.py",
                    },
                    {
                        "source": "slow on big input",
                        "route": "qa",
                        "check": "time 10k rows",
                    },
                ]
            }
        },
    )
    _run(project)
    qa_inputs = (run_dir(project) / "phases/01-p1/inputs/qa.md").read_text()
    review_inputs = (run_dir(project) / "phases/01-p1/inputs/review.md").read_text()
    assert ("time 10k rows" in qa_inputs, "read store.py" in qa_inputs) == (True, False)
    assert ("read store.py" in review_inputs, "time 10k" in review_inputs) == (
        True,
        False,
    )


def test_lead_missing_a_check_is_asked_again(project: Path, fc: FakeClaude) -> None:
    report = DEV_OK | {"deviations": ["d1", "d2"]}
    fc.on("orch-dev", dev("work.txt", report=report))
    one = {"source": "d1", "route": "review", "check": "c1"}
    two = {"source": "d2", "route": "qa", "check": "c2"}
    fc.on(
        "orch-add-checks",
        {"structured": {"checks": [one]}},
        {"structured": {"checks": [one, two]}},
    )
    st = _run(project)
    calls = fc.calls("orch-add-checks")
    assert (st.status, len(calls), calls[1]["resume"]) == (S.DONE, 2, True)


def test_triage_leaving_a_finding_out_is_asked_again(
    project: Path, fc: FakeClaude
) -> None:
    fc.on("orch-review", {"structured": checker("fix", finding("R1"), finding("R2"))})
    fc.on(
        "orch-triage",
        triage("fix", must_fix=[must("R1")]),
        triage(
            "fix",
            must_fix=[must("R1")],
            defer=[{"ids": ["R2"], "title": "later", "reason": "out of scope"}],
        ),
    )
    st = _run(project)
    assert (st.status, len(fc.calls("orch-triage"))) == (S.DONE, 2)
    assert (
        "**R2**: later (out of scope)"
        in (repo_home(project) / "backlog.md").read_text()
    )


def test_lead_is_one_session_resumed_across_steps(
    project: Path, fc: FakeClaude
) -> None:
    fc.on("orch-review", {"structured": checker("fix", finding("R1"))})
    fc.on("orch-triage", triage("fix", must_fix=[must("R1")]))
    _run(project)
    lead_calls = [
        c
        for c in fc.calls()
        if c["template"] in ("orch-add-checks", "orch-triage", "orch-learnings")
    ]
    assert len({c["session"] for c in lead_calls}) == 1
    assert [c["resume"] for c in lead_calls] == [False, True, True]


def test_lead_cost_is_counted_per_step_not_cumulative(
    project: Path, fc: FakeClaude
) -> None:
    fc.on("orch-add-checks", {"structured": {"checks": []}, "cost": 1.0})
    fc.on("orch-learnings", {"structured": LEARN, "cost": 0.5})
    st = _run(project)
    costs = {s.key: s.cost for s in st.phases[0].sessions}
    assert (costs["add_checks"], costs["learnings"]) == (1.0, 0.5)


def test_resume_mid_phase_skips_finished_steps(project: Path, fc: FakeClaude) -> None:
    fc.on("orch-qa", {"exit": 1, "stdout": "", "stderr": "boom"})
    assert _run(project).status == S.FAILED
    fc.on("orch-qa", {"structured": checker()})
    st = loop.execute(loop.start_or_resume(Repo(project)))
    assert st.status == S.DONE
    assert (len(fc.calls("orch-dev")), len(fc.calls("orch-review"))) == (1, 1)
