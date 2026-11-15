# Docs sites

**Zensical** (MIT, by the Material for MkDocs team). Not MkDocs + Material: Material
is in maintenance mode and MkDocs 2.0 drops plugins and Material support.

- `uv add --dev zensical`; config in `zensical.toml` at the repo root.
- Pages in `docs/site/` (`docs_dir`), so internal docs (`spec.md`, `plan.md`) stay
  off the site.
- Generate reference pages from the tool itself (a CLI's `guide`/`--help`) at build
  time; gitignore them, don't hand-copy.
- poe tasks `docs` (serve) and `docs-build`; deploy with a GitHub Pages workflow that
  runs `uv run poe docs-build` and uploads `/site/`.

Example: `~/git/orch` (`zensical.toml`, `.github/workflows/docs.yml`).
