"""`orch plan`: split the pitch into an outline of phases, once.

Outline (fresh session) -> checks in code (one retry) -> outline review (fresh,
read-only) -> one revision. The result is .orch/plan.md and the phases in
state.json."""

from __future__ import annotations

import re
import shutil
import time
from datetime import UTC, datetime
from typing import Any

from orch import claude, gitops, render, store
from orch import layers as L
from orch import state as S
from orch.errors import ConfigError
from orch.loop import (
    INTERRUPTED,
    Run,
    StopRun,
    archive_run,
    discover_phases,
    ensure_gitignore,
    execute,
    load_state,
    new_run_id,
    read_pitch,
    sigint_forwarding,
    start_or_resume,
)
from orch.repo import Repo
from orch.schemas import SCHEMAS

MAX_PHASES = 12
REQ_RE = re.compile(r"\bR\d+\b")
SLUG_RE = re.compile(r"[^a-z0-9]+")


def requirement_ids(pitch: str) -> list[str]:
    seen: list[str] = []
    for rid in REQ_RE.findall(pitch):
        if rid not in seen:
            seen.append(rid)
    return seen


def check_outline(  # noqa: PLR0913 -- the bounds one outline is checked against
    outline: dict[str, Any],
    req_ids: list[str],
    scenarios: dict[str, str],
    default_scenario: str,
    *,
    first_n: int = 1,
    max_phases: int = MAX_PHASES,
) -> list[str]:
    """Every requirement covered, no forward dependency, counts and sizes within
    bounds, scenarios known, every change to a default carries a reason."""
    phases = outline.get("phases") or []
    problems = []
    if not 1 <= len(phases) <= max_phases:
        problems.append(f"{len(phases)} phases; must be 1 to {max_phases}")
    covered = {r for p in phases for r in p.get("covers") or []}
    if missing := [r for r in req_ids if r not in covered]:
        problems.append(f"requirements not covered by any phase: {', '.join(missing)}")
    if unknown := sorted(covered - set(req_ids)) if req_ids else []:
        problems.append(f"phases cover ids the pitch does not define: {unknown}")
    for i, p in enumerate(phases):
        n = first_n + i
        label = f"phase {n} ({p.get('title', '?')})"
        if bad := [d for d in p.get("depends_on") or [] if d >= n]:
            problems.append(f"{label} depends on a later phase or itself: {bad}")
        if p.get("scenario") not in scenarios:
            problems.append(
                f"{label}: unknown scenario {p.get('scenario')!r}; "
                f"known: {', '.join(scenarios)}"
            )
        changed = p.get("scenario") != default_scenario or p.get("set")
        if changed and not str(p.get("reason") or "").strip():
            problems.append(
                f"{label} changes the default scenario or settings without a reason"
            )
        try:
            L.parse_sets(p.get("set") or [])
        except ConfigError as e:
            problems.append(f"{label}: {e}")
    slugs = [slugify(p.get("slug") or p.get("title") or "") for p in phases]
    if len(set(slugs)) != len(slugs):
        problems.append("phase slugs are not unique")
    return problems


def slugify(text: str) -> str:
    return SLUG_RE.sub("-", text.lower()).strip("-")[:40] or "phase"


