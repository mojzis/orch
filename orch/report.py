"""The run report written after the last phase (and on demand by `orch report`)."""

from __future__ import annotations

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
                f"| {rid} | {p.n}. {p.title} | {p.status} | "
                f"{'; '.join(ev[:3]).replace('|', '/') or '-'} |"
            )
    lines += ["", "## Phases", ""]
    lines += [
        "| # | phase | scenario | status | fix rounds | sessions | cost |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for p in st.phases:
        lines.append(
            f"| {p.n} | {p.title or p.slug} | {p.scenario} | {p.status} | "
            f"{p.fix_round} | {len(p.sessions)} | ${p.cost():.2f} |"
        )
    lines += ["", "## Tokens and cost by role", ""]
    by_role: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    for p in st.phases:
        for s in p.sessions:
            by_role[s.role][0] += s.tokens_in
            by_role[s.role][1] += s.tokens_out
            by_role[s.role][2] += s.cost
    for s in st.plan_sessions:
        by_role[s.role][2] += s.cost
    lines += ["| role | tokens in | tokens out | cost |", "| --- | --- | --- | --- |"]
    for role, (ti, to, c) in sorted(by_role.items()):
        lines.append(f"| {role} | {int(ti)} | {int(to)} | ${c:.2f} |")
    issues = _plan_issues(orch, st)
    lines += ["", "## Plan issues raised", ""] + (issues or ["(none)"])
    lines += ["", "## Replans", ""]
    lines += [f"- {r}" for r in st.replans] or ["(none)"]
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
