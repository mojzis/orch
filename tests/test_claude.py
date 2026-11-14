from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

from orch import claude
from orch.claude import StepConfig, build_argv, parse_result, run_session

CFG = StepConfig(step="dev", template="orch-dev")


def _ok(**extra: object) -> str:
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "session_id": "s1",
            "total_cost_usd": 0.5,
            "usage": {"input_tokens": 1},
            "result": "{}",
        }
        | extra
    )


def test_argv_starts_a_new_session_unattended() -> None:
    argv = build_argv(CFG, "/orch-dev x", session_id="abc")
    assert argv[1:5] == ["-p", "/orch-dev x", "--output-format", "json"]
    assert argv[argv.index("--session-id") + 1] == "abc"
    assert "--resume" not in argv
    assert argv[argv.index("--permission-mode") + 1] == "auto"
    assert argv[argv.index("--permission-prompts") + 1] == "none"
    assert "--tools" not in argv  # all tools
    assert "--bare" not in argv  # CLAUDE.md and hooks must load


def test_argv_resume_uses_resume_not_session_id() -> None:
    argv = build_argv(CFG, "p", session_id="abc", resume=True)
    assert argv[argv.index("--resume") + 1] == "abc"
    assert "--session-id" not in argv


def test_argv_carries_role_settings(tmp_path: Path) -> None:
    cfg = StepConfig(
        step="review",
        template="orch-review",
        tools=("Read", "Grep", "Glob"),
        model="opus",
        effort="high",
        max_budget_usd=25.0,
        fallback_model="sonnet",
    )
    argv = build_argv(
        cfg, "p", session_id="s", schema={"type": "object"}, add_dirs=(tmp_path,)
    )
    pairs = dict(itertools.pairwise(argv))
    flags = {k: pairs[k] for k in pairs if k.startswith("--") and k != "-p"}
    assert {
        k: flags[k]
        for k in (
            "--tools",
            "--model",
            "--effort",
            "--max-budget-usd",
            "--fallback-model",
            "--add-dir",
            "--json-schema",
        )
    } == {
        "--tools": "Read,Grep,Glob",
        "--model": "opus",
        "--effort": "high",
        "--max-budget-usd": "25",
        "--fallback-model": "sonnet",
        "--add-dir": str(tmp_path),
        "--json-schema": json.dumps({"type": "object"}),
    }


def test_prompt_invokes_the_skill_with_args_and_extra() -> None:
    cfg = StepConfig(step="qa", template="orch-qa", extra="open the CSV export")
    prompt = claude.build_prompt(cfg, "inputs.md")
    assert prompt.startswith(f"/{claude.SKILL_PREFIX}orch-qa inputs.md")
    assert prompt.endswith("open the CSV export")


def test_parse_success_with_structured_output() -> None:
    r = parse_result(
        0, _ok(structured_output={"a": 1}), "", session_id="s1", want_structured=True
    )
    assert r.ok
    assert r.structured == {"a": 1}
    assert r.cost_total == 0.5
    assert r.cost_delta == 0.5


def test_missing_structured_output_is_a_failed_step() -> None:
    r = parse_result(0, _ok(), "", session_id="s1", want_structured=True)
    assert not r.ok
    assert r.error_kind == "bad_output"


def test_cost_delta_on_resume_subtracts_previous_total() -> None:
    r = parse_result(
        0,
        _ok(total_cost_usd=1.25, structured_output={}),
        "",
        session_id="s1",
        want_structured=True,
        prev_cost=0.5,
    )
    assert r.cost_total == 1.25
    assert r.cost_delta == pytest.approx(0.75)


def test_usage_limit_with_epoch_reset() -> None:
    out = _ok(
        is_error=True,
        terminal_reason="api_error",
        result="Claude AI usage limit reached|1763150400",
    )
    r = parse_result(1, out, "", session_id="s1", want_structured=True)
    assert r.error_kind == "usage_limit"
    assert r.reset_at == "2025-11-14T20:00:00+00:00"


