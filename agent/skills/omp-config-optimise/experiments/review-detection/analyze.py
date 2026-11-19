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

Toolset: each record carries a toolset label (`find` availability plus a config hash, see
../toolset.py). Every table is computed per label; unlabelled records form a `pre-find` group,
and mixing labels prints a warning because arms then ran with different tools.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).resolve().parent.parent))
from toolset import describe, label_of, warn_if_mixed

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
TABLE_HEADER = [
    "| Arm | Model:effort | Scored | Invalid | Detected | via line / step / both | Any severity | File-level |"
    " Clean FP (mean, excl. shared) | Unmatched on seeded (mean) | Unparsed | Model mismatch |"
    " Mean reasoning tok | Mean API-eq $ | Mean credits | Median wall |",
    "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
]

Record = dict[str, Any]
TrialKey = tuple[str, str, int]


def latest_by_label(out: Path) -> dict[tuple[str, str], dict[TrialKey, Record]]:
    """Per toolset label, the latest record for each (case, arm, rep)."""
    groups: dict[tuple[str, str], dict[TrialKey, Record]] = defaultdict(dict)
    for line in (out / "results.jsonl").read_text().splitlines():
        if line.strip():
            rec = json.loads(line)
            groups[label_of(rec)][(rec["case"], rec["arm"], rec["rep"])] = rec
    return groups


def latest_records(out: Path, label: tuple[str, str]) -> dict[TrialKey, Record]:
    return latest_by_label(out).get(label, {})


def leaked(rec: Record, out: Path) -> bool:
    path = out / "runs" / rec.get("runId", "-") / "events.jsonl"
    return path.exists() and any(marker in path.read_text() for marker in LEAK_MARKERS)


def needs_rerun(rec: Record, out: Path) -> bool:
    return bool(rec.get("harnessError") or rec.get("failure")) or leaked(rec, out)


def same_file(reported: str, expected: str) -> bool:
    reported = reported.lstrip("./")
    return bool(reported) and (
        reported == expected
        or reported.endswith("/" + expected)
        or expected.endswith("/" + reported)
    )


def reported(rec: Record, any_severity: bool = False) -> list[Record]:
    return [
        f
        for f in rec.get("findings") or []
        if isinstance(f, dict) and (any_severity or f.get("severity") == "blocking")
    ]


def score(rec: Record, any_severity: bool = False) -> Record:
    defects = rec["defects"]
    findings = reported(rec, any_severity)
    detected: dict[str, str] = {}
    matched: set[int] = set()
    for i, f in enumerate(findings):
        line = f.get("line") if isinstance(f.get("line"), int) else None
        step = f.get("step") if isinstance(f.get("step"), int) else None
        candidates = []
        for defect_id, span in defects.items():
            if defect_id in detected or not same_file(
                str(f.get("file", "")), span["file"]
            ):
                continue
            by_line = (
                line is not None
                and span["start"] - WINDOW <= line <= span["end"] + WINDOW
            )
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
    file_level = {
        d
        for d, span in defects.items()
        if any(same_file(str(f.get("file", "")), span["file"]) for f in findings)
    }
    return {
        "detected": detected,
        "fileLevel": file_level,
        "unmatched": [f for i, f in enumerate(findings) if i not in matched],
    }


def credits_of(rec: Record) -> float | None:
    rate = CREDITS.get(rec["model"])
    if rate is None:
        return None
    u = rec["usage"]
    return float(
        (u["input"] * rate[0] + u["cacheRead"] * rate[1] + u["output"] * rate[2]) / 1e6
    )


def nearby_arms(
    items: list[tuple[str, int, str, int | None]],
    arm: str,
    file: str,
    line: int | None,
) -> set[str]:
    return {
        a
        for a, _, f2, l2 in items
        if a != arm
        and f2 == file
        and line is not None
        and l2 is not None
        and abs(l2 - line) <= WINDOW
    }


