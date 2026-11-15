"""Checks in code on the lead's structured answers. A failed check sends the
problem back to the lead once."""

from __future__ import annotations

from typing import Any

RISK_PREFIX = "risk:"
MAX_RISK_CHECKS = 5


def check_add_checks(dev_reports: list[dict[str, Any]], rep: dict[str, Any]) -> str:
    """Exactly one routed check per deviation and per unresolved issue, plus at
    most MAX_RISK_CHECKS "risk: <area>" checks for risky parts of the diff that no
    deviation covers (the phase 4 acceptance run: the human's findings that
    mattered came from such checks)."""
    wanted: list[str] = []
    for d in dev_reports:
        for x in [*(d.get("deviations") or []), *(d.get("unresolved_issues") or [])]:
            if x.strip() and x not in wanted:  # the same item twice needs one check
                wanted.append(x)
    checks = rep.get("checks") or []
    risks = [c for c in checks if c.get("source", "").startswith(RISK_PREFIX)]
    items = [c for c in checks if c not in risks]
    sources = [c.get("source", "") for c in items]
    problems = []
    if len(items) != len(wanted):
        problems.append(f"{len(wanted)} item(s) need a check, got {len(items)}")
    missing = [w for w in wanted if w not in sources]
    if missing:
        problems.append("no check for: " + "; ".join(missing))
    if len(risks) > MAX_RISK_CHECKS:
        problems.append(f"{len(risks)} risk checks, at most {MAX_RISK_CHECKS}")
    return " / ".join(problems)


def finding_ids(reports: dict[str, dict[str, Any]]) -> set[str]:
    ids = set()
    for rep in reports.values():
        ids |= {f["id"] for f in rep.get("findings") or []}
    return ids


def check_triage(reports: dict[str, dict[str, Any]], tri: dict[str, Any]) -> str:
    """Every finding in exactly one list; a `fix` decision has must-fix items."""
    seen: dict[str, str] = {}
    dupes = []
    for key in ("must_fix", "also_fix", "do_not_fix", "defer"):
        for item in tri.get(key) or []:
            for fid in item.get("ids") or []:
                if fid in seen and seen[fid] != key:
                    dupes.append(f"{fid} in {seen[fid]} and {key}")
                seen[fid] = key
    problems = []
    missing = sorted(finding_ids(reports) - set(seen))
    if missing:
        problems.append("not triaged: " + ", ".join(missing))
    if dupes:
        problems.append("in more than one list: " + "; ".join(dupes))
    if tri.get("decision") == "fix" and not (
        tri.get("must_fix") or tri.get("also_fix")
    ):
        problems.append("decision is fix but nothing is listed to fix")
    if tri.get("decision") == "ship" and tri.get("must_fix"):
        problems.append("decision is ship but must_fix is not empty")
    return " / ".join(problems)
