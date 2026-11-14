"""Scenarios: which steps a phase runs, and the rules every scenario keeps."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from orch.errors import ConfigError

# Steps in the only order the loop runs them. A scenario picks a subsequence.
STEP_ORDER = (
    "brief",
    "dev",
    "gate",
    "add_checks",
    "review_qa",
    "review_combined",
    "triage",
    "fix",
    "verify",
    "probe",
    "record",
)

# Session roles and the step they belong to. `review_qa` runs two sessions.
ROLES = (
    "outline",
    "outline_review",
    "brief",
    "plan_review",
    "dev",
    "add_checks",
    "review",
    "qa",
    "combined",
    "triage",
    "fix",
    "verify",
    "probe",
    "learnings",
)


@dataclass(frozen=True)
class Scenario:
    name: str
    steps: tuple[str, ...]
    description: str = ""
    # role -> settings, each tagged with the layer it came from, lowest first
    role_settings: tuple[tuple[str, str, dict[str, Any]], ...] = field(
        default_factory=tuple
    )
    sources: tuple[str, ...] = ()  # files, lowest layer first

    def has(self, step: str) -> bool:
        return step in self.steps


def validate(s: Scenario) -> None:
    """Refuse a scenario that breaks a rule every scenario keeps."""
    unknown = [x for x in s.steps if x not in STEP_ORDER]
    if unknown:
        raise ConfigError(
            f"scenario {s.name!r}: unknown step(s) {', '.join(unknown)}",
            hint=f"steps are: {', '.join(STEP_ORDER)}",
        )
    positions = [STEP_ORDER.index(x) for x in s.steps]
    if positions != sorted(positions) or len(set(positions)) != len(positions):
        raise ConfigError(
            f"scenario {s.name!r}: steps out of order or repeated",
            hint=f"keep this order: {' -> '.join(STEP_ORDER)}",
        )
    rules = [
        ("dev" in s.steps, "every scenario has a dev step"),
        (
            "dev" not in s.steps or _next(s, "dev") == "gate",
            "the gate runs right after dev (it runs after fix automatically)",
        ),
        (
            "review_qa" in s.steps or "review_combined" in s.steps,
            "dev's work is checked by a different session (review_qa or "
            "review_combined)",
        ),
        (
            not ("review_qa" in s.steps and "review_combined" in s.steps),
            "pick one of review_qa and review_combined",
        ),
        (s.steps[-1:] == ("record",), "every phase ends with record"),
        (
            "triage" not in s.steps or "fix" in s.steps,
            "triage needs a fix step",
        ),
    ]
    for ok, rule in rules:
        if not ok:
            raise ConfigError(f"scenario {s.name!r} breaks a rule: {rule}")
    for _layer, src, settings in s.role_settings:
        bad = [r for r in settings if r not in ROLES]
        if bad:
            raise ConfigError(
                f"{src}: unknown role(s) {', '.join(bad)}",
                hint=f"roles are: {', '.join(ROLES)}",
            )


def _next(s: Scenario, step: str) -> str | None:
    i = s.steps.index(step)
    return s.steps[i + 1] if i + 1 < len(s.steps) else None