def detections(defect: str, records: list[Record]) -> int:
    return sum(
        defect in r["score"]["detected"] for r in records if defect in r["defects"]
    )


def ever_detected(records: list[Record]) -> set[str]:
    detected: set[str] = set()
    for r in records:
        if r["defects"]:
            detected.update(r["score"]["detected"])
    return detected


def shared_clusters(clean: list[Record]) -> set[TrialKey]:
    """(case, arm, finding index) for clean-case findings that ≥2 arms reported at the same place."""
    by_case: dict[str, list[tuple[str, int, str, int | None]]] = defaultdict(list)
    for r in clean:
        for i, f in enumerate(r["score"]["unmatched"]):
            line = f.get("line") if isinstance(f.get("line"), int) else None
            by_case[r["case"]].append((
                r["arm"],
                i,
                str(f.get("file", "")).lstrip("./"),
                line,
            ))
    shared: set[TrialKey] = set()
    for case, items in by_case.items():
        for arm, i, file, line in items:
            if nearby_arms(items, arm, file, line):
                shared.add((case, arm, i))
    return shared


def scored_records(out: Path, latest: dict[TrialKey, Record]) -> list[Record]:
    """Records that need no re-run, each with its `score` attached."""
    valid = [r for r in latest.values() if not needs_rerun(r, out)]
    for r in valid:
        r["score"] = score(r)
    return valid


def detection_cells(seeded: list[Record]) -> tuple[str, int, int]:
    """Detected, via, any-severity and file-level cells, plus detected and total defect counts."""
    total = sum(len(r["defects"]) for r in seeded)
    hows = [h for r in seeded for h in r["score"]["detected"].values()]
    file_level = sum(len(r["score"]["fileLevel"]) for r in seeded)
    any_sev = sum(len(score(r, any_severity=True)["detected"]) for r in seeded)
    cells = (
        f" {len(hows)}/{total} | {hows.count('line')} / {hows.count('step')} / {hows.count('both')} |"
        f" {any_sev}/{total} | {file_level}/{total} |"
    )
    return cells, len(hows), total


def clean_fp(clean: list[Record], arm: str, shared: set[TrialKey]) -> float:
    """Mean blocking findings per clean trial, excluding clusters shared across arms."""
    if not clean:
        return 0.0
    return statistics.mean(
        sum(
            1
            for i in range(len(r["score"]["unmatched"]))
            if (r["case"], arm, i) not in shared
        )
        for r in clean
    )


def usage_cells(rs: list[Record]) -> tuple[str, float | None]:
    """Unparsed, mismatch, reasoning, cost, credits and wall cells, plus the mean credits."""
    unparsed = sum(r["findings"] is None for r in rs)
    mismatch = sum(r.get("effectiveModels") != [r["model"]] for r in rs)
    reasoning = statistics.mean(r["usage"].get("reasoning", 0) for r in rs) if rs else 0
    cost = statistics.mean(r["usage"]["cost"] for r in rs) if rs else 0
    cr = [c for c in (credits_of(r) for r in rs) if c is not None]
    mean_credits = statistics.mean(cr) if cr else None
    wall = statistics.median(r["wallSeconds"] for r in rs) if rs else 0
    credit_cell = f"{mean_credits:.2f}" if mean_credits is not None else "—"
    return (
        f" {unparsed} | {mismatch} | {reasoning:.0f} | ${cost:.3f} | {credit_cell} | {wall:.0f}s |",
        mean_credits,
    )


