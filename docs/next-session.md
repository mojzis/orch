# Next session

State at the end of session 1 (2026-11-14), branch `build`: all six build phases of
[spec.md](spec.md) "Build order" are implemented. `uv run poe check` passes, with
155 tests running against the fake claude. Three independent verifier subagents
reviewed phases 1, 2–4 and 5–6, and all their high and medium findings are fixed
except those listed below. Live runs in scratch repos all ended `done` with correct
output:

| run | what it proved | cost |
| --- | --- | --- |
| small scenario, 2 hand-written phases | dev, gate, combined check, record and the run branch | $1.88 |
| pitch with R1/R2 → `orch run --auto-decide` | outline, outline review, briefs with plan review and a revision, 2 phases | $8.89 |
| full scenario, 1 phase | review ‖ QA in worktrees, the lead resumed across its steps, a deviation routed to QA, archive of the finished run | $2.77 |
| small scenario after the final fixes | regression smoke test on the final code; report says done | $0.93 |

Read first: [plan.md](plan.md) (architecture and status), then `uv run orch guide use`.

## Verify you are where this doc says

```
cd ~/git/orch && git switch build && uv sync && uv run poe check
uv run pytest -m integration -o addopts= tests/test_live.py   # live smoke, ~1 cent
```

## Left to do, in priority order

1. **Live acceptance not yet run** (the spec asks for these):
   - Phase 4: rerun one finished happytube phase's dev report through the lead
     (`orch-add-checks`, `orch-triage`) and compare its checks and fix brief with the
     hand-written ones in `~/git/happytube` (`docs/process-learnings.md`). This is the
     main risk the spec names: is the lead as good as the human orchestrator was?
   - Phase 6: one real run of a five-phase pitch. orch could build part of its own
     backlog, below, as that pitch.
2. **Open findings from the phase 5–6 review** (not fixed):
   - A credential failure reported by dev, QA or review (in `unresolved_issues` or a
     finding) does not pause the run. Only the probe's does.
   - Report gaps: tokens by phase (only by role now), plan sessions record no tokens,
     the "QA evidence" column is not tied to each requirement, replans print as raw
     dicts.
   - `planning._session` resolves outline roles without the pitch's
     `scenario`/`set`.
   - `auto_decide` stays set in state for the rest of the run once passed.
   - `archive_run` on the old run branch starts the new run on top of it (works, but
     there is no warning).
   - The SIGINT handler takes `_CHILDREN_LOCK` (`claude.interrupt_children`). It can
     deadlock if the signal lands while the main thread holds the lock. Make it an
     RLock, or snapshot without the lock.
3. **Spec items not built:**
   - The `spike` scenario (brief → dev on a throwaway branch → findings report). It
     needs a scenario kind that relaxes the "checked by another session" rule and
     skips the push of code.
   - QA.md tiers: orch does not parse `QA.md`. The slowest tier is the config key
     `full_qa_cmd`, and "diff against main" is not done at all. Decide on a QA.md
     format with the shape skill, or keep config keys.
   - The probe: the spec says "the lead eyeballs the output". Today the probe is a
     worker and only credential problems stop the run. Add a lead step that reads
     the probe report and can send the phase back to fix.
   - The real-data rule that data is committed only after verify is not enforced in
     code. It is only in the dev template.
   - An end-to-end fake test for "a planner change to a repo's default scenario or
     effort must carry a reason". Only `check_outline` has a unit test.
4. **Unverified assumption:** orch materializes the winning skill templates into
   `.orch/.cache/skills/.claude/skills/` and passes them with `--add-dir`. If the user
   has a personal `~/.claude/skills/orch-dev`, Claude Code may prefer that one. Test
   it live, and if it does, prefix the materialized names or warn in `orch config`.

## Spec open questions (still open)

- Does the shape skill write `docs/pitch.md` with requirement ids and a proposed
  oracle per core requirement? orch reads ids with the regex `\bR\d+\b`.
- The default for open design choices is to pause (`needs-user`). `--auto-decide`
  takes the recommended options and records them in the report's replans.
- The lead is one resumed session per phase, as the plan says. If its session is
  gone (another machine), orch starts a fresh lead session from the files.

## Deferred features (from the spec, unchanged)

The lead fixing doc nits after `ship`, a simplify pass, a live usage guard
(`rate_limit_event`), `/code-review` instead of the custom reviewer, and opening a PR
at the end. Each waits for the evidence named in spec.md.

## Notes for working on orch

- The step vocabulary and loop semantics are in `orch/loop.py`; a scenario only picks
  steps and per-role settings. The report schemas are in `orch/schemas.py`. The
  prompts are `orch/defaults/skills/orch-*/SKILL.md`.
- Every step's key in `done_steps` makes resume deterministic. When adding a step,
  give it a stable key and keep it out of `_reopen_current`'s truncation if it must
  survive a reopen.
- The fake claude (`tests/fake_claude.py`) takes per-template response queues. It
  passes unknown keys through to the JSON result (for example `api_error_status`)
  and can run actions (write, append, rm, commit, sh) in the session's cwd.
- The suite takes about 55 s because every loop test makes real git repos and
  worktrees.
