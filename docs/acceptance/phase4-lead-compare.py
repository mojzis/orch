# ruff: noqa -- one-off acceptance harness, kept as a record; run by hand
"""Run orch's lead (add_checks, then triage) on happytube phase 5b's records."""

import json
import sys
from pathlib import Path

from orch import layers as L
from orch import loop, store
from orch import state as S
from orch.lead import check_add_checks, check_triage
from orch.repo import Repo

wt = Path(sys.argv[1])
step = sys.argv[2]
src = Path.home() / "git/happytube/docs"
repo = Repo(wt)
pdir = wt / ".orch/phases/01-trusted-player"
(pdir / "reports").mkdir(parents=True, exist_ok=True)
ctx = (src / "phases/phase-5b/context.md").read_text()
dev_brief = (src / "phases/phase-5b/1-dev.md").read_text()
store.write_text(
    pdir / "brief.md",
    "# Phase 5b brief\n\n"
    + dev_brief.split("## Report")[0]
    + "\n\n# Context (settled design)\n\n"
    + ctx.replace("](./", "](docs/phases/phase-5b/"),
)
pl = (src / "process-learnings.md").read_text()
store.write_text(wt / ".orch/process-learnings.md", pl[: pl.index("## Phase 5b")])
store.write_text(wt / ".orch/config.toml", 'gate_cmd = "true"\npush = false\n')
DEV = {
    "summary": "selection in trusted/player.py (select_videos pure, read_stage, channel_statuses, player_videos, videos_json); live route + export share it. Player routes in the FastAPI app, export from stages/trusted, Flask + legacy loader gone, deploy on push, daily_run push opt-in, docs.",
    "commits": [
        "42b3003 selection",
        "f4cc144 player in FastAPI app",
        "9d65273 export from stages/trusted, Flask + legacy loader gone",
        "b28604f deploy on push, daily_run push",
        "717ee70 docs (0010, deployment, architecture, CLAUDE.md)",
        "6ba33af drop None branch",
    ],
    "tests": {
        "before": 456,
        "after": 495,
        "explained": "coverage 85% (was 82%): new modules covered, old Flask/export gone",
    },
    "design": "trusted/player.py holds the rule; route and export both call player_videos/videos_json",
    "real_run": "fetch 42 new / 33 units; assess cincinnati-zoo 4/6, explore-org 3/3; enhance 7/7; export 7; serve /player/ 200, JSON 7; Host guard 400; unsuitable, not-enhanced, rejected-channel all hidden live. Cost ~$0.073.",
    "deviations": [
        "export exits 1 on missing registry too",
        "latest date copy wins even if broken (hidden)",
        "strict file validation (StrictBool, tz, id/filename match)",
        "unreadable reviews -> channel hidden",
        "--stages-dir / HAPPYTUBE_STAGES_DIR on serve + export",
        "TrustedStage.days()",
        "deploy_web.sh and web/DEPLOYMENT.md deleted",
        "LEGACY_FETCHED_DIR kept (videos.py)",
        "no-store caching",
        "empty message text",
    ],
    "unresolved_issues": [
        "push deploys fail until first stages/trusted commit on main",
        "daily_run pushes current branch",
        "plan.md runpy backlog item now fixed",
    ],
}


def f(i, sev, title, detail=""):
    return {
        "id": i,
        "severity": sev,
        "title": title,
        "detail": detail or title,
        "repro": "",
        "files": [],
    }


REVIEW = {
    "verdict": "ship",
    "findings": [
        f(
            "R1",
            "low",
            "deploy paths: misses channels/**, trusted/**, models/**, pyproject/uv.lock",
        ),
        f("R2", "low", "daily_run `status | tee` failure under set -e skips the push"),
        f(
            "R3",
            "low",
            "`git add stages/trusted` fails if dir missing -> misleading push error",
        ),
        f(
            "R4",
            "low",
            "live /player/videos.json returns [] silently on wrong stages dir",
        ),
        f(
            "R5",
            "low",
            "HAPPYTUBE_STAGES_DIR applies to player only, name suggests everywhere",
        ),
    ],
    "checks_run": [],
    "preexisting": [
        f("RP1", "low", "legacy commented git block in daily_run.sh contradicts 0010")
    ],
    "proposed_oracles": [],
}
QA = {
    "verdict": "fix",
    "findings": [
        f(
            "Q1",
            "medium",
            "failed push leaves commit local; next run with nothing new never pushes",
        ),
        f("Q2", "low", "git add stages/trusted fails if dir missing (= R3)"),
        f("Q3", "low", "live JSON [] silently on wrong stages dir (= R4)"),
        f(
            "Q4",
            "low",
            "HEAD /player/videos.json falls to the static mount (404 / stale file)",
        ),
        f("Q5", "low", "non-date stage dir skipped silently"),
        f("Q6", "low", "deploy paths filter incomplete (= R1)"),
    ],
    "checks_run": [],
    "preexisting": [
        f("QP1", "low", "logs/ not git-ignored"),
        f(
            "QP2",
            "low",
            "player.js loads list only after YT iframe API (no error if YouTube unreachable)",
        ),
        f("QP3", "low", "daily_run pushes current branch"),
    ],
    "proposed_oracles": [],
}

st_path = repo.state_path
ph = S.Phase(
    n=1,
    slug="trusted-player",
    title="Trusted player (5b)",
    base="326b098",
    status="running",
)
st = (
    S.load(st_path)
    if st_path.is_file()
    else S.RunState(run_id="cmp", branch="HEAD", phases=[ph])
)
ph = st.phases[0]
run = loop.Run(repo=repo, layers=L.Layers.for_repo(wt), st=st, config=repo.config())
run.skills_dir = L.materialize_skills(run.layers, repo.cache / "skills")
loop._commit_orch = lambda *a, **k: None  # ty: ignore[invalid-assignment] -- not a run branch
if "dev" not in ph.done_steps:
    store.write_json(pdir / "reports/dev.json", DEV)
    ph.done_steps.append("dev")
    run.save()
if step == "add_checks":
    rep = loop.lead(
        run,
        ph,
        "add_checks",
        "add_checks",
        loop._add_checks_inputs(run, ph, DEV),
        check=lambda a: check_add_checks([DEV], a),
    )
else:
    for k, r in (("review", REVIEW), ("qa", QA)):
        store.write_json(pdir / f"reports/{k}.json", r)
        if k not in ph.done_steps:
            ph.done_steps.append(k)
    reports = {"review": REVIEW, "qa": QA}
    rep = loop.lead(
        run,
        ph,
        "triage#1",
        "triage",
        loop._triage_inputs(run, ph, reports, 0),
        check=lambda t: check_triage(reports, t),
    )
print(json.dumps(rep, indent=1))
print("COST", sum(s.cost for s in ph.sessions), file=sys.stderr)