def plan(
    repo: Repo,
    *,
    scenario: str | None = None,
    sets: list[str] | None = None,
    from_n: int | None = None,
) -> S.RunState:
    pitch_path = repo.root / "docs" / "pitch.md"
    if not pitch_path.is_file():
        raise ConfigError(
            "no docs/pitch.md", hint="write the shaped pitch first (the shape skill)"
        )
    layers = L.Layers.for_repo(repo.root)
    pitch = read_pitch(repo.root)
    default_sc = scenario or pitch.get("scenario") or "full"
    L.load_scenario(layers, default_sc)
    if from_n is None:
        if repo.state_path.is_file():
            raise ConfigError(
                "a run already exists here",
                hint="`orch run` to continue it, or `orch plan --from N` to replan",
            )
        if not gitops.is_clean(repo.root, exclude_orch=True):
            raise ConfigError(
                "the working tree has uncommitted changes",
                hint="commit or stash them; orch starts a run branch from HEAD",
            )
        run_id = new_run_id(repo.root)
        st = S.RunState(run_id=run_id, branch=f"orch/{run_id}")
    else:
        st = load_state(repo)
        if not 1 <= from_n <= len(st.phases) + 1:
            raise ConfigError(
                f"--from {from_n}: the run has {len(st.phases)} phases",
                hint=f"use 1 to {len(st.phases) + 1}",
            )
    gitops.ensure_branch(repo.root, st.branch)
    ensure_gitignore(repo)
    run = Run(
        repo=repo,
        layers=layers,
        st=st,
        config=repo.config(),
        cli_scenario=scenario,
        cli_sets=L.parse_sets(sets or []),
        pitch=pitch,
    )
    run.skills_dir = L.materialize_skills(layers, repo.cache / "skills")
    st.claude_version = claude.claude_version()
    first_n = from_n or 1
    kept = [p for p in st.phases if p.n < first_n]
    req_ids = requirement_ids(pitch_path.read_text(encoding="utf-8"))
    if kept:  # requirements already delivered stay delivered
        done = {r for p in kept for r in p.covers}
        req_ids = [r for r in req_ids if r not in done]
    scenarios = L.list_scenarios(layers)
    INTERRUPTED.clear()
    try:
        with sigint_forwarding():
            outline = _outline(
                run,
                req_ids=req_ids,
                scenarios=scenarios,
                default_sc=default_sc,
                first_n=first_n,
                kept=kept,
            )
    except StopRun as stop:
        st.status, st.stop_reason, st.reset_at = stop.status, stop.reason, stop.reset_at
        st.stop_kind = stop.kind
        run.save()
        return st
    old = _drop_replaced(repo, st, first_n)
    st.phases = kept + [
        _phase(first_n + i, row, default_sc) for i, row in enumerate(outline["phases"])
    ]
    if from_n is not None:
        st.replans.append(
            {
                "at": S.now(),
                "from_phase": from_n,
                "before": old,
                "after": [p.title for p in st.phases if p.n >= first_n],
            }
        )
    st.status, st.stop_reason, st.reset_at, st.stop_kind = S.RUNNING, "", None, ""
    store.write_text(repo.orch / "plan.md", render_plan(st, req_ids))
    run.save()
    gitops.commit_paths(repo.root, [".orch"], f"orch: plan ({len(st.phases)} phases)")
    return st


def _drop_replaced(repo: Repo, st: S.RunState, first_n: int) -> list[str]:
    """Phases from first_n on are replaced: keep their cost in the run's total,
    move their folders aside and forget their pending sessions."""
    replaced = [p for p in st.phases if p.n >= first_n]
    for p in replaced:
        st.plan_sessions += p.sessions
        if (d := repo.phase_dir(p.dirname)).exists():
            stamp = S.now().replace(":", "")
            shutil.move(d, repo.orch / "replaced" / f"{stamp}-{p.dirname}")
    st.pending = {
        k: v for k, v in st.pending.items() if int(k.partition(":")[0]) < first_n
    }
    return [p.title for p in replaced]


def _phase(n: int, row: dict[str, Any], default_sc: str) -> S.Phase:
    return S.Phase(
        n=n,
        slug=slugify(row.get("slug") or row["title"]),
        title=row["title"],
        scenario=row.get("scenario") or default_sc,
        covers=list(row.get("covers") or []),
        deliverable=row.get("deliverable", ""),
        depends_on=list(row.get("depends_on") or []),
        size=row.get("size", ""),
        approach=row.get("approach", ""),
        reason=row.get("reason", ""),
        task_set=L.parse_sets(row.get("set") or []),
    )


def _outline(  # noqa: PLR0913 -- what the outline is checked against
    run: Run,
    *,
    req_ids: list[str],
    scenarios: dict[str, str],
    default_sc: str,
    first_n: int,
    kept: list[S.Phase],
) -> dict[str, Any]:
    def checks(o: dict[str, Any]) -> list[str]:
        return check_outline(o, req_ids, scenarios, default_sc, first_n=first_n)

    context = _context(
        run,
        req_ids=req_ids,
        scenarios=scenarios,
        default_sc=default_sc,
        first_n=first_n,
        kept=kept,
    )
    outline = _session(run, "outline", "outline", context)
    if problems := checks(outline):
        outline = _session(
            run, "outline#retry", "outline", context + _rejected(problems)
        )
        if problems := checks(outline):
            raise StopRun(
                S.NEEDS_REPLAN, "outline failed its checks: " + "; ".join(problems)
            )
    store.write_text(run.repo.orch / "plan.md", render_outline_draft(outline))
    review = _session(
        run,
        "outline_review",
        "outline_review",
        context + "\n## The outline to review\n\nSee `.orch/plan.md` (draft).\n",
    )
    blocking = [f for f in review.get("findings") or [] if f.get("blocking")]
    if blocking or not review.get("ok", False):
        notes = "\n".join(f"- {f['id']}: {f['title']}: {f['detail']}" for f in blocking)
        outline = _session(
            run,
            "outline#revision",
            "outline",
            context
            + "\n## Outline review findings to address\n\n"
            + (notes or "- the review did not approve the outline")
            + "\n\nYour previous outline is in `.orch/plan.md` (draft).\n",
        )
        if problems := checks(outline):
            raise StopRun(
                S.NEEDS_REPLAN,
                "revised outline failed its checks: " + "; ".join(problems),
            )
    return outline