def arm_row(
    arm: str, rs: list[Record], invalid: int, expected: int, shared: set[TrialKey]
) -> tuple[str, Record]:
    """One arm's decision-table row and its summary entry."""
    seeded = [r for r in rs if r["defects"]]
    detection, detected, total = detection_cells(seeded)
    fp = clean_fp([r for r in rs if not r["defects"]], arm, shared)
    unmatched_seeded = (
        statistics.mean(len(r["score"]["unmatched"]) for r in seeded) if seeded else 0.0
    )
    usage, mean_credits = usage_cells(rs)
    model = rs[0]["model"].split("/")[-1] + ":" + rs[0]["thinking"] if rs else "?"
    row = (
        f"| {arm} | {model} | {len(rs)}/{expected} |"
        f" {invalid} |{detection} {fp:.2f}{' VETO' if fp > FP_VETO else ''} | {unmatched_seeded:.2f} |{usage}"
    )
    return row, {
        "detected": detected,
        "total": total,
        "fp": fp,
        "credits": mean_credits,
    }


def defect_lines(
    valid: list[Record], by_arm: dict[str, list[Record]], arms: list[str]
) -> list[str]:
    """Per-defect detections across reps, one row per seeded defect."""
    lines = [
        "",
        "Per-defect detections (of reps):",
        "",
        "| Defect | " + " | ".join(arms) + " |",
        "|---|" + "---|" * len(arms),
    ]
    for d in sorted({d for r in valid for d in r["defects"]}):
        lines.append(
            f"| {d} | " + " | ".join(str(detections(d, by_arm[a])) for a in arms) + " |"
        )
    return lines


def discordance_lines(ever: dict[str, set[str]], arms: list[str]) -> list[str]:
    """Paired discordance of each arm against G-high, when G-high ran."""
    if "G-high" not in ever:
        return []
    lines = [
        "",
        "Paired discordance vs G-high (defects found in ≥1 rep): candidate-only − G-high-only",
        "",
    ]
    for arm in arms:
        if arm != "G-high":
            a_only = len(ever[arm] - ever["G-high"])
            g_only = len(ever["G-high"] - ever[arm])
            lines.append(f"- {arm}: +{a_only} / −{g_only} → {a_only - g_only:+d}")
    return lines


def analyse(
    out: Path, latest: dict[TrialKey, Record]
) -> tuple[list[str], dict[str, Record]]:
    arms = sorted({arm for _, arm, _ in latest})
    expected = len({case for case, _, _ in latest}) * len({rep for _, _, rep in latest})
    valid = scored_records(out, latest)
    shared = shared_clusters([r for r in valid if not r["defects"]])

    by_arm: dict[str, list[Record]] = defaultdict(list)
    for r in valid:
        by_arm[r["arm"]].append(r)
    complete = all(len(by_arm[a]) == expected for a in arms)

    lines = list(TABLE_HEADER)
    ever: dict[str, set[str]] = {}
    summary: dict[str, Record] = {}
    for arm in arms:
        invalid = sum(
            1 for (_, a, _), r in latest.items() if a == arm and needs_rerun(r, out)
        )
        row, summary[arm] = arm_row(arm, by_arm[arm], invalid, expected, shared)
        lines.append(row)
        ever[arm] = ever_detected(by_arm[arm])

    lines += defect_lines(valid, by_arm, arms)
    lines += discordance_lines(ever, arms)
    lines += [
        "",
        f"Shared clean-case clusters excluded from FP: {len(shared)}",
        f"Complete (every arm {expected}/{expected} scored): {complete}",
    ]
    if not complete:
        lines.append(
            "INCOMPLETE — decision withheld; run `run_reviews.py --rerun-failed` once, then re-analyse."
        )
    return lines, summary


def main() -> None:
    out = Path(sys.argv[1])
    groups = latest_by_label(out)
    lines: list[str] = []
    summary: dict[str, Record] = {}
    warning = warn_if_mixed(set(groups))
    if warning:
        lines += [warning, ""]
    for label in sorted(groups):
        group_lines, summary[describe(label)] = analyse(out, groups[label])
        lines += [f"## Toolset: {describe(label)}", "", *group_lines, ""]
    text = "\n".join(lines).rstrip()
    (out / "analysis.md").write_text(text + "\n")
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(text)


if __name__ == "__main__":
    main()
