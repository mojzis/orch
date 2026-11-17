"""orch's home: where run files live, the repo key, sessions.jsonl, `orch migrate`,
and the brief guard that replaces the commit check for files outside the repo."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from orch import claude, loop, planning
from orch import home as H
from orch import state as S
from orch.cli import app
from orch.errors import ConfigError
from orch.repo import Repo

from .conftest import FakeClaude, git
from .helpers import LEARN, checker, dev, make_project, run_dir

runner = CliRunner()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    return make_project(tmp_path, phases=1)


@pytest.fixture
def fc(fake_claude: FakeClaude) -> FakeClaude:
    fake_claude.on("orch-dev", dev("work.txt"))
    fake_claude.on("orch-combined", {"structured": checker()})
    fake_claude.on("orch-learnings", {"structured": LEARN})
    return fake_claude


def _run(project: Path) -> S.RunState:
    return planning.run(Repo(project), scenario="small")


# ---- where the home is


def test_home_env_then_user_config_then_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "user-config"
    cfg.mkdir()
    (cfg / "config.toml").write_text('home = "~/elsewhere"\n')
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ORCH_HOME", str(tmp_path / "env"))
    from_env = H.orch_home()
    monkeypatch.delenv("ORCH_HOME")
    from_config = H.orch_home()
    (cfg / "config.toml").unlink()
    default = H.orch_home()
    assert (from_env, from_config, default) == (
        tmp_path / "env",
        tmp_path / "elsewhere",
        tmp_path / ".orch",
    )


def test_home_repo_means_in_the_repo(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ORCH_HOME", "repo")
    repo = Repo(project, run_id="r1")
    assert (repo.in_repo, repo.run_dir, repo.cache) == (
        True,
        project / ".orch" / "runs" / "r1",
        project / ".orch" / ".cache",
    )


@pytest.mark.parametrize(
    ("url", "key"),
    [
        ("git@github.com:mojzis/gerenuk.git", "mojzis/gerenuk"),
        ("https://github.com/mojzis/gerenuk", "mojzis/gerenuk"),
        ("https://github.com/mojzis/gerenuk.git/", "mojzis/gerenuk"),
        ("ssh://git@host:2222/team/my repo.git", "team/my-repo"),
        ("/srv/git/project.git", "git/project"),
    ],
)
def test_repo_key_from_remote_url(tmp_path: Path, url: str, key: str) -> None:
    git(tmp_path, "init", "-q")
    git(tmp_path, "remote", "add", "origin", url)
    assert H.repo_key(tmp_path, "origin") == key


def test_repo_key_without_remote_is_the_repo_dir_even_from_a_worktree(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "myproj"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "commit", "-q", "--allow-empty", "-m", "x")
    git(repo, "worktree", "add", "-q", "--detach", str(tmp_path / "wt"))
    keys = (H.repo_key(repo, "origin"), H.repo_key(tmp_path / "wt", "origin"))
    assert keys == ("myproj", "myproj")


# ---- run files stay out of the repo


def test_run_leaves_the_repo_clean_and_logs_every_session(
    project: Path, fc: FakeClaude, tmp_path: Path
) -> None:
    st = _run(project)
    assert st.status == S.DONE, st.stop_reason
    assert git(project, "status", "--porcelain", "--untracked-files=all") == ""
    rows = [
        json.loads(line)
        for line in (tmp_path / "orch-home" / "sessions.jsonl").read_text().splitlines()
    ]
    assert [(r["phase"], r["role"]) for r in rows] == [
        (1, "dev"),
        (1, "combined"),
        (1, "learnings"),
    ]
    first = rows[0]
    assert (first["repo"], first["run_id"], first["branch"], first["cwd"]) == (
        f"{tmp_path.name}/remote",
        st.run_id,
        st.branch,
        str(project),
    )
    assert {"model", "effort", "exit", "duration_s", "tokens_in"} <= set(first)


def test_run_prints_one_line_per_session(
    project: Path, fc: FakeClaude, capsys: pytest.CaptureFixture[str]
) -> None:
    st = _run(project)
    assert st.status == S.DONE, st.stop_reason
    lines = [ln for ln in capsys.readouterr().err.splitlines() if "phase 1" in ln]
    assert [ln.split()[2] for ln in lines] == ["dev", "combined", "learnings"]
    tokens = re.compile(r"\d+[kM]?/\d+[kM]? +\$ *\d+\.\d\d +ok")
    assert all(tokens.search(ln) for ln in lines), lines


def test_step_line_shows_verdict_findings_and_failure() -> None:
    rec = S.SessionRecord(
        key="review",
        role="review",
        session_id="s",
        ok=True,
        cost=1.234,
        error_kind=None,
        tokens_in=1_250_000,
        tokens_out=34_000,
    )
    ok = claude.SessionResult(
        session_id="s",
        ok=True,
        error_kind=None,
        error="",
        structured={"verdict": "fix", "findings": [{}, {}]},
        cost_total=1.234,
        cost_delta=1.234,
        duration_s=252,
    )
    line = loop.step_line(2, rec, ok)
    assert line.split() == [
        "phase", "2", "review", "4m12s", "1.2M/34k", "$", "1.23",
        "ok", "fix,", "2", "findings",
    ]  # fmt: skip
    bad = replace(ok, ok=False, error_kind="budget", error="max budget\nmore")
    assert loop.step_line(0, rec, bad).split()[0] == "plan"
    assert loop.step_line(0, rec, bad).endswith("FAILED budget: max budget")


def test_session_editing_its_brief_is_caught_and_undone(
    project: Path, fc: FakeClaude, tmp_path: Path
) -> None:
    home = tmp_path / "orch-home" / "repos" / tmp_path.name / "remote"
    fc.on(
        "orch-dev",
        dev(
            "w.txt",
            {"sh": f"for f in {home}/runs/*/phases/*/brief.md; do echo x > $f; done"},
        ),
    )
    st = _run(project)
    brief = (run_dir(project) / "phases" / "01-p1" / "brief.md").read_text()
    assert (st.status, "edited protected file" in st.stop_reason) == (
        S.FAILED,
        True,
    ), st.stop_reason
    assert brief.startswith("# Phase 1"), "orch puts the brief back"


def test_status_and_report_take_a_run_id(
    project: Path, fc: FakeClaude, monkeypatch: pytest.MonkeyPatch
) -> None:
    st = _run(project)
    git(project, "switch", "-q", "main")
    monkeypatch.chdir(project)
    status = runner.invoke(app, ["status", "--run", st.run_id])
    report = runner.invoke(app, ["report", "--run", st.run_id])
    missing = runner.invoke(app, ["status", "--run", "nope"])
    assert (status.exit_code, report.exit_code, missing.exit_code) == (0, 0, 2)
    assert str(run_dir(project)) in status.output
    assert report.output.strip().endswith("report.md")


# ---- migrate


def _legacy(project: Path) -> None:
    """What an older orch left in .orch/: a current run, an archived one and the
    cross-run files."""
    orch = project / ".orch"
    st = S.RunState(
        run_id="20260101-000000",
        branch="orch/20260101-000000",
        phases=[S.Phase(n=1, slug="old")],
    )
    S.save(orch / "state.json", st)
    (orch / "plan.md").write_text("plan\n")
    (orch / "phases" / "01-old").mkdir()
    (orch / "phases" / "01-old" / "log.md").write_text("log\n")
    old = orch / "runs" / "20251201-000000"
    old.mkdir(parents=True)
    (old / "state.json").write_text("{}")
    (orch / "backlog.md").write_text("- old finding\n")
    (orch / ".cache").mkdir()
    (orch / ".gitignore").write_text(".cache/\n")


def test_legacy_run_files_block_until_migrated(project: Path, fc: FakeClaude) -> None:
    _legacy(project)
    with pytest.raises(ConfigError, match="older orch"):
        _run(project)


def test_migrate_moves_run_files_to_home(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _legacy(project)
    monkeypatch.chdir(project)
    res = runner.invoke(app, ["migrate"])
    assert res.exit_code == 0, res.output
    home = Repo(project).home
    run = home / "runs" / "20260101-000000"
    assert (
        (run / "state.json").is_file(),
        (run / "plan.md").read_text(),
        (run / "phases" / "01-old" / "log.md").read_text(),
        (home / "runs" / "20251201-000000" / "state.json").is_file(),
        (home / "backlog.md").read_text(),
    ) == (True, "plan\n", "log\n", True, "- old finding\n")
    left = sorted(
        str(p.relative_to(project / ".orch")) for p in (project / ".orch").rglob("*")
    )
    assert left == [
        "config.toml",
        "phases",
        "phases/01-p1",
        "phases/01-p1/brief.md",
    ], "the human's config and briefs stay"
    assert "git add -A .orch" in res.output
    assert runner.invoke(app, ["migrate"]).output.strip() == "nothing to migrate"


def test_migrate_in_repo_mode_moves_into_runs(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ORCH_HOME", "repo")
    _legacy(project)
    from orch.migrate import migrate  # noqa: PLC0415

    migrate(Repo(project))
    orch = project / ".orch"
    assert (
        (orch / "runs" / "20260101-000000" / "plan.md").is_file(),
        (orch / "backlog.md").is_file(),
        (orch / "state.json").exists(),
    ) == (True, True, False)


def test_migrate_refuses_to_overwrite_a_run(project: Path) -> None:
    _legacy(project)
    (Repo(project).runs / "20251201-000000").mkdir(parents=True)
    from orch.migrate import migrate  # noqa: PLC0415

    with pytest.raises(ConfigError, match="already there"):
        migrate(Repo(project))
    assert (project / ".orch" / "state.json").is_file(), "nothing moved"
