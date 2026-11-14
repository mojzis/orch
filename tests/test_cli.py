from __future__ import annotations

import re
from importlib import resources
from pathlib import Path

import pytest
from typer.testing import CliRunner

from orch.cli import app

from .conftest import git
from .helpers import LEARN, checker, dev, make_project

runner = CliRunner()


@pytest.fixture
def in_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.chdir(repo)
    return repo


def test_config_shows_layer_of_each_value(in_repo: Path) -> None:
    (in_repo / ".orch").mkdir()
    (in_repo / ".orch" / "config.toml").write_text('[role.qa]\neffort = "high"\n')
    res = runner.invoke(app, ["config", "--set", "dev.model=haiku"])
    assert res.exit_code == 0, res.output
    dev = next(line for line in res.output.splitlines() if re.match(r"^\W*dev\s", line))
    assert "haiku" in dev
    assert "cli:--set" in dev
    qa = next(line for line in res.output.splitlines() if re.match(r"^\W*qa\s", line))
    assert "repo:config.toml" in qa


def test_config_lists_scenarios(in_repo: Path) -> None:
    res = runner.invoke(app, ["config", "--scenarios"])
    assert res.exit_code == 0
    for name in ("full", "small", "ui", "real-data", "orch-dev"):
        assert name in res.output


def test_config_bad_scenario_is_usage_error(in_repo: Path) -> None:
    res = runner.invoke(app, ["config", "-s", "nope"])
    assert res.exit_code == 2
    assert "error: no scenario named 'nope'" in res.output


def test_eject(in_repo: Path) -> None:
    res = runner.invoke(app, ["eject", "orch-qa"])
    assert res.exit_code == 0
    assert (in_repo / ".orch" / "skills" / "orch-qa" / "SKILL.md").is_file()


@pytest.mark.parametrize("topic", ["setup", "use", "tune"])
def test_guide_topics_print(topic: str) -> None:
    res = runner.invoke(app, ["guide", topic])
    assert res.exit_code == 0
    assert res.output.startswith(f"# orch: {topic}")


def test_guide_autodetects(in_repo: Path) -> None:
    res = runner.invoke(app, ["guide"])
    assert res.output.startswith("# orch guide: no .orch/config.toml here -> setup")
    (in_repo / ".orch").mkdir()
    (in_repo / ".orch" / "config.toml").write_text("")
    res = runner.invoke(app, ["guide"])
    assert "-> use" in res.output.splitlines()[0]


PAGES = resources.files("orch") / "defaults" / "guide"
COMMANDS = {
    c.name or getattr(c.callback, "__name__", "") for c in app.registered_commands
}


@pytest.mark.parametrize("page", ["setup.md", "use.md", "tune.md"])
def test_every_command_in_guide_pages_exists(page: str) -> None:
    mentioned = set(re.findall(r"`orch ([a-z-]+)", (PAGES / page).read_text()))
    assert mentioned <= COMMANDS


def test_run_exit_codes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_claude
) -> None:
    repo = make_project(tmp_path, phases=1)
    monkeypatch.chdir(repo)
    fake_claude.on("orch-dev", dev("w.txt", {"write": "BROKEN", "content": "1"}))
    fake_claude.on("orch-fix", dev("f.txt"))
    res = runner.invoke(app, ["run", "-s", "small"])
    assert (res.exit_code, "blocked" in res.output) == (3, True)
    (repo / "BROKEN").unlink()
    git(repo, "commit", "-qam", "manual fix")
    fake_claude.on("orch-combined", {"structured": checker()})
    fake_claude.on("orch-learnings", {"structured": LEARN})
    res = runner.invoke(app, ["run"])
    assert res.exit_code == 0, res.output
    res = runner.invoke(app, ["status"])
    assert "done" in res.output
