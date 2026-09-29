#!/usr/bin/env python3
"""Run seeded-defect review trials in fresh headless OMP processes.

Each trial builds a case workspace (baseline commit + uncommitted change with
seeded defects), asks the model to review the change against the plan, and
stores its JSON findings with the seeded defect spans. Scoring is in analyze.py.

Example:
  python3 run_reviews.py --arm G-high=openai-codex/gpt-6-sol:high \
    --arm P-low=openai-codex/gpt-5.6-sol:low --reps 2 \
    --out ~/.omp/.todo/artifacts/<date>-review-detection
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import random
import re
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from analyze import latest_records, needs_rerun
from cases import CASES, FIXTURES, Case, build_workspace

WORKSPACES = Path("/tmp/omp-review-ws")
TRIAL_TIMEOUT_S = 900

PROMPT = """You are reviewing an uncommitted change in this repository (inspect it with `git diff` \
and by reading files). The change was meant to implement the fixed plan below exactly.

Find every defect: behaviour that violates the plan, or that breaks existing callers. Do not \
modify repository files; you may run code or write scratch files outside the repository.

End your reply with one fenced ```json block of this shape and nothing after it:
{{"findings": [{{"file": "<repo-relative path>", "line": <line in the working-tree file>, \
"step": <number of the plan step it violates, or null>, "severity": "blocking" | "minor", \
"summary": "<one sentence>"}}]}}
Use "blocking" only for plan violations or bugs. Report an empty list if there are none.

<plan>
{plan}
</plan>
"""


@dataclass(frozen=True)
class Arm:
    name: str
    model: str
    thinking: str


def parse_arm(spec: str) -> Arm:
    """Parse NAME=provider/model:effort."""
    name, _, selector = spec.partition("=")
    model, _, thinking = selector.rpartition(":")
    if not (name and model and thinking):
        raise argparse.ArgumentTypeError(f"expected NAME=provider/model:effort, got {spec!r}")
    return Arm(name, model, thinking)


def parse_findings(texts: list[str]) -> list[dict] | None:
    """Last JSON object with a `findings` list across all assistant messages (fenced or bare)."""
    decoder = json.JSONDecoder()
    for text in reversed(texts):
        starts = [m.start() for m in re.finditer(r'\{\s*"findings"', text)]
        for start in reversed(starts):
            try:
                findings = decoder.raw_decode(text, start)[0]["findings"]
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
            if isinstance(findings, list):
                return findings
    return None


def summarize_events(stdout: str) -> dict:
    usage = {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "reasoning": 0, "cost": 0.0}
    texts: list[str] = []
    models: set[str] = set()
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = event.get("message") or {}
        if event.get("type") != "message_end" or msg.get("role") != "assistant":
            continue
        models.add(f"{msg.get('provider')}/{msg.get('model')}")
        u = msg.get("usage") or {}
        for key in ("input", "output", "cacheRead", "cacheWrite"):
            usage[key] += u.get(key, 0)
        usage["reasoning"] += u.get("reasoningTokens", 0)
        usage["cost"] += (u.get("cost") or {}).get("total", 0.0)
        text = "".join(c.get("text", "") for c in msg.get("content") or [] if c.get("type") == "text")
        if text:
            texts.append(text)
    return {"usage": usage, "assistantMessages": len(texts), "texts": texts, "effectiveModels": sorted(models)}


def run_trial(case: Case, arm: Arm, rep: int, runs: Path) -> dict:
    run_id = uuid.uuid4().hex[:10]
    run_dir = runs / run_id
    run_dir.mkdir(parents=True)
    ws = WORKSPACES / run_id
    defects = build_workspace(case, ws)
    before = subprocess.run(["git", "diff"], cwd=ws, capture_output=True, text=True).stdout
    prompt = PROMPT.format(plan=(FIXTURES / case.fixture / "task.md").read_text())
    cmd = ["omp", "-p", "--no-session", "--no-extensions", "--no-title", "--approval-mode", "yolo",
           "--model", arm.model, "--thinking", arm.thinking, "--mode", "json", "--cwd", str(ws), prompt]
    started = time.time()
    timed_out = False
    try:
        proc = subprocess.run(cmd, cwd=ws, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=TRIAL_TIMEOUT_S)
        stdout, stderr, code = proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        code, timed_out = -1, True
    wall = time.time() - started
    (run_dir / "events.jsonl").write_text(stdout)
    (run_dir / "stderr.txt").write_text(stderr)
    after = subprocess.run(["git", "diff"], cwd=ws, capture_output=True, text=True).stdout
    events = summarize_events(stdout)
    texts = events.pop("texts")
    failure = None
    if timed_out:
        failure = "timeout"
    elif code != 0:
        failure = f"exit {code}"
    elif not texts:
        failure = "empty reply"
    elif re.search(r"(?i)rate.?limit|quota|usage limit|429", stderr):
        failure = "rate limit"
    return {
        "runId": run_id, "case": case.id, "fixture": case.fixture, "arm": arm.name, "rep": rep,
        "model": arm.model, "thinking": arm.thinking, "exitCode": code, "timedOut": timed_out, "failure": failure,
        "wallSeconds": round(wall, 1), "defects": defects, "modifiedRepo": before != after,
        "findings": parse_findings(texts), "finalText": texts[-1][-2000:] if texts else "", **events,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arm", dest="arms", type=parse_arm, action="append", required=True,
                        help="NAME=provider/model:effort; repeatable")
    parser.add_argument("--out", type=Path, required=True, help="artifact directory (outside the skill)")
    parser.add_argument("--reps", type=int, default=2)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--seed", type=int, default=280926)
    parser.add_argument("--cases", nargs="*", help="case ids to run (default: all)")
    parser.add_argument("--rerun-failed", action="store_true",
                        help="re-run only (case, arm, rep) whose latest record failed or leaked")
    args = parser.parse_args()

    cases = [c for c in CASES if not args.cases or c.id in args.cases]
    jobs = [(c, a, r) for c in cases for a in args.arms for r in range(args.reps)]
    if args.rerun_failed:
        latest = latest_records(args.out)
        jobs = [(c, a, r) for c, a, r in jobs if (c.id, a.name, r) in latest and needs_rerun(latest[(c.id, a.name, r)], args.out)]
    random.Random(args.seed).shuffle(jobs)
    runs = args.out / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    with cf.ThreadPoolExecutor(args.workers) as pool, (args.out / "results.jsonl").open("a") as out:
        futures = {pool.submit(run_trial, c, a, r, runs): (c, a, r) for c, a, r in jobs}
        for fut in cf.as_completed(futures):
            case, arm, rep = futures[fut]
            try:
                rec = fut.result()
            except Exception as exc:  # record harness failure against the job, never drop it
                rec = {"case": case.id, "arm": arm.name, "rep": rep, "harnessError": repr(exc)}
            out.write(json.dumps(rec) + "\n")
            out.flush()
            found = rec.get("findings")
            print(json.dumps({"case": rec["case"], "arm": rec["arm"], "rep": rec["rep"],
                              "findings": None if found is None else len(found),
                              "wall": rec.get("wallSeconds"), "err": rec.get("harnessError")}), flush=True)


if __name__ == "__main__":
    main()
