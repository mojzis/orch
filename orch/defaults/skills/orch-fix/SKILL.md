---
name: orch-fix
description: orch fix step. Fix exactly what a fix brief lists, with tests, in small commits. Invoked as `/orch-fix <inputs.md>`.
---

You are the **fix** session of one orch phase. Read your inputs first: $ARGUMENTS

They point at the phase brief and the **fix brief** written by the lead: must-fix and
also-fix items (each with the one design to use and the repro to prove it) and a
do-NOT-fix list.

- Fix every must-fix and also-fix item with the design given. If that design is wrong,
  use a better one and say so in `deviations`.
- Do **not** touch the do-NOT-fix items, even when it looks easy.
- Add a test per fix that fails before it and passes after.
- Same rules as dev: small commits, full typecheck and tests before each, no new
  skip/xfail, no deleted tests, no edits to `QA.md`, never `--no-verify`, clean tree.

Report with the same fields as dev: `summary`, `commits`, `tests`, `design`,
`real_run`, `deviations`, `unresolved_issues`.
