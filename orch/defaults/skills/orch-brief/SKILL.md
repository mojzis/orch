---
name: orch-brief
description: orch lead step before a phase. Write the phase brief from the pitch, the outline row and the code as it is now. Invoked as `/orch-brief <inputs.md>`.
---

You are the **lead** of this orch phase. Do not edit files; orch writes the brief from
your answer. Read your inputs first: $ARGUMENTS

If the inputs list "Plan review findings to fix", revise your current brief to fix
exactly those, and return the whole revised brief.

They point at `docs/pitch.md`, `docs/decisions/`, `docs/architecture.md`, `QA.md`,
the outline row for this phase, the earlier phases' handoffs and
`.orch/process-learnings.md`. Read the code as it is **now**: every file, module and
command you name must exist or be marked *(new)*.

If the code turns out different from what the outline assumed and another scenario
fits better, set `scenario` (one of full, small, ui, real-data) and
`scenario_reason`; orch records it as a replan. Otherwise leave both empty.

If this phase has design choices the pitch and decisions leave open, return them in
`questions` (each with options and a recommended one) and leave `brief` empty: orch
asks the user once, in one batch, then calls you again with the answers. Settle
everything else yourself under "Settled by orch".

The `brief` is markdown with these sections, in order:

- **Goal**: one or two sentences, user-visible
- **Covers**: requirement ids
- **Settled with you**: answers to this phase's design questions, if any
- **Settled by orch**: defaults dev may argue against, plus: "If a settled fact or
  rule turns out wrong, flag it in `deviations`, don't silently switch."
- **Failure scenarios**: one item fails, a quota runs out mid-run, a rerun the next
  day, malformed input (the ones that apply)
- **Safety rules**: when the phase writes files, serves pages or handles keys
- **Real data and credentials**: one-call probe before any batch, a budget cap, stop
  and report on a credential failure, commit nothing from a scratch run
- **Acceptance**: numbered, observable criteria `A1`, `A2`, ...
- **Functional QA**: per criterion a command, a reference case, an invariant or a
  bound with units, each `confirmed` or `proposed`, with at least one negative
  control. A browser check for UI phases. A check for new behaviour must fail today
- **Tests to write**
- **Baseline**: test count, coverage and check status right now (run them)
