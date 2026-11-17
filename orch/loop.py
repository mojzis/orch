"""`orch run`: code runs the phase loop and writes all state. Sessions build and
check; the lead session makes the judgment calls through structured output."""

from __future__ import annotations

import contextlib
import re
import shutil
import signal
import subprocess
import tempfile
import threading
from collections.abc import Callable, Generator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn

import yaml

from orch import claude, gitops, pr, render, store
from orch import home as H
from orch import layers as L
from orch import state as S
from orch.errors import ConfigError, OrchError
from orch.gate import GateResult, GateSpec, run_gate
from orch.lead import check_add_checks, check_triage
from orch.migrate import require_migrated
from orch.repo import Repo
from orch.report import render_report
from orch.scenario import Scenario
from orch.schemas import SCHEMAS
from orch.ui import err

EXIT_PAUSED = 75
EXIT_INTERRUPTED = 130
# when parallel sessions stop differently, the most actionable state wins
STOP_RANK = {S.PAUSED: 0, S.NEEDS_USER: 1, S.BLOCKED: 2, S.NEEDS_REPLAN: 3, S.FAILED: 4}
_LOCK = threading.Lock()
INTERRUPTED = threading.Event()


class StopRun(Exception):
    """Ends `orch run` in a stop state; the next `orch run` resumes."""

    def __init__(
        self,
        status: str,
        reason: str,
        reset_at: str | None = None,
        kind: str = "",
    ) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason
        self.reset_at = reset_at
        self.kind = kind  # usage_limit | interrupted | empty


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

    def scenario_of(self, ph: S.Phase) -> str:
        """`orch run -s X` applies to this run only; the planner's pick stays."""
        return self.cli_scenario or ph.scenario

    def rel(self, path: Path) -> str:
        """How a session is told about a file: relative to the repo when it is
        in the repo (so it resolves in a worktree too), absolute otherwise."""
        try:
            return str(path.relative_to(self.root))
        except ValueError:
            return str(path)

    def add_dirs(self) -> tuple[Path, ...]:
        """Directories outside the repo every session may read: the skills, and
        the run files when they live in orch's home."""
        dirs = (self.skills_dir,) if self.skills_dir else ()
        return dirs if self.repo.in_repo else (*dirs, self.repo.home)


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
    """Hand-written briefs: .orch/phases/NN-slug/brief.md in the repo, in order."""
    phases = []
    for d in _hand_written(repo):
        brief = d / "brief.md"
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


def _hand_written(repo: Repo) -> list[Path]:
    return [
        d
        for d in sorted((repo.orch / "phases").glob("[0-9][0-9]-*"))
        if (d / "brief.md").is_file()
    ]


def current_run(repo: Repo) -> Repo | None:
    """The repo with its current run selected: the run of the checked-out
    `orch/<id>` branch, else the newest unfinished run, else the newest run."""
    require_migrated(repo)
    branch = gitops.current_branch(repo.root)
    run_id = branch.removeprefix("orch/") if branch.startswith("orch/") else ""
    if run_id and (repo.runs / run_id / "state.json").is_file():
        return repo.with_run(run_id)
    found = sorted(repo.runs.glob("*/state.json"), reverse=True)
    for path in found:
        with contextlib.suppress(ValueError, TypeError, KeyError):
            if S.load(path).status != S.DONE:
                return repo.with_run(path.parent.name)
    return repo.with_run(found[0].parent.name) if found else None


def select_run(repo: Repo, run_id: str | None = None) -> Repo:
    """The repo with `run_id` (or the current run) selected; an error if none."""
    if run_id:
        require_migrated(repo)
        if not (repo.runs / run_id / "state.json").is_file():
            raise ConfigError(
                f"no run {run_id} in {repo.runs}", hint="`orch status` shows the runs"
            )
        return repo.with_run(run_id)
    if repo.run_id is not None:
        return repo
    found = current_run(repo)
    if found is None:
        raise ConfigError("no run here yet", hint="run `orch run` to start one")
    return found


def load_state(repo: Repo) -> S.RunState:
    path = select_run(repo).state_path
    try:
        return S.load(path)
    except (ValueError, TypeError, KeyError) as e:
        raise ConfigError(
            f"{path} is unreadable: {e}",
            hint="restore it from git: `git checkout -- .orch/`"
            if repo.in_repo
            else "restore it from a backup of orch's home, or remove the run",
        ) from None


def open_run(repo: Repo) -> tuple[Repo, S.RunState | None]:
    """The run `orch run` carries on (with its state), or the repo with no run
    selected when the next run is a new one (no run yet, or the last one is done)."""
    found = current_run(repo)
    if found is None:
        return repo, None
    st = load_state(found)
    if st.status == S.DONE:
        if gitops.current_branch(repo.root) == st.branch:
            err.print(
                f"[yellow]warning:[/] the new run starts on top of the finished run's "
                f"branch {st.branch}. If that run is not merged yet, its commits come "
                "along; switch to main first to start clean."
            )
        return repo, None
    return found, st


def new_run_id(root: Path | None = None) -> str:
    base = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    run_id, n = base, 1
    while root is not None and gitops.git(
        root, "rev-parse", "--verify", "--quiet", f"orch/{run_id}", check=False
    ):
        n += 1
        run_id = f"{base}-{n}"
    return run_id


