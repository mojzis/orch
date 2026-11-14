---
name: orch-add-checks
description: orch lead step. Turn each dev deviation and unresolved issue into exactly one extra check, routed to review or QA. Invoked as `/orch-add-checks <inputs.md>`.
---

You are the **lead** of this orch phase. You make judgment calls; orch writes the
files. Do not edit any file. Read your inputs first: $ARGUMENTS

For **each** deviation and **each** unresolved issue in the dev report (and in any
gate-repair reports the inputs list), write exactly one extra check. An item that
appears in more than one report gets one check:

- `source`: the deviation or issue, copied verbatim
- `route`: `review` if it is about logic (can be judged by reading code and cheap
  repros), `qa` if it is about runtime behaviour (needs running the thing)
- `check`: one concrete instruction: what to run or read, and what result would mean
  the deviation is fine

One check per item, no more, no fewer.

Then read the phase diff (`git diff <base>..HEAD`, base in the brief or inputs) and
add up to **5 risk checks** for risky changes that no deviation or issue covers and
that the brief's Functional QA does not already exercise. Their `source` is
`risk: <area>`. Where a human orchestrator found the bugs that mattered:

- a script with side effects (git commit/push, deploy, cron, file deletion): route
  to `qa`, run it in a throwaway repo with a bare local remote and stubbed commands,
  and walk the failure paths (nothing changed, the command fails, a retry next run)
- CI or deploy config: `review` its triggers and `paths:` filters against every
  file that changes the output
- a new HTTP route or static mount: `qa` the guard, path traversal, other methods
  (HEAD), and which handler wins when two match

No risky change left uncovered: add no risk checks. If there are no deviations,
issues or risks, return an empty list.
