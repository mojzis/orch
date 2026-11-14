from __future__ import annotations

from pathlib import Path

import pytest

from orch import layers as L
from orch.errors import ConfigError


def _skill(base: Path, name: str, body: str) -> None:
    d = base / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\n---\n{body}\n")


@pytest.fixture
def layers(tmp_path: Path) -> L.Layers:
    repo = tmp_path / "repo"
    (repo / ".orch").mkdir(parents=True)
    return L.Layers.for_repo(repo)


def test_user_layer_comes_from_env(layers: L.Layers, tmp_path: Path) -> None:
    assert layers.user == tmp_path / "user-config"


def test_repo_template_beats_user_and_default(layers: L.Layers) -> None:
    _skill(layers.user, "orch-qa", "USER")
    assert L.find_template(layers, "orch-qa")[0] == "user"
    _skill(layers.repo, "orch-qa", "REPO")
    layer, path = L.find_template(layers, "orch-qa")
    assert layer == "repo"
    assert "REPO" in (path / "SKILL.md").read_text()


def test_materialize_copies_only_the_winner(layers: L.Layers, tmp_path: Path) -> None:
    _skill(layers.user, "orch-qa", "USER")
    _skill(layers.repo, "orch-qa", "REPO")
    dest = L.materialize_skills(layers, tmp_path / "cache")
    text = (dest / ".claude" / "skills" / "orch-qa" / "SKILL.md").read_text()
    assert "REPO" in text
    assert (dest / ".claude" / "skills" / "orch-dev" / "SKILL.md").is_file()


def test_default_full_scenario_resolves(layers: L.Layers) -> None:
    sc, rows = L.describe(layers, "full")
    assert (sc.steps[0], sc.steps[-1]) == ("brief", "record")
    got = {r["role"]: (r["model"], r["tools"]) for r in rows}
    assert got["dev"] == ("sonnet", "all")
    assert got["review"] == ("opus", "Read,Grep,Glob,Bash")


def test_layers_override_in_order(layers: L.Layers) -> None:
    layers.user.mkdir(parents=True)
    (layers.user / "config.toml").write_text(
        '[role.dev]\nmodel = "haiku"\neffort = "low"\n'
    )
    (layers.repo / "config.toml").write_text('[role.dev]\nmodel = "opus"\n')
    sc = L.load_scenario(layers, "full")
    r = L.resolve_role(layers, sc, "dev")
    assert (r.config.model, r.sources["model"]) == ("opus", "repo:config.toml")
    assert (r.config.effort, r.sources["effort"]) == ("low", "user:config.toml")
    task = L.TaskLayer((("cli:--set", L.parse_sets(["dev.model=sonnet"])),))
    r = L.resolve_role(layers, sc, "dev", task)
    assert (r.config.model, r.sources["model"]) == ("sonnet", "cli:--set")


def test_repo_scenario_extends_default_of_same_name(layers: L.Layers) -> None:
    (layers.repo / "scenarios").mkdir()
    (layers.repo / "scenarios" / "full.toml").write_text(
        'extends = "full"\n[role.qa]\neffort = "high"\n'
        'extra = "check CSV in LibreOffice"\n'
    )
    sc = L.load_scenario(layers, "full")
    assert "review_qa" in sc.steps  # inherited
    r = L.resolve_role(layers, sc, "qa")
    assert r.config.effort == "high"
    assert r.sources["effort"] == "repo:full.toml"
    assert "LibreOffice" in r.config.extra


def test_repo_scenario_beats_user_config(layers: L.Layers) -> None:
    layers.user.mkdir(parents=True)
    (layers.user / "config.toml").write_text('[role.qa]\nmodel = "haiku"\n')
    (layers.repo / "scenarios").mkdir()
    (layers.repo / "scenarios" / "mine.toml").write_text(
        'extends = "full"\n[role.qa]\nmodel = "opus"\n'
    )
    r = L.resolve_role(layers, L.load_scenario(layers, "mine"), "qa")
    assert r.config.model == "opus"


