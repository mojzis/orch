"""`orch run`: code runs the phase loop and writes all state. Sessions build and
check; the lead session makes the judgment calls through structured output."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from orch import claude, gitops, render, store
from orch import layers as L
from orch import state as S
from orch.errors import ConfigError, StepError
from orch.gate import GateResult, GateSpec, run_gate
from orch.repo import Repo
from orch.scenario import Scenario
from orch.schemas import SCHEMAS

EXIT_PAUSED = 75
_LOCK = threading.Lock()


class StopRun(Exception):
    """Ends `orch run` in a stop state; the next `orch run` resumes."""

    def __init__(self, status: str, reason: str, reset_at: str | None = None) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason
        self.reset_at = reset_at


@dataclass
class Run:
    repo: Repo
    layers: L.Layers
    st: S.RunState
    config: dict[str, Any]
    cli_scenario: str | None = None
    cli_sets: dict[str, dict[str, Any]] = field(default_factory=dict)
    pitch: dict[str, Any] = field(default_factory=dict)
    skills_dir: Path | None = None

    @property
    def root(self) -> Path:
        return self.repo.root

    def save(self) -> None:
        with _LOCK:
            S.save(self.repo.state_path, self.st)

    def task(self, ph: S.Phase) -> L.TaskLayer:
        entries: list[tuple[str, dict[str, dict[str, Any]]]] = []
        if self.pitch.get("set"):
            entries.append(("task:pitch", self.pitch["set"]))
        if ph.task_set:
            entries.append((f"task:outline phase {ph.n}", ph.task_set))
        if self.cli_sets:
            entries.append(("cli:--set", self.cli_sets))
        return L.TaskLayer(tuple(entries))

    def rel(self, path: Path) -> str:
        return str(path.relative_to(self.root))


# ---------------------------------------------------------------- run entry


def read_pitch(root: Path) -> dict[str, Any]:
    """Frontmatter of docs/pitch.md (scenario, set), if present."""
    p = root / "docs" / "pitch.md"
    if not p.is_file():
        return {}
    text = p.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        return {}
    data = yaml.safe_load(m.group(1)) or {}
    if not isinstance(data, dict):
        return {}
    if data.get("set"):
        data["set"] = _sets_from_mapping(data["set"])
    return data


def _sets_from_mapping(raw: Any) -> dict[str, dict[str, Any]]:
    if isinstance(raw, list):
        return L.parse_sets([str(x) for x in raw])
    if isinstance(raw, dict):
        flat = []
        for k, v in raw.items():
            if isinstance(v, dict):
                flat += [f"{k}.{a}={b}" for a, b in v.items()]
            else:
                flat.append(f"{k}={v}")
        return L.parse_sets(flat)
    raise ConfigError("`set` must be a mapping or a list of role.key=value")


def discover_phases(repo: Repo, scenario: str) -> list[S.Phase]:
    """Hand-written briefs: .orch/phases/NN-slug/brief.md, in order."""
    phases = []
    for d in sorted((repo.orch / "phases").glob("[0-9][0-9]-*")):
        brief = d / "brief.md"
        if not brief.is_file():
            continue
        n, _, slug = d.name.partition("-")
        title = next(
            (
                ln[2:].strip()
                for ln in brief.read_text().splitlines()
                if ln.startswith("# ")
            ),
            slug,
        )
        phases.append(S.Phase(n=int(n), slug=slug, title=title, scenario=scenario))
    return phases


def load_state(repo: Repo) -> S.RunState:
    if not repo.state_path.is_file():
        raise ConfigError("no run here yet", hint="run `orch run` to start one")
    try:
        return S.load(repo.state_path)
    except (ValueError, TypeError, KeyError) as e:
        raise ConfigError(
            f"{repo.state_path} is unreadable: {e}",
            hint="restore it from git: `git checkout -- .orch/state.json`",
        ) from None


def new_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


def ensure_gitignore(repo: Repo) -> None:
    gi = repo.orch / ".gitignore"
    if not gi.exists():
        store.write_text(gi, ".cache/\n")


def start_or_resume(
    repo: Repo,
    *,
    scenario: str | None = None,
    sets: list[str] | None = None,
    phases: list[S.Phase] | None = None,
) -> Run:
    """Load state.json or start a new run on a fresh `orch/<id>` branch."""
    layers = L.Layers.for_repo(repo.root)
    pitch = read_pitch(repo.root)
    cli_sets = L.parse_sets(sets or [])
    if scenario:
        L.load_scenario(layers, scenario)  # fail early on a bad name
    if repo.state_path.is_file():
        st = load_state(repo)
        if gitops.current_branch(repo.root) != st.branch:
            gitops.ensure_branch(repo.root, st.branch)
    else:
        if not gitops.is_clean(repo.root, exclude_orch=True):
            raise ConfigError(
                "the working tree has uncommitted changes",
                hint="commit or stash them; orch starts a run branch from HEAD",
            )
        default_sc = scenario or pitch.get("scenario") or "full"
        found = phases if phases is not None else discover_phases(repo, default_sc)
        if not found:
            raise ConfigError(
                "no phases to run",
                hint="run `orch plan` first, or write .orch/phases/01-<slug>/brief.md",
            )
        run_id = new_run_id()
        st = S.RunState(run_id=run_id, branch=f"orch/{run_id}", phases=found)
        gitops.ensure_branch(repo.root, st.branch)
    ensure_gitignore(repo)
    run = Run(
        repo=repo,
        layers=layers,
        st=st,
        config=repo.config(),
        cli_scenario=scenario,
        cli_sets=cli_sets,
        pitch=pitch,
    )
    if scenario:
        for ph in st.phases:
            if ph.status != "done":
                ph.scenario = scenario
    return run


def execute(run: Run) -> S.RunState:
    """Run every phase that is not done. Stop states end the call cleanly."""
    st = run.st
    if st.status in (S.BLOCKED, S.FAILED):
        _reopen_current(st)
    st.status, st.stop_reason, st.reset_at = S.RUNNING, "", None
    st.claude_version = claude.claude_version()
    run.skills_dir = L.materialize_skills(run.layers, run.repo.cache / "skills")
    run.save()
    try:
        while (ph := st.current()) is not None:
            run_phase(run, ph)
        st.status = S.DONE
    except StopRun as stop:
        st.status, st.stop_reason, st.reset_at = stop.status, stop.reason, stop.reset_at
        if (ph := st.current()) is not None and stop.status in (S.BLOCKED, S.FAILED):
            ph.status = stop.status
    run.save()
    _commit_orch(run, f"orch: run {st.status}")
    return st


def _reopen_current(st: S.RunState) -> None:
    """Resuming a blocked or failed phase reruns everything after dev on the code
    as it is now (the user may have fixed it by hand)."""
    ph = st.current()
    if ph is None:
        return
    if "dev" in ph.done_steps:
        ph.done_steps = ph.done_steps[: ph.done_steps.index("dev") + 1]
    else:
        ph.done_steps = []
    ph.fix_round = 0
    ph.status = "running"


# -------------------------------------------------------------- phase loop


def run_phase(run: Run, ph: S.Phase) -> None:
    sc = L.load_scenario(run.layers, ph.scenario)
    pdir = run.repo.phase_dir(ph.dirname)
    if ph.status == "todo":
        ph.status = "running"
        ph.base = gitops.head(run.root)
        run.save()
    brief = pdir / "brief.md"
    if not brief.is_file():
        if sc.has("brief"):
            brief_step(run, ph, sc)
        if not brief.is_file():
            raise StopRun(
                S.NEEDS_REPLAN, f"phase {ph.n} has no brief at {run.rel(brief)}"
            )
    _commit_orch(run, f"orch: phase {ph.n} start")

    dev = worker(run, ph, "dev", "dev", _dev_inputs(run, ph))
    gate_loop(run, ph, "dev")

    checks: list[dict[str, Any]] = []
    if sc.has("add_checks"):
        rep = lead(
            run, ph, "add_checks", "add_checks", _add_checks_inputs(run, ph, dev)
        )
        checks = rep.get("checks") or []
        _write_checks(run, ph, checks)

    check_and_fix(run, ph, sc, checks)
    if sc.has("probe"):
        probe(run, ph)
    record(run, ph)


# ------------------------------------------------------------------- steps


def check_and_fix(
    run: Run, ph: S.Phase, sc: Scenario, checks: list[dict[str, Any]]
) -> None:
    """Review and QA (or the combined check), then triage, fix and verify rounds
    until everything ships or the fix rounds run out."""
    pdir = run.repo.phase_dir(ph.dirname)
    brief = pdir / "brief.md"
    if sc.has("review_qa"):
        reports = review_qa(run, ph, checks)
    else:
        reports = {
            "combined": worker(
                run,
                ph,
                "combined",
                "combined",
                _checker_inputs(run, ph, "combined", checks),
            )
        }

    rnd = 0  # rounds replay from done_steps on resume, so count locally
    while True:
        if all(r.get("verdict") == "ship" for r in reports.values()):
            if not sc.has("triage"):
                _defer(run, ph, reports, None, rnd)
            break
        tri = None
        if sc.has("triage"):
            tri = lead(
                run,
                ph,
                f"triage#{rnd + 1}",
                "triage",
                _triage_inputs(run, ph, reports, rnd),
            )
            _defer(run, ph, reports, tri, rnd)
            if tri["decision"] == "ship":
                break
        if rnd >= int(run.config["max_fix_rounds"]):
            raise StopRun(
                S.BLOCKED,
                f"phase {ph.n}: must-fix findings left after {rnd} fix round(s)",
            )
        rnd += 1
        fix_brief = pdir / f"fix-brief-{rnd}.md"
        if f"fix#{rnd}" not in ph.done_steps:
            text = (
                render.triage_fix_brief(tri, run.rel(brief))
                if tri
                else render.plain_fix_brief(reports, run.rel(brief))
            )
            store.write_text(fix_brief, text)
        worker(run, ph, f"fix#{rnd}", "fix", _fix_inputs(run, ph, fix_brief))
        ph.fix_round = max(ph.fix_round, rnd)
        run.save()
        gate_loop(run, ph, f"fix#{rnd}")
        if not sc.has("verify"):
            break
        reports = {
            "verify": worker(
                run,
                ph,
                f"verify#{rnd}",
                "verify",
                _verify_inputs(run, ph, fix_brief, rnd),
            )
        }


def gate_loop(run: Run, ph: S.Phase, after: str) -> None:
    """Gate after a code-changing step. On failure a repair session gets the
    gate's output; past max_fix_rounds repairs the phase is blocked."""
    cap = int(run.config["max_fix_rounds"])
    k = 0
    while True:
        key = f"gate[{after}]#{k}"
        if key in ph.done_steps:
            res = store.read_json(_report_path(run, ph, key))
        else:
            g = run_gate(run.root, GateSpec.from_config(run.config), ph.base or "HEAD")
            res = g.to_dict()
            _save_report(run, ph, key, role="gate", session="-", cost=0.0, report=res)
        if res["ok"]:
            return
        if k >= cap:
            raise StopRun(
                S.BLOCKED,
                f"phase {ph.n}: gate still failing after {k} repair(s): "
                f"{GateResult(**res).summary()}",
            )
        k += 1
        pdir = run.repo.phase_dir(ph.dirname)
        brief_path = pdir / f"gate-fix-{after.replace('#', '')}-{k}.md"
        rkey = f"repair[{after}]#{k}"
        if rkey not in ph.done_steps:
            store.write_text(
                brief_path,
                render.gate_fix_brief(GateResult(**res), run.rel(pdir / "brief.md")),
            )
        worker(run, ph, rkey, "fix", _fix_inputs(run, ph, brief_path))


