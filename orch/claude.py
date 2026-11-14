"""Run one `claude -p` session and turn its JSON result into a SessionResult.

Every flag orch passes to Claude Code is built in `build_argv`, checked against
Claude Code 2.1.325.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

READ_ONLY_TOOLS = ("Read", "Grep", "Glob")
READ_BASH_TOOLS = ("Read", "Grep", "Glob", "Bash")


@dataclass(frozen=True)
class StepConfig:
    """How one step's session is started. Resolved across layers by `layers`."""

    step: str
    template: str
    tools: tuple[str, ...] | None = None  # None = all tools
    model: str | None = None
    effort: str | None = None
    max_budget_usd: float | None = None
    fallback_model: str | None = None
    extra: str = ""


@dataclass(frozen=True)
class SessionResult:
    session_id: str
    ok: bool
    error_kind: str | None  # usage_limit | auth | bad_output | other
    error: str
    structured: dict[str, Any] | None
    cost_total: float
    cost_delta: float
    usage: dict[str, Any] = field(default_factory=dict)
    reset_at: str | None = None  # ISO time a usage limit resets, when known
    raw: dict[str, Any] = field(default_factory=dict)


class Runner(Protocol):
    def __call__(
        self, argv: list[str], cwd: Path, env: dict[str, str]
    ) -> tuple[int, str, str]: ...


def claude_bin() -> str:
    return os.environ.get("ORCH_CLAUDE_BIN", "claude")


def build_prompt(cfg: StepConfig, args: str) -> str:
    prompt = f"/{cfg.template} {args}".rstrip()
    if cfg.extra:
        prompt += f"\n\nExtra instructions for this step:\n{cfg.extra}"
    return prompt


def build_argv(  # noqa: PLR0913 -- mirrors the claude flags one to one
    cfg: StepConfig,
    prompt: str,
    *,
    session_id: str,
    resume: bool = False,
    schema: dict[str, Any] | None = None,
    add_dirs: tuple[Path, ...] = (),
) -> list[str]:
    argv = [claude_bin(), "-p", prompt, "--output-format", "json"]
    argv += ["--resume", session_id] if resume else ["--session-id", session_id]
    argv += ["--permission-mode", "auto", "--permission-prompts", "none"]
    if schema is not None:
        argv += ["--json-schema", json.dumps(schema)]
    if cfg.tools is not None:
        argv += ["--tools", ",".join(cfg.tools)]
    if cfg.model:
        argv += ["--model", cfg.model]
    if cfg.effort:
        argv += ["--effort", cfg.effort]
    if cfg.max_budget_usd is not None:
        argv += ["--max-budget-usd", f"{cfg.max_budget_usd:g}"]
    if cfg.fallback_model:
        argv += ["--fallback-model", cfg.fallback_model]
    for d in add_dirs:
        argv += ["--add-dir", str(d)]
    return argv


def child_env() -> dict[str, str]:
    """Environment for a child session. In a cloud session the child would
    otherwise reuse the parent's session id."""
    env = dict(os.environ)
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    return env


def _subprocess_runner(
    argv: list[str], cwd: Path, env: dict[str, str]
) -> tuple[int, str, str]:
    proc = subprocess.run(  # noqa: S603 -- argv is built by build_argv
        argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


_run: Runner = _subprocess_runner

_USAGE_RE = re.compile(
    r"usage limit|limit reached|out of (extra )?usage|hit your limit|"
    r"weekly limit|5-hour limit|rate_limit",
    re.IGNORECASE,
)
_AUTH_RE = re.compile(
    r"invalid api key|authenticat|not logged in|/login|oauth token|"
    r"credential|unauthori[sz]ed|\b401\b",
    re.IGNORECASE,
)
_EPOCH_RE = re.compile(r"\|(\d{10})\b")
_ISO_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:\d{2})"
)


def classify_error(text: str) -> str:
    if _USAGE_RE.search(text):
        return "usage_limit"
    if _AUTH_RE.search(text):
        return "auth"
    return "other"


def parse_reset_at(text: str) -> str | None:
    """Reset time from a usage-limit message: `...|<epoch>` or an ISO timestamp."""
    if m := _EPOCH_RE.search(text):
        return datetime.fromtimestamp(int(m.group(1)), UTC).isoformat()
    if m := _ISO_RE.search(text):
        return m.group(0)
    return None


def new_session_id() -> str:
    return str(uuid.uuid4())


def parse_result(  # noqa: PLR0913 -- the inputs of one finished session
    returncode: int,
    stdout: str,
    stderr: str,
    *,
    session_id: str,
    want_structured: bool,
    prev_cost: float = 0.0,
) -> SessionResult:
    """Turn a finished `claude -p --output-format json` call into a result."""
    raw: dict[str, Any] = {}
    try:
        parsed = json.loads(stdout) if stdout.strip() else {}
        if isinstance(parsed, dict):
            raw = parsed
    except json.JSONDecodeError:
        raw = {}

    cost_total = float(raw.get("total_cost_usd") or 0.0)
    usage = raw.get("usage") or {}
    sid = str(raw.get("session_id") or session_id)
    message = str(raw.get("result") or "") + "\n" + stderr

    def fail(kind: str, error: str) -> SessionResult:
        return SessionResult(
            session_id=sid,
            ok=False,
            error_kind=kind,
            error=error.strip()[:2000],
            structured=None,
            cost_total=cost_total,
            cost_delta=max(cost_total - prev_cost, 0.0),
            usage=usage,
            reset_at=parse_reset_at(message) if kind == "usage_limit" else None,
            raw=raw,
        )

    if not raw:
        text = (stderr + "\n" + stdout).strip() or f"exit code {returncode}"
        kind = classify_error(text)
        return fail("bad_output" if kind == "other" and returncode == 0 else kind, text)
    if (
        returncode != 0
        or raw.get("is_error")
        or raw.get("subtype")
        not in (
            None,
            "success",
        )
    ):
        return fail(classify_error(message), message or f"exit code {returncode}")
    structured = raw.get("structured_output")
    if want_structured and not isinstance(structured, dict):
        return fail("bad_output", "session ended without structured_output")
    return SessionResult(
        session_id=sid,
        ok=True,
        error_kind=None,
        error="",
        structured=structured if isinstance(structured, dict) else None,
        cost_total=cost_total,
        cost_delta=max(cost_total - prev_cost, 0.0),
        usage=usage,
        raw=raw,
    )


def run_session(  # noqa: PLR0913 -- one call per session, all knobs explicit
    cfg: StepConfig,
    args: str,
    cwd: Path,
    *,
    schema: dict[str, Any] | None,
    session_id: str | None = None,
    resume: bool = False,
    prev_cost: float = 0.0,
    add_dirs: tuple[Path, ...] = (),
) -> SessionResult:
    """Start (or resume) one session and wait for its result."""
    sid = session_id or new_session_id()
    argv = build_argv(
        cfg,
        build_prompt(cfg, args),
        session_id=sid,
        resume=resume,
        schema=schema,
        add_dirs=add_dirs,
    )
    code, out, err = _run(argv, cwd, child_env())
    return parse_result(
        code,
        out,
        err,
        session_id=sid,
        want_structured=schema is not None,
        prev_cost=prev_cost,
    )


def claude_version() -> str:
    try:
        proc = subprocess.run(  # noqa: S603 -- fixed argv
            [claude_bin(), "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    return proc.stdout.strip() or "unknown"
