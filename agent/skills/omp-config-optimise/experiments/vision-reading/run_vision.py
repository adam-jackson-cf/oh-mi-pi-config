#!/usr/bin/env python3
"""Run image-reading trials in fresh headless OMP processes and score them exactly.

Items come from generate_items.py (answers computed from the plotted data). Each
trial copies one image into a fresh directory and asks the model under test to
read it and answer; the last `ANSWER:` line is compared after normalisation
(case, spaces and punctuation other than `,` `-` `#` removed).

  python3 run_vision.py --items <dir> --arm S-low=anthropic/claude-sonnet-5-5:low \
    --arm G-medium=openai-codex/gpt-6-sol:medium --reps 3 --out <artifact-dir>
  python3 run_vision.py --analyze <artifact-dir>
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import random
import re
import shutil
import statistics
import subprocess
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

WORKSPACES = Path("/tmp/omp-vision-ws")
TRIAL_TIMEOUT_S = 300
CREDITS = {"openai-codex/gpt-6-sol": (50, 5, 250), "openai-codex/gpt-6-luna": (2.5, 0.25, 12.5),
           "openai-codex/gpt-5.6-sol": (100, 10, 500), "openai-codex/gpt-5.6-terra": (50, 5, 300)}
PROMPT = ("Use the read tool to view the image `{image}` in the current directory, then answer: {question}\n"
          "End your reply with one line `ANSWER: <answer>` and nothing after it.")


@dataclass(frozen=True)
class Arm:
    name: str
    model: str
    thinking: str


def parse_arm(spec: str) -> Arm:
    name, _, selector = spec.partition("=")
    model, _, thinking = selector.rpartition(":")
    if not (name and model and thinking):
        raise argparse.ArgumentTypeError(f"expected NAME=provider/model:effort, got {spec!r}")
    return Arm(name, model, thinking)


def normalise(text: str) -> str:
    return re.sub(r"[^0-9a-z,#-]", "", text.lower())


def run_trial(items_dir: Path, item: dict, arm: Arm, rep: int, runs: Path) -> dict:
    run_id = uuid.uuid4().hex[:10]
    ws = WORKSPACES / run_id
    ws.mkdir(parents=True)
    shutil.copy(items_dir / item["image"], ws / item["image"])
    cmd = ["omp", "-p", "--no-session", "--no-extensions", "--no-title", "--approval-mode", "yolo",
           "--model", arm.model, "--thinking", arm.thinking, "--mode", "json", "--cwd", str(ws),
           PROMPT.format(image=item["image"], question=item["question"])]
    started = time.time()
    try:
        proc = subprocess.run(cmd, cwd=ws, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=TRIAL_TIMEOUT_S)
        stdout, timed_out = proc.stdout, False
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        timed_out = True
    wall = time.time() - started
    (runs / f"{run_id}.jsonl").write_text(stdout)
    usage = {"input": 0, "output": 0, "cacheRead": 0, "cost": 0.0}
    final_text = ""
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = event.get("message") or {}
        if event.get("type") != "message_end" or msg.get("role") != "assistant":
            continue
        u = msg.get("usage") or {}
        for key in ("input", "output", "cacheRead"):
            usage[key] += u.get(key, 0)
        usage["cost"] += (u.get("cost") or {}).get("total", 0.0)
        text = "".join(c.get("text", "") for c in msg.get("content") or [] if c.get("type") == "text")
        if text:
            final_text = text
    answers = re.findall(r"ANSWER:\s*(.+)", final_text)
    given = answers[-1].strip() if answers else None
    return {"runId": run_id, "item": item["id"], "arm": arm.name, "model": arm.model, "thinking": arm.thinking,
            "rep": rep, "timedOut": timed_out, "wallSeconds": round(wall, 1), "expected": item["answer"],
            "given": given, "correct": given is not None and normalise(given) == normalise(item["answer"]),
            "usage": usage}


def analyze(out: Path) -> None:
    recs = [json.loads(line) for line in (out / "results.jsonl").read_text().splitlines()]
    by_arm: dict[str, list[dict]] = defaultdict(list)
    for r in recs:
        by_arm[r.get("arm", "?")].append(r)
    lines = ["| Arm | Model:effort | Trials | Correct | Errors | Mean API-eq $ | Mean credits | Median wall |",
             "|---|---|---|---|---|---|---|---|"]
    for arm, rs in sorted(by_arm.items()):
        ok = [r for r in rs if "usage" in r]
        rate = CREDITS.get(ok[0]["model"]) if ok else None
        cr = (f"{statistics.mean((r['usage']['input'] * rate[0] + r['usage']['cacheRead'] * rate[1] + r['usage']['output'] * rate[2]) / 1e6 for r in ok):.3f}"
              if rate else "—")
        lines.append(f"| {arm} | {ok[0]['model'].split('/')[-1]}:{ok[0]['thinking']} | {len(rs)} |"
                     f" {sum(r.get('correct', False) for r in rs)}/{len(rs)} | {len(rs) - len(ok) + sum(r['timedOut'] for r in ok)} |"
                     f" ${statistics.mean(r['usage']['cost'] for r in ok):.4f} | {cr} | {statistics.median(r['wallSeconds'] for r in ok):.0f}s |")
    items = sorted({r["item"] for r in recs if "item" in r})
    arms = sorted(by_arm)
    lines += ["", "| Item | " + " | ".join(arms) + " |", "|---|" + "---|" * len(arms)]
    for item in items:
        cells = [str(sum(r.get("correct", False) for r in by_arm[a] if r.get("item") == item)) for a in arms]
        lines.append(f"| {item} | " + " | ".join(cells) + " |")
    text = "\n".join(lines)
    (out / "analysis.md").write_text(text + "\n")
    print(text)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--items", type=Path)
    parser.add_argument("--arm", dest="arms", type=parse_arm, action="append")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--seed", type=int, default=280926)
    parser.add_argument("--analyze", type=Path)
    args = parser.parse_args()
    if args.analyze:
        analyze(args.analyze)
        return
    items = json.loads((args.items / "items.json").read_text())
    jobs = [(i, a, r) for i in items for a in args.arms for r in range(args.reps)]
    random.Random(args.seed).shuffle(jobs)
    runs = args.out / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    with cf.ThreadPoolExecutor(args.workers) as pool, (args.out / "results.jsonl").open("a") as out:
        futures = {pool.submit(run_trial, args.items, i, a, r, runs): (i, a, r) for i, a, r in jobs}
        for fut in cf.as_completed(futures):
            item, arm, rep = futures[fut]
            try:
                rec = fut.result()
            except Exception as exc:  # record harness failure against the job, never drop it
                rec = {"item": item["id"], "arm": arm.name, "rep": rep, "harnessError": repr(exc)}
            out.write(json.dumps(rec) + "\n")
            out.flush()
            print(json.dumps({k: rec.get(k) for k in ("item", "arm", "rep", "given", "correct", "wallSeconds", "harnessError")}), flush=True)


if __name__ == "__main__":
    main()
