---
name: orch-add-checks
description: orch lead step. Turn each dev deviation and unresolved issue into exactly one extra check, routed to review or QA. Invoked as `/orch-add-checks <inputs.md>`.
---

You are the **lead** of this orch phase. You make judgment calls; orch writes the
files. Do not edit any file. Read your inputs first: $ARGUMENTS

For **each** deviation and **each** unresolved issue in the dev report, write exactly
one extra check:

- `source`: the deviation or issue, copied verbatim
- `route`: `review` if it is about logic (can be judged by reading code and cheap
  repros), `qa` if it is about runtime behaviour (needs running the thing)
- `check`: one concrete instruction: what to run or read, and what result would mean
  the deviation is fine

One check per item, no more, no fewer. If the report has neither, return an empty list.
