# Next session

State at the end of session 2 (2026-11-15), branch `build` (not merged to `main`, not
pushed). `uv run poe check` passes. Read [plan.md](plan.md) for architecture and
status, then `uv run orch guide use`.

## Is orch usable?

**Yes, for real work.** Every build phase of [spec.md](spec.md) works, and every live
run so far ended `done` with correct output (small, full and planned-from-pitch runs,
$1–9 each). What is left (below) is spec extras and one acceptance run, not missing
basics.

To use it on a real repo (for example happytube):

```
uv tool install --editable ~/git/orch      # once; puts `orch` on PATH
cd ~/git/<repo> && orch init               # then set gate_cmd / setup_cmd in .orch/config.toml, commit
# give it work: docs/pitch.md with R1, R2, ... (shape skill), or .orch/phases/01-<slug>/brief.md
orch run --pause                           # plan only; read .orch/plan.md
orch run                                   # run every phase; `orch status`, `orch report`
```

orch never merges. A finished run leaves an `orch/<run-id>` branch and `.orch/report.md`.

## Done in session 2

- Fixed all open findings from the phase 5–6 review. Dev, fix, review and QA now pause
  the run on `credential failure:`. The report shows tokens per phase, evidence tied
  to requirement ids, and replans as sentences. Plan sessions use the pitch's
  scenario and `set`. `--auto-decide` lasts one invocation. Archiving warns when the
  new run starts on the old run branch. The Ctrl+C handler uses an RLock.
- A personal `~/.claude/skills/<name>` was shown live to shadow orch's template.
  Templates are now materialized and invoked as `orchskill-<name>`.
- Phase 4 live acceptance against happytube 5b:
  [acceptance/phase4-lead-vs-human.md](acceptance/phase4-lead-vs-human.md). Triage
  matched the human. add_checks now also writes up to 5 `risk:` checks, and on a
  rerun they targeted the areas where the human's important findings came from.
  Not yet shown: that review/QA then find those bugs.

## Next, in priority order

1. **Use it on real work.** The best remaining test is a real phase in a real repo with
   the full scenario. Watch whether the new risk checks lead review/QA to real bugs.
2. **Phase 6 acceptance: one real five-phase pitch** (~$20–40). Candidate: orch builds
   item 3 below on itself. Needs a `docs/pitch.md` and the QA.md decision first.
3. **Spec items not built:**
   - **QA.md tiers: needs the user's decision.** Either define a QA.md format orch
     parses, or keep the config key `full_qa_cmd` and drop "diff against main".
   - The `spike` scenario (brief → dev on a throwaway branch → findings report). It
     relaxes "checked by another session" and does not push code.
   - The probe: a lead step that reads the probe report and can send the phase back
     to fix. Today only credential problems stop the run.
   - Enforce in code that real data is committed only after verify (today it is only
     in the dev template).
   - An end-to-end fake test for "a planner change to the default scenario or effort
     carries a reason" (only `check_outline` has a unit test).
4. Lower: Q5-style judgement. In the phase 4 run, triage declined a low finding that
   was a real bug. Watch for it before changing the prompt.

## Verify you are where this doc says

```
cd ~/git/orch && git switch build && uv sync && uv run poe check
uv run pytest -m integration -o addopts= tests/test_live.py   # live, ~2 cents
```

## Spec open questions (still open)

- Does the shape skill write `docs/pitch.md` with requirement ids and a proposed
  oracle per core requirement? orch reads ids with the regex `\bR\d+\b`.
- The default for open design choices is to pause (`needs-user`). `--auto-decide`
  takes the recommended options and records them in the report's replans.
- Each lead step is a fresh session that works from the phase files.

## Deferred features (from the spec, unchanged)

The lead fixing doc nits after `ship`, a simplify pass, a live usage guard
(`rate_limit_event`), `/code-review` instead of the custom reviewer, and opening a PR
at the end. Each waits for the evidence named in spec.md.

## Notes for working on orch

- The step vocabulary and loop semantics are in `orch/loop.py`; a scenario only picks
  steps and per-role settings. Report schemas: `orch/schemas.py`. Prompts:
  `orch/defaults/skills/orch-*/SKILL.md`. Code checks on the lead: `orch/lead.py`.
- Every step's key in `done_steps` makes resume deterministic. When adding a step,
  give it a stable key and keep it out of `_reopen_current`'s truncation if it must
  survive a reopen.
- The fake claude (`tests/fake_claude.py`) takes per-template response queues, strips
  the `orchskill-` prefix, passes unknown keys through to the JSON result, and can
  run actions (write, append, rm, commit, sh) in the session's cwd.
- `docs/acceptance/phase4-lead-compare.py` shows how to drive `lead()` directly on
  another repo's records. Reuse it for later comparisons.
- The suite takes about 45–60 s because loop tests make real git repos and worktrees.
