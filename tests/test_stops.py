"""Stops and resumes: interrupt, usage limit, auth, budget, probe credentials,
plus regressions found by the phase 2-4 review."""

from __future__ import annotations

import json
import os
import signal
import threading
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from orch import planning
from orch import state as S
from orch.cli import app
from orch.repo import Repo

from .conftest import FakeClaude, git
from .helpers import DEV_OK, LEARN, checker, dev, finding, make_project

USAGE = {
    "is_error": True,
    "terminal_reason": "api_error",
    "result": "Claude AI usage limit reached|1763150400",
}
USAGE_NO_TIME = {
    "is_error": True,
    "terminal_reason": "api_error",
    "result": "You've hit your limit",
}


def _run(repo: Path, scenario: str | None = "small", **kw: Any) -> S.RunState:
    return planning.run(Repo(repo), scenario=scenario, **kw)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    return make_project(tmp_path, phases=1)


@pytest.fixture
def fc(fake_claude: FakeClaude) -> FakeClaude:
    fake_claude.on("orch-dev", dev("work.txt"))
    fake_claude.on("orch-combined", {"structured": checker()})
    fake_claude.on("orch-add-checks", {"structured": {"checks": []}})
    fake_claude.on("orch-review", {"structured": checker()})
    fake_claude.on("orch-qa", {"structured": checker()})
    fake_claude.on("orch-learnings", {"structured": LEARN})
    fake_claude.on("orch-fix", dev("fix.txt"))
    fake_claude.on(
        "orch-probe",
        {
            "structured": {
                "ok": True,
                "ran": "x",
                "observations": "fine",
                "problems": [],
            }
        },
    )
    return fake_claude


def test_interrupt_then_resume_carries_on_in_the_same_session(
    project: Path, fc: FakeClaude
) -> None:
    fc.on("orch-dev", {"sleep": 5}, dev("work.txt"))
    timer = threading.Timer(1.0, os.kill, (os.getpid(), signal.SIGINT))
    timer.start()
    st = _run(project)
    timer.join()
    assert (st.status, st.stop_kind, list(st.pending)) == (
        S.PAUSED,
        "interrupted",
        ["1:dev"],
    )
    st = _run(project, scenario=None)
    first, second = fc.calls("orch-dev")
    assert (st.status, second["resume"], second["session"]) == (
        S.DONE,
        True,
        first["session"],
    )


def test_usage_limit_pauses_then_resume_continues(
    project: Path, fc: FakeClaude
) -> None:
    fc.on("orch-dev", USAGE, dev("work.txt"))
    st = _run(project)
    assert (st.status, st.stop_kind, st.reset_at) == (
        S.PAUSED,
        "usage_limit",
        "2025-11-14T20:00:00+00:00",
    )
    assert _run(project, scenario=None).status == S.DONE


def test_wait_sleeps_until_reset_then_finishes(
    project: Path, fc: FakeClaude, monkeypatch: pytest.MonkeyPatch
) -> None:
    slept: list[float] = []
    monkeypatch.setattr(planning, "_sleep", slept.append)
    fc.on("orch-dev", USAGE, dev("work.txt"))
    st = _run(project, wait=True)
    assert (st.status, slept) == (S.DONE, [planning.WAIT_MARGIN_S])  # reset is past


@pytest.mark.parametrize(("resp", "code"), [(USAGE, 75), (USAGE_NO_TIME, 75)])
def test_usage_limit_exit_code_is_75(
    project: Path,
    fc: FakeClaude,
    monkeypatch: pytest.MonkeyPatch,
    resp: dict[str, Any],
    code: int,
) -> None:
    monkeypatch.chdir(project)
    fc.on("orch-dev", resp)
    res = CliRunner().invoke(app, ["run", "-s", "small"])
    assert res.exit_code == code


