"""The run report's tables, rendered from a hand-built state."""

from __future__ import annotations

import json
from pathlib import Path

from orch import state as S
from orch.report import render_report

from .helpers import checker


def _state(tmp_path: Path) -> S.RunState:
    rec = S.SessionRecord(
        key="dev",
        role="dev",
        session_id="s",
        ok=True,
        cost=0.5,
        tokens_in=1000,
        tokens_out=20,
    )
    plan = S.SessionRecord(
        key="outline",
        role="outline",
        session_id="p",
        ok=True,
        cost=0.1,
        tokens_in=300,
        tokens_out=7,
    )
    ph = S.Phase(n=1, slug="add", title="Add", covers=["R1", "R2"], sessions=[rec])
    qa = checker()
    qa["checks_run"] = [
        {"check": "R1: add 2 2 prints 4", "result": "pass", "evidence": "4"}
    ]
    reports = tmp_path / ".orch" / "phases" / ph.dirname / "reports"
    reports.mkdir(parents=True)
    (reports / "qa.json").write_text(json.dumps(qa))
    return S.RunState(
        run_id="r",
        branch="orch/r",
        phases=[ph],
        plan_sessions=[plan],
        replans=[
            {
                "at": "2026-11-14T10:00:00",
                "from_phase": 2,
                "before": ["Old"],
                "after": ["New"],
            },
            {
                "at": "2026-11-14T11:00:00",
                "phase": 1,
                "kind": "auto-decide",
                "answers": "- Q1 db?: files",
            },
        ],
    )


def test_evidence_is_tied_to_the_requirement_it_names(tmp_path: Path) -> None:
    out = render_report(tmp_path, _state(tmp_path), ["R1", "R2"], None)
    assert "| R1 | 1. Add | todo | qa: R1: add 2 2 prints 4 -> pass |" in out
    assert "| R2 | 1. Add | todo | no check names R2 |" in out


def test_tokens_by_phase_and_for_plan_sessions(tmp_path: Path) -> None:
    out = render_report(tmp_path, _state(tmp_path), [], None)
    assert "| 1 | Add | full | todo | 0 | 1 | 1000 | 20 | $0.50 |" in out
    assert "| outline | 300 | 7 | $0.10 |" in out


def test_replans_read_as_sentences(tmp_path: Path) -> None:
    out = render_report(tmp_path, _state(tmp_path), [], None)
    assert "- 2026-11-14 10:00 replanned from phase 2: Old -> New" in out
    assert "--auto-decide took\n  - Q1 db?: files" in out
    assert "{" not in out.split("## Replans")[1].split("##")[0]
