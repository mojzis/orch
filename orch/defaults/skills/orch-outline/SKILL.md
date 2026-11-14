---
name: orch-outline
description: orch outline. Split a shaped pitch into phases, each with a scenario and approach. Read-only. Invoked as `/orch-outline <inputs.md>`.
---

You split a shaped pitch into an outline of phases. You cannot edit files; orch writes
`.orch/plan.md` from your answer. Read your inputs first: $ARGUMENTS

Read `docs/pitch.md`, `docs/decisions/` (newer wins), `docs/architecture.md`,
`QA.md` and the code. Then return one row per phase:

- `title`, `slug` (kebab-case), `deliverable` (one or two sentences)
- `covers`: requirement ids; together the phases cover **every** id
- `depends_on`: earlier phase numbers only
- `size`: `S`, `M` or `L` (one session at most)
- `scenario`: one of the scenarios listed in the inputs, and `reason` (one line)
  - a phase that calls a paid API or touches real data uses `real-data`
  - a phase that changes pages uses `ui`
  - docs, config or one small module: `small`
- `approach`: one line ("extend the existing importer, no new module")
- `set`: optional per-role changes such as `{"dev": {"effort": "high"}}`, only with
  a reason in `reason`

Respect the no-gos and the appetite. Order phases so the gate stays green after
every one.
