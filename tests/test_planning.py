"""`orch plan` and per-phase briefs, with the fake claude."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from orch import planning
from orch import state as S
from orch.repo import Repo

from .conftest import FakeClaude, git
from .helpers import LEARN, checker, dev

PITCH = """---
title: toy
---
# Pitch

- R1: adding works
- R2: the CLI prints results
"""


def row(title: str, covers: list[str], **kw: Any) -> dict[str, Any]:
    return {
        "title": title,
        "slug": title.lower().replace(" ", "-"),
        "deliverable": "d",
        "covers": covers,
        "depends_on": [],
        "size": "S",
        "scenario": "small",
        "approach": "a",
        "reason": "low risk",
        "set": [],
    } | kw


GOOD = {
    "structured": {"phases": [row("Add", ["R1"]), row("Cli", ["R2"], depends_on=[1])]}
}
OK_REVIEW = {"structured": {"ok": True, "findings": []}}
BRIEF_TEXT = "## Goal\n\nadd\n\n## Acceptance\n\n- A1\n"
BRIEF = {
    "structured": {
        "questions": [],
        "brief": BRIEF_TEXT,
        "scenario": "",
        "scenario_reason": "",
    }
}
PR_OK = {
    "structured": {
        "ok": True,
        "findings": [],
        "qa_dry_run": [
            {
                "check": "python3 -c 'import calc'",
                "result": "fails_now",
                "new_behaviour": True,
                "evidence": "ImportError",
            }
        ],
    }
}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    repo = tmp_path / "project"
    (repo / "docs").mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    (repo / "docs" / "pitch.md").write_text(PITCH)
    (repo / ".orch").mkdir()
    (repo / ".orch" / "config.toml").write_text('gate_cmd = "true"\npush = false\n')
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    return repo


@pytest.fixture
def fc(fake_claude: FakeClaude) -> FakeClaude:
    fake_claude.on("orch-outline", GOOD)
    fake_claude.on("orch-outline-review", OK_REVIEW)
    fake_claude.on("orch-brief", BRIEF)
    fake_claude.on("orch-plan-review", PR_OK)
    fake_claude.on("orch-dev", dev("work.txt"))
    fake_claude.on("orch-combined", {"structured": checker()})
    fake_claude.on("orch-learnings", {"structured": LEARN})
    return fake_claude


def test_requirement_ids_in_order() -> None:
    assert planning.requirement_ids(PITCH) == ["R1", "R2"]


def test_plan_writes_outline_and_phases(project: Path, fc: FakeClaude) -> None:
    st = planning.plan(Repo(project))
    assert (st.status, [p.slug for p in st.phases]) == (S.RUNNING, ["add", "cli"])
    plan_md = (project / ".orch" / "plan.md").read_text()
    assert "- R2: phase 2" in plan_md
    assert git(project, "rev-parse", "--abbrev-ref", "HEAD") == st.branch


def test_uncovered_requirement_is_retried_once(project: Path, fc: FakeClaude) -> None:
    fc.on("orch-outline", {"structured": {"phases": [row("Add", ["R1"])]}}, GOOD)
    st = planning.plan(Repo(project))
    retry = (project / ".orch/plan/inputs/outline-retry.md").read_text()
    assert (len(st.phases), "not covered by any phase: R2" in retry) == (2, True)


def test_outline_failing_checks_twice_needs_replan(
    project: Path, fc: FakeClaude
) -> None:
    fc.on("orch-outline", {"structured": {"phases": [row("Add", ["R1"])]}})
    st = planning.plan(Repo(project))
    assert (st.status, "R2" in st.stop_reason) == (S.NEEDS_REPLAN, True)


@pytest.mark.parametrize(
    ("bad", "problem"),
    [
        (row("Cli", ["R2"], depends_on=[2]), "depends on a later phase or itself"),
        (row("Cli", ["R2"], scenario="small", reason=""), "without a reason"),
        (
            row("Cli", ["R2"], set=["dev.effort=high"], scenario="full", reason=""),
            "without a reason",
        ),
        (row("Cli", ["R2"], scenario="nope"), "unknown scenario"),
    ],
)
def test_outline_checks(bad: dict[str, Any], problem: str) -> None:
    outline = {"phases": [row("Add", ["R1"], scenario="full"), bad]}
    problems = planning.check_outline(
        outline, ["R1", "R2"], {"full": "default", "small": "default"}, "full"
    )
    assert any(problem in p for p in problems), problems


def test_outline_review_blocking_finding_gets_one_revision(
    project: Path, fc: FakeClaude
) -> None:
    # a paid-API phase without real-data: the review flags it, the outline is revised
    fc.on(
        "orch-outline",
        {"structured": {"phases": [row("Fetch prices", ["R1", "R2"])]}},
        {
            "structured": {
                "phases": [
                    row(
                        "Fetch prices",
                        ["R1", "R2"],
                        scenario="real-data",
                        reason="paid API",
                    )
                ]
            }
        },
    )
    fc.on(
        "orch-outline-review",
        {
            "structured": {
                "ok": False,
                "findings": [
                    {
                        "id": "O1",
                        "blocking": True,
                        "title": "paid API without real-data",
                        "detail": "phase 1 calls a paid API",
                    }
                ],
            }
        },
    )
    st = planning.plan(Repo(project))
    rev = (project / ".orch/plan/inputs/outline-revision.md").read_text()
    assert ("paid API without real-data" in rev, st.phases[0].scenario) == (
        True,
        "real-data",
    )


def test_run_plans_then_briefs_then_runs(project: Path, fc: FakeClaude) -> None:
    st = planning.run(Repo(project))
    assert (st.status, [p.status for p in st.phases]) == (S.DONE, ["done", "done"])
    brief = (project / ".orch/phases/01-add/brief.md").read_text()
    assert brief.startswith("# Add\n\n## Goal")


def test_pause_stops_after_plan(project: Path, fc: FakeClaude) -> None:
    st = planning.run(Repo(project), pause=True)
    assert (st.status, fc.calls("orch-brief")) == (S.RUNNING, [])


def test_qa_check_that_already_passes_is_blocking(
    project: Path, fc: FakeClaude
) -> None:
    passes = {
        "structured": {
            "ok": True,
            "findings": [],
            "qa_dry_run": [
                {
                    "check": "calc.add exists",
                    "result": "passes_now",
                    "new_behaviour": True,
                    "evidence": "it does",
                }
            ],
        }
    }
    fc.on("orch-plan-review", passes, passes, PR_OK)
    st = planning.run(Repo(project))
    rev = (project / ".orch/phases/01-add/inputs/brief_rev-1.md").read_text()
    assert (st.status, "already passes" in rev) == (S.DONE, True)


def test_brief_naming_missing_file_is_blocking(project: Path, fc: FakeClaude) -> None:
    bad = {
        "structured": {
            "questions": [],
            "scenario": "",
            "scenario_reason": "",
            "brief": "## Goal\n\nChange `orch/importer.py` to dedupe.\n",
        }
    }
    fc.on("orch-brief", bad, BRIEF)
    planning.run(Repo(project))
    rev = (project / ".orch/phases/01-add/inputs/brief_rev-1.md").read_text()
    assert "`orch/importer.py`, which does not exist" in rev


def test_brief_still_blocked_after_two_revisions_needs_replan(
    project: Path, fc: FakeClaude
) -> None:
    fc.on(
        "orch-plan-review",
        {
            "structured": {
                "ok": False,
                "qa_dry_run": [],
                "findings": [
                    {
                        "id": "P1",
                        "blocking": True,
                        "title": "too big",
                        "detail": "two sessions",
                    }
                ],
            }
        },
    )
    st = planning.run(Repo(project))
    assert (st.status, len(fc.calls("orch-plan-review"))) == (S.NEEDS_REPLAN, 3)


QUESTION = {
    "structured": {
        "questions": [
            {
                "id": "D1",
                "question": "files or sqlite?",
                "options": ["files", "sqlite"],
                "recommended": "files",
            }
        ],
        "brief": "",
        "scenario": "",
        "scenario_reason": "",
    }
}


def test_design_question_pauses_as_needs_user(project: Path, fc: FakeClaude) -> None:
    fc.on("orch-brief", QUESTION, BRIEF)
    st = planning.run(Repo(project))
    qfile = project / ".orch/phases/01-add/questions.md"
    assert (st.status, "files or sqlite?" in qfile.read_text()) == (S.NEEDS_USER, True)
    qfile.write_text(qfile.read_text().replace("answer: ", "answer: sqlite"))
    st = planning.run(Repo(project))
    answered = (project / ".orch/phases/01-add/inputs/brief-answered.md").read_text()
    assert (st.status, "files or sqlite?: sqlite" in answered) == (S.DONE, True)


def test_auto_decide_takes_recommended(project: Path, fc: FakeClaude) -> None:
    fc.on("orch-brief", QUESTION, BRIEF)
    st = planning.run(Repo(project), auto_decide=True)
    answers = (project / ".orch/phases/01-add/inputs/brief-answered.md").read_text()
    assert (st.status, "files (recommended option" in answers) == (S.DONE, True)
    assert any(r.get("kind") == "auto-decide" for r in st.replans)


def test_lead_scenario_switch_is_recorded_as_replan(
    project: Path, fc: FakeClaude
) -> None:
    switch = {
        "structured": {
            "questions": [],
            "brief": BRIEF_TEXT,
            "scenario": "full",
            "scenario_reason": "touches 3 modules",
        }
    }
    fc.on("orch-brief", switch, BRIEF)
    fc.on("orch-add-checks", {"structured": {"checks": []}})
    fc.on("orch-review", {"structured": checker()})
    fc.on("orch-qa", {"structured": checker()})
    st = planning.run(Repo(project))
    assert (st.phases[0].scenario, st.replans[0]["reason"]) == (
        "full",
        "touches 3 modules",
    )
    assert len(fc.calls("orch-review")) == 1


def test_replan_from_keeps_done_phases(project: Path, fc: FakeClaude) -> None:
    fc.on("orch-dev", dev("work.txt"), {"exit": 1, "stdout": "", "stderr": "x"})
    st = planning.run(Repo(project))
    assert [p.status for p in st.phases] == ["done", S.FAILED]
    fc.on("orch-outline", {"structured": {"phases": [row("Cli two", ["R2"])]}})
    st = planning.plan(Repo(project), from_n=2)
    assert ([p.title for p in st.phases], st.replans[-1]["from_phase"]) == (
        ["Add", "Cli two"],
        2,
    )


def test_plan_without_pitch_is_config_error(tmp_path: Path, fc: FakeClaude) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    git(repo, "init", "-q")
    with pytest.raises(planning.ConfigError, match=r"no docs/pitch\.md"):
        planning.plan(Repo(repo))
