"""Opening the run's pull request (`open_pr = true`): title from the pitch, body
from the run report, `Fixes #N` when the pitch names its source issue. orch
never merges."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

from orch.errors import GitError
from orch.home import repo_key

BODY_MAX = 60_000  # GitHub caps a PR body at 65536 characters
ISSUE_RE = re.compile(
    r"^\s*source:\s*<?https://github\.com/([\w.-]+/[\w.-]+)/issues/(\d+)",
    re.I | re.M,
)


def gh_bin() -> str:
    return os.environ.get("ORCH_GH_BIN", "gh")


def title(root: Path, pitch: dict[str, Any], fallback: str) -> str:
    """The pitch's `title:`, else its first heading, else `fallback`."""
    if t := str(pitch.get("title") or "").strip():
        return t
    p = root / "docs" / "pitch.md"
    text = p.read_text(encoding="utf-8") if p.is_file() else ""
    m = re.search(r"^# (.+)$", text, re.M)
    return m.group(1).strip() if m else fallback


def fixes(root: Path, remote: str, pitch: dict[str, Any]) -> str:
    """`Fixes #N` for the pitch's `issue:` or its `Source:` issue link
    (`Fixes owner/repo#N` for another repo's issue), else ""."""
    if issue := str(pitch.get("issue") or "").strip().lstrip("#"):
        return f"Fixes #{issue}" if issue.isdigit() else ""
    p = root / "docs" / "pitch.md"
    m = ISSUE_RE.search(p.read_text(encoding="utf-8")) if p.is_file() else None
    if not m:
        return ""
    same = m.group(1).lower() == repo_key(root, remote).lower()
    return f"Fixes #{m.group(2)}" if same else f"Fixes {m.group(1)}#{m.group(2)}"


def body(report: str, fixes_line: str) -> str:
    text = f"{fixes_line}\n\n{report}" if fixes_line else report
    if len(text) > BODY_MAX:
        text = text[:BODY_MAX] + "\n\n(report truncated; see report.md)\n"
    return text


def _gh(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # noqa: S603 -- fixed gh argv
            [gh_bin(), *args], cwd=cwd, capture_output=True, text=True, check=False
        )
    except OSError as e:
        raise GitError(f"gh failed: {e}") from None


def open_pr(cwd: Path, branch: str, pr_title: str, pr_body: str) -> str:
    """The URL of the branch's open PR, creating it if there is none."""
    found = _gh(
        cwd, "pr", "view", branch, "--json", "url,state", "-q", '.state+" "+.url'
    )
    if found.returncode == 0 and found.stdout.startswith("OPEN "):
        return found.stdout.split(" ", 1)[1].strip()
    made = _gh(
        cwd, "pr", "create", "--head", branch, "--title", pr_title, "--body", pr_body
    )
    if made.returncode != 0:
        raise GitError(f"gh pr create failed: {(made.stderr or made.stdout).strip()}")
    return made.stdout.strip().splitlines()[-1]
