# orch

orch takes a shaped pitch and delivers it phase by phase with fresh Claude Code
sessions. It writes an outline once. For each phase it then writes a brief from the
code as it is at that moment, runs dev, a gate, and review ‖ QA, then triage, fix and
verify, and pushes a commit.

- **Code owns the loop and the state.** Every step reports structured JSON, and orch
  writes its own state file, so any stop can be resumed.
- **A lead session makes the judgment calls**: which extra checks to run, what to fix,
  when to ship.
- **Worker sessions do the rest**, each one fresh, so dev's work is always checked by a
  different session.
- **It never touches main.** Everything lands on an `orch/<run-id>` branch for you to
  review. orch never merges.

```bash
uv tool install git+https://github.com/mojzis/orch
orch guide        # start here
```

[Quickstart](quickstart.md){ .md-button .md-button--primary }
[How it works](how-it-works.md){ .md-button }
