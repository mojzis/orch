"""The gate: an opaque command plus diff checks for what is not evidence."""

from __future__ import annotations

import fnmatch
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from orch import gitops

SKIP_RE = re.compile(
    r"@pytest\.mark\.(skip|skipif|xfail)\b|\bpytest\.(skip|xfail)\(|"
    r"@unittest\.(skip|expectedFailure)|\b(it|test|describe)\.(skip|todo)\(|\bxit\(",
)
TEST_FN_RE = re.compile(r"^\s*(?:async\s+)?def\s+(test_\w+)")
OUTPUT_TAIL = 4000


@dataclass
class GateResult:
    ok: bool
    cmd: str
    returncode: int | None
    output: str
    violations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> str:
        parts = []
        if self.returncode not in (0, None):
            parts.append(f"`{self.cmd}` exited {self.returncode}")
        parts += self.violations
        return "; ".join(parts) or "ok"


def _matches(path: str, globs: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, g) for g in globs)


def diff_violations(
    cwd: Path,
    base: str,
    test_globs: list[str],
    protected: list[str],
    dirty: list[str] | None = None,
) -> list[str]:
    """The not-evidence checks: a new skip/xfail, a deleted test, a session's edit
    to QA.md or another protected file (orch's config, briefs, templates), and
    uncommitted changes."""
    out: list[str] = []
    for status, path in gitops.name_status(cwd, base):
        if status == "D" and _matches(path, test_globs):
            out.append(f"deleted test file {path}")
    for path in sorted(gitops.files_in_session_commits(cwd, base)):
        if _matches(path, protected):
            out.append(f"edited protected file {path} (only humans change it)")
    for path, line in gitops.added_lines(cwd, base):
        if SKIP_RE.search(line):
            out.append(f"new skip/xfail in {path}: {line.strip()[:120]}")
    removed = _test_names(gitops.removed_lines(cwd, base), test_globs)
    added = _test_names(gitops.added_lines(cwd, base), test_globs)
    out += [f"deleted test {name}" for name in sorted(removed - added)]
    if dirty is None:
        dirty = gitops.dirty_files(cwd)
    dirty = [p for p in dirty if not p.startswith(".orch/")]
    if dirty:
        out.append(f"uncommitted changes: {', '.join(dirty[:10])}")
    return out


def _test_names(lines: list[tuple[str, str]], test_globs: list[str]) -> set[str]:
    names = set()
    for path, line in lines:
        if not _matches(path, test_globs):
            continue
        if m := TEST_FN_RE.match(line):
            names.add(m.group(1))  # by name: a test moved to another file is kept
    return names


@dataclass(frozen=True)
class GateSpec:
    """What the gate runs and protects, from the repo's config."""

    cmd: str
    test_globs: tuple[str, ...]
    protected: tuple[str, ...]
    timeout_s: int = 1800
    dirty_ignore: tuple[str, ...] = ()

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> GateSpec:
        return cls(
            cmd=str(config.get("gate_cmd") or ""),
            test_globs=tuple(config.get("test_globs") or ()),
            protected=tuple(config.get("protected") or ()),
            timeout_s=int(config.get("gate_timeout_s") or 1800),
            dirty_ignore=tuple(config.get("dirty_ignore") or ()),
        )


def run_gate(cwd: Path, spec: GateSpec, base: str) -> GateResult:
    gate_cmd, timeout_s = spec.cmd, spec.timeout_s
    dirty = [  # before the command makes its own files
        p for p in gitops.dirty_files(cwd) if not _matches(p, list(spec.dirty_ignore))
    ]
    code: int | None = None
    output = ""
    if gate_cmd:
        try:
            proc = subprocess.run(  # noqa: S602 -- gate_cmd is the repo's own config
                gate_cmd,
                shell=True,
                cwd=cwd,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_s,
                start_new_session=True,  # Ctrl+C pauses orch, not the gate
            )
            code = proc.returncode
            output = (proc.stdout + proc.stderr)[-OUTPUT_TAIL:]
        except subprocess.TimeoutExpired:
            code, output = -1, f"timed out after {timeout_s}s"
    violations = diff_violations(
        cwd, base, list(spec.test_globs), list(spec.protected), dirty
    )
    return GateResult(
        ok=code in (0, None) and not violations,
        cmd=gate_cmd,
        returncode=code,
        output=output,
        violations=violations,
    )
