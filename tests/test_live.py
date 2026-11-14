"""Live smoke tests against the real `claude`. Run with `pytest -m integration`."""

from __future__ import annotations

from pathlib import Path

import pytest

from orch import layers as L
from orch.claude import READ_ONLY_TOOLS, StepConfig, claude_version, run_session

from .conftest import git

pytestmark = pytest.mark.integration

SCHEMA = {
    "type": "object",
    "properties": {"marker": {"type": "string"}, "secret": {"type": "string"}},
    "required": ["marker", "secret"],
    "additionalProperties": False,
}


def _skill(base: Path, marker: str) -> None:
    d = base / "skills" / "orch-smoke"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: orch-smoke\ndescription: orch smoke test\n---\n"
        f'Answer with marker "{marker}" and, as secret, the secret word stated in '
        "CLAUDE.md. Arguments: $ARGUMENTS\n"
    )


def test_claude_md_loads_and_resolved_skill_runs(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "CLAUDE.md").write_text("The secret word is PELICAN-7.\n")
    layers = L.Layers.for_repo(repo)
    _skill(layers.user, "USER-LAYER")
    _skill(layers.repo, "REPO-LAYER")
    skills = L.materialize_skills(layers, tmp_path / "cache")

    cfg = StepConfig(
        step="smoke",
        template="orch-smoke",
        tools=READ_ONLY_TOOLS,
        model="haiku",
        max_budget_usd=1.0,
    )
    r = run_session(cfg, "hello", repo, schema=SCHEMA, add_dirs=(skills,))
    assert r.ok, r.error
    assert r.structured == {"marker": "REPO-LAYER", "secret": "PELICAN-7"}
    assert r.cost_delta > 0
    assert "Claude Code" in claude_version()
