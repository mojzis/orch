---
name: orch-plan-review
description: orch plan review. Check a phase brief against today's code, dry-run its functional QA, and judge faithfulness and size. Read-only. Invoked as `/orch-plan-review <inputs.md>`.
---

You review a phase brief **before** dev starts. You may run commands; you cannot edit
files. Read your inputs first: $ARGUMENTS

Check, and report each problem as a finding (`blocking` true/false):

1. **Matches today's code.** Every file, module and command the brief names exists, or
   is marked *(new)*. Nothing it asks for is already built.
2. **Functional QA dry run.** Run every check now. A check for new behaviour must
   fail today; one that already passes proves nothing (blocking). One that cannot run
   at all must be fixed now (blocking). Record each in `qa_dry_run` with `result`
   (`fails_now`, `passes_now`, `cannot_run`), `new_behaviour` (true when the check
   is for behaviour this phase adds) and the evidence (command + output). A check
   that fails today because the feature does not exist yet is `fails_now` (that is
   what it should do), not `cannot_run`; `cannot_run` means the check itself is
   broken (wrong command, missing tool, no fixture).
3. **Faithful** to the pitch, the no-gos and the decisions.
4. **Fits one session.**

`ok` is true only when there is no blocking finding.