GITIGNORE = (".cache/", "*.lock")


def ensure_gitignore(repo: Repo) -> None:
    """With run files in the repo, keep the cache and lock files out of it."""
    if not repo.in_repo:
        return
    gi = repo.orch / ".gitignore"
    have = gi.read_text(encoding="utf-8").splitlines() if gi.is_file() else []
    if missing := [line for line in GITIGNORE if line not in have]:
        store.write_text(gi, "".join(f"{line}\n" for line in [*have, *missing]))


def copy_hand_written(repo: Repo) -> None:
    """Copy the hand-written briefs into the new run. Briefs an earlier run
    already ran unchanged are an error: they would run twice."""
    dirs = _hand_written(repo)
    for d in dirs:
        prior = _ran_before(repo, d)
        if prior:
            raise ConfigError(
                f"{d.relative_to(repo.root)}/brief.md was already run in run {prior}",
                hint="delete or edit the briefs in .orch/phases/, then `orch run`",
            )
    for d in dirs:
        shutil.copytree(d, repo.phase_dir(d.name), dirs_exist_ok=True)


def _ran_before(repo: Repo, d: Path) -> str:
    text = (d / "brief.md").read_bytes()
    for old in sorted(repo.runs.glob(f"*/phases/{d.name}/brief.md"), reverse=True):
        run_dir = old.parents[2]
        if (run_dir / "state.json").is_file() and old.read_bytes() == text:
            return run_dir.name
    return ""


def start_or_resume(
    repo: Repo,
    *,
    scenario: str | None = None,
    sets: list[str] | None = None,
    phases: list[S.Phase] | None = None,
) -> Run:
    """Load the current run's state, or start a new run on a fresh `orch/<id>`
    branch."""
    layers = L.Layers.for_repo(repo.root)
    pitch = read_pitch(repo.root)
    cli_sets = L.parse_sets(sets or [])
    if scenario:
        L.load_scenario(layers, scenario)  # fail early on a bad name
    repo, existing = open_run(repo)
    if existing is not None:
        st = existing
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
        run_id = new_run_id(repo.root)
        repo = repo.with_run(run_id)
        if phases is None:
            copy_hand_written(repo)
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
    return run


def execute(run: Run) -> S.RunState:
    """Run every phase that is not done. Stop states end the call cleanly."""
    st = run.st
    if st.status == S.BLOCKED:
        _reopen_current(st)
    elif (ph := st.current()) is not None and ph.status == S.FAILED:
        ph.status = "running"  # a crash: carry on exactly where it stopped
    st.status, st.stop_reason, st.reset_at, st.stop_kind = S.RUNNING, "", None, ""
    st.claude_version = claude.claude_version()
    run.skills_dir = L.materialize_skills(run.layers, run.repo.cache / "skills")
    run.save()
    INTERRUPTED.clear()
    try:
        with sigint_forwarding():
            while (ph := st.current()) is not None:
                run_phase(run, ph)
            st.status = S.DONE
            finish(run)
    except StopRun as stop:
        st.status, st.stop_reason, st.reset_at = stop.status, stop.reason, stop.reset_at
        st.stop_kind = stop.kind
        ph = st.current()
        if (
            ph is not None
            and ph.status == "running"
            and stop.status
            in (
                S.BLOCKED,
                S.FAILED,
            )
        ):
            ph.status = stop.status
    except (OrchError, OSError) as e:
        st.status, st.stop_reason = S.FAILED, f"{type(e).__name__}: {e}"
        if (ph := st.current()) is not None and ph.status == "running":
            ph.status = S.FAILED
    run.save()
    _commit_orch(run, f"orch: run {st.status}")
    with contextlib.suppress(OrchError):
        _push(run)
        if st.status == S.DONE:
            _open_pr(run)
    return st


def _open_pr(run: Run) -> None:
    """Open the PR of a finished, pushed run (`open_pr = true`) and put its URL
    in the state and the report. A failure is a warning: the branch is pushed."""
    cfg, st = run.config, run.st
    remote = str(cfg["remote"])
    if not (cfg.get("open_pr") and cfg.get("push")):
        return
    if not gitops.has_remote(run.root, remote):
        return
    report = (run.repo.run_dir / "report.md").read_text(encoding="utf-8")
    try:
        st.pr_url = pr.open_pr(
            run.root,
            st.branch,
            pr.title(run.root, run.pitch, f"orch run {st.run_id}"),
            pr.body(report, pr.fixes(run.root, remote, run.pitch)),
        )
    except OrchError as e:
        err.print(f"[yellow]warning:[/] the PR was not opened: {e}", markup=True)
        return
    run.save()
    write_report(run.repo, st, _full_qa(run))
    _commit_orch(run, "orch: PR opened")
    _push(run)


def _full_qa(run: Run) -> dict[str, Any] | None:
    path = run.repo.run_dir / "full-qa.json"
    return store.read_json(path) if path.is_file() else None


