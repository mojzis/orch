"""state.json (orch-owned) and PROGRESS.md, which is rendered from it."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from orch import store

STATE_VERSION = 1

# Run statuses. `done` and the stop states end `orch run`.
RUNNING = "running"
DONE = "done"
PAUSED = "paused"
NEEDS_USER = "needs-user"
BLOCKED = "blocked"
NEEDS_REPLAN = "needs-replan"
FAILED = "failed"


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class SessionRecord:
    key: str  # step key, e.g. "dev", "review", "fix#1"
    role: str
    session_id: str
    ok: bool
    cost: float
    error_kind: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    at: str = field(default_factory=now)


@dataclass
class Phase:
    n: int
    slug: str
    title: str = ""
    scenario: str = "full"
    status: str = "todo"  # todo | running | done | blocked | failed
    done_steps: list[str] = field(default_factory=list)
    fix_round: int = 0
    lead_session: str | None = None
    lead_cost_total: float = 0.0
    sessions: list[SessionRecord] = field(default_factory=list)
    base: str | None = None  # HEAD when the phase started
    commit: str | None = None
    note: str = ""
    covers: list[str] = field(default_factory=list)
    deliverable: str = ""
    depends_on: list[int] = field(default_factory=list)
    size: str = ""
    approach: str = ""
    reason: str = ""
    task_set: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def dirname(self) -> str:
        return f"{self.n:02d}-{self.slug}"

    def cost(self) -> float:
        return sum(s.cost for s in self.sessions)


@dataclass
class RunState:
    run_id: str
    branch: str
    status: str = RUNNING
    stop_reason: str = ""
    reset_at: str | None = None
    claude_version: str = ""
    started_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)
    phases: list[Phase] = field(default_factory=list)
    pending: dict[str, str] | None = None  # session in flight: key, role, id
    replans: list[dict[str, Any]] = field(default_factory=list)
    plan_sessions: list[SessionRecord] = field(default_factory=list)
    auto_decide: bool = False
    version: int = STATE_VERSION

    def phase(self, n: int) -> Phase:
        for p in self.phases:
            if p.n == n:
                return p
        raise KeyError(n)

    def cost(self) -> float:
        return sum(p.cost() for p in self.phases) + sum(
            s.cost for s in self.plan_sessions
        )

    def current(self) -> Phase | None:
        return next((p for p in self.phases if p.status != "done"), None)


def _from_dict(cls: Any, data: dict[str, Any]) -> Any:
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in names})


def load(path: Path) -> RunState:
    data = store.read_json(path)
    plan_sessions = [
        _from_dict(SessionRecord, s) for s in data.get("plan_sessions", [])
    ]
    phases = []
    for p in data.get("phases", []):
        sessions = [_from_dict(SessionRecord, s) for s in p.get("sessions", [])]
        phases.append(_from_dict(Phase, p | {"sessions": sessions}))
    return _from_dict(
        RunState, data | {"phases": phases, "plan_sessions": plan_sessions}
    )


def save(path: Path, st: RunState) -> None:
    st.updated_at = now()
    store.write_json(path, asdict(st))
    store.write_text(path.with_name("PROGRESS.md"), render_progress(st))


def render_progress(st: RunState) -> str:
    lines = [
        f"# orch run {st.run_id}",
        "",
        "Rendered from `.orch/state.json`; do not edit.",
        "",
        f"- branch: `{st.branch}`",
        f"- status: **{st.status}**"
        + (f" ({st.stop_reason})" if st.stop_reason else ""),
        f"- updated: {st.updated_at}",
        f"- cost so far: ${st.cost():.2f}",
        "",
        "| # | phase | scenario | status | last step | fix rounds | cost |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for p in st.phases:
        last = p.done_steps[-1] if p.done_steps else ""
        lines.append(
            f"| {p.n} | {p.title or p.slug} | {p.scenario} | {p.status} | {last} "
            f"| {p.fix_round} | ${p.cost():.2f} |"
        )
    return "\n".join(lines) + "\n"