def review_qa(run: Run, ph: S.Phase, checks: list[dict[str, Any]]) -> dict[str, Any]:
    """Review and QA in parallel, each in its own detached worktree."""
    todo = [r for r in ("review", "qa") if r not in ph.done_steps]
    if todo:
        _commit_orch(run, f"orch: phase {ph.n} before review/qa")
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {r: pool.submit(_in_worktree, run, ph, r, checks) for r in todo}
            errors = []
            for f in futures.values():
                try:
                    f.result()
                except (StopRun, StepError) as e:
                    errors.append(e)
            if errors:
                raise errors[0]
    return {r: store.read_json(_report_path(run, ph, r)) for r in ("review", "qa")}


def _in_worktree(
    run: Run, ph: S.Phase, role: str, checks: list[dict[str, Any]]
) -> None:
    wt = Path(tempfile.mkdtemp(prefix=f"orch-{role}-"))
    wt.rmdir()
    gitops.add_worktree(run.root, wt)
    try:
        if cmd := str(run.config.get("setup_cmd") or ""):
            subprocess.run(  # noqa: S602 -- setup_cmd is the repo's own config
                cmd, shell=True, cwd=wt, check=False, capture_output=True
            )
        worker(run, ph, role, role, _checker_inputs(run, ph, role, checks), cwd=wt)
    finally:
        gitops.remove_worktree(run.root, wt)
        shutil.rmtree(wt, ignore_errors=True)