def _rejected(problems: list[str]) -> str:
    return (
        "\n## Your previous outline was rejected\n\n"
        + "\n".join(f"- {p}" for p in problems)
        + "\n\nAnswer again, fixing exactly these.\n"
    )


def _context(  # noqa: PLR0913 -- everything the outline session needs to know
    run: Run,
    *,
    req_ids: list[str],
    scenarios: dict[str, str],
    default_sc: str,
    first_n: int,
    kept: list[S.Phase],
) -> str:
    root = run.root
    items = [("Pitch", "docs/pitch.md")]
    for label, rel in (
        ("Decisions (newer wins)", "docs/decisions"),
        ("Architecture", "docs/architecture.md"),
        ("QA principles and tiers", "QA.md"),
        ("Process learnings", ".orch/process-learnings.md"),
    ):
        if (root / rel).exists():
            items.append((label, rel))
    sc_lines = []
    for name in scenarios:
        sc = L.load_scenario(run.layers, name)
        sc_lines.append(f"- `{name}`: {sc.description} Steps: {', '.join(sc.steps)}")
    sections = {
        "Requirement ids to cover": ", ".join(req_ids) or "(none found in the pitch)",
        "Scenarios": "\n".join(sc_lines),
        "Default scenario": f"`{default_sc}`. Any other choice, and any `set`, "
        "needs a one-line `reason`.",
        "Bounds": f"At most {MAX_PHASES} phases, each one session (size S, M or L).",
    }
    if kept:
        sections["Replanning"] = (
            f"Phases 1 to {first_n - 1} are done; plan phases from {first_n} on. "
            "Done phases:\n"
            + "\n".join(
                f"- {p.n}. {p.title}: covers {', '.join(p.covers)}" for p in kept
            )
            + "\nNumber `depends_on` with the real phase numbers."
        )
    return render.inputs_md("Inputs for the outline", items, sections)


def _session(run: Run, key: str, role: str, inputs: str) -> dict[str, Any]:
    pdir = run.repo.orch / "plan"
    inputs_path = pdir / "inputs" / f"{key.replace('#', '-')}.md"
    store.write_text(inputs_path, inputs)
    sc = L.load_scenario(run.layers, run.cli_scenario or "full")
    res = L.resolve_role(
        run.layers,
        sc,
        role,
        L.TaskLayer((("cli:--set", run.cli_sets),) if run.cli_sets else ()),
    )
    for attempt in (1, 2):
        r = claude.run_session(
            res.config,
            run.rel(inputs_path),
            run.root,
            schema=SCHEMAS[role],
            add_dirs=(run.skills_dir,) if run.skills_dir else (),
        )
        run.st.plan_sessions.append(
            S.SessionRecord(
                key=key,
                role=role,
                session_id=r.session_id,
                ok=r.ok,
                cost=r.cost_delta,
                error_kind=r.error_kind,
            )
        )
        run.save()
        if INTERRUPTED.is_set():
            raise StopRun(S.PAUSED, f"interrupted during {key}", kind="interrupted")
        if r.ok and r.structured is not None:
            store.write_json(
                pdir / "reports" / f"{key.replace('#', '-')}.json", r.structured
            )
            store.append_text(
                pdir / "log.md",
                render.log_entry(key, role, r.session_id, r.cost_delta, r.structured),
            )
            return r.structured
        if r.error_kind == "usage_limit":
            raise StopRun(
                S.PAUSED, f"usage limit during {key}", r.reset_at, kind="usage_limit"
            )
        if r.error_kind in ("auth", "budget") or attempt == 2:  # noqa: PLR2004
            status = S.NEEDS_USER if r.error_kind == "auth" else S.FAILED
            raise StopRun(status, f"{key}: {r.error_kind}: {r.error[:300]}")
    raise AssertionError("unreachable")


