# Phase 4 acceptance: the lead vs the human orchestrator

Run 2026-11-14 (session 2) on happytube phase 5b. Script:
[phase4-lead-compare.py](phase4-lead-compare.py), which drives orch's own `lead()` on a
local clone at the dev's last commit (`6ba33af`). The inputs were the 5b brief
(`1-dev.md` + `context.md`), process learnings up to 5a, and dev/review/QA reports
rebuilt in orch's schemas from `docs/phases/phase-5b/log.md`. Cost: add_checks $0.69,
triage $2.41 (one resumed lead session).

## Triage vs `3-fix.md`: as good, one design better

| | human | lead |
| --- | --- | --- |
| must fix | Q1, R2, R3/Q2, R1/Q6, R4/Q3, Q4 | the same six, merged the same way |
| R1 deploy paths design | add 3 subpackages + pyproject/uv.lock | `src/happytube/**` + pyproject/uv.lock. Verify later flagged the human's list as incomplete (V1) and the human widened it to exactly this |
| also fix | R5, Q5, logs/, stale block | QP3 (refuse to push off main), stale block, logs/ |
| do not fix | QP2, QP3 (documented), first stages commit | Q5 ("by design") |
| defer | - | R5, QP2 |

Differences are on low-severity items. Q5 was declined, but the human's Q5 fix closed a
real bug (compact dir names read as dates, 5a V2). QP3 was promoted against a choice
the human had recorded as documented.

## add_checks vs "added after dev": narrower by design

The lead wrote exactly one check per deviation and unresolved issue (13), each
concrete (file, command, what "fine" means), often sharper than the human's. But the
human's checks were **risk-driven**, not deviation-driven, and the findings that
mattered came from the risk checks:

| human check | found | lead |
| --- | --- | --- |
| review 4: is the workflow `paths:` filter complete? | R1/Q6 (and V1 later) | no check |
| QA d: daily_run.sh in a throwaway repo with a bare remote and a stub `uv` | **Q1, the only medium** | routed daily_run to *review* (reading only) |
| QA c / review 3: routes, traversal, Host guard, static mount | Q4 | only no-store caching |
| review 1: join across date dirs, can a good video hide forever? | - | partly (latest-copy check) |

## Change made

`orch-add-checks` may now add up to `MAX_RISK_CHECKS` checks with `source:
"risk: <area>"` for risky parts of the diff that no deviation covers, and its prompt
names the routes the human used: side-effecting scripts (git, push, deploy, cron) go
to QA in a throwaway repo with stubs; CI/deploy config gets its triggers checked
against what changes the output; new HTTP routes get guard/traversal/method checks.
`check_add_checks` still demands exactly one check per deviation and issue.

Rerun live after the change ($0.93): 13 deviation checks plus 4 risk checks.
Three of them are the human's checks that found bugs:

- `qa` daily_run.sh stranded commit after a failed push, in a throwaway repo with a
  bare remote: it names Q1's exact failure ("if push_trusted_stages returns early
  on 'nothing to commit' and never pushes, that is a finding")
- `review` deploy.yml `paths:` against every module the export imports (R1/Q6, V1)
- `qa` GET **and HEAD** /player/videos.json with a stale static file, traversal,
  Host guard (Q4)

The fourth (`.gitignore` stage rules) is extra. Whether review and QA then find the
bugs is not tested here; the next live full-scenario run will show it.
