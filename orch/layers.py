"""Resolve scenarios, templates (orch- skills), model and effort across layers.

Lowest to highest: defaults shipped with orch -> user (~/.config/orch or
$ORCH_CONFIG_HOME) -> repo (.orch/) -> task (pitch frontmatter, outline row,
`orch run --scenario/--set`). Each layer overrides the one before it.
"""

from __future__ import annotations

import os
import shutil
import tomllib
from dataclasses import dataclass, replace
from importlib import resources
from pathlib import Path
from typing import Any

from orch.claude import READ_BASH_TOOLS, StepConfig
from orch.errors import ConfigError
from orch.scenario import ROLES, Scenario, validate

STRONG = "opus"
MID = "sonnet"

# The sessions table from the spec: template, tools, model per role.
DEFAULT_ROLES: dict[str, dict[str, Any]] = {
    "outline": {"template": "orch-outline", "tools": READ_BASH_TOOLS, "model": STRONG},
    "outline_review": {
        "template": "orch-outline-review",
        "tools": READ_BASH_TOOLS,
        "model": STRONG,
    },
    "brief": {"template": "orch-brief", "tools": READ_BASH_TOOLS, "model": STRONG},
    "plan_review": {
        "template": "orch-plan-review",
        "tools": READ_BASH_TOOLS,
        "model": STRONG,
    },
    "dev": {"template": "orch-dev", "tools": None, "model": MID},
    "add_checks": {
        "template": "orch-add-checks",
        "tools": READ_BASH_TOOLS,
        "model": STRONG,
    },
    "review": {"template": "orch-review", "tools": READ_BASH_TOOLS, "model": STRONG},
    "qa": {"template": "orch-qa", "tools": READ_BASH_TOOLS, "model": MID},
    "combined": {"template": "orch-combined", "tools": READ_BASH_TOOLS, "model": MID},
    "triage": {"template": "orch-triage", "tools": READ_BASH_TOOLS, "model": STRONG},
    "fix": {"template": "orch-fix", "tools": None, "model": MID},
    "verify": {"template": "orch-verify", "tools": READ_BASH_TOOLS, "model": MID},
    "probe": {"template": "orch-probe", "tools": READ_BASH_TOOLS, "model": MID},
    "learnings": {
        "template": "orch-learnings",
        "tools": READ_BASH_TOOLS,
        "model": STRONG,
    },
}
KEYS = (
    "template",
    "tools",
    "model",
    "effort",
    "max_budget_usd",
    "fallback_model",
    "extra",
)
DEFAULT_BUDGET_USD = 50.0

LAYER_RANK = {"default": 0, "user": 1, "repo": 2, "task": 3}


@dataclass(frozen=True)
class Layers:
    """Where each layer lives. `defaults` is a directory inside the package."""

    defaults: Path
    user: Path
    repo: Path

    @classmethod
    def for_repo(cls, repo_root: Path) -> Layers:
        user = Path(
            os.environ.get("ORCH_CONFIG_HOME") or Path.home() / ".config" / "orch"
        )
        return cls(defaults_dir(), user, repo_root / ".orch")

    def dirs(self) -> list[tuple[str, Path]]:
        return [("default", self.defaults), ("user", self.user), ("repo", self.repo)]


def defaults_dir() -> Path:
    return Path(str(resources.files("orch") / "defaults"))


@dataclass(frozen=True)
class Resolved:
    """A value plus where it came from, for `orch config`."""

    value: Any
    source: str


@dataclass(frozen=True)
class RoleResolution:
    config: StepConfig
    sources: dict[str, str]  # key -> layer/source that set it
    template_path: Path


