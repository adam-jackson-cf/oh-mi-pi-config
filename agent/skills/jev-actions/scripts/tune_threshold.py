#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import random
import sys
from collections import defaultdict
from dataclasses import dataclass, fields
from itertools import pairwise
from typing import Any, TextIO, TypedDict

EPS = 1e-6
Z95 = 1.959964

USAGE = """\
Reads a CSV with columns `case_id, label, score`, where `label` is 1 when the
condition holds and `score` is a raw probability in [0, 1]. Optional columns:

- `model`, `question_version`: each combination is reported separately. Groups
  that do not share the same cases, labels, splits and units are flagged as not
  comparable.
- `split`: `dev` or `test`. Tuning and cross-validation use `dev` only; `test` is
  scored once at the shipped threshold. Without the column, every row is `dev`.
- `unit`: the independent unit a case came from, such as a session, user,
  repository or conversation. Cross-validation keeps each unit in one fold, and a
  unit may not appear in both `dev` and `test`. Without the column, each case is
  its own unit.
- `stratum`: per-stratum precision and recall at the shipped threshold.

Before any threshold, each group reports its ranking on `dev`: AUC, the chance a
labeled 1 scores above a labeled 0, with a 95% interval from resampling whole
units. Ranking fails when the interval includes 0.5 or AUC is below `--min-auc`;
then no threshold or band is meaningful, whatever the report tunes.

Uses only saved scores; makes no model calls. Exits 0 on a report, 1 on invalid
input or a failed self-test, and 2 on a usage error.

Example:
    python3 -B scripts/tune_threshold.py scores.csv --min-precision 0.8 --min-recall 0.8
    python3 -B scripts/tune_threshold.py scores.csv --beta 0.5 --repeats 5 --calibration --errors
    python3 -B scripts/tune_threshold.py --self-test
"""

Row = tuple[int, float]
Counts = tuple[int, int, int]
Result = dict[str, Any]


class Case(TypedDict):
    id: str
    unit: str
    y: int
    s: float
    split: str
    stratum: str


class Metrics(TypedDict):
    precision: float
    recall: float
    f: float
    tp: int
    fp: int
    fn: int


class StratumMetrics(Metrics):
    n: int


@dataclass(frozen=True)
class Options:
    min_precision: float
    min_recall: float
    conservative: bool
    beta: float
    folds: int
    repeats: int
    seed: int
    calibration: bool
    errors: bool
    min_auc: float = 0.7
    auc_resamples: int = 1000

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> Options:
        return cls(**{f.name: getattr(args, f.name) for f in fields(cls)})


def load(path: str) -> dict[str, list[Case]]:
    with open(path, newline="") as f:
        return parse(f)


def parse(f: TextIO) -> dict[str, list[Case]]:
    groups: dict[str, list[Case]] = defaultdict(list)
    reader = csv.DictReader(f)
    missing = {"label", "score"} - set(reader.fieldnames or [])
    if missing:
        sys.exit(f"missing columns: {', '.join(sorted(missing))}")
    for n, row in enumerate(reader):
        label = int(row["label"])
        if label not in (0, 1):
            sys.exit(f"label must be 0 or 1, got {row['label']!r}")
        split = (row.get("split") or "dev").strip().lower()
        if split not in ("dev", "test"):
            sys.exit(f"split must be dev or test, got {row['split']!r}")
        score = float(row["score"])
        if not (math.isfinite(score) and 0.0 <= score <= 1.0):
            sys.exit(
                f"score must be a finite probability in [0, 1], got {row['score']!r}"
            )
        name = row.get("model") or "model"
        if row.get("question_version"):
            name += f" @ {row['question_version']}"
        case_id = row.get("case_id") or str(n)
        groups[name].append({
            "id": case_id,
            "unit": row.get("unit") or case_id,
            "y": label,
            "s": score,
            "split": split,
            "stratum": row.get("stratum") or "",
        })
    for name, cases in groups.items():
        check_group(name, cases)
    return groups