def test_usage_limit_with_iso_reset() -> None:
    out = _ok(
        is_error=True, result="You've hit your limit · resets 2026-11-14T20:00:00Z"
    )
    r = parse_result(1, out, "", session_id="s1", want_structured=True)
    assert r.error_kind == "usage_limit"
    assert r.reset_at == "2026-11-14T20:00:00Z"


def _kind(code: int = 1, **raw: object) -> str | None:
    out = _ok(**raw)
    return parse_result(code, out, "", session_id="s", want_structured=True).error_kind


def test_not_logged_in_is_auth() -> None:  # shape of real claude 2.1.325 output
    kind = _kind(
        is_error=True,
        result="Not logged in · Please run /login",
        terminal_reason="api_error",
        api_error_status=None,
    )
    assert kind == "auth"


def test_bad_key_is_auth_from_structured_fields() -> None:
    kind = _kind(
        is_error=True,
        result="x",
        api_error_status=401,
        api_error_type="authentication_error",
        terminal_reason="api_error",
    )
    assert kind == "auth"


def test_model_text_mentioning_login_is_not_auth() -> None:
    kind = _kind(
        is_error=True,
        subtype="error_max_turns",
        num_turns=40,
        result="Implemented the /login route; 401 tests pass",
    )
    assert kind == "other"


def test_budget_exhausted_is_its_own_kind() -> None:  # real shape: exit 0, result null
    out = _ok(
        is_error=True,
        subtype="error_max_budget_usd",
        result=None,
        terminal_reason="budget_exhausted",
        errors=["Reached maximum budget ($1)"],
    )
    r = parse_result(0, out, "", session_id="s", want_structured=True)
    assert (r.error_kind, r.error) == (
        "budget",
        "error_max_budget_usd | budget_exhausted | Reached maximum budget ($1)",
    )


def test_api_429_is_retryable_not_a_usage_limit() -> None:
    kind = _kind(
        is_error=True,
        result="rate limited",
        api_error_status=429,
        api_error_type="rate_limit_error",
        terminal_reason="api_error",
    )
    assert kind == "other"


def test_other_error_and_non_json_output() -> None:
    r = parse_result(1, "Segmentation fault", "", session_id="s1", want_structured=True)
    assert (r.ok, r.error_kind) == (False, "other")
    r = parse_result(0, "not json", "", session_id="s1", want_structured=True)
    assert r.error_kind == "bad_output"


def test_auth_error_on_stderr_without_json() -> None:
    r = parse_result(
        1, "", "Error: not logged in", session_id="s", want_structured=False
    )
    assert r.error_kind == "auth"


def test_run_session_clears_parent_session_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "parent")
    seen: dict[str, object] = {}

    def runner(argv: list[str], cwd: Path, env: dict[str, str]) -> tuple[int, str, str]:
        seen["argv"], seen["env"] = argv, env
        return 0, _ok(session_id=argv[argv.index("--session-id") + 1]), ""

    monkeypatch.setattr(claude, "_run", runner)
    r = run_session(CFG, "x", Path(), schema=None, session_id="mine")
    assert r.ok
    assert r.session_id == "mine"
    env = seen["env"]
    assert isinstance(env, dict)
    assert "CLAUDE_CODE_SESSION_ID" not in env


def test_run_session_against_fake_binary(fake_claude, tmp_path: Path) -> None:
    fake_claude.on("orch-dev", {"structured": {"x": 1}, "cost": 0.2})
    r = run_session(CFG, "a.md", tmp_path, schema={"type": "object"})
    assert r.ok
    assert r.structured == {"x": 1}
    r2 = run_session(
        CFG,
        "a.md",
        tmp_path,
        schema={"type": "object"},
        session_id=r.session_id,
        resume=True,
        prev_cost=r.cost_total,
    )
    assert r2.cost_delta == pytest.approx(0.2)
    assert fake_claude.calls()[1]["resume"] is True


def test_claude_version_from_fake(fake_claude) -> None:
    assert claude.claude_version() == "9.9.9 (fake claude)"