def _load_toml(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: invalid TOML: {e}") from None


def find_scenario_file(layers: Layers, name: str, below: int = 99) -> tuple[str, Path]:
    """Highest layer (below rank `below`) that defines scenario `name`."""
    for layer, d in reversed(layers.dirs()):
        if LAYER_RANK[layer] >= below:
            continue
        p = d / "scenarios" / f"{name}.toml"
        if p.is_file():
            return layer, p
    raise ConfigError(
        f"no scenario named {name!r}",
        hint="run `orch config --scenarios` to list them",
    )


def list_scenarios(layers: Layers) -> dict[str, str]:
    found: dict[str, str] = {}
    for layer, d in layers.dirs():
        for p in sorted((d / "scenarios").glob("*.toml")):
            found[p.stem] = layer
    return found


def load_scenario(layers: Layers, name: str) -> Scenario:
    """Load a scenario and its `extends` chain. A scenario may extend one of the
    same name; that resolves to the next layer down (a repo `full` extending the
    default `full`)."""
    chain: list[tuple[str, Path, dict[str, Any]]] = []
    below = 99
    current = name
    seen: set[Path] = set()
    while True:
        layer, path = find_scenario_file(layers, current, below)
        if path in seen:
            raise ConfigError(f"scenario {name!r}: `extends` loops at {path}")
        seen.add(path)
        data = _load_toml(path)
        chain.append((layer, path, data))
        parent = data.get("extends")
        if not parent:
            break
        below = LAYER_RANK[layer] if parent == current else 99
        current = parent
    chain.reverse()  # base first
    steps: tuple[str, ...] = ()
    description = ""
    settings: list[tuple[str, str, dict[str, Any]]] = []
    for layer, path, data in chain:
        if "steps" in data:
            steps = tuple(data["steps"])
        for step, extra_steps in (data.get("add_steps") or {}).items():
            steps = _insert_after(steps, step, tuple(extra_steps))
        description = data.get("description", description)
        if roles := data.get("role"):
            settings.append((layer, f"{layer}:{path.name}", roles))
    sc = Scenario(
        name=name,
        steps=steps,
        description=description,
        role_settings=tuple(settings),
        sources=tuple(str(p) for _, p, _ in chain),
    )
    validate(sc)
    return sc


def _insert_after(
    steps: tuple[str, ...], anchor: str, new: tuple[str, ...]
) -> tuple[str, ...]:
    if anchor not in steps:
        raise ConfigError(f"add_steps: anchor step {anchor!r} not in scenario")
    i = steps.index(anchor) + 1
    return steps[:i] + new + steps[i:]


def load_config(layers: Layers) -> list[tuple[str, str, dict[str, Any]]]:
    """User then repo config.toml contents, tagged with their layer."""
    out = []
    for layer, d in layers.dirs()[1:]:
        p = d / "config.toml"
        if p.is_file():
            out.append((layer, f"{layer}:config.toml", _load_toml(p)))
    return out


def parse_sets(sets: list[str] | tuple[str, ...]) -> dict[str, dict[str, Any]]:
    """`dev.model=opus` -> {"dev": {"model": "opus"}}."""
    out: dict[str, dict[str, Any]] = {}
    for item in sets:
        key, sep, value = item.partition("=")
        role, dot, attr = key.partition(".")
        if not sep or not dot or role not in ROLES or attr not in KEYS:
            raise ConfigError(
                f"bad --set {item!r}",
                hint="use role.key=value, e.g. dev.model=opus or review.effort=high",
            )
        v: Any = value
        if attr == "max_budget_usd":
            v = float(value)
        elif attr == "tools":
            v = None if value == "all" else tuple(t for t in value.split(",") if t)
        out.setdefault(role, {})[attr] = v
    return out


@dataclass(frozen=True)
class TaskLayer:
    """Task-level overrides, lowest first: pitch, outline row, command line."""

    entries: tuple[tuple[str, dict[str, dict[str, Any]]], ...] = ()


def resolve_role(
    layers: Layers,
    scenario: Scenario,
    role: str,
    task: TaskLayer | None = None,
) -> RoleResolution:
    values: dict[str, Any] = {"max_budget_usd": DEFAULT_BUDGET_USD} | dict(
        DEFAULT_ROLES[role]
    )
    sources = dict.fromkeys(values, "default")
    stack: list[tuple[int, str, dict[str, Any]]] = []
    for layer, src, data in load_config(layers):
        stack.append((LAYER_RANK[layer], src, (data.get("role") or {}).get(role) or {}))
    for layer, src, roles in scenario.role_settings:
        stack.append((LAYER_RANK[layer], src, roles.get(role) or {}))
    for src, roles in task.entries if task else ():
        stack.append((LAYER_RANK["task"], src, roles.get(role) or {}))
    stack.sort(
        key=lambda t: t[0]
    )  # stable: within a layer, a scenario beats config.toml
    for _rank, src, vals in stack:
        for k, v in vals.items():
            if k not in KEYS:
                raise ConfigError(
                    f"{src}: unknown key {role}.{k}", hint=f"keys: {KEYS}"
                )
            values[k] = _norm_tools(v) if k == "tools" else v
            sources[k] = src
    template = values["template"]
    tlayer, tpath = find_template(layers, template)
    sources["template"] = f"{sources['template']} ({tlayer} skill)"
    cfg = StepConfig(
        step=role,
        template=template,
        tools=values.get("tools"),
        model=values.get("model"),
        effort=values.get("effort"),
        max_budget_usd=values.get("max_budget_usd"),
        fallback_model=values.get("fallback_model"),
        extra=values.get("extra", ""),
    )
    return RoleResolution(cfg, sources, tpath)


def _norm_tools(v: Any) -> tuple[str, ...] | None:
    if isinstance(v, str):
        return None if v == "all" else tuple(t.strip() for t in v.split(","))
    return None if v is None else tuple(v)


def find_template(layers: Layers, name: str) -> tuple[str, Path]:
    """Highest layer holding skills/<name>/SKILL.md."""
    for layer, d in reversed(layers.dirs()):
        p = d / "skills" / name
        if (p / "SKILL.md").is_file():
            return layer, p
    raise ConfigError(
        f"no template (skill) named {name!r}",
        hint=f"add skills/{name}/SKILL.md under .orch/ or ~/.config/orch/",
    )


def list_templates(layers: Layers) -> dict[str, str]:
    found: dict[str, str] = {}
    for layer, d in layers.dirs():
        for p in sorted((d / "skills").glob("orch-*/SKILL.md")):
            found[p.parent.name] = layer
    return found


def materialize_skills(layers: Layers, dest: Path) -> Path:
    """Copy the winning version of every template to `dest/.claude/skills/` and
    return `dest`, to be passed with --add-dir. Claude Code ranks a personal skill
    above a project one, the reverse of what a repo override needs, so orch picks
    the winner itself."""
    skills = dest / ".claude" / "skills"
    if skills.exists():
        shutil.rmtree(skills)
    skills.mkdir(parents=True)
    for name in list_templates(layers):
        _layer, src = find_template(layers, name)
        shutil.copytree(src, skills / name)
    return dest


def eject(layers: Layers, name: str) -> Path:
    """Copy a default template into the repo layer so it can be edited."""
    src = layers.defaults / "skills" / name
    if not (src / "SKILL.md").is_file():
        raise ConfigError(
            f"no default template {name!r}",
            hint="run `orch config` to see template names",
        )
    dest = layers.repo / "skills" / name
    if dest.exists():
        raise ConfigError(f"{dest} already exists", hint="edit it in place")
    shutil.copytree(src, dest)
    return dest


def with_extra(cfg: StepConfig, extra: str) -> StepConfig:
    if not extra:
        return cfg
    joined = f"{cfg.extra}\n{extra}".strip()
    return replace(cfg, extra=joined)


def describe(
    layers: Layers, scenario_name: str, task: TaskLayer | None = None
) -> tuple[Scenario, list[dict[str, str]]]:
    """For `orch config`: every role the scenario uses, what it resolves to and
    which layer each value came from."""
    sc = load_scenario(layers, scenario_name)
    rows = []
    for role in roles_for(sc):
        r = resolve_role(layers, sc, role, task)
        c = r.config
        rows.append(
            {
                "role": role,
                "template": c.template,
                "template_from": r.sources["template"],
                "model": c.model or "(claude default)",
                "model_from": r.sources.get("model", "default"),
                "effort": c.effort or "(claude default)",
                "effort_from": r.sources.get("effort", "default"),
                "tools": "all" if c.tools is None else ",".join(c.tools),
            }
        )
    return sc, rows


def roles_for(sc: Scenario) -> list[str]:
    """Session roles a scenario's steps start, in run order."""
    by_step = {
        "brief": ["brief", "plan_review"],
        "dev": ["dev"],
        "add_checks": ["add_checks"],
        "review_qa": ["review", "qa"],
        "review_combined": ["combined"],
        "triage": ["triage"],
        "fix": ["fix"],
        "verify": ["verify"],
        "probe": ["probe"],
        "record": ["learnings"],
    }
    out: list[str] = []
    for step in sc.steps:
        out += by_step.get(step, [])
    return out
