# orch: setup

orch is not wired into this repo yet (no `.orch/config.toml`).

1. Check what orch will run per step: `orch config`
2. List scenarios and templates: `orch config --scenarios`
3. To change a step's prompt for this repo: `orch eject orch-qa`, then edit
   `.orch/skills/orch-qa/SKILL.md`.

Do not:

- edit orch's default skills inside the installed package; eject them instead.

See `orch guide tune` for config keys and layers.

next: `orch config`
