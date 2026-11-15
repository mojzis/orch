"""`open_pr = true`: orch opens the run's PR with gh once the run is done."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from orch import planning, pr
from orch import state as S
from orch.repo import Repo

from .conftest import FakeClaude, git
from .helpers import LEARN, checker, dev, make_project, run_dir

PITCH = """---
title: Add the thing
scenario: small
---
# Heading that loses to the title

Source: https://github.com/{repo}/issues/31
"""
FAKE_GH = """
import json, os, sys
log = os.environ["FAKE_GH_LOG"]
with open(log, "a") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[1:3] == ["pr", "view"]:
    existing = os.environ.get("FAKE_GH_EXISTING")
    if existing:
        print("OPEN " + existing)
        sys.exit(0)
    print("no pull requests found", file=sys.stderr)
    sys.exit(1)
if os.environ.get("FAKE_GH_FAIL"):
    print("not logged in", file=sys.stderr)
    sys.exit(4)
print("fake-pr-7")
"""


@pytest.fixture
def gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    script = tmp_path / "fake_gh.py"
    script.write_text(FAKE_GH)
    binary = tmp_path / "gh"
    binary.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
    binary.chmod(0o755)
    log = tmp_path / "gh.jsonl"
    monkeypatch.setenv("ORCH_GH_BIN", str(binary))
    monkeypatch.setenv("FAKE_GH_LOG", str(log))
    return log


def _calls(log: Path) -> list[list[str]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines()]


@pytest.fixture
def project(tmp_path: Path) -> Path:
    repo = make_project(tmp_path, phases=1, config="open_pr = true\n")
    (repo / "docs").mkdir()
    (repo / "docs" / "pitch.md").write_text(PITCH.format(repo="mojzis/other"))
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "pitch")
    return repo


@pytest.fixture
def fc(fake_claude: FakeClaude) -> FakeClaude:
    fake_claude.on("orch-dev", dev("work.txt"))
    fake_claude.on("orch-combined", {"structured": checker()})
    fake_claude.on("orch-learnings", {"structured": LEARN})
    return fake_claude


def _run(project: Path) -> S.RunState:
    st = planning.run(Repo(project), scenario="small")
    assert st.status == S.DONE, st.stop_reason
    return st


def test_done_run_opens_its_pr(project: Path, fc: FakeClaude, gh: Path) -> None:
    st = _run(project)
    create = next(c for c in _calls(gh) if c[:2] == ["pr", "create"])
    opt = dict(zip(create[2::2], create[3::2], strict=True))
    assert (opt["--head"], opt["--title"]) == (st.branch, "Add the thing")
    assert opt["--body"].startswith("Fixes mojzis/other#31\n\n# Run report")
    report = (run_dir(project) / "report.md").read_text()
    assert (st.pr_url, "PR: fake-pr-7" in report) == (
        "fake-pr-7",
        True,
    )


def test_existing_pr_is_reused(
    project: Path, fc: FakeClaude, gh: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_GH_EXISTING", "fake-pr-3")
    st = _run(project)
    assert st.pr_url == "fake-pr-3"
    assert not [c for c in _calls(gh) if c[:2] == ["pr", "create"]]


def test_failing_gh_is_a_warning_not_a_failed_run(
    project: Path,
    fc: FakeClaude,
    gh: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("FAKE_GH_FAIL", "1")
    st = _run(project)
    assert (st.pr_url, "not logged in" in capsys.readouterr().err) == ("", True)


def test_off_by_default(tmp_path: Path, fc: FakeClaude, gh: Path) -> None:
    project = make_project(tmp_path, phases=1)
    _run(project)
    assert _calls(gh) == []


@pytest.mark.parametrize(
    ("frontmatter", "body", "expected"),
    [
        ({"issue": 12}, "", "Fixes #12"),
        ({"issue": "#12"}, "", "Fixes #12"),
        ({}, "Source: https://github.com/team/proj/issues/31\n", "Fixes #31"),
        ({}, "source: <https://github.com/x/y/issues/5>\n", "Fixes x/y#5"),
        ({}, "See https://github.com/team/proj/issues/31\n", ""),
    ],
)
def test_fixes_line(
    tmp_path: Path, frontmatter: dict, body: str, expected: str
) -> None:
    git(tmp_path, "init", "-q")
    git(tmp_path, "remote", "add", "origin", "git@github.com:team/proj.git")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "pitch.md").write_text(body)
    assert pr.fixes(tmp_path, "origin", frontmatter) == expected


def test_title_falls_back_to_heading_then_run(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    without = pr.title(tmp_path, {}, "orch run r1")
    (tmp_path / "docs" / "pitch.md").write_text("intro\n\n# The heading\n")
    assert (pr.title(tmp_path, {}, "orch run r1"), without) == (
        "The heading",
        "orch run r1",
    )


def test_long_report_is_truncated() -> None:
    text = pr.body("x" * (pr.BODY_MAX + 10), "Fixes #1")
    assert len(text) < pr.BODY_MAX + 100
    assert text.endswith("(report truncated; see report.md)\n")