@contextlib.contextmanager
def sigint_forwarding() -> Generator[None]:
    """First Ctrl+C: forward SIGINT to the running sessions so their turn is
    recorded, then stop as `paused`. Second Ctrl+C: the default, exit now."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def handler(signum: int, frame: object) -> None:
        del signum, frame
        INTERRUPTED.set()
        signal.signal(signal.SIGINT, signal.default_int_handler)
        claude.interrupt_children()

    previous = signal.signal(signal.SIGINT, handler)
    try:
        yield
    except KeyboardInterrupt:  # second Ctrl+C: leave no session running
        claude.interrupt_children()
        raise
    finally:
        signal.signal(signal.SIGINT, previous)


def _reopen_current(st: S.RunState) -> None:
    """Resuming a blocked phase reruns everything after dev on the code as it is
    now (the user fixed it by hand or edited the brief)."""
    ph = st.current()
    if ph is None:
        return
    keep = ph.done_steps.index("dev") + 1 if "dev" in ph.done_steps else 0
    ph.done_steps = ph.done_steps[:keep] + [
        k for k in ph.done_steps[keep:] if k.startswith("defer#")
    ]
    ph.attempt += 1
    ph.fix_round = 0
    ph.status = "running"


# -------------------------------------------------------------- phase loop


def run_phase(run: Run, ph: S.Phase) -> None:
    sc = L.load_scenario(run.layers, run.scenario_of(ph))
    pdir = run.repo.phase_dir(ph.dirname)
    if ph.status == "todo" or ph.base is None:
        ph.status = "running"
        ph.base = ph.base or gitops.head(run.root)
        run.save()
    brief = pdir / "brief.md"
    planned = bool(ph.covers or ph.deliverable)  # from `orch plan`, not hand-written
    if planned and sc.has("brief") and "brief:ok" not in ph.done_steps:
        brief_step(run, ph, sc)
        ph.done_steps.append("brief:ok")
        run.save()
        sc = L.load_scenario(run.layers, run.scenario_of(ph))  # the lead may switch
    if not brief.is_file():
        raise StopRun(S.NEEDS_REPLAN, f"phase {ph.n} has no brief at {run.rel(brief)}")
    _commit_orch(run, f"orch: phase {ph.n} start")

    dev = worker(run, ph, "dev", "dev", _dev_inputs(run, ph))
    gate_loop(run, ph, "dev")

    checks: list[dict[str, Any]] = []
    if sc.has("add_checks"):
        dev_reports = [
            dev,
            *[
                store.read_json(_report_path(run, ph, k))
                for k in _dev_keys(ph)
                if k != "dev"
            ],
        ]
        rep = lead(
            run,
            ph,
            "add_checks",
            "add_checks",
            _add_checks_inputs(run, ph, dev),
            check=lambda a: check_add_checks(dev_reports, a),
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
                check=lambda t, reps=reports: check_triage(reps, t),
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
            _stop_if_interrupted(f"gate[{after}]")
            g = run_gate(run.root, GateSpec.from_config(run.config), ph.base or "HEAD")
            _stop_if_interrupted(f"gate[{after}]")  # a gate cut short proves nothing
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
        # the worktree is made from HEAD: run files in the repo must be committed
        for r in todo:
            _write_inputs(run, ph, r, _checker_inputs(run, ph, r, checks))
        run.save()
        _commit_orch(run, f"orch: phase {ph.n} before review/qa")
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {r: pool.submit(_in_worktree, run, ph, r, checks) for r in todo}
            errors: list[StopRun] = []
            for f in futures.values():
                try:
                    f.result()
                except StopRun as e:
                    errors.append(e)
                except OrchError as e:
                    errors.append(StopRun(S.FAILED, str(e)))
            if errors:
                raise min(errors, key=lambda e: STOP_RANK.get(e.status, 99))
    return {r: store.read_json(_report_path(run, ph, r)) for r in ("review", "qa")}


def _in_worktree(
    run: Run, ph: S.Phase, role: str, checks: list[dict[str, Any]]
) -> None:
    wt = Path(tempfile.mkdtemp(prefix=f"orch-{role}-"))
    wt.rmdir()
    gitops.add_worktree(run.root, wt)
    try:
        if cmd := str(run.config.get("setup_cmd") or ""):
            proc = subprocess.run(  # noqa: S602 -- setup_cmd is the repo's own config
                cmd, shell=True, cwd=wt, check=False, capture_output=True, text=True
            )
            if proc.returncode != 0:
                tail = (proc.stdout + proc.stderr).strip()[-500:]
                raise StopRun(
                    S.FAILED, f"setup_cmd `{cmd}` failed in a {role} worktree: {tail}"
                )
        worker(run, ph, role, role, _checker_inputs(run, ph, role, checks), cwd=wt)
    finally:
        gitops.remove_worktree(run.root, wt)
        shutil.rmtree(wt, ignore_errors=True)


def probe(run: Run, ph: S.Phase) -> None:
    rep = worker(run, ph, "probe", "probe", _base_inputs(run, ph, "probe"))
    problems = " ".join(rep.get("problems") or [])
    if re.search(r"credential|api key|unauthori[sz]ed|forbidden", problems, re.I):
        ph.done_steps.remove("probe")  # probe again once the user has fixed it
        run.save()
        raise StopRun(
            S.NEEDS_USER, f"phase {ph.n} probe: credential problem: {problems}"
        )


MAX_BRIEF_REVISIONS = 2
_PATH_RE = re.compile(r"`([\w./-]+/[\w.-]+\.\w+)`")


MAX_QUESTION_ROUNDS = 2


def brief_step(run: Run, ph: S.Phase, sc: Scenario) -> None:
    """The lead writes the brief (asking the user if a design choice is open),
    then a separate session reviews it; at most two revisions."""
    del sc
    pdir = run.repo.phase_dir(ph.dirname)
    rep = lead(run, ph, "brief", "brief", _brief_inputs(run, ph, None, ""))
    answers: str | None = None
    rnd = 0
    while rep.get("questions") and not rep.get("brief", "").strip():
        rnd += 1
        if rnd > MAX_QUESTION_ROUNDS:
            raise StopRun(
                S.NEEDS_REPLAN,
                f"phase {ph.n}: the lead keeps asking "
                "design questions instead of writing the brief",
            )
        got = _answers(run, ph, rnd)
        if got is None:
            got = _ask(run, ph, rep["questions"], rnd)
        answers = f"{answers}\n{got}" if answers else got
        key = "brief#answered" if rnd == 1 else f"brief#answered{rnd}"
        rep = lead(run, ph, key, "brief", _brief_inputs(run, ph, answers, ""))
    _apply_brief(run, ph, rep)
    for i in range(1, MAX_BRIEF_REVISIONS + 2):
        review = worker(
            run, ph, f"plan_review#{i}", "plan_review", _plan_review_inputs(run, ph)
        )
        blocking = plan_review_blockers(run, review, pdir / "brief.md")
        store.write_json(pdir / "reports" / f"plan_review-{i}-blocking.json", blocking)
        if not blocking:
            return
        if i > MAX_BRIEF_REVISIONS:
            raise StopRun(
                S.NEEDS_REPLAN,
                f"phase {ph.n}: the brief still has blocking plan-review findings "
                f"after {MAX_BRIEF_REVISIONS} revisions: " + "; ".join(blocking)[:400],
            )
        notes = "\n".join(f"- {b}" for b in blocking)
        rep = lead(
            run, ph, f"brief_rev#{i}", "brief", _brief_inputs(run, ph, answers, notes)
        )
        _apply_brief(run, ph, rep)


# a path named on a line like this must exist already
_EXISTING_RE = re.compile(
    r"\b(change|modify|edit|update|extend|refactor|fix|rename|move|in)\b", re.I
)
_SKIP_SECTION_RE = re.compile(r"new|test|qa|output|creat", re.I)


def plan_review_blockers(run: Run, review: dict[str, Any], brief: Path) -> list[str]:
    """Blocking findings: the reviewer's, plus what code can check itself (a QA
    check for new behaviour that already passes or cannot run, and a file the
    brief says to change that does not exist)."""
    out = [
        f"{f['id']}: {f['title']}: {f['detail']}"
        for f in review.get("findings") or []
        if f.get("blocking")
    ]
    for c in review.get("qa_dry_run") or []:
        if c.get("result") == "cannot_run":
            out.append(f"QA check cannot run: {c['check']} ({c.get('evidence', '')})")
        elif c.get("result") == "passes_now" and c.get("new_behaviour"):
            out.append(
                "QA check for new behaviour already passes, so it proves "
                f"nothing: {c['check']}"
            )
    if brief.is_file():
        out += _missing_files(run.root, brief.read_text(encoding="utf-8"))
    return list(dict.fromkeys(out))


def _missing_files(root: Path, text: str) -> list[str]:
    out = []
    skip = False
    for line in text.splitlines():
        if line.startswith("#"):
            skip = bool(_SKIP_SECTION_RE.search(line))
            continue
        if skip or "(new)" in line.lower() or not _EXISTING_RE.search(line):
            continue
        for path in _PATH_RE.findall(line):
            if not path.startswith(".orch/") and not (root / path).exists():
                out.append(
                    f"the brief says to change `{path}`, which does not exist "
                    "(mark it *(new)* or fix the path)"
                )
    return out


def _apply_brief(run: Run, ph: S.Phase, rep: dict[str, Any]) -> None:
    text = rep.get("brief", "").strip()
    if not text:
        raise StopRun(S.FAILED, f"phase {ph.n}: the lead returned no brief")
    pdir = run.repo.phase_dir(ph.dirname)
    store.write_text(
        pdir / "brief.md",
        f"# {ph.title or ph.slug}\n\n{text}\n"
        if not text.startswith("# ")
        else text + "\n",
    )
    new_sc = (rep.get("scenario") or "").strip()
    if new_sc and new_sc != ph.scenario and not run.cli_scenario:
        L.load_scenario(run.layers, new_sc)  # must exist
        run.st.replans.append(
            {
                "at": S.now(),
                "phase": ph.n,
                "kind": "scenario",
                "before": ph.scenario,
                "after": new_sc,
                "reason": rep.get("scenario_reason", ""),
            }
        )
        ph.scenario = new_sc
        run.save()


QUESTIONS_HELP = (
    "Write your choice after each `answer:` (an option or your own words), save, "
    "then run `orch run` again. `orch run --auto-decide` takes the recommended "
    "options instead."
)


def _qfile(pdir: Path, rnd: int, ext: str) -> Path:
    return pdir / (f"questions.{ext}" if rnd == 1 else f"questions-{rnd}.{ext}")


def _ask(run: Run, ph: S.Phase, questions: list[dict[str, Any]], rnd: int) -> str:
    """Pause as needs-user with every question in one file, or, with
    --auto-decide, take the recommended options."""
    pdir = run.repo.phase_dir(ph.dirname)
    store.write_json(_qfile(pdir, rnd, "json"), questions)
    if run.st.auto_decide:
        return _auto_answers(run, ph, questions)
    lines = [f"# Design questions for phase {ph.n}: {ph.title}", "", QUESTIONS_HELP, ""]
    for q in questions:
        lines += [f"## {q['id']}: {q['question']}", ""]
        lines += [f"- {o}" for o in q.get("options") or []]
        lines += ["", f"recommended: {q['recommended']}", "", "answer: ", ""]
    qmd = _qfile(pdir, rnd, "md")
    store.write_text(qmd, "\n".join(lines))
    raise StopRun(
        S.NEEDS_USER,
        f"phase {ph.n} has {len(questions)} design question(s): answer them in "
        f"{run.rel(qmd)}",
    )


def _auto_answers(run: Run, ph: S.Phase, questions: list[dict[str, Any]]) -> str:
    text = "\n".join(
        f"- {q['id']} {q['question']}: {q['recommended']} "
        "(recommended option, taken by --auto-decide)"
        for q in questions
    )
    run.st.replans.append(
        {"at": S.now(), "phase": ph.n, "kind": "auto-decide", "answers": text}
    )
    return text


def _answers(run: Run, ph: S.Phase, rnd: int) -> str | None:
    """Answers to round `rnd`'s questions from the filled-in questions file
    (None when nothing was asked yet). Empty answers take the recommended option
    under --auto-decide, else the run stays needs-user."""
    pdir = run.repo.phase_dir(ph.dirname)
    qjson, qmd = _qfile(pdir, rnd, "json"), _qfile(pdir, rnd, "md")
    if not qjson.is_file():
        return None
    questions = store.read_json(qjson)
    text = qmd.read_text(encoding="utf-8") if qmd.is_file() else ""
    out = []
    for q in questions:
        m = re.search(
            rf"^## {re.escape(q['id'])}:.*?^answer:(.*?)(?=^## |\Z)",
            text,
            re.M | re.S,
        )
        answer = m.group(1).strip() if m else ""
        if not answer and run.st.auto_decide:
            answer = f"{q['recommended']} (recommended option, taken by --auto-decide)"
        if not answer:
            raise StopRun(
                S.NEEDS_USER,
                f"phase {ph.n}: unanswered question {q['id']} in {run.rel(qmd)}",
            )
        out.append(f"- {q['id']} {q['question']}: {answer}")
    return "\n".join(out)


def finish(run: Run) -> None:
    """After the last phase: the slowest QA tier, then the run report."""
    final: dict[str, Any] | None = None
    if cmd := str(run.config.get("full_qa_cmd") or ""):
        proc = subprocess.run(  # noqa: S602 -- the repo's own config
            cmd, shell=True, cwd=run.root, capture_output=True, text=True, check=False
        )
        final = {
            "cmd": cmd,
            "returncode": proc.returncode,
            "output": (proc.stdout + proc.stderr)[-4000:],
        }
        store.write_json(run.repo.run_dir / "full-qa.json", final)
    write_report(run.repo, run.st, final)


def write_report(repo: Repo, st: S.RunState, final: dict[str, Any] | None) -> Path:
    from orch.planning import requirement_ids  # noqa: PLC0415 -- avoids an import cycle

    pitch = repo.root / "docs" / "pitch.md"
    req_ids = (
        requirement_ids(pitch.read_text(encoding="utf-8")) if pitch.is_file() else []
    )
    repo = select_run(repo, st.run_id)
    path = repo.run_dir / "report.md"
    store.write_text(path, render_report(repo, st, req_ids, final))
    return path


def record(run: Run, ph: S.Phase) -> None:
    pdir = run.repo.phase_dir(ph.dirname)
    rep = lead(run, ph, "learnings", "learnings", _learnings_inputs(run, ph))
    if "record" not in ph.done_steps:
        store.write_text(pdir / "learnings.md", rep["phase_learnings"].strip() + "\n")
        store.write_text(pdir / "handoff.md", rep["handoff"].strip() + "\n")
        lessons = [x for x in rep.get("process_lessons") or [] if x.strip()]
        if lessons:
            store.append_text(
                run.repo.home / "process-learnings.md",
                f"\n## Phase {ph.n}: {ph.title}\n\n"
                + "".join(f"- {x}\n" for x in lessons),
            )
        ph.done_steps.append("record")
    ph.status = "done"
    ph.commit = gitops.head(run.root)
    run.save()
    _commit_orch(run, f"orch: phase {ph.n} done ({ph.title or ph.slug})")
    try:
        _push(run)
    except OrchError as e:  # the next push (or `git push`) carries this phase too
        store.append_text(run.repo.run_dir / "push-failures.log", f"{S.now()} {e}\n")


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
    rep = _step(run, ph, key, role, inputs, cwd=cwd, is_lead=False)
    if found := credential_failures(rep):
        _pause_for_credentials(run, ph, key, found, resumable=cwd is None)
    return rep


_CRED_FAILURE_RE = re.compile(r"credential failure", re.I)


def credential_failures(rep: dict[str, Any]) -> str:
    """The items a worker marked "credential failure" (templates ask for that
    exact phrase), in its unresolved issues or its findings."""
    items = [
        *(rep.get("unresolved_issues") or []),
        *(
            f"{f.get('title', '')}: {f.get('detail', '')}"
            for f in rep.get("findings") or []
        ),
    ]
    return "; ".join(x for x in items if _CRED_FAILURE_RE.search(x))


def _pause_for_credentials(
    run: Run, ph: S.Phase, key: str, found: str, *, resumable: bool
) -> NoReturn:
    """Forget the step so `orch run` redoes it once the user has fixed the key.
    A session in the repo root is resumed; one in a worktree (gone now) is not."""
    with _LOCK:
        ph.done_steps.remove(key)
        if resumable:
            last = next(s for s in reversed(ph.sessions) if s.key == key)
            run.st.pending[f"{ph.n}:{key}"] = last.session_id
    run.save()
    raise StopRun(S.NEEDS_USER, f"phase {ph.n} {key}: credential failure: {found}")


def lead(  # noqa: PLR0913 -- the step's identity plus its check
    run: Run,
    ph: S.Phase,
    key: str,
    role: str,
    inputs: str,
    *,
    check: Callable[[dict[str, Any]], str] | None = None,
) -> dict[str, Any]:
    """The phase's lead session, resumed at each of its steps. `check` returns a
    problem description; the lead gets one chance to correct its answer."""
    return _step(run, ph, key, role, inputs, cwd=None, is_lead=True, check=check)


def _step(  # noqa: PLR0913 -- one place that runs every session
    run: Run,
    ph: S.Phase,
    key: str,
    role: str,
    inputs: str,
    *,
    cwd: Path | None,
    is_lead: bool,
    check: Callable[[dict[str, Any]], str] | None = None,
) -> dict[str, Any]:
    if key in ph.done_steps:
        return store.read_json(_report_path(run, ph, key))
    _stop_if_interrupted(key)
    pkey = f"{ph.n}:{key}"
    inputs_path = _write_inputs(run, ph, key, inputs)
    res = L.resolve_role(
        run.layers, L.load_scenario(run.layers, run.scenario_of(ph)), role, run.task(ph)
    )
    schema = SCHEMAS[role]
    attempts = 0
    retry_sid: str | None = None
    while True:
        attempts += 1
        carry_on = retry_sid or run.st.pending.get(pkey)
        resume = bool(carry_on) or (is_lead and ph.lead_session is not None)
        sid = carry_on or (ph.lead_session if resume else claude.new_session_id())
        with _LOCK:
            run.st.pending[pkey] = str(sid)
        run.save()
        if cwd is None and role in ("dev", "fix"):
            # run files in the repo: commit them (pending included) so dev's
            # `git add -A` and the gate's clean-tree check never see them
            _commit_orch(run, f"orch: phase {ph.n} before {key}")
        with _briefs_guarded(run, ph, key):
            r = claude.run_session(
                res.config,
                run.rel(inputs_path),
                cwd or run.root,
                schema=schema,
                session_id=sid,
                resume=resume,
                prev_cost=_prev_cost(ph, key, sid, is_lead),
                add_dirs=run.add_dirs(),
            )
            _record_session(
                run, ph, (key, role), r, cfg=res.config, cwd=cwd or run.root
            )
        if is_lead and (r.ok or resume):
            ph.lead_session = r.session_id
            if "total_cost_usd" in r.raw:  # an interrupted turn reports no total
                ph.lead_cost_total = r.cost_total
        if INTERRUPTED.is_set():
            _keep_finished(run, ph, (key, pkey, role), r, checked=check is not None)
        with _LOCK:
            run.st.pending.pop(pkey, None)
        if r.ok and r.structured is not None:
            problem = check(r.structured) if check else ""
            if not problem:
                break
            if attempts >= 2:  # noqa: PLR2004 -- one correction, then stop
                raise StopRun(S.FAILED, f"phase {ph.n} {key}: {problem}")
            store.write_text(
                inputs_path,
                inputs + f"\n## Your previous answer was rejected\n\n{problem}\n"
                "Answer again, fixing exactly this.\n",
            )
            continue
        _stop_unless_retryable(run, ph, pkey, r, res.config)
        if is_lead and resume:
            # the lead's session may live on another machine: start it fresh
            ph.lead_session, ph.lead_cost_total = None, 0.0
        elif r.error_kind == "bad_output" or role in ("dev", "fix"):
            # ask the same session for its report rather than redoing the work
            retry_sid = r.session_id
        if attempts >= 2:  # noqa: PLR2004 -- one retry, then stop
            raise StopRun(
                S.FAILED,
                f"phase {ph.n} {key}: {r.error_kind}: {r.error[:300]}",
            )
    report = r.structured or {}
    _save_report(
        run, ph, key, role=role, session=r.session_id, cost=r.cost_delta, report=report
    )
    return report


def _keep_finished(
    run: Run,
    ph: S.Phase,
    step: tuple[str, str, str],
    r: claude.SessionResult,
    *,
    checked: bool,
) -> NoReturn:
    """Ctrl+C landed while this session ran. If it finished anyway, keep its
    report so resume does not run it again; then pause."""
    key, pkey, role = step
    if r.ok and r.structured is not None and not checked:
        with _LOCK:
            run.st.pending.pop(pkey, None)
        _save_report(
            run,
            ph,
            key,
            role=role,
            session=r.session_id,
            cost=r.cost_delta,
            report=r.structured,
        )
        raise StopRun(
            S.PAUSED,
            f"interrupted after {key}; `orch run` carries on",
            kind="interrupted",
        )
    raise StopRun(
        S.PAUSED,
        f"interrupted during {key}; `orch run` resumes that session",
        kind="interrupted",
    )


def _stop_if_interrupted(where: str) -> None:
    if INTERRUPTED.is_set():
        raise StopRun(
            S.PAUSED,
            f"interrupted before {where}; `orch run` carries on",
            kind="interrupted",
        )


def _stop_unless_retryable(
    run: Run, ph: S.Phase, pkey: str, r: claude.SessionResult, cfg: claude.StepConfig
) -> None:
    """A usage limit, an auth failure or an exhausted budget stop the run; the
    session is kept as pending so `orch run` carries on in it."""
    key = pkey.partition(":")[2]
    if r.error_kind in ("usage_limit", "budget"):
        with _LOCK:
            run.st.pending[pkey] = r.session_id
    if r.error_kind == "usage_limit":
        raise StopRun(
            S.PAUSED, f"usage limit during {key}", r.reset_at, kind="usage_limit"
        )
    if r.error_kind == "auth":
        raise StopRun(S.NEEDS_USER, f"claude auth failed during {key}: {r.error}")
    if r.error_kind == "budget":
        raise StopRun(
            S.NEEDS_USER,
            f"phase {ph.n} {key} hit max_budget_usd ({cfg.max_budget_usd}); raise it "
            f"with --set {cfg.step}.max_budget_usd=<usd>, then `orch run`",
        )


def _prev_cost(ph: S.Phase, key: str, sid: str | None, is_lead: bool) -> float:
    """What the session had cost before this call: claude reports totals for the
    whole conversation on a resumed session."""
    if is_lead and sid == ph.lead_session:
        return ph.lead_cost_total
    return sum(s.cost for s in ph.sessions if s.key == key and s.session_id == sid)


def session_record(key: str, role: str, r: claude.SessionResult) -> S.SessionRecord:
    usage = r.usage or {}
    return S.SessionRecord(
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


def _record_session(  # noqa: PLR0913 -- the session and where it ran
    run: Run,
    ph: S.Phase,
    step: tuple[str, str],
    r: claude.SessionResult,
    *,
    cfg: claude.StepConfig,
    cwd: Path,
) -> None:
    rec = session_record(*step, r)
    with _LOCK:
        ph.sessions.append(rec)
    run.save()
    log_session(run, (ph.n, ph.slug), rec, r, cfg=cfg, cwd=cwd)


def log_session(  # noqa: PLR0913 -- one line of sessions.jsonl
    run: Run,
    phase: tuple[int, str],
    rec: S.SessionRecord,
    r: claude.SessionResult,
    *,
    cfg: claude.StepConfig,
    cwd: Path,
) -> None:
    """Append the session to sessions.jsonl, across every repo and run."""
    row = {
        "repo": str(run.repo.home.relative_to(run.repo.base / "repos"))
        if run.repo.base
        else run.root.name,
        "run_id": run.st.run_id,
        "branch": run.st.branch,
        "phase": phase[0],  # 0 = planning
        "phase_slug": phase[1],
        **asdict(rec),
        "model": cfg.model,
        "effort": cfg.effort,
        "exit": r.exit_code,
        "duration_s": round(r.duration_s, 1),
        "cwd": str(cwd),
    }
    H.append_session(run.repo.sessions_log, row)
    err.print(step_line(phase[0], rec, r), markup=False, highlight=False)


def _tokens(n: int) -> str:
    if n >= 1_000_000:  # noqa: PLR2004
        return f"{n / 1_000_000:.1f}M"
    return f"{n / 1000:.0f}k" if n >= 1000 else str(n)  # noqa: PLR2004


def step_line(phase: int, rec: S.SessionRecord, r: claude.SessionResult) -> str:
    """One line per finished session: where, how long, tokens, cost, outcome."""
    m, s = divmod(round(r.duration_s), 60)
    where = "plan" if phase == 0 else f"phase {phase}"
    if r.ok:
        out = r.structured or {}
        verdict = out.get("verdict") or out.get("decision")
        outcome = "ok" + (f" {verdict}" if verdict else "")
        if isinstance(out.get("findings"), list):
            outcome += f", {len(out['findings'])} findings"
    else:
        outcome = (
            f"FAILED {r.error_kind}: {r.error.splitlines()[0][:80] if r.error else ''}"
        )
    return (
        f"  {where} {rec.key:<14} {m:>3}m{s:02d}s  "
        f"{_tokens(rec.tokens_in):>6}/{_tokens(rec.tokens_out):<5}  "
        f"${rec.cost:6.2f}  {outcome}"
    )


BRIEF_GLOBS = ("brief.md", "fix-brief-*.md", "gate-fix-*.md")


@contextlib.contextmanager
def _briefs_guarded(run: Run, ph: S.Phase, key: str) -> Generator[None]:
    """Only orch and humans write a phase's briefs. Sessions can write to the run
    dir (they read it through --add-dir), so a brief a session changed or deleted
    is put back and the step fails, like an edit to a protected file in the repo."""
    pdir = run.repo.phase_dir(ph.dirname)
    before = {p: p.read_bytes() for g in BRIEF_GLOBS for p in pdir.glob(g)}
    yield
    edited = []
    for path, data in before.items():
        if not path.is_file() or path.read_bytes() != data:
            path.write_bytes(data)
            edited.append(run.rel(path))
    if edited:
        raise StopRun(
            S.FAILED,
            f"phase {ph.n} {key}: edited protected file {', '.join(edited)} "
            "(only humans change it; orch put it back)",
        )


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


def _write_inputs(run: Run, ph: S.Phase, key: str, inputs: str) -> Path:
    path = run.repo.phase_dir(ph.dirname) / "inputs" / f"{_safe(key)}.md"
    store.write_text(path, inputs)
    return path


def _report_path(run: Run, ph: S.Phase, key: str) -> Path:
    return run.repo.phase_dir(ph.dirname) / "reports" / f"{_safe(key)}.json"


def _safe(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", key).strip("-")


# ---------------------------------------------------------------- git glue


def _commit_orch(run: Run, message: str) -> None:
    """Commit run files that live in the repo (`home = "repo"`); in orch's home
    they are not committed, and the run branch carries code only."""
    if not run.repo.in_repo:
        return
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
        ("Process learnings so far", run.repo.home / "process-learnings.md"),
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


def _brief_inputs(run: Run, ph: S.Phase, answers: str | None, findings: str) -> str:
    items = [(k, v) for k, v in _base_items(run, ph) if k != "Phase brief"]
    for label, rel in (
        ("Decisions (newer wins)", "docs/decisions"),
        ("Architecture", "docs/architecture.md"),
        ("Outline", run.rel(run.repo.run_dir / "plan.md")),
    ):
        if (run.root / rel).exists():
            items.append((label, rel))
    pdir = run.repo.phase_dir(ph.dirname)
    if (pdir / "brief.md").is_file():
        items.append(("Your current brief", run.rel(pdir / "brief.md")))
    row = (
        f"- phase {ph.n}: **{ph.title}**\n- deliverable: {ph.deliverable}\n"
        f"- covers: {', '.join(ph.covers)}\n- size: {ph.size}\n"
        f"- scenario: `{ph.scenario}` ({ph.reason or 'default'})\n"
        f"- approach: {ph.approach}"
    )
    return render.inputs_md(
        f"Inputs for the brief: phase {ph.n}, {ph.title or ph.slug}",
        items,
        {
            "This phase's outline row": row,
            "Answers to your design questions (settled with the user)": answers or "",
            "Plan review findings to fix in this revision": findings,
        },
    )


def _plan_review_inputs(run: Run, ph: S.Phase) -> str:
    items = _base_items(run, ph)
    for label, rel in (
        ("Decisions (newer wins)", "docs/decisions"),
        ("Outline", run.rel(run.repo.run_dir / "plan.md")),
    ):
        if (run.root / rel).exists():
            items.append((label, rel))
    return render.inputs_md(
        f"Inputs for the plan review: phase {ph.n}, {ph.title or ph.slug}",
        items,
        {
            "Outline row": f"{ph.title}: {ph.deliverable}; covers "
            + ", ".join(ph.covers)
        },
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
    key = f"defer#{ph.attempt}.{rnd}"
    if key in ph.done_steps:
        return
    items: list[tuple[str, str]] = []
    if tri is not None:
        items = [
            (" = ".join(d["ids"]), f"{d['title']} ({d['reason']})")
            for d in tri.get("defer") or []
        ]
    else:  # everything shipped: what was found is worth keeping
        items = [
            (f["id"], f"{f['title']} [{f['severity']}]")
            for f in render.findings_of(reports)
        ]
    promoted = {i for it in (tri or {}).get("also_fix") or [] for i in it["ids"]}
    for rep in reports.values():
        items += [
            (f["id"], f"{f['title']} [preexisting]")
            for f in rep.get("preexisting") or []
            if f["id"] not in promoted
        ]
    items = [(ids, text) for ids, text in items if ids not in ph.deferred]
    ph.deferred += [
        ids for ids, _ in items
    ]  # triage re-defers the same item each round
    if text := render.backlog_entry(f"{ph.n} ({ph.title or ph.slug})", items):
        store.append_text(run.repo.home / "backlog.md", text)
    ph.done_steps.append(key)
    run.save()
