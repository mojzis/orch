"""Markdown orch writes for sessions and people: inputs, fix briefs, log entries."""

from __future__ import annotations

import json
from typing import Any

from orch.gate import GateResult


def inputs_md(
    title: str, items: list[tuple[str, str]], sections: dict[str, str]
) -> str:
    """The one file a session gets as $ARGUMENTS: pointers, then short sections."""
    lines = [f"# {title}", "", "Read these files; they are the contract.", ""]
    lines += [f"- {label}: `{path}`" for label, path in items]
    for heading, body in sections.items():
        if body.strip():
            lines += ["", f"## {heading}", "", body.strip()]
    return "\n".join(lines) + "\n"


def log_entry(key: str, role: str, session: str, cost: float, report: Any) -> str:
    body = json.dumps(report, indent=2, ensure_ascii=False)
    return (
        f"\n## {key} ({role})\n\nsession `{session}`, ${cost:.4f}\n\n"
        f"```json\n{body}\n```\n"
    )


def gate_fix_brief(gate: GateResult, phase_brief: str) -> str:
    lines = [
        "# Fix brief: the gate failed",
        "",
        f"Phase brief: `{phase_brief}`",
        "",
        "## Must fix",
        "",
    ]
    if gate.returncode not in (0, None):
        lines += [
            f"- `{gate.cmd}` exited {gate.returncode}. Make it pass without "
            "weakening any check.",
            "",
            "```",
            gate.output.strip()[-3000:],
            "```",
            "",
        ]
    lines += [f"- {v}" for v in gate.violations]
    lines += [
        "",
        "## Do NOT",
        "",
        "- add skip/xfail, delete tests, or edit QA.md to get past the gate",
    ]
    return "\n".join(lines) + "\n"


def findings_of(reports: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for rep in reports.values():
        out += rep.get("findings") or []
    return out


def plain_fix_brief(reports: dict[str, dict[str, Any]], phase_brief: str) -> str:
    """Without a lead (small scenario): every finding of a `fix` report is must-fix."""
    lines = ["# Fix brief", "", f"Phase brief: `{phase_brief}`", "", "## Must fix", ""]
    for name, rep in reports.items():
        if rep.get("verdict") != "fix":
            continue
        for f in rep.get("findings") or []:
            lines += [
                f"### {f['id']} ({f['severity']}, from {name}): {f['title']}",
                "",
                f["detail"],
                "",
                f"Repro: {f['repro']}",
                "",
            ]
    lines += ["## Do NOT fix", "", "- anything not listed above"]
    return "\n".join(lines) + "\n"


def triage_fix_brief(triage: dict[str, Any], phase_brief: str) -> str:
    lines = ["# Fix brief", "", f"Phase brief: `{phase_brief}`", ""]
    for heading, key in (("Must fix", "must_fix"), ("Also fix", "also_fix")):
        lines += [f"## {heading}", ""]
        items = triage.get(key) or []
        if not items:
            lines += ["(none)", ""]
        for it in items:
            lines += [
                f"### {' = '.join(it['ids'])}: {it['title']}",
                "",
                f"Design: {it['design']}",
                "",
                f"Repro for verify: {it['repro']}",
                "",
            ]
    lines += ["## Do NOT fix", ""]
    dnf = triage.get("do_not_fix") or []
    lines += [f"- {' = '.join(d['ids'])}: {d['reason']}" for d in dnf] or ["(none)"]
    lines += ["", "Anything not listed above is out of scope for this fix."]
    return "\n".join(lines) + "\n"


def checks_md(checks: list[dict[str, Any]], route: str) -> str:
    mine = [c for c in checks if c.get("route") == route]
    return "\n".join(f"- {c['check']}  (from: {c['source']})" for c in mine)


def backlog_entry(phase: str, items: list[tuple[str, str]]) -> str:
    if not items:
        return ""
    lines = [f"\n## From phase {phase}", ""]
    lines += [f"- **{ids}**: {text}" for ids, text in items]
    return "\n".join(lines) + "\n"
