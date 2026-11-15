# orch: setup

orch is not wired into this repo yet (no `.orch/config.toml`).

1. `orch init`, then set `gate_cmd` in `.orch/config.toml` to the one command that
   must pass after every change (tests + full typecheck), and `setup_cmd` to what a
   fresh worktree needs (e.g. `uv sync`). Commit it.
2. Give orch work, one of:
   - a shaped pitch in `docs/pitch.md` with requirement ids (`R1`, `R2`, ...),
     plus optional `docs/decisions/`, `docs/architecture.md`, `QA.md`; or
   - hand-written briefs in `.orch/phases/01-<slug>/brief.md`, `02-...` (copied
     into the run when it starts).
3. Check what each step will run: `orch config` (`orch config -s small` for
   another scenario).
4. Start: `orch run` (with a pitch it plans first; `orch run --pause` stops after
   the plan so you can read the plan; `orch status` shows where it is).

Do not:

- edit orch's default skills inside the installed package; `orch eject <name>`
  copies one into `.orch/skills/` to edit.
- start a run with uncommitted changes; orch refuses.

See `orch guide tune` for config keys and layers.

next: `orch init`