def render_outline_draft(outline: dict[str, Any]) -> str:
    lines = ["# Outline (draft, under review)", ""]
    for i, p in enumerate(outline.get("phases") or [], 1):
        lines += [
            f"## {i}. {p.get('title')}",
            "",
            f"- deliverable: {p.get('deliverable')}",
            f"- covers: {', '.join(p.get('covers') or [])}",
            f"- depends on: {p.get('depends_on') or '-'}",
            f"- size: {p.get('size')}; scenario: {p.get('scenario')}",
            f"- approach: {p.get('approach')}",
            f"- reason: {p.get('reason') or '-'}",
            f"- set: {', '.join(p.get('set') or []) or '-'}",
            "",
        ]
    return "\n".join(lines)


def render_plan(st: S.RunState, req_ids: list[str]) -> str:
    lines = [
        "# Plan",
        "",
        f"Run `{st.run_id}` on branch `{st.branch}`. Written by `orch plan`; "
        "each phase's brief is written just before it runs.",
        "",
        "| # | phase | covers | depends on | size | scenario | approach | reason |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for p in st.phases:
        lines.append(
            f"| {p.n} | {p.title} | {', '.join(p.covers)} | "
            f"{', '.join(map(str, p.depends_on)) or '-'} | {p.size} | {p.scenario} | "
            f"{p.approach} | {p.reason or '-'} |"
        )
    lines += ["", "## Deliverables", ""]
    lines += [f"{p.n}. **{p.title}**: {p.deliverable}" for p in st.phases]
    covered = {r for p in st.phases for r in p.covers}
    lines += ["", "## Requirement coverage", ""]
    lines += [
        f"- {r}: phase {', '.join(str(p.n) for p in st.phases if r in p.covers)}"
        for r in req_ids
        if r in covered
    ]
    return "\n".join(lines) + "\n"


WAIT_FALLBACK_S = 30 * 60
WAIT_MARGIN_S = 60
_sleep = time.sleep


def run(  # noqa: PLR0913 -- the options of `orch run`
    repo: Repo,
    *,
    scenario: str | None = None,
    sets: list[str] | None = None,
    auto_decide: bool = False,
    pause: bool = False,
    wait: bool = False,
) -> S.RunState:
    """`orch run`: plan first when there is a pitch but no run and no hand-written
    briefs, then run (or resume) every phase. With `wait`, a usage limit sleeps
    until the reset time and carries on."""
    if repo.state_path.is_file() and load_state(repo).status == S.DONE:
        archive_run(repo)
    has_pitch = (repo.root / "docs" / "pitch.md").is_file()
    while True:
        st = _run_once(repo, scenario, sets, auto_decide, pause, has_pitch=has_pitch)
        if not (wait and st.status == S.PAUSED and st.stop_kind == "usage_limit"):
            return st
        _sleep(seconds_until(st.reset_at) + WAIT_MARGIN_S)


def _run_once(  # noqa: PLR0913 -- the options of `orch run`
    repo: Repo,
    scenario: str | None,
    sets: list[str] | None,
    auto_decide: bool,
    pause: bool,
    *,
    has_pitch: bool,
) -> S.RunState:
    existing = load_state(repo) if repo.state_path.is_file() else None
    if existing is None and has_pitch and not discover_phases(repo, "full"):
        st = plan(repo, scenario=scenario, sets=sets)
        if st.status != S.RUNNING or pause:
            return st
    elif existing is not None and not existing.phases:
        if not has_pitch:  # a planning stop with nothing to plan from
            raise ConfigError("the run has no phases", hint="write docs/pitch.md")
        st = plan(repo, scenario=scenario, sets=sets, from_n=1)  # finish planning
        if st.status != S.RUNNING or pause:
            return st
    r = start_or_resume(repo, scenario=scenario, sets=sets)
    r.st.auto_decide = r.st.auto_decide or auto_decide
    return execute(r)


def seconds_until(iso: str | None) -> float:
    if not iso:
        return WAIT_FALLBACK_S
    try:
        when = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return WAIT_FALLBACK_S
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max((when - datetime.now(UTC)).total_seconds(), 0.0)