def test_real_data_adds_probe_before_record(layers: L.Layers) -> None:
    sc = L.load_scenario(layers, "real-data")
    assert sc.steps[-2:] == ("probe", "record")


@pytest.mark.parametrize(
    "steps",
    [
        '["brief", "dev", "review_qa", "record"]',  # no gate after dev
        '["dev", "gate", "record"]',  # nobody checks dev
        '["dev", "gate", "review_qa"]',  # no record
        '["dev", "gate", "review_qa", "deploy", "record"]',  # unknown step
        '["gate", "dev", "review_qa", "record"]',  # order
    ],
)
def test_scenarios_breaking_rules_are_refused(layers: L.Layers, steps: str) -> None:
    (layers.repo / "scenarios").mkdir()
    (layers.repo / "scenarios" / "bad.toml").write_text(f"steps = {steps}\n")
    with pytest.raises(ConfigError):
        L.load_scenario(layers, "bad")


def test_unknown_scenario_and_bad_set(layers: L.Layers) -> None:
    with pytest.raises(ConfigError, match="no scenario"):
        L.load_scenario(layers, "nope")
    with pytest.raises(ConfigError, match="bad --set"):
        L.parse_sets(["dev.colour=red"])


def test_parse_sets_types() -> None:
    out = L.parse_sets(
        ["review.tools=Read,Grep", "dev.max_budget_usd=5", "fix.tools=all"]
    )
    assert out == {
        "review": {"tools": ("Read", "Grep")},
        "dev": {"max_budget_usd": 5.0},
        "fix": {"tools": None},
    }


def test_eject_copies_default_into_repo(layers: L.Layers) -> None:
    dest = L.eject(layers, "orch-qa")
    assert dest == layers.repo / "skills" / "orch-qa"
    assert L.find_template(layers, "orch-qa")[0] == "repo"
    with pytest.raises(ConfigError, match="already exists"):
        L.eject(layers, "orch-qa")


@pytest.mark.parametrize(
    ("toml", "match"),
    [
        ('[role.reveiw]\nmodel = "haiku"\n', "unknown role 'reveiw'"),
        ("[role.outline]\nbogus = 1\n", "unknown key outline.bogus"),
        ('[role.dev]\neffort = "bogus"\n', "dev.effort"),
        ('[role.dev]\nmax_budget_usd = "lots"\n', "must be a number"),
    ],
)
def test_config_typos_are_refused(layers: L.Layers, toml: str, match: str) -> None:
    (layers.repo / "config.toml").write_text(toml)
    with pytest.raises(ConfigError, match=match):
        L.describe(layers, "full")


@pytest.mark.parametrize(
    "item", ["dev.max_budget_usd=abc", "dev.effort=bogus", "dev.colour=red"]
)
def test_bad_sets_are_config_errors(item: str) -> None:
    with pytest.raises(ConfigError):
        L.parse_sets([item])


@pytest.mark.parametrize(
    "body",
    [
        'steps = ["dev", "gate", "review_qa", "verify", "record"]',  # verify, no fix
        'steps = "dev"',
        'extends = "full"\nadd_steps = { verify = "probe" }',
    ],
)
def test_malformed_scenarios_are_refused(layers: L.Layers, body: str) -> None:
    (layers.repo / "scenarios").mkdir()
    (layers.repo / "scenarios" / "bad.toml").write_text(body + "\n")
    with pytest.raises(ConfigError):
        L.load_scenario(layers, "bad")


def test_extra_adds_up_across_layers(layers: L.Layers) -> None:
    layers.user.mkdir(parents=True)
    (layers.user / "config.toml").write_text('[role.qa]\nextra = "USER EXTRA"\n')
    r = L.resolve_role(layers, L.load_scenario(layers, "ui"), "qa")
    assert ("browser" in r.config.extra, "USER EXTRA" in r.config.extra) == (True, True)


def test_non_orch_named_template_is_materialized(
    layers: L.Layers, tmp_path: Path
) -> None:
    _skill(layers.repo, "my-qa", "MINE")
    dest = L.materialize_skills(layers, tmp_path / "cache")
    assert (dest / ".claude" / "skills" / "my-qa" / "SKILL.md").is_file()
