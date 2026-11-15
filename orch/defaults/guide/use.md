# orch: use

| situation | command |
| --- | --- |
| start, or carry on after any stop | `orch run` |
| plan only, read it first | `orch plan` (or `orch run --pause`) |
| where is the run, where are its files | `orch status` (`--run <id>` for another run) |
| what will each step run, and why | `orch config` |
| scenarios and templates available | `orch config --scenarios` |
| change one step's prompt for this repo | `orch eject <template>` |
| report for the run so far | `orch report` |
| run files left in `.orch/` by an older orch | `orch migrate` |

## Where the run files are

Plan, briefs, reports, `state.json` and `PROGRESS.md` live in orch's home, not in
the repo: `~/.orch/repos/<owner>/<repo>/runs/<run-id>/` (`orch status` prints the
path; `<run>` below). Backlog and process learnings are per repo, one level up.
The run branch carries code commits only. `home = "repo"` (see `orch guide tune`)
keeps them in `.orch/runs/<run-id>/` and commits them, for machines whose home
does not last.

## When `orch run` stops (exit code, state)

1. `3`, **needs-user**: read the reason. A design question: fill in each
   `answer:` in `<run>/phases/NN-*/questions.md`, then `orch run` (or
   `orch run --auto-decide` to take the recommended options; the flag lasts one
   invocation). Credentials (from the probe, or a dev, fix, review or QA session
   that reported `credential failure:`) or budget: fix them, then `orch run`. A
   dev or fix session resumes where it stopped; a check runs again.
2. `75`, **paused** on a usage limit: `orch run` after the reset time
   (`orch status` shows it), or `orch run --wait`.
3. `130`, **paused** by Ctrl+C: `orch run` resumes the interrupted session.
4. `3`, **blocked**: fix rounds used up. Fix by hand on the run branch and commit,
   or edit the brief, then `orch run` reruns the phase from the gate.
5. `3`, **needs-replan**: edit `docs/pitch.md` or the outline, then
   `orch plan --from N`.
6. `3`, **failed**: a session crashed twice. Read `<run>/phases/NN-*/log.md`, then
   `orch run`.

Done looks like: exit 0, `status: done` in `<run>/PROGRESS.md`, a run report in
`<run>/report.md`, and the `orch/<run-id>` branch pushed. orch never merges.
The next `orch run` starts a new run; hand-written briefs it already ran are
refused, so delete or replace them first.

Do not:

- edit `<run>/state.json` by hand, or a brief while a session runs (a session that
  changes a brief fails the step).
- commit to the run branch with `--no-verify` to get past the gate.

next: `orch status`