def test_auth_failure_needs_user(project: Path, fc: FakeClaude) -> None:
    fc.on("orch-dev", {"is_error": True, "api_error_status": 401, "result": "x"})
    st = _run(project)
    assert (st.status, len(fc.calls("orch-dev"))) == (S.NEEDS_USER, 1)


def test_budget_needs_user_without_retry(project: Path, fc: FakeClaude) -> None:
    fc.on(
        "orch-dev",
        {
            "is_error": True,
            "subtype": "error_max_budget_usd",
            "terminal_reason": "budget_exhausted",
            "result": None,
        },
    )
    st = _run(project)
    assert (st.status, len(fc.calls("orch-dev"))) == (S.NEEDS_USER, 1)
    assert "dev.max_budget_usd" in st.stop_reason


def test_probe_credential_problem_needs_user(project: Path, fc: FakeClaude) -> None:
    fc.on(
        "orch-probe",
        {
            "structured": {
                "ok": False,
                "ran": "x",
                "observations": "",
                "problems": ["credential rejected: 403"],
            }
        },
    )
    st = _run(project, scenario="real-data")
    assert (st.status, "credential" in st.stop_reason) == (S.NEEDS_USER, True)


def test_report_written_after_last_phase(project: Path, fc: FakeClaude) -> None:
    (project / ".orch" / "config.toml").write_text(
        'gate_cmd = "true"\nfull_qa_cmd = "echo FULL-TIER-OK"\n'
    )
    git(project, "commit", "-qam", "full qa")
    _run(project)
    report = (project / ".orch" / "report.md").read_text()
    assert ("FULL-TIER-OK" in report, "| dev |" in report) == (True, True)


# ---- regressions from the phase 2-4 review


def test_review_and_qa_inputs_exist_inside_their_worktree(
    project: Path, fc: FakeClaude
) -> None:
    fc.on(
        "orch-review",
        {
            "structured": checker(),
            "actions": [{"sh": "test -f .orch/phases/01-p1/inputs/review.md"}],
        },
    )
    fc.on(
        "orch-qa",
        {
            "structured": checker(),
            "actions": [{"sh": "test -f .orch/phases/01-p1/inputs/qa.md"}],
        },
    )
    assert _run(project, scenario="full").status == S.DONE


def test_gate_ignores_orchs_own_records(project: Path, fc: FakeClaude) -> None:
    fc.on(
        "orch-dev",
        dev(
            "tests/test_x.py",
            {
                "write": "tests/test_y.py",
                "content": "import pytest\n@pytest.mark.skip\n"
                "def test_y():\n    pass\n",
            },
        ),
    )
    fc.on("orch-fix", dev("fix.txt", {"rm": "tests/test_y.py"}))
    st = _run(project)
    assert st.status == S.DONE, st.stop_reason


def test_session_editing_orch_config_is_caught(project: Path, fc: FakeClaude) -> None:
    fc.on("orch-dev", dev("w.txt", {"write": ".orch/config.toml", "content": "x=1\n"}))
    fc.on("orch-fix", dev("f.txt"))
    st = _run(project)
    assert (st.status, "protected file .orch/config.toml" in st.stop_reason) == (
        S.BLOCKED,
        True,
    )


def test_gate_cmd_artifacts_are_not_uncommitted_changes(
    project: Path, fc: FakeClaude
) -> None:
    (project / ".orch" / "config.toml").write_text('gate_cmd = "date > gate.out"\n')
    git(project, "commit", "-qam", "gate writes a file")
    assert _run(project).status == S.DONE


def test_full_scenario_keeps_shipped_lows_and_preexisting(
    project: Path, fc: FakeClaude
) -> None:
    pre = checker("ship", finding("R1", "low", "nit"))
    pre["preexisting"] = [finding("RP1", "high", "old bug")]
    fc.on("orch-review", {"structured": pre})
    _run(project, scenario="full")
    backlog = (project / ".orch" / "backlog.md").read_text()
    assert (
        "**R1**: nit [low]" in backlog,
        "**RP1**: old bug [preexisting]" in backlog,
    ) == (True, True)


