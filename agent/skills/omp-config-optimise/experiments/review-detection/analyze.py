#!/usr/bin/env python3
"""Score review trials deterministically.

Detection (primary): a `blocking` finding in the defect's file whose `step` is one of the
defect's plan steps OR whose `line` is within WINDOW of the mutated span. Each
finding credits at most one defect and each defect at most one finding (nearest
span centre wins). `file-level` (any blocking finding in the file) is reported
as a sensitivity check, as is detection counting `minor` findings too.

False positives: blocking findings on clean cases, excluding clusters (same
file, lines within WINDOW) that at least two distinct arms reported on the same
case, since those likely mark a real issue in the reference. An arm averaging
more than FP_VETO such findings per clean trial is vetoed.

Missingness: records are de-duplicated per (case, arm, rep), latest wins.
Failed, timed-out and leak-flagged trials are re-run once
(`run_reviews.py --rerun-failed`); the decision table is withheld unless every
arm has every trial scored.
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

WINDOW = 3
FP_VETO = 3.0
CREDITS = {  # Codex Standard credits per 1M tokens: input, cached input, output (2026-09-28)
    "openai-codex/gpt-6-astra": (250, 25, 1250),
    "openai-codex/gpt-6-sol": (50, 5, 250),
    "openai-codex/gpt-6-luna": (2.5, 0.25, 12.5),
    "openai-codex/gpt-5.6-sol": (100, 10, 500),
    "openai-codex/gpt-5.6-terra": (50, 5, 300),
}
LEAK_MARKERS = ("fixed-plan-effort", "review-detection", "/reference/")


def latest_records(out: Path) -> dict[tuple[str, str, int], dict]:
    latest: dict[tuple[str, str, int], dict] = {}
    for line in (out / "results.jsonl").read_text().splitlines():
        rec = json.loads(line)
        latest[(rec["case"], rec["arm"], rec["rep"])] = rec
    return latest


def leaked(rec: dict, out: Path) -> bool:
    path = out / "runs" / rec.get("runId", "-") / "events.jsonl"
    return path.exists() and any(marker in path.read_text() for marker in LEAK_MARKERS)


def needs_rerun(rec: dict, out: Path) -> bool:
    return bool(rec.get("harnessError") or rec.get("failure")) or leaked(rec, out)


def same_file(reported: str, expected: str) -> bool:
    reported = reported.lstrip("./")
    return bool(reported) and (reported == expected or reported.endswith("/" + expected) or expected.endswith("/" + reported))


def reported(rec: dict, any_severity: bool = False) -> list[dict]:
    return [f for f in rec.get("findings") or [] if isinstance(f, dict) and (any_severity or f.get("severity") == "blocking")]


def score(rec: dict, any_severity: bool = False) -> dict:
    defects = rec["defects"]
    findings = reported(rec, any_severity)
    detected: dict[str, str] = {}
    matched: set[int] = set()
    for i, f in enumerate(findings):
        line = f.get("line") if isinstance(f.get("line"), int) else None
        step = f.get("step") if isinstance(f.get("step"), int) else None
        candidates = []
        for defect_id, span in defects.items():
            if defect_id in detected or not same_file(str(f.get("file", "")), span["file"]):
                continue
            by_line = line is not None and span["start"] - WINDOW <= line <= span["end"] + WINDOW
            by_step = step is not None and step in span["steps"]
            if by_line or by_step:
                centre = (span["start"] + span["end"]) / 2
                distance = abs(line - centre) if line is not None else float("inf")
                how = "both" if by_line and by_step else "line" if by_line else "step"
                candidates.append((distance, span["start"], defect_id, how))
        if candidates:
            _, _, defect_id, how = min(candidates)
            detected[defect_id] = how
            matched.add(i)
    file_level = {d for d, span in defects.items() if any(same_file(str(f.get("file", "")), span["file"]) for f in findings)}
    return {"detected": detected, "fileLevel": file_level, "unmatched": [f for i, f in enumerate(findings) if i not in matched]}


def credits(rec: dict) -> float | None:
    rate = CREDITS.get(rec["model"])
    if rate is None:
        return None
    u = rec["usage"]
    return (u["input"] * rate[0] + u["cacheRead"] * rate[1] + u["output"] * rate[2]) / 1e6


def shared_clusters(clean: list[dict]) -> set[tuple[str, str, int]]:
    """(case, arm, finding index) for clean-case findings that ≥2 arms reported at the same place."""
    by_case: dict[str, list[tuple[str, int, str, int | None]]] = defaultdict(list)
    for r in clean:
        for i, f in enumerate(r["score"]["unmatched"]):
            line = f.get("line") if isinstance(f.get("line"), int) else None
            by_case[r["case"]].append((r["arm"], i, str(f.get("file", "")).lstrip("./"), line))
    shared: set[tuple[str, str, int]] = set()
    for case, items in by_case.items():
        for arm, i, file, line in items:
            others = {a for a, _, f2, l2 in items if a != arm and f2 == file and line is not None and l2 is not None
                      and abs(l2 - line) <= WINDOW}
            if others:
                shared.add((case, arm, i))
    return shared


def main() -> None:
    out = Path(sys.argv[1])
    latest = latest_records(out)
    arms = sorted({arm for _, arm, _ in latest})
    cases = sorted({case for case, _, _ in latest})
    reps = sorted({rep for _, _, rep in latest})
    expected = len(cases) * len(reps)
    valid = [r for r in latest.values() if not needs_rerun(r, out)]
    for r in valid:
        r["score"] = score(r)
    clean = [r for r in valid if not r["defects"]]
    shared = shared_clusters(clean)

    by_arm: dict[str, list[dict]] = defaultdict(list)
    for r in valid:
        by_arm[r["arm"]].append(r)
    complete = all(len(by_arm[a]) == expected for a in arms)

    lines = ["| Arm | Model:effort | Scored | Invalid | Detected | via line / step / both | Any severity | File-level |"
             " Clean FP (mean, excl. shared) | Unmatched on seeded (mean) | Unparsed | Model mismatch |"
             " Mean reasoning tok | Mean API-eq $ | Mean credits | Median wall |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    ever: dict[str, set[str]] = {}
    summary: dict[str, dict] = {}
    for arm in arms:
        rs = by_arm[arm]
        invalid = sum(1 for (c, a, _), r in latest.items() if a == arm and needs_rerun(r, out))
        seeded = [r for r in rs if r["defects"]]
        total = sum(len(r["defects"]) for r in seeded)
        hows = [h for r in seeded for h in r["score"]["detected"].values()]
        det = len(hows)
        fil = sum(len(r["score"]["fileLevel"]) for r in seeded)
        any_sev = sum(len(score(r, any_severity=True)["detected"]) for r in seeded)
        arm_clean = [r for r in rs if not r["defects"]]
        fp = statistics.mean(
            sum(1 for i in range(len(r["score"]["unmatched"])) if (r["case"], arm, i) not in shared) for r in arm_clean
        ) if arm_clean else 0.0
        unmatched_seeded = statistics.mean(len(r["score"]["unmatched"]) for r in seeded) if seeded else 0.0
        unparsed = sum(r["findings"] is None for r in rs)
        mismatch = sum(r.get("effectiveModels") != [r["model"]] for r in rs)
        reasoning = statistics.mean(r["usage"].get("reasoning", 0) for r in rs) if rs else 0
        cost = statistics.mean(r["usage"]["cost"] for r in rs) if rs else 0
        cr = [c for c in (credits(r) for r in rs) if c is not None]
        wall = statistics.median(r["wallSeconds"] for r in rs) if rs else 0
        ever[arm] = {d for r in seeded for d in r["score"]["detected"]}
        summary[arm] = {"detected": det, "total": total, "fp": fp, "credits": statistics.mean(cr) if cr else None}
        lines.append(
            f"| {arm} | {rs[0]['model'].split('/')[-1] + ':' + rs[0]['thinking'] if rs else '?'} | {len(rs)}/{expected} |"
            f" {invalid} | {det}/{total} | {hows.count('line')} / {hows.count('step')} / {hows.count('both')} |"
            f" {any_sev}/{total} | {fil}/{total} | {fp:.2f}{' VETO' if fp > FP_VETO else ''} | {unmatched_seeded:.2f} | {unparsed} | {mismatch} |"
            f" {reasoning:.0f} | ${cost:.3f} | {f'{statistics.mean(cr):.2f}' if cr else '—'} | {wall:.0f}s |")

    defect_ids = sorted({d for r in valid for d in r["defects"]})
    lines += ["", "Per-defect detections (of reps):", "", "| Defect | " + " | ".join(arms) + " |", "|---|" + "---|" * len(arms)]
    for d in defect_ids:
        lines.append(f"| {d} | " + " | ".join(
            str(sum(d in r["score"]["detected"] for r in by_arm[a] if d in r["defects"])) for a in arms) + " |")
    if "G-high" in ever:
        lines += ["", "Paired discordance vs G-high (defects found in ≥1 rep): candidate-only − G-high-only", ""]
        for arm in arms:
            if arm != "G-high":
                a_only = len(ever[arm] - ever["G-high"])
                g_only = len(ever["G-high"] - ever[arm])
                lines.append(f"- {arm}: +{a_only} / −{g_only} → {a_only - g_only:+d}")
    lines += ["", f"Shared clean-case clusters excluded from FP: {len(shared)}",
              f"Complete (every arm {expected}/{expected} scored): {complete}"]
    if not complete:
        lines.append("INCOMPLETE — decision withheld; run `run_reviews.py --rerun-failed` once, then re-analyse.")
    text = "\n".join(lines)
    (out / "analysis.md").write_text(text + "\n")
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(text)


if __name__ == "__main__":
    main()
