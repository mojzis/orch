"""A fake `claude` executable for tests.

Driven by the JSON file in $ORCH_FAKE_SCRIPT:

    {"responses": {"orch-dev": [<response>, ...], ...}}

Each call pops the next response for the template named in the prompt
(`/orch-dev ...`); the last one repeats. A response may hold:

    structured  dict for structured_output (omit -> none)
    cost        cost of this turn; the fake reports the session's running total
    is_error, result, subtype, exit, stdout, stderr
    actions     [{"write": path, "content": str}, {"append": path, "content": str},
                 {"rm": path}, {"commit": msg}, {"sh": cmd}]
    sleep       seconds to sleep before answering

Every call is appended to <script>.calls.jsonl.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def _flag(argv: list[str], name: str) -> str | None:
    if name in argv:
        i = argv.index(name)
        return argv[i + 1] if i + 1 < len(argv) else None
    return None


def _act(actions: list[dict], cwd: Path) -> None:
    for a in actions:
        if "write" in a:
            p = cwd / a["write"]
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(a["content"])
        elif "append" in a:
            p = cwd / a["append"]
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a") as fh:
                fh.write(a["content"])
        elif "rm" in a:
            (cwd / a["rm"]).unlink()
        elif "commit" in a:
            subprocess.run(["git", "add", "-A"], cwd=cwd, check=True)
            subprocess.run(
                ["git", "commit", "-qm", a["commit"], "--no-verify"],
                cwd=cwd,
                check=True,
            )
        elif "sh" in a:
            subprocess.run(a["sh"], shell=True, cwd=cwd, check=True)  # noqa: S602


def main() -> int:
    argv = sys.argv[1:]
    if argv == ["--version"]:
        print("9.9.9 (fake claude)")
        return 0
    script = Path(os.environ["ORCH_FAKE_SCRIPT"])
    prompt = _flag(argv, "-p") or ""
    template = prompt.split()[0].lstrip("/") if prompt.startswith("/") else ""
    session = _flag(argv, "--session-id") or _flag(argv, "--resume") or ""
    cwd = Path.cwd()

    lock = script.with_suffix(".lock")
    while True:  # crude cross-process lock, review and QA run in parallel
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL)
            os.close(fd)
            break
        except FileExistsError:
            time.sleep(0.01)
    try:
        data = json.loads(script.read_text())
        counters_path = script.with_suffix(".counters.json")
        counters = (
            json.loads(counters_path.read_text()) if counters_path.exists() else {}
        )
        queue = data.get("responses", {}).get(template) or [{}]
        idx = counters.get(template, 0)
        resp = queue[min(idx, len(queue) - 1)]
        counters[template] = idx + 1
        costs = counters.setdefault("_costs", {})
        costs[session] = costs.get(session, 0.0) + float(resp.get("cost", 0.01))
        total = costs[session]
        counters_path.write_text(json.dumps(counters))
        with script.with_suffix(".calls.jsonl").open("a") as fh:
            fh.write(
                json.dumps(
                    {
                        "template": template,
                        "argv": argv,
                        "cwd": str(cwd),
                        "session": session,
                        "resume": "--resume" in argv,
                        "env_session": os.environ.get("CLAUDE_CODE_SESSION_ID"),
                        "n": idx,
                    }
                )
                + "\n"
            )
    finally:
        lock.unlink()

    time.sleep(float(resp.get("sleep", 0)))
    _act(resp.get("actions", []), cwd)
    if "stdout" in resp:
        sys.stdout.write(resp["stdout"])
        sys.stderr.write(resp.get("stderr", ""))
        return int(resp.get("exit", 0))
    out = {
        "type": "result",
        "subtype": resp.get("subtype", "success"),
        "is_error": bool(resp.get("is_error", False)),
        "session_id": session,
        "total_cost_usd": total,
        "usage": {"input_tokens": 100, "output_tokens": 10},
        "result": resp.get("result", "done"),
    }
    if "structured" in resp:
        out["structured_output"] = resp["structured"]
    known = {
        "structured",
        "cost",
        "is_error",
        "result",
        "subtype",
        "exit",
        "stdout",
        "stderr",
        "actions",
        "sleep",
    }
    out |= {k: v for k, v in resp.items() if k not in known}  # e.g. api_error_status
    sys.stdout.write(json.dumps(out))
    sys.stderr.write(resp.get("stderr", ""))
    return int(resp.get("exit", 0))


if __name__ == "__main__":
    sys.exit(main())
