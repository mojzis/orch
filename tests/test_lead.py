"""Code checks on the lead's add-checks answer."""

from __future__ import annotations

from typing import Any

from orch.lead import MAX_RISK_CHECKS, check_add_checks

DEV = [{"deviations": ["kept X"], "unresolved_issues": []}]


def _c(source: str) -> dict[str, Any]:
    return {"source": source, "route": "qa", "check": "run it"}


def test_risk_checks_come_on_top_of_one_per_deviation() -> None:
    rep = {"checks": [_c("kept X"), _c("risk: daily_run push")]}
    assert check_add_checks(DEV, rep) == ""


def test_a_risk_check_does_not_stand_in_for_a_deviation() -> None:
    problem = check_add_checks(DEV, {"checks": [_c("risk: daily_run push")]})
    assert "no check for: kept X" in problem


def test_risk_checks_are_capped() -> None:
    risks = [_c(f"risk: area {i}") for i in range(MAX_RISK_CHECKS + 1)]
    problem = check_add_checks(DEV, {"checks": [_c("kept X"), *risks]})
    assert f"at most {MAX_RISK_CHECKS}" in problem
