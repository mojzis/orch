---
name: orch-probe
description: orch probe step. A small real run on the final code of a phase that touches real data or paid APIs. Invoked as `/orch-probe <inputs.md>`.
---

You are the **probe** session of an orch phase that touches real data or paid APIs.
You cannot edit files. Read your inputs first: $ARGUMENTS

1. Follow the brief's **Real data and credentials** section: one-call probe first,
   stay inside the budget cap, commit nothing.
2. On a credential failure, stop at once and report it in `problems` with the word
   "credential". Never work around a bad key.
3. Run the smallest real run that exercises this phase's behaviour and look at the
   output yourself.

Report `ok`, `ran` (commands), `observations` (what the output looked like) and
`problems`.
