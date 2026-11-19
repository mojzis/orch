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
  lead.py         lead steps (fresh session each, state in the phase docs): add checks, triage, learnings
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
| 2 | Minimal loop | fake claude: 3 phases finish; gate failing past cap → `blocked`; resume from fresh clone of pushed branch; diff checks | done; live 2-phase toy run ok ($1.88) |
| 3 | Review ‖ QA + verify | fake: both ship; fix then ship; rounds exhausted; malformed output → failed after one retry; two worktrees run at once; `log.md` | done; live full-scenario run ok ($2.77) |
| 4 | Lead session | fake: deviation → exactly one routed check; duplicates merge; do-NOT-fix stays unfixed; learnings + handoff written | done; live comparison with happytube 5b: triage matched, add_checks gained risk checks |
| 5 | `orch plan` + briefs | fake: uncovered requirement; already-passing QA check; brief naming missing file; paid-API phase w/o real-data; planner change needs reason; design question → `needs-user` | done; live plan→brief→run of a 2-requirement pitch ok ($8.89) |
| 6 | Stops, probe, report | SIGINT → paused + resume; usage limit → exit 75 + `--wait`; credentials → `needs-user`; probe step; run report | done (fake) |

All six phases were built in session 1. Remaining work:
[next-session.md](next-session.md) if it exists.

## Session log

- 2026-11-14: plan written; verified `claude` 2.1.325 supports every flag the spec uses
  (`--json-schema`, `--session-id`, `--permission-mode auto`, `--permission-prompts none`,
  `--tools`, `--effort`, `--max-budget-usd`, `--fallback-model`, `--add-dir`). JSON result
  carries `session_id`, `total_cost_usd`, `usage`, `structured_output`, `is_error`, `subtype`.
- 2026-11-14 (later): phases 1–6 built, each verified by an independent subagent; their
  findings fixed (classifier, worktree inputs, gate scanning .orch, resume of
  code-changing sessions, backlog). Live runs in scratch repos: small scenario 2 phases
  ($1.88); plan + briefs + 2 phases from a pitch ($8.89); full scenario with review ‖ QA
  and the lead ($2.77). The live runs found the gate flagging `__pycache__` (fixed with
  `dirty_ignore`).
- 2026-11-14 (end): phase 5–6 review findings fixed; live smoke run on the final code ok ($0.93). Handoff: docs/next-session.md.
- 2026-11-14 (session 2): fixed the open phase 5–6 review findings (see next-session.md item 2).
- 2026-11-14 (session 2): phase 4 live acceptance against happytube 5b ($4.03); add_checks gained risk checks. Personal skills shadowed orch's (fixed with the `orchskill-` prefix).
- 2026-11-15: run files moved out of the project repo into orch's home (`~/.orch`,
  `$ORCH_HOME`, user config `home`; `home = "repo"` keeps today's committed layout for
  web/CI). One central `sessions.jsonl`; the run branch carries code commits only;
  briefs are guarded by hash instead of the gate's commit check; `orch migrate`;
  `status`/`report --run`. Asked for after gerenuk#33 carried 37 `.orch` files.
- 2026-11-15: `open_pr` (default false): a done, pushed run opens its PR with `gh`
  (title from the pitch, body from the report, `Fixes #N` from `issue:` or a `Source:`
  issue link); an existing open PR is reused; a gh failure is a warning.
