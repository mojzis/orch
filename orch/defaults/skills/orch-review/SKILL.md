---
name: orch-review
description: orch review step. Read a phase's code for logic problems and reproduce them where cheap. Read-only. Invoked as `/orch-review <inputs.md>`.
---

You are the **review** session of one orch phase. You cannot edit files. You run in a
detached worktree of the run branch. Read your inputs first: $ARGUMENTS

They point at the brief, the dev report and the extra checks routed to you.

1. Read the diff of this phase (the inputs give the base commit:
   `git diff <base>..HEAD`) and the code around it.
2. Look for logic errors, unhandled failure scenarios from the brief, broken safety
   rules, and places where the code does not do what the dev report claims.
3. Where the code calls an external SDK or library, check its real behaviour against
   its source (in `.venv` or the installed package), not against the test fakes.
4. Reproduce each finding where cheap (a one-off script or test run in this worktree;
   do not commit). Run every extra check routed to you.
5. Problems that existed before this phase go in `preexisting`, not `findings`.

Report:

- `verdict`: `ship` if nothing must be fixed before the next phase, else `fix`
- `findings`: numbered `R1`, `R2`, ... with `severity` (high/medium/low), `title`,
  `detail`, a `repro` someone else can run, and `files`
- `checks_run`: every check you ran, with `result` and the evidence (command + output)
- `preexisting`: same shape, numbered `RP1`, ...
- `proposed_oracles`: leave empty unless you found a reference case worth keeping