def check_group(name: str, cases: list[Case]) -> None:
    seen: set[str] = set()
    unit_split: dict[str, str] = {}
    for c in cases:
        if c["id"] in seen:
            sys.exit(f"{name}: case_id {c['id']!r} appears more than once")
        seen.add(c["id"])
        if unit_split.setdefault(c["unit"], c["split"]) != c["split"]:
            sys.exit(
                f"{name}: unit {c['unit']!r} appears in both dev and test; split by unit"
            )


def case_set(cases: list[Case]) -> list[tuple[str, int, str, str]]:
    return sorted((c["id"], c["y"], c["split"], c["unit"]) for c in cases)


def comparable(groups: dict[str, list[Case]]) -> bool:
    sets = [case_set(cases) for cases in groups.values()]
    return all(s == sets[0] for s in sets)


def pairs(cases: list[Case]) -> list[Row]:
    return [(c["y"], c["s"]) for c in cases]


def counts(rows: list[Row], threshold: float) -> Counts:
    tp = sum(1 for y, s in rows if s >= threshold and y == 1)
    fp = sum(1 for y, s in rows if s >= threshold and y == 0)
    fn = sum(1 for y, s in rows if s < threshold and y == 1)
    return tp, fp, fn


def metrics(rows: list[Row], threshold: float, beta: float) -> Metrics:
    return from_counts(*counts(rows, threshold), beta)


def from_counts(tp: int, fp: int, fn: int, beta: float) -> Metrics:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    b2 = beta * beta
    denom = b2 * precision + recall
    f = (1 + b2) * precision * recall / denom if denom else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "f": f,
        "tp": tp,
        "fp": fp,
        "fn": fn,
    }


def wilson_lower(successes: int, n: int) -> float:
    if n == 0:
        return 0.0
    p = successes / n
    z2 = Z95 * Z95
    centre = p + z2 / (2 * n)
    margin = Z95 * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    return (centre - margin) / (1 + z2 / n)


def meets(m: Metrics, opts: Options) -> bool:
    if opts.conservative:
        precision = wilson_lower(m["tp"], m["tp"] + m["fp"])
        recall = wilson_lower(m["tp"], m["tp"] + m["fn"])
    else:
        precision, recall = m["precision"], m["recall"]
    return precision >= opts.min_precision and recall >= opts.min_recall


def candidates(rows: list[Row]) -> list[float]:
    u = sorted({s for _, s in rows})
    return u[:1] + [(lo + hi) / 2 for lo, hi in pairwise(u)]


def choose(rows: list[Row], opts: Options) -> float | None:
    best: tuple[float, Metrics] | None = None
    for t in candidates(rows):
        m = metrics(rows, t, opts.beta)
        if not meets(m, opts):
            continue
        if best is None or m["f"] > best[1]["f"]:
            best = (t, m)
    return best[0] if best else None


def label_units(
    cases: list[Case],
) -> tuple[dict[str, list[int]], list[list[str]]]:
    units: dict[str, list[int]] = defaultdict(list)
    for i, c in enumerate(cases):
        units[c["unit"]].append(i)
    kinds: dict[str, list[str]] = {"pos": [], "mixed": [], "neg": []}
    for u in sorted(units):
        labels = {cases[i]["y"] for i in units[u]}
        kind = "mixed" if len(labels) == 2 else "pos" if 1 in labels else "neg"
        kinds[kind].append(u)
    return units, [kinds["pos"], kinds["mixed"], kinds["neg"]]


def unit_folds(cases: list[Case], k: int, seed: int) -> list[list[int]]:
    rng = random.Random(seed)
    units, groups = label_units(cases)
    folds: list[list[int]] = [[] for _ in range(k)]
    j = 0
    for group in groups:
        rng.shuffle(group)
        for u in group:
            folds[j % k].extend(units[u])
            j += 1
    return folds


def pooled(per_fold: list[Counts], beta: float) -> Metrics:
    tp, fp, fn = (sum(c[i] for c in per_fold) for i in range(3))
    return from_counts(tp, fp, fn, beta)


def split_fold(rows: list[Row], test_idx: list[int]) -> tuple[list[Row], list[Row]]:
    test_set = set(test_idx)
    train = [row for i, row in enumerate(rows) if i not in test_set]
    return train, [rows[i] for i in test_idx]


