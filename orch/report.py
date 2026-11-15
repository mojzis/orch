"""The run report written after the last phase (and on demand by `orch report`)."""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from orch import state as S
from orch import store


def _reports(pdir: Path) -> dict[str, Any]:
    out = {}
    for p in sorted((pdir / "reports").glob("*.json")):
        try:
            out[p.stem] = store.read_json(p)
        except ValueError:
            continue
    return out


def _evidence(reports: dict[str, Any]) -> list[str]:
    """Passing checks from QA, the combined check and verify, as evidence."""
    lines = []
    for key, rep in reports.items():
        if not key.startswith(("qa", "combined", "verify")) or not isinstance(
            rep, dict
        ):
            continue
        for c in rep.get("checks_run") or []:
            lines.append(f"{key}: {c['check']} -> {c['result']}")
    return lines


def _naming(evidence: list[str], rid: str) -> list[str]:
    """The checks that name the requirement (QA is asked to prefix them with its
    id). None named: say so, rather than pass off the phase's checks as proof."""
    named = [e for e in evidence if re.search(rf"\b{re.escape(rid)}\b", e)]
    if named or not evidence:
        return named
    return [f"no check names {rid}"]


def _cell(items: list[str]) -> str:
    return "; ".join(items).replace("|", "/").replace("\n", " ") or "-"


def _replan(r: dict[str, Any]) -> str:
    at = str(r.get("at", ""))[:16].replace("T", " ")
    if "from_phase" in r:
        before = ", ".join(r.get("before") or []) or "-"
        after = ", ".join(r.get("after") or []) or "-"
        return f"- {at} replanned from phase {r['from_phase']}: {before} -> {after}"
    if r.get("kind") == "scenario":
        return (
            f"- {at} phase {r.get('phase')}: scenario {r.get('before')} -> "
            f"{r.get('after')} ({r.get('reason') or 'no reason given'})"
        )
    if r.get("kind") == "auto-decide":
        answers = str(r.get("answers", "")).replace("\n", "\n  ")
        return f"- {at} phase {r.get('phase')}: --auto-decide took\n  {answers}"
    return f"- {at} {r}"


def render_report(
    root: Path, st: S.RunState, req_ids: list[str], final_qa: dict[str, Any] | None
) -> str:
    orch = root / ".orch"
    lines = [
        f"# Run report: {st.run_id}",
        "",
        f"- branch: `{st.branch}` (not merged; open a PR by hand)",
        f"- status: **{st.status}**",
        f"- claude: {st.claude_version}",
        f"- total cost: ${st.cost():.2f}",
        "",
        "## Requirements",
        "",
        "| requirement | phase | status | QA evidence |",
        "| --- | --- | --- | --- |",
    ]
    for rid in req_ids:
        phases = [p for p in st.phases if rid in p.covers]
        if not phases:
            lines.append(f"| {rid} | - | **left out** | - |")
            continue
        for p in phases:
            ev = _evidence(_reports(orch / "phases" / p.dirname))
            lines.append(
                f"| {rid} | {p.n}. {p.title} | {p.status} | {_cell(_naming(ev, rid))} |"
            )
    lines += ["", "## Phases", ""]
    lines += [
        "| # | phase | scenario | status | fix rounds | sessions | tokens in "
        "| tokens out | cost |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for p in st.phases:
        lines.append(
            f"| {p.n} | {p.title or p.slug} | {p.scenario} | {p.status} | "
            f"{p.fix_round} | {len(p.sessions)} | "
            f"{sum(s.tokens_in for s in p.sessions)} | "
            f"{sum(s.tokens_out for s in p.sessions)} | ${p.cost():.2f} |"
        )
    lines += ["", "## Tokens and cost by role", ""]
    by_role: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    for p in st.phases:
        for s in p.sessions:
            by_role[s.role][0] += s.tokens_in
            by_role[s.role][1] += s.tokens_out
            by_role[s.role][2] += s.cost
    for s in st.plan_sessions:
        by_role[s.role][0] += s.tokens_in
        by_role[s.role][1] += s.tokens_out
        by_role[s.role][2] += s.cost
    lines += ["| role | tokens in | tokens out | cost |", "| --- | --- | --- | --- |"]
    for role, (ti, to, c) in sorted(by_role.items()):
        lines.append(f"| {role} | {int(ti)} | {int(to)} | ${c:.2f} |")
    issues = _plan_issues(orch, st)
    lines += ["", "## Plan issues raised", ""] + (issues or ["(none)"])
    lines += ["", "## Replans", ""]
    lines += [_replan(r) for r in st.replans] or ["(none)"]
    if final_qa is not None:
        lines += [
            "",
            "## Full QA tier",
            "",
            f"`{final_qa['cmd']}` exited {final_qa['returncode']}",
            "",
            "```",
            final_qa["output"].strip()[-2000:],
            "```",
        ]
    for title, name in (
        ("Backlog (deferred findings)", "backlog.md"),
        ("Process lessons", "process-learnings.md"),
    ):
        p = orch / name
        lines += ["", f"## {title}", ""]
        lines.append(p.read_text(encoding="utf-8").strip() if p.is_file() else "(none)")
    return "\n".join(lines) + "\n"


def _plan_issues(orch: Path, st: S.RunState) -> list[str]:
    out = []
    review = orch / "plan" / "reports" / "outline_review.json"
    if review.is_file():
        for f in store.read_json(review).get("findings") or []:
            out.append(f"- outline: {f['id']} {f['title']}")
    for p in st.phases:
        for f in sorted(
            (orch / "phases" / p.dirname / "reports").glob(
                "plan_review-*-blocking.json"
            )
        ):
            out += [f"- phase {p.n} brief: {b}" for b in store.read_json(f)]
    return out