def probe(run: Run, ph: S.Phase) -> None:
    rep = worker(run, ph, "probe", "probe", _base_inputs(run, ph, "probe"))
    problems = " ".join(rep.get("problems") or [])
    if re.search(r"credential|api key|unauthori[sz]ed|forbidden", problems, re.I):
        raise StopRun(
            S.NEEDS_USER, f"phase {ph.n} probe: credential problem: {problems}"
        )


def brief_step(run: Run, ph: S.Phase, sc: Scenario) -> None:
    """Placeholder until build phase 5: per-phase briefs come from `orch plan`."""
    del run, ph, sc


def record(run: Run, ph: S.Phase) -> None:
    pdir = run.repo.phase_dir(ph.dirname)
    rep = lead(run, ph, "learnings", "learnings", _learnings_inputs(run, ph))
    if "record" not in ph.done_steps:
        store.write_text(pdir / "learnings.md", rep["phase_learnings"].strip() + "\n")
        store.write_text(pdir / "handoff.md", rep["handoff"].strip() + "\n")
        lessons = [x for x in rep.get("process_lessons") or [] if x.strip()]
        if lessons:
            store.append_text(
                run.repo.orch / "process-learnings.md",
                f"\n## Phase {ph.n}: {ph.title}\n\n"
                + "".join(f"- {x}\n" for x in lessons),
            )
        ph.done_steps.append("record")
    ph.status = "done"
    ph.commit = gitops.head(run.root)
    run.save()
    _commit_orch(run, f"orch: phase {ph.n} done ({ph.title or ph.slug})")
    _push(run)


