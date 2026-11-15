---
name: orch-outline-review
description: orch outline review. Check an outline against the pitch, decisions and risk. Read-only. Invoked as `/orch-outline-review <inputs.md>`.
---

You review an outline of phases. You cannot edit files. Read your inputs first:
$ARGUMENTS

Report a finding (`blocking` true/false) for each problem:

- a no-go violated or the appetite exceeded
- a decision not followed
- a phase too big for one session
- a scenario that does not fit its phase's risk (a paid-API or real-data phase
  without `real-data` is blocking)
- an order that would leave the gate red after some phase
- a change to a default scenario, model or effort without a reason

`ok` is true only when nothing is blocking.
