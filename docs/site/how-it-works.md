# How it works

```mermaid
flowchart TD
  P[Pitch + decisions] --> O[Outline, once]
  O --> OR[Outline review]
  OR --> B
  subgraph Each phase
    B[Brief + plan review] --> D[Dev]
    D --> G[Gate]
    G --> AC[Add checks]
    AC --> RQ[Review ‖ QA]
    RQ --> T[Triage]
    T -->|fix| F[Fix]
    F --> V[Verify]
    V -->|must-fix left| T
    T -->|ship| R[Probe, record, learnings, push]
    V -->|ship| R
  end
  R -->|next phase| B
  R --> E[Full QA tier + run report]
```

## Outline once, brief each phase

`orch plan` splits the pitch into an outline (`.orch/plan.md`): one row per phase with
a deliverable, the requirement ids it covers, its dependencies, a size, and a scenario.
Code then checks that every requirement is covered and that no phase depends on a later
one, and a read-only session reviews the outline.

Each phase gets its detailed brief only when it is about to run, written from the code
as it is then. That keeps every brief in step with the code, and lets later phases use
what earlier ones learned.

## The phase loop

| Step | Who | What |
| --- | --- | --- |
| Brief | lead, then plan review | Goal, acceptance criteria, functional QA, baseline |
| Dev | fresh session | Implements the brief with tests, in small commits |
| Gate | code, no model | `gate_cmd`, plus diff checks: no new skips, no deleted tests |
| Add checks | lead | Turns each deviation into one extra check |
| Review ‖ QA | two fresh sessions | In parallel, each in its own worktree |
| Triage | lead | Ship, or write a fix brief |
| Fix → Verify | fresh sessions | At most `max_fix_rounds` rounds, then `blocked` |
| Record | code and lead | Log, learnings, handoff, commit, push |

## Scenarios

A scenario lists the steps a phase runs. The planner picks one for each phase and gives
a reason:

| Scenario | Fits |
| --- | --- |
| `full` | The default, most phases |
| `small` | Low-risk phases: docs, config, one module |
| `ui` | Phases that change pages; QA drives a browser |
| `real-data` | Phases that touch real data or paid APIs |
| `spike` | An open question that later phases depend on |

Every step's prompt is a skill (`orch-dev`, `orch-review`, ...). Scenarios, templates,
models and effort can be overridden per user, per repo or per task; see
[Tune](reference/tune.md).

## Where things live

```
.orch/plan.md                     # outline
.orch/phases/03-dedupe/brief.md   # written just before the phase
.orch/phases/03-dedupe/log.md     # every step's JSON report, verbatim
.orch/process-learnings.md        # fed to every later brief
.orch/backlog.md                  # deferred findings
.orch/state.json                  # orch-owned; PROGRESS.md is rendered from it
.orch/report.md                   # the run report
```