# ---------------------------------------------------------------- sessions


def worker(  # noqa: PLR0913 -- the step's identity plus where it runs
    run: Run,
    ph: S.Phase,
    key: str,
    role: str,
    inputs: str,
    *,
    cwd: Path | None = None,
) -> dict[str, Any]:
    """A fresh session. Retried once on a crash or bad output."""
    return _step(run, ph, key, role, inputs, cwd=cwd, is_lead=False)


def lead(run: Run, ph: S.Phase, key: str, role: str, inputs: str) -> dict[str, Any]:
    """The phase's lead session, resumed at each of its steps."""
    return _step(run, ph, key, role, inputs, cwd=None, is_lead=True)


def _step(  # noqa: PLR0913 -- one place that runs every session
    run: Run,
    ph: S.Phase,
    key: str,
    role: str,
    inputs: str,
    *,
    cwd: Path | None,
    is_lead: bool,
) -> dict[str, Any]:
    if key in ph.done_steps:
        return store.read_json(_report_path(run, ph, key))
    pdir = run.repo.phase_dir(ph.dirname)
    inputs_path = pdir / "inputs" / f"{_safe(key)}.md"
    store.write_text(inputs_path, inputs)
    if cwd is None and role in ("dev", "fix"):
        _commit_orch(run, f"orch: phase {ph.n} before {key}")
    res = L.resolve_role(
        run.layers, L.load_scenario(run.layers, ph.scenario), role, run.task(ph)
    )
    schema = SCHEMAS[role]
    add_dirs = (run.skills_dir,) if run.skills_dir else ()
    attempts = 0
    while True:
        attempts += 1
        resume = is_lead and ph.lead_session is not None
        sid = ph.lead_session if resume else claude.new_session_id()
        with _LOCK:
            run.st.pending = {"key": key, "role": role, "session_id": str(sid)}
        run.save()
        r = claude.run_session(
            res.config,
            run.rel(inputs_path),
            cwd or run.root,
            schema=schema,
            session_id=sid,
            resume=resume,
            prev_cost=ph.lead_cost_total if resume else 0.0,
            add_dirs=add_dirs,
        )
        _record_session(run, ph, key, role, r)
        if is_lead:
            ph.lead_session = r.session_id
            ph.lead_cost_total = r.cost_total
        if r.ok and r.structured is not None:
            break
        if r.error_kind == "usage_limit":
            raise StopRun(S.PAUSED, f"usage limit during {key}", r.reset_at)
        if r.error_kind == "auth":
            raise StopRun(S.NEEDS_USER, f"claude auth failed during {key}: {r.error}")
        if is_lead and resume:
            # the lead's session may live on another machine: start it fresh
            ph.lead_session, ph.lead_cost_total = None, 0.0
        if attempts >= 2:  # noqa: PLR2004 -- one retry, then stop
            raise StopRun(
                S.FAILED,
                f"phase {ph.n} {key}: {r.error_kind}: {r.error[:300]}",
            )
    with _LOCK:
        run.st.pending = None
    report = r.structured or {}
    _save_report(
        run, ph, key, role=role, session=r.session_id, cost=r.cost_delta, report=report
    )
    return report