def tune(rows: list[Row], fold_sets: list[list[list[int]]], opts: Options) -> Result:
    tuned: list[Counts] = []
    default: list[Counts] = []
    failed: list[dict[str, Any]] = []
    thresholds: list[float] = []
    for r, folds in enumerate(fold_sets):
        for n, test_idx in enumerate(folds):
            train, test = split_fold(rows, test_idx)
            default.append(counts(test, 0.5))
            t = choose(train, opts)
            where = f"repeat {r} fold {n}" if len(fold_sets) > 1 else f"fold {n}"
            if t is None:
                failed.append({
                    "fold": where,
                    "reason": "no threshold met minimums on train",
                })
                continue
            m = metrics(test, t, opts.beta)
            thresholds.append(t)
            tuned.append(counts(test, t))
            if not meets(m, opts):
                failed.append({
                    "fold": where,
                    "reason": "held-out below minimums",
                    "threshold": t,
                })
    return {
        "shipped_threshold": choose(rows, opts),
        "fold_thresholds": thresholds,
        "default_0.5_heldout": pooled(default, opts.beta),
        "tuned_heldout": pooled(tuned, opts.beta) if tuned else None,
        "tuned_folds": len(tuned),
        "total_folds": len(default),
        "failed_folds": failed,
    }


def auc(rows: list[Row]) -> float | None:
    """Rank-sum AUC; tied scores count half. None without both labels."""
    positives = sum(y for y, _ in rows)
    negatives = len(rows) - positives
    if not positives or not negatives:
        return None
    ordered = sorted(rows, key=lambda r: r[1])
    rank_sum = 0.0
    i = 0
    while i < len(ordered):
        j = i
        while j < len(ordered) and ordered[j][1] == ordered[i][1]:
            j += 1
        rank_sum += (i + 1 + j) / 2 * sum(y for y, _ in ordered[i:j])
        i = j
    return (rank_sum - positives * (positives + 1) / 2) / (positives * negatives)


def auc_interval(
    cases: list[Case], resamples: int, seed: int
) -> tuple[float, float] | None:
    """95% percentile interval from resampling whole units with replacement."""
    units, (pos_only, mixed, neg_only) = label_units(cases)
    if min(len(pos_only), len(neg_only)) + len(mixed) < 2:
        return None
    names = sorted(units)
    rng = random.Random(seed)
    values: list[float] = []
    for _ in range(resamples):
        drawn = rng.choices(names, k=len(names))
        value = auc([(cases[i]["y"], cases[i]["s"]) for u in drawn for i in units[u]])
        if value is not None:
            values.append(value)
    if not values:
        return None
    values.sort()
    last = len(values) - 1
    return values[int(0.025 * last)], values[math.ceil(0.975 * last)]


def discrimination(cases: list[Case], opts: Options) -> Result:
    value = auc(pairs(cases))
    interval = (
        auc_interval(cases, opts.auc_resamples, opts.seed)
        if value is not None
        else None
    )
    passes = (
        value is not None
        and interval is not None
        and interval[0] > 0.5
        and value >= opts.min_auc
    )
    return {
        "auc": value,
        "interval": list(interval) if interval else None,
        "floor": opts.min_auc,
        "resamples": opts.auc_resamples,
        "passes": passes,
    }


def rank_warnings(rank: Result) -> list[str]:
    if rank["passes"]:
        return []
    return [
        "ranking does not beat chance on dev (AUC interval includes 0.5, is "
        "missing, or AUC is below the floor); do not tune or band, and take the "
        "question to error analysis as wording, missing state or a policy gap"
    ]


def brier(scored: list[tuple[int, float]]) -> float:
    return sum((p - y) ** 2 for y, p in scored) / len(scored)


def sigmoid(z: float) -> float:
    z = min(max(z, -30.0), 30.0)
    return 1 / (1 + math.exp(-z))


def logit(p: float) -> float:
    p = min(max(p, EPS), 1 - EPS)
    return math.log(p / (1 - p))


