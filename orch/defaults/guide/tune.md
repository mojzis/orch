# orch: tune

## Layers (lowest first; each overrides the one before)

1. defaults shipped with orch
2. user: `~/.config/orch/` (or `$ORCH_CONFIG_HOME`): `config.toml`, `scenarios/`, `skills/`
3. repo: `.orch/`: `config.toml`, `scenarios/`, `skills/`
4. task: pitch frontmatter, one outline row, the command line (`--scenario`, `--set`)

Within a layer a scenario file beats `config.toml`.

## config.toml

```toml
[role.dev]          # any role: outline, outline_review, brief, plan_review, dev,
model = "sonnet"    # add_checks, review, qa, combined, triage, fix, verify, probe,
effort = "high"     # learnings
tools = "all"       # or "Read,Grep,Glob,Bash"
max_budget_usd = 50
extra = "one or two lines passed to the template"
```

## Scenario file (`scenarios/<name>.toml`)

```toml
extends = "full"                    # optional; same name = next layer down
steps = ["brief", "dev", "gate", "review_combined", "fix", "record"]
add_steps = { verify = ["probe"] }  # insert after a step
[role.qa]
extra = "also open the CSV export"
```

Rules orch enforces: a dev step, the gate right after dev, review_qa or
review_combined, record last, steps in canonical order.

## Environment

- `ORCH_CLAUDE_BIN`: claude executable (default `claude`)
- `ORCH_CONFIG_HOME`: user layer directory

next: `orch config`