def test_dev_commits_never_contain_orch_state(project: Path, fc: FakeClaude) -> None:
    _run(project)
    files = git(project, "show", "--name-only", "--format=", "HEAD~0", "--", "work.txt")
    dev_commit = git(project, "log", "--format=%H", "-1", "--", "work.txt")
    changed = git(project, "show", "--name-only", "--format=", dev_commit)
    assert (files, ".orch" in changed) == ("", False)


def test_bad_output_from_dev_asks_the_same_session(
    project: Path, fc: FakeClaude
) -> None:
    fc.on(
        "orch-dev",
        dev("work.txt", report=None) | {"structured": None},
        {"structured": DEV_OK},
    )
    fc.responses["orch-dev"][0].pop("structured")
    fc.save()
    st = _run(project)
    first, second = fc.calls("orch-dev")
    assert (st.status, second["session"], second["resume"]) == (
        S.DONE,
        first["session"],
        True,
    )
    assert (project / "work.txt").read_text() == "x\n"  # the work ran once


def test_parallel_stop_prefers_usage_limit(project: Path, fc: FakeClaude) -> None:
    fc.on("orch-review", {"exit": 1, "stdout": "", "stderr": "boom"})
    fc.on("orch-qa", USAGE)
    st = _run(project, scenario="full")
    assert (st.status, st.stop_kind) == (S.PAUSED, "usage_limit")


def test_new_lead_crash_retries_fresh_not_resume(project: Path, fc: FakeClaude) -> None:
    fc.on(
        "orch-add-checks",
        {"exit": 1, "stdout": "", "stderr": "boom"},
        {"structured": {"checks": []}},
    )
    st = _run(project, scenario="full")
    calls = fc.calls("orch-add-checks")
    assert (st.status, [c["resume"] for c in calls]) == (S.DONE, [False, False])


def test_finished_run_is_archived_and_a_new_one_starts(
    project: Path, fc: FakeClaude
) -> None:
    st1 = _run(project)
    git(project, "switch", "-q", "main")
    git(project, "merge", "-q", "--no-edit", st1.branch)
    d = project / ".orch" / "phases" / "02-more"
    d.mkdir()
    (d / "brief.md").write_text("# More\n")
    git(project, "add", "-A")
    git(project, "commit", "-qm", "next phase")
    st2 = _run(project)
    assert (st2.run_id != st1.run_id, [p.slug for p in st2.phases]) == (True, ["more"])
    assert (project / ".orch" / "runs" / st1.run_id / "state.json").is_file()


def test_final_state_is_pushed(project: Path, fc: FakeClaude) -> None:
    st = _run(project)
    remote = json.loads(git(project, "show", f"origin/{st.branch}:.orch/state.json"))
    assert remote["status"] == S.DONE


def test_reopened_blocked_phase_does_not_duplicate_backlog(
    project: Path, fc: FakeClaude
) -> None:
    fc.on("orch-review", {"structured": checker("fix", finding("R1"), finding("R2"))})
    fc.on(
        "orch-triage",
        {
            "structured": {
                "decision": "fix",
                "also_fix": [],
                "do_not_fix": [],
                "must_fix": [
                    {"ids": ["R1"], "title": "t", "design": "d", "repro": "r"}
                ],
                "defer": [{"ids": ["R2"], "title": "later", "reason": "scope"}],
            }
        },
    )
    fc.on(
        "orch-verify",
        {
            "structured": checker("fix", finding("R1"))
            | {"fixes": [], "deviation_verdicts": []}
        },
    )
    assert _run(project, scenario="full").status == S.BLOCKED
    assert _run(project, scenario=None).status == S.BLOCKED
    backlog = (project / ".orch" / "backlog.md").read_text()
    assert backlog.count("**R2**") == 1
