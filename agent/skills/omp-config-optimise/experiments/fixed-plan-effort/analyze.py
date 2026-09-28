#!/usr/bin/env python3
"""Aggregate <out>/results.jsonl per arm, arm x fixture kind, and fixture x arm.

Usage: python3 analyze.py <out-dir>
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def kind_of(fixture: str) -> str:
    return json.loads((FIXTURES / fixture / "meta.json").read_text())["kind"]


def row(label: str, recs: list[dict]) -> str:
    ok = [r for r in recs if "harnessError" not in r]
    n = len(ok)
    if not n:
        return f"| {label} | 0 | – | – | – | – | – | – | – |"
    full = sum(1 for r in ok if r["checkPassed"] and not r["scopeViolations"])
    frac = statistics.mean(r["testsPassed"] / r["testsTotal"] if r["testsTotal"] else 0 for r in ok)
    scope = sum(1 for r in ok if r["scopeViolations"])
    out_tok = statistics.median(r["usage"]["output"] for r in ok)
    cost = statistics.mean(r["usage"]["cost"] for r in ok)
    wall = statistics.median(r["wallSeconds"] for r in ok)
    turns = statistics.median(r["assistantMessages"] for r in ok)
    return (f"| {label} | {n} | {full}/{n} ({full / n:.0%}) | {frac:.3f} | {scope} | "
            f"{out_tok:.0f} | ${cost:.4f} | {wall:.0f}s | {turns:.0f} |")


def main() -> None:
    out_dir = Path(sys.argv[1]).expanduser()
    recs = [json.loads(l) for l in (out_dir / "results.jsonl").read_text().splitlines() if l.strip()]
    errors = [r for r in recs if "harnessError" in r]
    header = ("| Group | n | Full pass | Mean test fraction | Scope violations | "
              "Median output tokens | Mean API-equiv cost | Median wall | Median turns |\n"
              "|---|---|---|---|---|---|---|---|---|")
    by_arm: dict[str, list[dict]] = defaultdict(list)
    by_arm_kind: dict[tuple[str, str], list[dict]] = defaultdict(list)
    by_arm_fixture: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in recs:
        by_arm[r["arm"]].append(r)
        by_arm_kind[(r["arm"], kind_of(r["fixture"]))].append(r)
        by_arm_fixture[(r["fixture"], r["arm"])].append(r)
    print("## By arm\n\n" + header)
    for arm in sorted(by_arm):
        print(row(arm, by_arm[arm]))
    print("\n## By arm and fixture kind\n\n" + header)
    for key in sorted(by_arm_kind):
        print(row(f"{key[0]} / {key[1]}", by_arm_kind[key]))
    print("\n## By fixture and arm\n\n" + header)
    for key in sorted(by_arm_fixture):
        print(row(f"{key[0]} / {key[1]}", by_arm_fixture[key]))
    if errors:
        print(f"\nHarness errors: {len(errors)}")
        for e in errors:
            print(f"- {e['fixture']} {e['arm']} rep{e['rep']}: {e['harnessError']}")


if __name__ == "__main__":
    main()
