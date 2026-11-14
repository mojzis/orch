from __future__ import annotations

from pathlib import Path

from orch import state as S


def test_state_roundtrip_and_progress(tmp_path: Path) -> None:
    st = S.RunState(run_id="r1", branch="orch/r1")
    st.phases = [S.Phase(n=1, slug="a", title="Alpha"), S.Phase(n=2, slug="b")]
    st.phases[0].sessions.append(
        S.SessionRecord(key="dev", role="dev", session_id="x", ok=True, cost=0.25)
    )
    st.phases[0].done_steps.append("dev")
    path = tmp_path / ".orch" / "state.json"
    S.save(path, st)
    back = S.load(path)
    assert back == st
    progress = (tmp_path / ".orch" / "PROGRESS.md").read_text()
    assert "| 1 | Alpha | full | todo | dev | 0 | $0.25 |" in progress
    assert "cost so far: $0.25" in progress
