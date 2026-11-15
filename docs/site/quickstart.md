# Quickstart

## Install

```bash
uv tool install git+https://github.com/mojzis/orch
```

orch drives the `claude` CLI, so Claude Code must be installed and logged in.
Point `ORCH_CLAUDE_BIN` at it if it isn't on `PATH` as `claude`.

## Wire it into a repo

```bash
cd your-project
orch init
```

This creates `.orch/config.toml`. Set two keys there:

- `gate_cmd`: the one command that must pass after every change, for example
  `uv run pytest && uv run ty check`.
- `setup_cmd`: what a fresh worktree needs before review and QA can run, for example
  `uv sync`.

Commit the file.

## Give it work

Pick one:

=== "A shaped pitch"

    Write `docs/pitch.md` with requirement ids (`R1`, `R2`, ...), each stated as
    observable behaviour. Optional extras: `docs/decisions/`, `docs/architecture.md`,
    `QA.md`.

=== "Hand-written briefs"

    Put one brief per phase in `.orch/phases/01-<slug>/brief.md`,
    `.orch/phases/02-<slug>/brief.md`, ...

## Run

```bash
orch config        # what every step will run, and which layer it came from
orch run           # plans first if there is a pitch, then runs every phase
orch status        # also prints where the run's files are
```

`orch run --pause` stops after the plan so you can read the plan (`plan.md`, in the run dir `orch status` prints) before any
code is written.

!!! tip "Stopped?"
    Whatever stopped the run, `orch run` resumes it once you have dealt with the
    cause. The exit code tells you what that cause is; see [Use](reference/use.md).
