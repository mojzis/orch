# orch

Delivers a shaped pitch phase by phase with fresh Claude Code sessions: an outline
once, then for each phase a brief written from the code as it is, dev, a gate,
review ‖ QA, triage, fix and verify, and a pushed commit. Code owns the loop and
the state; a lead session makes the judgment calls; worker sessions do the rest.

```
uv tool install --editable ~/git/orch   # or: uv run --project ~/git/orch orch ...
orch guide                              # start here: instructions for the agent/you
```

In a project repo:

```
orch init                # .orch/config.toml: set gate_cmd (and setup_cmd)
orch run                 # plans from docs/pitch.md first, then runs every phase
orch status              # or read .orch/PROGRESS.md
```

Everything orch writes lives in `.orch/` on an `orch/<run-id>` branch; it never
touches main and never merges. Docs: <https://mojzis.github.io/orch/>. See [docs/spec.md](docs/spec.md) for the design and
[docs/plan.md](docs/plan.md) for build status.
