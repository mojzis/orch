# orch: use

| situation | command |
| --- | --- |
| start, or carry on after any stop | `orch run` |
| plan only, read it first | `orch plan` (or `orch run --pause`) |
| where is the run | `orch status`, `.orch/PROGRESS.md` |
| what will each step run, and why | `orch config` |
| scenarios and templates available | `orch config --scenarios` |
| change one step's prompt for this repo | `orch eject <template>` |
| report for the run so far | `orch report` |

## When `orch run` stops (exit code, state)

1. `3`, **needs-user**: read the reason. A design question: fill in each
   `answer:` in `.orch/phases/NN-*/questions.md`, then `orch run` (or
   `orch run --auto-decide` to take the recommended options). Credentials or
   budget: fix them, then `orch run`.
2. `75`, **paused** on a usage limit: `orch run` after the reset time
   (`orch status` shows it), or `orch run --wait`.
3. `130`, **paused** by Ctrl+C: `orch run` resumes the interrupted session.
4. `3`, **blocked**: fix rounds used up. Fix by hand on the run branch and commit,
   or edit the brief, then `orch run` reruns the phase from the gate.
5. `3`, **needs-replan**: edit `docs/pitch.md` or the outline, then
   `orch plan --from N`.
6. `3`, **failed**: a session crashed twice. Read `.orch/phases/NN-*/log.md`, then
   `orch run`.

Done looks like: exit 0, `status: done` in `.orch/PROGRESS.md`, a run report in
`.orch/report.md`, and the `orch/<run-id>` branch pushed. orch never merges.

Do not:

- edit `.orch/state.json` by hand.
- commit to the run branch with `--no-verify` to get past the gate.

next: `orch status`
