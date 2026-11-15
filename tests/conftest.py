from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from typer_agentic import DEFAULT_AGENT_ENV_VARS

# orch.ui picks agent mode and console width at import, from the environment. Pin both
# before any test imports orch, so a run under Claude Code (agent mode, width 200)
# renders the same as CI (human mode, Rich's 80-column fallback truncates tables).
for _var in DEFAULT_AGENT_ENV_VARS:
    os.environ.pop(_var, None)
os.environ["COLUMNS"] = "200"

FAKE = Path(__file__).with_name("fake_claude.py")


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No test touches the real user config, a real claude or the real repo."""
    monkeypatch.setenv("ORCH_CONFIG_HOME", str(tmp_path / "user-config"))
    monkeypatch.setenv("ORCH_HOME", str(tmp_path / "orch-home"))
    monkeypatch.setenv("_TYPER_FORCE_DISABLE_TERMINAL", "1")
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.setenv("GIT_AUTHOR_NAME", "t")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "t")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@example.com")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


class FakeClaude:
    """Writes the script the fake `claude` reads and exposes its call log."""

    def __init__(self, root: Path) -> None:
        self.script = root / "fake-script.json"
        self.bin = root / "claude"
        self.bin.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n')
        self.bin.chmod(0o755)
        self.responses: dict[str, list[dict[str, Any]]] = {}
        self.save()

    def on(self, template: str, *responses: dict[str, Any]) -> FakeClaude:
        self.responses[template] = list(responses)
        self.save()
        return self

    def save(self) -> None:
        self.script.write_text(json.dumps({"responses": self.responses}))

    def calls(self, template: str | None = None) -> list[dict[str, Any]]:
        log = self.script.with_suffix(".calls.jsonl")
        if not log.exists():
            return []
        rows = [json.loads(line) for line in log.read_text().splitlines()]
        return [r for r in rows if template is None or r["template"] == template]


@pytest.fixture
def fake_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeClaude:
    root = tmp_path / "fake"
    root.mkdir()
    fc = FakeClaude(root)
    monkeypatch.setenv("ORCH_CLAUDE_BIN", str(fc.bin))
    monkeypatch.setenv("ORCH_FAKE_SCRIPT", str(fc.script))
    return fc
