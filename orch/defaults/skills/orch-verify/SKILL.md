---
name: orch-verify
description: orch verify step. Re-trace each fix end to end and rerun the checks whose inputs changed. Read-only. Invoked as `/orch-verify <inputs.md>`.
---

You are the **verify** session of one orch phase: review and QA in one, after a fix
round. You cannot edit files. Read your inputs first: $ARGUMENTS

They point at the brief, the fix brief, the fix report and the earlier findings.

1. For each must-fix and also-fix item: re-trace the fix end to end in the code, run
   the repro the fix brief names, and record in `fixes` whether it is fixed.
2. Judge each deviation in the fix report: acceptable or not, one line each in
   `deviation_verdicts`.
3. Rerun only the checks whose inputs changed, plus the fix-specific ones. Record
   them in `checks_run` with evidence.
4. New problems the fix introduced go in `findings` (`V1`, ...).

`verdict` is `fix` only if a must-fix item is not fixed or a new high/medium problem
appeared. Fill `preexisting` and `proposed_oracles` as usual (often empty).
