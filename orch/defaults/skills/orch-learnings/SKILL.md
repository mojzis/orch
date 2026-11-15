---
name: orch-learnings
description: orch lead step after a phase ships. Write phase learnings, process lessons and the handoff to the next brief. Invoked as `/orch-learnings <inputs.md>`.
---

You are the **lead** of this orch phase, which has just shipped. Do not edit files;
orch writes them from your answer. Read your inputs first: $ARGUMENTS

- `phase_learnings`: markdown, short: what was built, what went wrong and how it was
  caught, what the reports got right or wrong.
- `process_lessons`: zero or more one-line lessons about *the process* (briefs,
  checks, reviews) that later phases should apply. Do not repeat lessons already in
  the process learnings file named in your inputs.
- `handoff`: what the next phase's brief must know about the code as it is now:
  new modules and commands, changed contracts, known gaps, deferred findings that
  touch it.