def _record_session(
    run: Run, ph: S.Phase, key: str, role: str, r: claude.SessionResult
) -> None:
    usage = r.usage or {}
    with _LOCK:
        ph.sessions.append(
            S.SessionRecord(
                key=key,
                role=role,
                session_id=r.session_id,
                ok=r.ok,
                cost=r.cost_delta,
                error_kind=r.error_kind,
                tokens_in=int(usage.get("input_tokens") or 0)
                + int(usage.get("cache_read_input_tokens") or 0)
                + int(usage.get("cache_creation_input_tokens") or 0),
                tokens_out=int(usage.get("output_tokens") or 0),
            )
        )
    run.save()


def _save_report(  # noqa: PLR0913 -- the fields of one log entry
    run: Run,
    ph: S.Phase,
    key: str,
    *,
    role: str,
    session: str,
    cost: float,
    report: Any,
) -> None:
    pdir = run.repo.phase_dir(ph.dirname)
    store.write_json(_report_path(run, ph, key), report)
    with _LOCK:
        store.append_text(
            pdir / "log.md", render.log_entry(key, role, session, cost, report)
        )
        ph.done_steps.append(key)
    run.save()


def _report_path(run: Run, ph: S.Phase, key: str) -> Path:
    return run.repo.phase_dir(ph.dirname) / "reports" / f"{_safe(key)}.json"


