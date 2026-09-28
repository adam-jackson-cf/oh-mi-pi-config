#!/usr/bin/env python3
"""Run fixed-plan implementation trials in fresh headless OMP processes.

Each trial copies a fixture's repo/ into a fresh git workspace, runs
`omp -p` with the arm's model, effort and service tier, then scores the
workspace with the fixture's hidden check and an allowed-path scope check.
Results append to <out>/results.jsonl; per-trial logs go to <out>/runs/<id>/.

Example:
  python3 run_trials.py --model openai-codex/gpt-6-luna \
    --arm A-low=low --arm B-medium=medium --arm F-low-fast=low@priority \
    --reps 3 --out ~/.omp/.todo/artifacts/<date>-<model>-effort
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import random
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"
WORKSPACES = Path("/tmp/omp-effort-ws")
TRIAL_TIMEOUT_S = 900


@dataclass(frozen=True)
class Arm:
    name: str
    thinking: str
    tier: str | None


def parse_arm(spec: str) -> Arm:
    """Parse NAME=EFFORT[@TIER], e.g. A-low=low or F-fast=low@priority."""
    name, _, rest = spec.partition("=")
    thinking, _, tier = rest.partition("@")
    if not name or not thinking:
        raise argparse.ArgumentTypeError(f"arm must be NAME=EFFORT[@TIER], got {spec!r}")
    return Arm(name, thinking, tier or None)


def sh(cmd: list[str], cwd: Path, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=timeout)


def prepare_workspace(fixture: Path, ws: Path) -> None:
    shutil.copytree(fixture / "repo", ws)
    sh(["git", "init", "-q"], ws)
    # Test runs and indexing tools leave byproducts that are not model edits.
    (ws / ".git" / "info" / "exclude").write_text("__pycache__/\n*.pyc\n.pytest_cache/\nnode_modules/\n.codegraph/\n")
    sh(["git", "add", "-A"], ws)
    sh(["git", "-c", "user.email=eval@local", "-c", "user.name=eval", "commit", "-qm", "baseline"], ws)


def changed_paths(ws: Path) -> list[str]:
    sh(["git", "add", "-A"], ws)
    out = sh(["git", "diff", "--cached", "--name-only"], ws).stdout
    return sorted(p for p in out.splitlines() if p)


def summarize_events(stdout: str) -> dict:
    usage = {"input": 0, "output": 0, "cacheRead": 0, "cost": 0.0}
    assistant_msgs = 0
    tool_calls = 0
    stop_reasons: list[str] = []
    final_text = ""
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "message_end":
            continue
        msg = event.get("message", {})
        if msg.get("role") != "assistant":
            continue
        assistant_msgs += 1
        u = msg.get("usage") or {}
        usage["input"] += u.get("input", 0)
        usage["output"] += u.get("output", 0)
        usage["cacheRead"] += u.get("cacheRead", 0)
        usage["cost"] += (u.get("cost") or {}).get("total", 0.0)
        stop_reasons.append(msg.get("stopReason", ""))
        content = msg.get("content") or []
        tool_calls += sum(1 for c in content if c.get("type") == "toolCall")
        text = "".join(c.get("text", "") for c in content if c.get("type") == "text")
        if text:
            final_text = text
    return {
        "usage": usage,
        "assistantMessages": assistant_msgs,
        "toolCalls": tool_calls,
        "stopReasons": sorted(set(stop_reasons)),
        "finalText": final_text[-400:],
    }


def run_trial(fixture_id: str, arm: Arm, rep: int, model: str, runs: Path) -> dict:
    fixture = FIXTURES / fixture_id
    run_id = uuid.uuid4().hex[:10]
    run_dir = runs / run_id
    ws = WORKSPACES / run_id
    run_dir.mkdir(parents=True)
    prepare_workspace(fixture, ws)
    cmd = [
        "omp", "-p", "--no-session", "--no-extensions", "--no-title",
        "--approval-mode", "yolo",
        "--model", model, "--thinking", arm.thinking,
        "--mode", "json", "--cwd", str(ws),
    ]
    if arm.tier:
        cmd += ["--service-tier", arm.tier]
    cmd.append((fixture / "task.md").read_text())
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
    allowed = {p.strip() for p in (fixture / "allowed_paths.txt").read_text().splitlines() if p.strip()}
    changed = changed_paths(ws)
    (run_dir / "diff.patch").write_text(sh(["git", "diff", "--cached"], ws).stdout)
    violations = [p for p in changed if p not in allowed]

    check = sh(["bash", str(fixture / "hidden" / "check.sh"), str(ws)], fixture, timeout=180)
    (run_dir / "check.txt").write_text(check.stdout + check.stderr)
    score = {"passed": 0, "total": 0}
    for line in reversed(check.stdout.splitlines()):
        try:
            score = json.loads(line)
            break
        except json.JSONDecodeError:
            continue
    record = {
        "runId": run_id, "fixture": fixture_id, "arm": arm.name, "rep": rep,
        "model": model, "thinking": arm.thinking, "tier": arm.tier or "standard",
        "exitCode": code, "timedOut": timed_out, "wallSeconds": round(wall, 1),
        "checkPassed": check.returncode == 0,
        "testsPassed": score.get("passed", 0), "testsTotal": score.get("total", 0),
        "changedPaths": changed, "scopeViolations": violations,
        **summarize_events(stdout),
    }
    return record


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, help="provider/model selector under test")
    parser.add_argument("--arm", dest="arms", type=parse_arm, action="append", required=True, help="NAME=EFFORT[@TIER]; repeatable")
    parser.add_argument("--out", type=Path, required=True, help="artifact directory (outside the skill)")
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--fixtures", nargs="*")
    parser.add_argument("--seed", type=int, default=280926)
    args = parser.parse_args()

    fixture_ids = args.fixtures or sorted(p.name for p in FIXTURES.iterdir() if (p / "task.md").exists())
    jobs = [(f, a, r) for f in fixture_ids for a in args.arms for r in range(args.reps)]
    random.Random(args.seed).shuffle(jobs)  # interleave arms so time-of-day/load effects spread evenly
    runs = args.out / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    with cf.ThreadPoolExecutor(args.workers) as pool, (args.out / "results.jsonl").open("a") as out:
        futures = {pool.submit(run_trial, f, a, r, args.model, runs): (f, a, r) for f, a, r in jobs}
        for fut in cf.as_completed(futures):
            fixture_id, arm, rep = futures[fut]
            try:
                rec = fut.result()
            except Exception as exc:  # record harness failure against the job, never drop it
                rec = {"fixture": fixture_id, "arm": arm.name, "rep": rep, "harnessError": repr(exc)}
            out.write(json.dumps(rec) + "\n")
            out.flush()
            print(json.dumps({k: rec.get(k) for k in ("fixture", "arm", "rep", "testsPassed", "testsTotal", "scopeViolations", "wallSeconds", "harnessError")}), flush=True)


if __name__ == "__main__":
    main()