def fit_platt(
    rows: list[Row], iters: int = 100, l2: float = 1.0
) -> tuple[float, float]:
    a, b = 1.0, 0.0
    for _ in range(iters):
        ga, gb = l2 * (a - 1), l2 * b
        haa = hbb = l2
        hab = 0.0
        for y, s in rows:
            x = logit(s)
            p = sigmoid(a * x + b)
            w = p * (1 - p)
            ga += (p - y) * x
            gb += p - y
            haa += w * x * x
            hab += w * x
            hbb += w
        det = haa * hbb - hab * hab
        if abs(det) < 1e-12:
            break
        da = (hbb * ga - hab * gb) / det
        db = (haa * gb - hab * ga) / det
        a, b = a - da, b - db
        if abs(da) + abs(db) < 1e-9:
            break
    return a, b


def calibrate(rows: list[Row], folds: list[list[int]]) -> Result:
    raw: list[tuple[int, float]] = []
    scaled: list[tuple[int, float]] = []
    for test_idx in folds:
        test_set = set(test_idx)
        train = [r for i, r in enumerate(rows) if i not in test_set]
        a, b = fit_platt(train)
        for i in test_idx:
            y, s = rows[i]
            raw.append((y, s))
            scaled.append((y, sigmoid(a * logit(s) + b)))
    raw_b, scaled_b = brier(raw), brier(scaled)
    a, b = fit_platt(rows)
    return {
        "platt_mapping": {"a": a, "b": b, "apply": "sigmoid(a * logit(score) + b)"},
        "brier_raw": raw_b,
        "brier_platt_heldout": scaled_b,
        "keep": "platt" if scaled_b < raw_b else "raw",
        "reliability_raw": reliability(raw),
        "reliability_platt_heldout": reliability(scaled),
    }