def _safe(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", key).strip("-")


# ---------------------------------------------------------------- git glue


def _commit_orch(run: Run, message: str) -> None:
    with _LOCK:
        gitops.commit_paths(run.root, [".orch"], message)


def _push(run: Run) -> None:
    remote = str(run.config["remote"])
    if run.config.get("push") and gitops.has_remote(run.root, remote):
        gitops.push(run.root, remote, run.st.branch)


# ------------------------------------------------------------------ inputs


def _base_items(run: Run, ph: S.Phase) -> list[tuple[str, str]]:
    pdir = run.repo.phase_dir(ph.dirname)
    items = [("Phase brief", run.rel(pdir / "brief.md"))]
    for label, p in (
        ("Pitch", run.root / "docs" / "pitch.md"),
        ("QA principles and tiers", run.root / "QA.md"),
        ("Process learnings so far", run.repo.orch / "process-learnings.md"),
    ):
        if p.is_file():
            items.append((label, run.rel(p)))
    prev = [p for p in run.st.phases if p.n < ph.n and p.status == "done"]
    if prev:
        h = run.repo.phase_dir(prev[-1].dirname) / "handoff.md"
        if h.is_file():
            items.append(("Handoff from the previous phase", run.rel(h)))
    return items


def _base_inputs(run: Run, ph: S.Phase, role: str) -> str:
    return render.inputs_md(
        f"Inputs for {role}: phase {ph.n}, {ph.title or ph.slug}",
        _base_items(run, ph),
        {
            "Phase base commit": f"`{ph.base}`; this phase's diff: "
            f"`git diff {ph.base}..HEAD`"
        },
    )


def _dev_inputs(run: Run, ph: S.Phase) -> str:
    gate = str(run.config["gate_cmd"]) or "(none configured)"
    return render.inputs_md(
        f"Inputs for dev: phase {ph.n}, {ph.title or ph.slug}",
        _base_items(run, ph),
        {
            "Gate": f"After you finish, orch runs `{gate}` plus diff checks "
            "(no new skip/xfail, no deleted tests, no QA.md edits, clean tree).",
            "Phase base commit": f"`{ph.base}`",
        },
    )


def _report_items(run: Run, ph: S.Phase, keys: list[str]) -> list[tuple[str, str]]:
    out = []
    for k in keys:
        p = _report_path(run, ph, k)
        if p.is_file():
            out.append((f"Report of {k}", run.rel(p)))
    return out


def _dev_keys(ph: S.Phase) -> list[str]:
    return [k for k in ph.done_steps if k == "dev" or k.startswith(("fix#", "repair["))]


def _add_checks_inputs(run: Run, ph: S.Phase, dev: dict[str, Any]) -> str:
    del dev
    return render.inputs_md(
        f"Inputs for add-checks: phase {ph.n}",
        _base_items(run, ph) + _report_items(run, ph, _dev_keys(ph)),
        {},
    )


def _write_checks(run: Run, ph: S.Phase, checks: list[dict[str, Any]]) -> None:
    pdir = run.repo.phase_dir(ph.dirname)
    body = "# Extra checks from the lead\n\n"
    for route in ("review", "qa"):
        body += f"## {route}\n\n{render.checks_md(checks, route) or '(none)'}\n\n"
    store.write_text(pdir / "checks.md", body)


def _checker_inputs(
    run: Run, ph: S.Phase, role: str, checks: list[dict[str, Any]]
) -> str:
    routed = (
        render.checks_md(checks, "review") + "\n" + render.checks_md(checks, "qa")
        if role == "combined"
        else render.checks_md(checks, role)
    )
    return render.inputs_md(
        f"Inputs for {role}: phase {ph.n}, {ph.title or ph.slug}",
        _base_items(run, ph) + _report_items(run, ph, _dev_keys(ph)),
        {
            "Phase base commit": f"`{ph.base}`; this phase's diff: "
            f"`git diff {ph.base}..HEAD`",
            "Extra checks routed to you": routed,
        },
    )


def _triage_inputs(run: Run, ph: S.Phase, reports: dict[str, Any], rnd: int) -> str:
    keys = [f"verify#{rnd}"] if rnd else list(reports)
    if rnd:  # the findings verify re-checked
        keys += [k for k in ("review", "qa", "combined") if k in ph.done_steps]
        keys += [f"fix#{rnd}"]
    return render.inputs_md(
        f"Inputs for triage: phase {ph.n}, round {rnd + 1}",
        [
            *_base_items(run, ph),
            *_report_items(run, ph, _dev_keys(ph)),
            *_report_items(run, ph, keys),
        ],
        {"Fix rounds": f"{rnd} used of {run.config['max_fix_rounds']}"},
    )


def _fix_inputs(run: Run, ph: S.Phase, fix_brief: Path) -> str:
    return render.inputs_md(
        f"Inputs for fix: phase {ph.n}",
        [("Fix brief (what to fix)", run.rel(fix_brief)), *_base_items(run, ph)],
        {"Phase base commit": f"`{ph.base}`"},
    )


def _verify_inputs(run: Run, ph: S.Phase, fix_brief: Path, rnd: int) -> str:
    keys = ["review", "qa", "combined", f"fix#{rnd}"]
    if rnd > 1:
        keys.append(f"verify#{rnd - 1}")
    return render.inputs_md(
        f"Inputs for verify: phase {ph.n}, round {rnd}",
        [
            ("Fix brief", run.rel(fix_brief)),
            *_base_items(run, ph),
            *_report_items(run, ph, keys),
        ],
        {"Phase base commit": f"`{ph.base}`"},
    )


def _learnings_inputs(run: Run, ph: S.Phase) -> str:
    pdir = run.repo.phase_dir(ph.dirname)
    return render.inputs_md(
        f"Inputs for learnings: phase {ph.n}",
        [
            *_base_items(run, ph),
            ("Every report of this phase", run.rel(pdir / "log.md")),
        ],
        {},
    )


def _defer(
    run: Run,
    ph: S.Phase,
    reports: dict[str, Any],
    tri: dict[str, Any] | None,
    rnd: int,
) -> None:
    """Move deferred findings to the backlog (lows in a `ship`, triage's defer)."""
    key = f"defer#{rnd}"
    if key in ph.done_steps:
        return
    items: list[tuple[str, str]] = []
    if tri is not None:
        items = [
            (" = ".join(d["ids"]), f"{d['title']} ({d['reason']})")
            for d in tri.get("defer") or []
        ]
    else:
        items = [
            (f["id"], f"{f['title']} [{f['severity']}]")
            for f in render.findings_of(reports)
        ]
        for rep in reports.values():
            items += [
                (f["id"], f"{f['title']} [preexisting]")
                for f in rep.get("preexisting") or []
            ]
    if text := render.backlog_entry(f"{ph.n} ({ph.title or ph.slug})", items):
        store.append_text(run.repo.orch / "backlog.md", text)
    ph.done_steps.append(key)
    run.save()
