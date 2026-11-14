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
from orch.errors import OrchError
from orch.repo import find_repo
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