def reliability(
    scored: list[tuple[int, float]], per_bin: int = 20
) -> list[dict[str, float]]:
    scored = sorted(scored, key=lambda r: r[1])
    bins = max(1, len(scored) // per_bin)
    size = math.ceil(len(scored) / bins)
    out: list[dict[str, float]] = []
    for i in range(0, len(scored), size):
        chunk = scored[i : i + size]
        out.append({
            "n": len(chunk),
            "mean_predicted": sum(p for _, p in chunk) / len(chunk),
            "observed_rate": sum(y for y, _ in chunk) / len(chunk),
        })
    return out


def errors(cases: list[Case], threshold: float) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for c in cases:
        flagged = c["s"] >= threshold
        if flagged != (c["y"] == 1):
            out.append({
                "case_id": c["id"],
                "split": c["split"],
                "stratum": c["stratum"],
                "label": c["y"],
                "score": c["s"],
                "kind": "false_positive" if flagged else "false_negative",
            })
    return sorted(out, key=lambda e: e["score"])


def by_stratum(
    cases: list[Case], threshold: float, beta: float
) -> dict[str, StratumMetrics]:
    groups: dict[str, list[Row]] = defaultdict(list)
    for c in cases:
        groups[c["stratum"]].append((c["y"], c["s"]))
    return {
        name: StratumMetrics(**metrics(rows, threshold, beta), n=len(rows))
        for name, rows in sorted(groups.items())
    }


def data_warnings(
    rows: list[Row], positives: int, opts: Options, comparable_groups: bool
) -> list[str]:
    warnings = []
    if not comparable_groups:
        warnings.append(
            "models or question versions do not share the same cases, labels, splits "
            "and units; do not compare them"
        )
    if min(positives, len(rows) - positives) < 50:
        warnings.append("fewer than 50 dev cases per label; treat results as noisy")
    if opts.min_precision == 0 and opts.min_recall == 0:
        warnings.append(
            "no precision or recall minimums set; agree them before shipping"
        )
    if max(s for y, s in rows if y == 0) < min(s for y, s in rows if y == 1):
        warnings.append(
            "dev classes are perfectly separated; expect optimistic results"
        )
    return warnings


def fold_warnings(result: Result) -> list[str]:
    warnings = []
    if (
        result["shipped_threshold"] is not None
        and result["tuned_folds"] < result["total_folds"]
    ):
        warnings.append(
            f"only {result['tuned_folds']} of {result['total_folds']} folds found "
            "a qualifying threshold; the shipped threshold is not well supported"
        )
    if result["tuned_folds"] < result["total_folds"]:
        warnings.append(
            "default 0.5 covers all folds; tuned covers qualifying folds only"
        )
    ts = result["fold_thresholds"]
    if ts and max(ts) - min(ts) > 0.05:
        warnings.append("fold thresholds vary by more than 0.05; grow the label set")
    return warnings


def score_test(
    result: Result, test: list[Case], threshold: float, opts: Options
) -> list[str]:
    test_rows = pairs(test)
    result["test_at_shipped"] = metrics(test_rows, threshold, opts.beta)
    result["test_at_0.5"] = metrics(test_rows, 0.5, opts.beta)
    if not meets(result["test_at_shipped"], opts):
        return ["test is below the minimums at the shipped threshold"]
    return []


def calibration_report(
    rows: list[Row], folds: list[list[int]], test: list[Case]
) -> Result:
    cal = calibrate(rows, folds)
    if test:
        a, b = cal["platt_mapping"]["a"], cal["platt_mapping"]["b"]
        test_rows = pairs(test)
        cal["test_brier_raw"] = brier(test_rows)
        cal["test_brier_platt"] = brier([
            (y, sigmoid(a * logit(s) + b)) for y, s in test_rows
        ])
        if cal["keep"] == "platt" and cal["test_brier_platt"] > cal["test_brier_raw"]:
            cal["keep"] = "raw"
            cal["platt_rejected_on_test"] = True
    return cal


def report(
    name: str, cases: list[Case], opts: Options, comparable_groups: bool = True
) -> Result:
    dev = [c for c in cases if c["split"] == "dev"]
    test = [c for c in cases if c["split"] == "test"]
    rows = pairs(dev)
    positives = sum(y for y, _ in rows)
    _, (pos_only, mixed, neg_only) = label_units(dev)
    k = min(opts.folds, len(pos_only) + len(mixed), len(mixed) + len(neg_only))
    if k < 2:
        return {
            "model": name,
            "error": "need at least 2 dev units with each label",
            "discrimination": discrimination(dev, opts),
        }
    result: Result = {
        "model": name,
        "dev_cases": len(rows),
        "dev_positives": positives,
        "test_cases": len(test),
        "folds": k,
        "repeats": opts.repeats,
        "comparable": comparable_groups,
        "discrimination": discrimination(dev, opts),
    }
    warnings = rank_warnings(result["discrimination"])
    warnings += data_warnings(rows, positives, opts, comparable_groups)
    fold_sets = [unit_folds(dev, k, opts.seed + r) for r in range(opts.repeats)]
    result.update(tune(rows, fold_sets, opts))
    warnings += fold_warnings(result)
    t = result["shipped_threshold"]
    if test and t is not None:
        warnings += score_test(result, test, t, opts)
    if t is not None and any(c["stratum"] for c in cases):
        result["strata_scope"] = "test" if test else "dev (in-sample)"
        result["strata"] = by_stratum(test or dev, t, opts.beta)
    if opts.calibration:
        result["calibration"] = calibration_report(rows, fold_sets[0], test)
    if opts.errors and t is not None:
        result["errors"] = errors(dev, t)
    result["warnings"] = warnings
    return result


def fmt_rank(rank: Result) -> str:
    verdict = "passes" if rank["passes"] else "fails"
    if rank["auc"] is None:
        return f"- Ranking on dev: no AUC without both labels; {verdict}"
    interval = rank["interval"]
    spread = (
        f"95% unit interval {interval[0]:.2f} to {interval[1]:.2f}"
        if interval
        else "no interval without 2 units of each label"
    )
    return (
        f"- Ranking on dev: AUC {rank['auc']:.2f} ({spread}; floor {rank['floor']}): "
        f"{verdict}"
    )


def fmt(m: Metrics | None) -> str:
    if not m:
        return "none qualified"
    return f"P {m['precision']:.2f} · R {m['recall']:.2f} · F {m['f']:.2f}"


def print_summary(r: Result) -> None:
    t = r["shipped_threshold"]
    print(fmt_rank(r["discrimination"]))
    print(
        f"- Dev: {r['dev_cases']} cases ({r['dev_positives']} positive), "
        f"{r['folds']} folds × {r['repeats']} repeats; test: {r['test_cases']} cases"
    )
    print(f"- Default 0.5, dev held-out: {fmt(r['default_0.5_heldout'])}")
    print(
        f"- Tuned, dev held-out ({r['tuned_folds']} of {r['total_folds']} folds): "
        f"{fmt(r['tuned_heldout'])}"
    )
    print(f"- Shipped threshold: {'none meets minimums' if t is None else f'{t:.4f}'}")
    if r["fold_thresholds"]:
        ts = r["fold_thresholds"]
        print(f"- Fold thresholds: {min(ts):.4f} to {max(ts):.4f}")
    if "test_at_shipped" in r:
        print(
            f"- Test at shipped threshold: {fmt(r['test_at_shipped'])}; "
            f"at 0.5: {fmt(r['test_at_0.5'])}"
        )
    failed = r["failed_folds"]
    for f in failed[:10]:
        print(f"- {f['fold']}: {f['reason']}")
    if len(failed) > 10:
        print(f"- … {len(failed) - 10} more failed folds")


def print_calibration(c: Result) -> None:
    print(
        f"- Brier raw {c['brier_raw']:.4f}, Platt held-out "
        f"{c['brier_platt_heldout']:.4f}: keep {c['keep']}"
    )
    if "test_brier_raw" in c:
        print(
            f"- Test Brier raw {c['test_brier_raw']:.4f}, Platt {c['test_brier_platt']:.4f}"
        )
    if c.get("platt_rejected_on_test"):
        print("- Platt mapping rejected: worse than raw on test")
    if c["keep"] == "platt":
        pm = c["platt_mapping"]
        print(f"- Platt mapping fitted on all dev: a {pm['a']:.4f}, b {pm['b']:.4f}")


def print_tables(r: Result) -> None:
    if r.get("strata"):
        print(f"\nPer stratum at the shipped threshold, {r['strata_scope']}:\n")
        print("| stratum | n | precision | recall |\n| --- | --- | --- | --- |")
        for name, m in r["strata"].items():
            print(
                f"| {name or '(none)'} | {m['n']} | {m['precision']:.2f} | {m['recall']:.2f} |"
            )
    if r.get("errors"):
        print("\nDev errors at the shipped threshold:\n")
        print(
            "| case_id | stratum | label | score | kind |\n| --- | --- | --- | --- | --- |"
        )
        for e in r["errors"]:
            print(
                f"| {e['case_id']} | {e['stratum']} | {e['label']} | {e['score']:.4f} "
                f"| {e['kind']} |"
            )


def print_markdown(results: list[Result], opts: Options) -> None:
    rule = "95% lower bounds" if opts.conservative else "point estimates"
    print(
        f"Minimums: precision {opts.min_precision}, recall {opts.min_recall} ({rule}); "
        f"metric F-beta with beta {opts.beta}; seed {opts.seed}\n"
    )
    for r in results:
        print(f"## {r['model']}\n")
        if "error" in r:
            print(fmt_rank(r["discrimination"]))
            print(r["error"] + "\n")
            continue
        for w in r["warnings"]:
            print(f"Warning: {w}")
        if r["warnings"]:
            print()
        print_summary(r)
        if "calibration" in r:
            print_calibration(r["calibration"])
        print_tables(r)
        print()


def self_test() -> list[str]:
    args = Options(
        min_precision=0.8,
        min_recall=0.8,
        conservative=False,
        beta=1.0,
        folds=5,
        repeats=1,
        seed=0,
        calibration=True,
        errors=True,
    )
    fails = []

    def case(i: int, y: int, s: float, split: str = "dev", stratum: str = "") -> Case:
        return {
            "id": str(i),
            "unit": str(i),
            "y": y,
            "s": s,
            "split": split,
            "stratum": stratum,
        }

    shifted = [case(i, int(i < 30), 0.40 if i < 30 else 0.10) for i in range(60)]
    r = report("shifted", shifted, args)
    if r["shipped_threshold"] != 0.25:
        fails.append(
            f"0.40/0.10 threshold: expected 0.25, got {r['shipped_threshold']}"
        )
    if r["default_0.5_heldout"]["recall"] != 0.0:
        fails.append("0.40/0.10 at 0.5 should catch nothing")
    cal = r["calibration"]
    pm = cal["platt_mapping"]
    if not all(
        math.isfinite(v) for v in (pm["a"], pm["b"], cal["brier_platt_heldout"])
    ):
        fails.append("Platt fit is not finite on separable data")

    overlap = [case(i, i % 2, 0.5) for i in range(40)]
    if report("overlap", overlap, args)["shipped_threshold"] is not None:
        fails.append("impossible minimums should ship no threshold")

    split = [case(i, int(i < 30), 0.40 if i < 30 else 0.10) for i in range(60)]
    split += [case(100 + i, 1, 0.05, "test", "phone") for i in range(10)]
    r = report("split", split, args)
    if (
        "test_brier_platt" not in r["calibration"]
        or not r["calibration"]["reliability_platt_heldout"]
    ):
        fails.append("calibration does not report test Brier or Platt reliability")
    if r["dev_cases"] != 60 or r["test_cases"] != 10:
        fails.append("dev and test cases are not kept apart")
    if (
        r["shipped_threshold"] != 0.25
        or r.get("test_at_shipped", {}).get("recall") != 0.0
    ):
        fails.append("test cases influenced tuning or were not scored once")

    text = "case_id,label,score,model,question_version\n1,1,0.9,a,v1\n2,0,0.1,a,v2\n3,1,0.8,b,v1\n"
    names = sorted(parse(io.StringIO(text)))
    if names != ["a @ v1", "a @ v2", "b @ v1"]:
        fails.append(f"model and version grouping: got {names}")
    return (
        fails
        + input_failures()
        + unit_failures(args)
        + comparable_failures()
        + rank_failures(args)
    )


def rejected(text: str) -> bool:
    try:
        parse(io.StringIO(text))
    except SystemExit:
        return True
    return False


def input_failures() -> list[str]:
    fails = [
        f"score {bad} was accepted"
        for bad in ("1.5", "-0.1", "nan", "inf")
        if not rejected(f"case_id,label,score\n1,1,{bad}\n")
    ]
    if not rejected("case_id,label,score,split,unit\n1,1,0.9,dev,u\n2,0,0.1,test,u\n"):
        fails.append("a unit in both dev and test was accepted")
    return fails


def unit_case(i: int, y: int, s: float, unit: str) -> Case:
    return {"id": str(i), "unit": unit, "y": y, "s": s, "split": "dev", "stratum": ""}


def fold_ids(cases: list[Case]) -> list[list[str]]:
    return [sorted(cases[i]["id"] for i in f) for f in unit_folds(cases, 5, 0)]


def split_units(cases: list[Case], folds: list[list[int]]) -> bool:
    where = {cases[i]["unit"]: n for n, f in enumerate(folds) for i in f}
    return any(where[cases[i]["unit"]] != n for n, f in enumerate(folds) for i in f)


def training_labels(
    cases: list[Case], folds: list[list[int]], held_out: int
) -> set[int]:
    return {cases[i]["y"] for m, f in enumerate(folds) if m != held_out for i in f}


def lost_label(cases: list[Case], folds: list[list[int]]) -> bool:
    return any(training_labels(cases, folds, n) != {0, 1} for n in range(len(folds)))


def unit_failures(args: Options) -> list[str]:
    fails = []
    grouped = [
        unit_case(i, int(i < 30), 0.40 if i < 30 else 0.10, f"u{i // 3}")
        for i in range(60)
    ]
    if any(split_units(grouped, unit_folds(grouped, 5, seed)) for seed in range(5)):
        fails.append("a unit's cases were split across folds")
    if fold_ids(grouped) != fold_ids(grouped[::-1]):
        fails.append("row order changed the folds for the same cases")
    mixed = [
        unit_case(i, i % 2, 0.80 if i % 2 else 0.20, f"s{i // 4}") for i in range(40)
    ]
    r = report("mixed units", mixed, args)
    if r.get("folds") != 5 or r["shipped_threshold"] is None:
        fails.append(
            f"units holding both labels gave no grouped CV: {r.get('error', r.get('folds'))}"
        )
    if any(lost_label(mixed, unit_folds(mixed, 5, seed)) for seed in range(5)):
        fails.append("a training set lost a label")
    return fails


def rank_failures(args: Options) -> list[str]:
    fails = []
    checks = [
        ([(1, 0.9), (1, 0.4), (0, 0.6), (0, 0.1)], 0.75),
        ([(1, 0.5), (0, 0.5)], 0.5),
        ([(1, 0.1), (0, 0.9)], 0.0),
    ]
    for rows, expected in checks:
        if auc(rows) != expected:
            fails.append(f"AUC of {rows}: expected {expected}, got {auc(rows)}")
    if auc([(1, 0.9), (1, 0.8)]) is not None:
        fails.append("AUC with one label should be None")
    flat = [unit_case(i, i % 2, 0.5, f"u{i}") for i in range(40)]
    if discrimination(flat, args)["passes"]:
        fails.append("tied scores passed the ranking check")
    ranked = [unit_case(i, i % 2, 0.2 + 0.6 * (i % 2), f"u{i}") for i in range(40)]
    if not discrimination(ranked, args)["passes"]:
        fails.append("perfect ranking failed the ranking check")
    lumped = [unit_case(i, i % 2, 0.2 + 0.6 * (i % 2), f"l{i % 2}") for i in range(40)]
    if auc_interval(lumped, 100, 0) is not None:
        fails.append("one unit per label gave an AUC interval")
    return fails


def comparable_failures() -> list[str]:
    fails = []
    text = (
        "case_id,label,score,model\n1,1,0.9,a\n2,0,0.1,a\n3,1,0.8,a\n"
        "1,1,0.7,b\n2,0,0.2,b\n"
    )
    if comparable(parse(io.StringIO(text))):
        fails.append("groups over different cases were reported as comparable")
    text = "case_id,label,score,model\n1,1,0.9,a\n2,0,0.1,a\n1,1,0.7,b\n2,0,0.2,b\n"
    if not comparable(parse(io.StringIO(text))):
        fails.append("groups over the same cases were reported as not comparable")
    return fails


def main() -> None:
    p = argparse.ArgumentParser(
        description="Tune a decision threshold and check calibration from saved scores.",
        epilog=USAGE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("csv", nargs="?")
    p.add_argument(
        "--self-test", action="store_true", help="run built-in checks and exit"
    )
    p.add_argument("--min-precision", type=float, default=0.0)
    p.add_argument("--min-recall", type=float, default=0.0)
    p.add_argument(
        "--conservative",
        action="store_true",
        help="require the 95%% lower bound, not the point estimate, to meet minimums",
    )
    p.add_argument(
        "--beta", type=float, default=1.0, help="<1 favors precision, >1 recall"
    )
    p.add_argument("--folds", type=int, default=5)
    p.add_argument("--repeats", type=int, default=1, help="repeat CV with new seeds")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--calibration", action="store_true", help="also test Platt scaling")
    p.add_argument(
        "--errors",
        action="store_true",
        help="list dev false positives and negatives at the shipped threshold",
    )
    p.add_argument(
        "--min-auc",
        type=float,
        default=0.7,
        help="ranking floor; below it, or with 0.5 in the interval, ranking fails",
    )
    p.add_argument(
        "--auc-resamples",
        type=int,
        default=1000,
        help="unit resamples for the AUC interval",
    )
    p.add_argument("--json", action="store_true")
    args = p.parse_args()
    if args.self_test:
        fails = self_test()
        for f in fails:
            print(f"self-test FAIL: {f}")
        print("self-test ok" if not fails else f"self-test failed: {len(fails)}")
        sys.exit(1 if fails else 0)
    if not args.csv:
        p.error("the csv argument is required unless --self-test is given")
    if args.repeats < 1:
        p.error("--repeats must be at least 1")
    if args.auc_resamples < 1:
        p.error("--auc-resamples must be at least 1")
    if not 0.5 <= args.min_auc <= 1:
        p.error("--min-auc must be between 0.5 and 1")
    try:
        groups = load(args.csv)
    except (OSError, ValueError) as e:
        sys.exit(f"cannot read {args.csv}: {e}")
    opts = Options.from_args(args)
    same = comparable(groups)
    results = [report(name, groups[name], opts, same) for name in sorted(groups)]
    if args.json:
        json.dump(results, sys.stdout, indent=2)
        print()
    else:
        print_markdown(results, opts)


if __name__ == "__main__":
    main()
