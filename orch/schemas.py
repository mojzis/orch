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

OUTLINE_PHASE = _obj(
    {
        "title": STR,
        "slug": STR,
        "deliverable": STR,
        "covers": STRS,
        "depends_on": {"type": "array", "items": INT},
        "size": {"type": "string", "enum": ["S", "M", "L"]},
        "scenario": STR,
        "approach": STR,
        "reason": STR,
        "set": STRS,  # role.key=value, each needs the reason above
    }
)
OUTLINE = _obj({"phases": {"type": "array", "items": OUTLINE_PHASE}})

REVIEW_FINDING = _obj(
    {"id": STR, "blocking": {"type": "boolean"}, "title": STR, "detail": STR}
)
OUTLINE_REVIEW = _obj(
    {"ok": {"type": "boolean"}, "findings": {"type": "array", "items": REVIEW_FINDING}}
)

BRIEF = _obj(
    {
        "questions": {
            "type": "array",
            "items": _obj(
                {"id": STR, "question": STR, "options": STRS, "recommended": STR}
            ),
        },
        "brief": STR,  # markdown; empty while questions wait
        "scenario": STR,  # "" keeps the outline's choice
        "scenario_reason": STR,
    }
)
PLAN_REVIEW = _obj(
    {
        "ok": {"type": "boolean"},
        "findings": {"type": "array", "items": REVIEW_FINDING},
        "qa_dry_run": {
            "type": "array",
            "items": _obj(
                {
                    "check": STR,
                    "result": {
                        "type": "string",
                        "enum": ["fails_now", "passes_now", "cannot_run"],
                    },
                    "new_behaviour": {"type": "boolean"},
                    "evidence": STR,
                }
            ),
        },
    }
)

SCHEMAS |= {
    "outline": OUTLINE,
    "outline_review": OUTLINE_REVIEW,
    "brief": BRIEF,
    "plan_review": PLAN_REVIEW,
}
