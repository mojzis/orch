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



## .orch/config.toml (repo keys)

- `gate_cmd`: must pass after every code change; empty = diff checks only
- `setup_cmd`: run in each fresh review/QA worktree
- `full_qa_cmd`: the slowest QA tier, run once after the last phase
- `max_fix_rounds` (2), `remote` ("origin"), `push` (true)
- `test_globs`, `protected`: what the gate treats as tests and as files a session
  must not change (QA.md, orch's config, briefs and templates)

## Exit codes of `orch run`

0 done, 2 usage/config error, 3 stopped (needs-user, blocked, needs-replan,
failed), 75 usage limit, 130 interrupted.
next: `orch config`
