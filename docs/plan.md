# orch — implementation plan

Source of truth for *what* orch is: [spec.md](spec.md) (copied from `ideas/ideas/orch.md`,
2026-11-14). This file says *how* it is built here and tracks progress. Update the
status column and the "Session log" as work lands.

## Architecture

```
orch/
  cli.py          typer router: init, run, plan, status, config, eject, guide
  errors.py       OrchError(+hint) and subclasses; caught once in cli
  claude.py       build_argv(StepConfig) + run_session() -> SessionResult
                  (session id, cost delta, usage, structured output, error kind)
  schemas.py      JSON schema for every step's report (owned by code, not templates)
  layers.py       resolve scenarios / skills / model / effort across
                  defaults -> user (~/.config/orch, $ORCH_CONFIG_HOME) -> repo (.orch/) -> task
  scenario.py     Scenario + Step dataclasses, `extends`, invariant checks
  store.py        atomic writes; state.json; PROGRESS.md rendered from state
  gitops.py       run branch, commit, push, detached worktrees, diff inspection
  gate.py         gate_cmd + "not evidence" diff checks (skip/xfail, deleted tests, QA.md)
  loop.py         phase runner: executes a scenario's steps, fix rounds, stop states
  lead.py         lead session (one per phase, resumed): add checks, triage, learnings
  planning.py     orch plan: outline + checks + outline review; per-phase brief + plan review
  report.py       run report
  defaults/       scenarios/*.toml, skills/orch-*/SKILL.md, guide/*.md
tests/
  fake_claude.py  fake `claude` executable driven by a JSON script (env ORCH_FAKE_SCRIPT)
```

Key choices (beyond the spec's decision table):

- The step vocabulary is fixed in code (`brief, dev, gate, add_checks, review_qa,
  review_combined, triage, fix, verify, probe, record`). A scenario picks which run and
  sets per-step template/tools/model/effort/extra. Loop semantics (fix rounds, parallel
  review‖QA) stay in code, so an edited scenario cannot break control flow.
- `claude` binary is `$ORCH_CLAUDE_BIN` or `claude`. Tests point it at the fake.
- Repo settings live in `.orch/config.toml` (`gate_cmd`, `setup_cmd`, `max_fix_rounds`,
  `remote`, `push`). 
- Before `orch plan` exists (build phase 5), phases are hand-written:
  `.orch/phases/NN-slug/brief.md`, discovered in order.
- Run branch: `orch/<run-id>`; orch never touches main and never merges.

## Build phases

Each phase: implement with tests → `uv run poe check` green → commit → independent
verification by a subagent (fresh eyes, given only the acceptance list) → fix → commit.

| # | Phase | Acceptance (from spec "Build order") | Status |
|---|---|---|---|
| 1 | Session wrapper, layers, state | unit tests: argv, parsing, missing structured output, each error kind, cost delta on resume, repo template beats user one; `orch config` shows provenance; live smoke test (marked `integration`) checks CLAUDE.md loaded and the resolved skill ran | done |
| 2 | Minimal loop | fake claude: 3 phases finish; gate failing past cap → `blocked`; resume from fresh clone of pushed branch; diff checks | todo |
| 3 | Review ‖ QA + verify | fake: both ship; fix then ship; rounds exhausted; malformed output → failed after one retry; two worktrees run at once; `log.md` | todo |
| 4 | Lead session | fake: deviation → exactly one routed check; duplicates merge; do-NOT-fix stays unfixed; learnings + handoff written | todo |
| 5 | `orch plan` + briefs | fake: uncovered requirement; already-passing QA check; brief naming missing file; paid-API phase w/o real-data; planner change needs reason; design question → `needs-user` | todo |
| 6 | Stops, probe, report | SIGINT → paused + resume; usage limit → exit 75 + `--wait`; credentials → `needs-user`; probe step; run report | todo |

Expectation: phases 1–4 this session; 5–6 may spill to the next. See
[next-session.md](next-session.md) if it exists.

## Session log

- 2026-11-14: plan written; verified `claude` 2.1.325 supports every flag the spec uses
  (`--json-schema`, `--session-id`, `--permission-mode auto`, `--permission-prompts none`,
  `--tools`, `--effort`, `--max-budget-usd`, `--fallback-model`, `--add-dir`). JSON result
  carries `session_id`, `total_cost_usd`, `usage`, `structured_output`, `is_error`, `subtype`.
