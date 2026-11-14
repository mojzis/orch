"""orch command line. A router: each command parses options and calls one function."""

from __future__ import annotations

from enum import StrEnum
from importlib import resources
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table
from typer_agentic import agent_errors

from orch import layers as L
from orch import loop, planning
from orch import state as S
from orch.errors import OrchError
from orch.repo import find_repo, init_repo
from orch.ui import TABLE_BOX, console, err

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
    help="Deliver a shaped pitch phase by phase with fresh Claude Code sessions. "
    "Run `orch guide` first.",
)

ScenarioOpt = Annotated[
    str | None,
    typer.Option("--scenario", "-s", help="Scenario to use for every phase."),
]
SetOpt = Annotated[
    list[str] | None,
    typer.Option("--set", help="Per-role override for this run, e.g. dev.model=opus."),
]


def _fail(e: OrchError) -> typer.Exit:
    err.print(f"error: {e}", markup=False)
    if e.hint:
        err.print(f"hint: {e.hint}", markup=False)
    return typer.Exit(e.exit_code)


def _task(sets: list[str] | None) -> L.TaskLayer:
    return L.TaskLayer(((("cli:--set"), L.parse_sets(sets or [])),))


@app.command()
def init() -> None:
    """Create .orch/config.toml with the keys orch reads, commented."""
    try:
        path = init_repo(find_repo())
    except OrchError as e:
        raise _fail(e) from None
    console.print(f"wrote {path}; set gate_cmd, then commit it", markup=False)


@app.command()
def run(
    scenario: ScenarioOpt = None,
    sets: SetOpt = None,
    auto_decide: Annotated[
        bool,
        typer.Option("--auto-decide", help="Take the recommended design options."),
    ] = False,
    pause: Annotated[
        bool, typer.Option("--pause", help="Stop after planning, to read the plan.")
    ] = False,
) -> None:
    """Run (or resume) the phase loop on the run branch, planning first if there
    is a docs/pitch.md and no run yet. Exit 0 when every phase is done (or
    --pause stopped after the plan), 3 when stopped (fix, then re-run to resume),
    75 on a usage limit."""
    try:
        st = planning.run(
            find_repo(),
            scenario=scenario,
            sets=sets,
            auto_decide=auto_decide,
            pause=pause,
        )
    except OrchError as e:
        raise _fail(e) from None
    _exit_with(st, ok=(S.DONE,) + ((S.RUNNING,) if pause else ()))


@app.command()
def plan(
    scenario: ScenarioOpt = None,
    sets: SetOpt = None,
    from_n: Annotated[
        int | None,
        typer.Option("--from", help="Replan from phase N on; earlier phases stay."),
    ] = None,
) -> None:
    """Split docs/pitch.md into an outline of phases (.orch/plan.md) and stop.
    `orch run` then runs it."""
    try:
        st = planning.plan(find_repo(), scenario=scenario, sets=sets, from_n=from_n)
    except OrchError as e:
        raise _fail(e) from None
    _exit_with(st, ok=(S.RUNNING,))


def _exit_with(st: S.RunState, ok: tuple[str, ...]) -> None:
    _print_status(st)
    if st.status in ok:
        return
    if st.status == S.PAUSED and st.reset_at:
        raise typer.Exit(loop.EXIT_PAUSED)
    raise typer.Exit(3)


@app.command()
def status() -> None:
    """Show the run's state (from .orch/state.json)."""
    try:
        st = loop.load_state(find_repo())
    except OrchError as e:
        raise _fail(e) from None
    _print_status(st)


def _print_status(st: S.RunState) -> None:
    reason = f" ({st.stop_reason})" if st.stop_reason else ""
    console.print(f"run {st.run_id} on {st.branch}: {st.status}{reason}", markup=False)
    if st.reset_at:
        console.print(f"usage limit resets at {st.reset_at}", markup=False)
    t = Table(
        "#",
        "phase",
        "scenario",
        "status",
        "last step",
        "fix rounds",
        "cost",
        box=TABLE_BOX,
    )
    for p in st.phases:
        t.add_row(
            str(p.n),
            p.title or p.slug,
            p.scenario,
            p.status,
            p.done_steps[-1] if p.done_steps else "",
            str(p.fix_round),
            f"${p.cost():.2f}",
        )
    console.print(t)


@app.command("config")
def config_cmd(
    scenario: ScenarioOpt = None,
    sets: SetOpt = None,
    scenarios: Annotated[
        bool, typer.Option("--scenarios", help="List scenarios and templates.")
    ] = False,
) -> None:
    """Show, for every step, the template, model and effort in use and the layer
    each came from."""
    try:
        layers = L.Layers.for_repo(find_repo().root)
        if scenarios:
            t = Table("scenario", "layer", box=TABLE_BOX)
            for name, layer in L.list_scenarios(layers).items():
                t.add_row(name, layer)
            console.print(t)
            t = Table("template", "layer", box=TABLE_BOX)
            for name, layer in L.list_templates(layers).items():
                t.add_row(name, layer)
            console.print(t)
            return
        sc, rows = L.describe(layers, scenario or "full", _task(sets))
    except OrchError as e:
        raise _fail(e) from None
    console.print(f"scenario: {sc.name}  ({' <- '.join(sc.sources)})", markup=False)
    console.print(f"steps: {' -> '.join(sc.steps)}", markup=False)
    t = Table(
        "role",
        "template",
        "from",
        "model",
        "from",
        "effort",
        "from",
        "tools",
        box=TABLE_BOX,
    )
    for r in rows:
        t.add_row(
            r["role"],
            r["template"],
            r["template_from"],
            r["model"],
            r["model_from"],
            r["effort"],
            r["effort_from"],
            r["tools"],
        )
    console.print(t)


@app.command()
def eject(
    template: Annotated[str, typer.Argument(help="Template name, e.g. orch-qa.")],
) -> None:
    """Copy a default template into .orch/skills/ so you can edit it."""
    try:
        dest = L.eject(L.Layers.for_repo(find_repo().root), template)
    except OrchError as e:
        raise _fail(e) from None
    console.print(f"ejected {template} -> {dest}", markup=False)


class Topic(StrEnum):
    setup = "setup"
    use = "use"
    tune = "tune"


@app.command()
def guide(
    topic: Annotated[Topic | None, typer.Argument(help="setup | use | tune")] = None,
) -> None:
    """Print instructions for an agent about to use orch. setup: wire orch into a
    repo. use: which command answers which situation. tune: config keys, layers,
    exit codes."""
    if topic is None:
        configured = (Path.cwd() / ".orch" / "config.toml").is_file()
        topic = Topic.use if configured else Topic.setup
        why = ".orch/config.toml found" if configured else "no .orch/config.toml here"
        print(f"# orch guide: {why} -> {topic.value}\n")  # noqa: T201 -- plain stdout
    page = resources.files("orch") / "defaults" / "guide" / f"{topic.value}.md"
    print(page.read_text(encoding="utf-8"))  # noqa: T201 -- plain stdout


main = agent_errors(app)
