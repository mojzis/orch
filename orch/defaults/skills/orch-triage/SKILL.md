---
name: orch-triage
description: orch lead step. Turn review and QA findings into a decision and a fix brief. Invoked as `/orch-triage <inputs.md>`.
---

You are the **lead** of this orch phase. Do not edit any file; orch writes the fix
brief from your answer. Read your inputs first: $ARGUMENTS

They hold the review and QA (or verify) reports for this round.

1. If every report says `ship` and there is nothing worth fixing now, `decision` is
   `ship` and the lists stay empty, except `defer` for findings worth keeping.
2. Otherwise `decision` is `fix`, and:
   - **merge duplicates**: one item per real problem, with all its ids
     (`["R2", "Q1"]`)
   - `must_fix`: problems that must be fixed before the next phase. Pick **one**
     design per item and name the repro verify should use
   - `also_fix`: preexisting problems worth promoting now (a security issue, a
     one-liner in code this phase touched)
   - `do_not_fix`: findings you reject, each with a reason (false positive, out of
     scope, contradicts a decision)
   - `defer`: real but not now; orch moves these to the backlog
3. Every finding id from the reports appears in exactly one list.
