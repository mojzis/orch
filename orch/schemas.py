"""JSON schemas for every step's report. They live in code, not in templates,
so an edited template cannot break parsing."""

from __future__ import annotations

from typing import Any


def _obj(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": props,
        "required": required if required is not None else list(props),
        "additionalProperties": False,
    }


STR = {"type": "string"}
STRS = {"type": "array", "items": STR}
INT = {"type": "integer"}
VERDICT = {"type": "string", "enum": ["ship", "fix"]}
SEVERITY = {"type": "string", "enum": ["high", "medium", "low"]}

FINDING = _obj(
    {
        "id": STR,  # R1, Q1, V1
        "severity": SEVERITY,
        "title": STR,
        "detail": STR,
        "repro": STR,
        "files": STRS,
    }
)
CHECK_RUN = _obj(
    {"check": STR, "result": {"enum": ["pass", "fail", "error"]}, "evidence": STR}
)
TESTS = _obj(
    {
        "before": INT,
        "after": INT,
        "explained": STR,  # every change in count or status, explained
    }
)

DEV = _obj(
    {
        "summary": STR,
        "commits": STRS,
        "tests": TESTS,
        "design": STR,
        "real_run": STR,
        "deviations": STRS,
        "unresolved_issues": STRS,
    }
)
FIX = DEV

_CHECKER = {
    "verdict": VERDICT,
    "findings": {"type": "array", "items": FINDING},
    "checks_run": {"type": "array", "items": CHECK_RUN},
    "preexisting": {"type": "array", "items": FINDING},
    "proposed_oracles": STRS,
}
REVIEW = _obj(_CHECKER)
QA = _obj(_CHECKER)
COMBINED = _obj(_CHECKER)
VERIFY = _obj(
    _CHECKER
    | {
        "fixes": {
            "type": "array",
            "items": _obj({"finding": STR, "fixed": {"type": "boolean"}, "note": STR}),
        },
        "deviation_verdicts": STRS,
    }
)

ADD_CHECKS = _obj(
    {
        "checks": {
            "type": "array",
            "items": _obj(
                {
                    "source": STR,  # the deviation or unresolved issue, verbatim
                    "route": {"type": "string", "enum": ["review", "qa"]},
                    "check": STR,
                }
            ),
        }
    }
)

TRIAGE = _obj(
    {
        "decision": {"type": "string", "enum": ["ship", "fix"]},
        "must_fix": {
            "type": "array",
            "items": _obj(
                {
                    "ids": STRS,  # merged duplicates, e.g. ["R2", "Q1"]
                    "title": STR,
                    "design": STR,  # the one fix design picked
                    "repro": STR,  # the repro verify should use
                }
            ),
        },
        "also_fix": {
            "type": "array",
            "items": _obj({"ids": STRS, "title": STR, "design": STR, "repro": STR}),
        },
        "do_not_fix": {
            "type": "array",
            "items": _obj({"ids": STRS, "reason": STR}),
        },
        "defer": {
            "type": "array",
            "items": _obj({"ids": STRS, "title": STR, "reason": STR}),
        },
    }
)

LEARNINGS = _obj(
    {
        "phase_learnings": STR,  # markdown
        "process_lessons": STRS,  # appended to .orch/process-learnings.md
        "handoff": STR,  # for the next phase's brief
    }
)

PROBE = _obj(
    {"ok": {"type": "boolean"}, "ran": STR, "observations": STR, "problems": STRS}
)

SCHEMAS: dict[str, dict[str, Any]] = {
    "dev": DEV,
    "fix": FIX,
    "review": REVIEW,
    "qa": QA,
    "combined": COMBINED,
    "verify": VERIFY,
    "add_checks": ADD_CHECKS,
    "triage": TRIAGE,
    "learnings": LEARNINGS,
    "probe": PROBE,
}
