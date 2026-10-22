#!/usr/bin/env python3
"""Tune a decision threshold and check calibration from saved scores.

Reads a CSV with columns `case_id, label, score`, where `label` is 1 when the
condition holds. Optional columns:

- `model`, `question_version`: each combination is reported separately.
- `split`: `dev` or `test`. Tuning and cross-validation use `dev` only; `test` is
  scored once at the shipped threshold. Without the column, every row is `dev`.
- `stratum`: per-stratum precision and recall at the shipped threshold.

Uses only saved scores; makes no model calls. Exits 0 on a report, 1 on invalid
input or a failed self-test, and 2 on a usage error.

Example:
    python3 -B scripts/tune_threshold.py scores.csv --min-precision 0.8 --min-recall 0.8
    python3 -B scripts/tune_threshold.py scores.csv --beta 0.5 --repeats 5 --calibration --errors
    python3 -B scripts/tune_threshold.py --self-test
"""

import argparse
import csv
import io
import json
import math
import random
import sys
from collections import defaultdict

EPS = 1e-6
Z95 = 1.959964


def load(path):
    with open(path, newline="") as f:
        return parse(f)


def parse(f):
    groups = defaultdict(list)
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
        name = row.get("model") or "model"
        if row.get("question_version"):
            name += f" @ {row['question_version']}"
        groups[name].append({
            "id": row.get("case_id") or str(n),
            "y": label,
            "s": float(row["score"]),
            "split": split,
            "stratum": row.get("stratum") or "",
        })
    return groups


def pairs(cases):
    return [(c["y"], c["s"]) for c in cases]


def counts(rows, threshold):
    tp = sum(1 for y, s in rows if s >= threshold and y == 1)
    fp = sum(1 for y, s in rows if s >= threshold and y == 0)
    fn = sum(1 for y, s in rows if s < threshold and y == 1)
    return tp, fp, fn


def metrics(rows, threshold, beta):
    return from_counts(*counts(rows, threshold), beta)


def from_counts(tp, fp, fn, beta):
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    b2 = beta * beta
    denom = b2 * precision + recall
    f = (1 + b2) * precision * recall / denom if denom else 0.0
    return {"precision": precision, "recall": recall, "f": f, "tp": tp, "fp": fp, "fn": fn}


def wilson_lower(successes, n):
    """95% Wilson lower bound for a proportion."""
    if n == 0:
        return 0.0
    p = successes / n
    z2 = Z95 * Z95
    centre = p + z2 / (2 * n)
    margin = Z95 * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    return (centre - margin) / (1 + z2 / n)


def meets(m, args):
    if args.conservative:
        precision = wilson_lower(m["tp"], m["tp"] + m["fp"])
        recall = wilson_lower(m["tp"], m["tp"] + m["fn"])
    else:
        precision, recall = m["precision"], m["recall"]
    return precision >= args.min_precision and recall >= args.min_recall


def candidates(rows):
    """Midpoints between adjacent distinct scores, plus the lowest score."""
    u = sorted({s for _, s in rows})
    return u[:1] + [(lo + hi) / 2 for lo, hi in zip(u, u[1:])]


def choose(rows, args):
    """Best threshold on rows meeting both minimums, or None."""
    best = None
    for t in candidates(rows):
        m = metrics(rows, t, args.beta)
        if not meets(m, args):
            continue
        if best is None or m["f"] > best[1]["f"]:
            best = (t, m)
    return best[0] if best else None


def stratified_folds(rows, k, seed):
    rng = random.Random(seed)
    folds = [[] for _ in range(k)]
    for label in (0, 1):
        idx = [i for i, (y, _) in enumerate(rows) if y == label]
        rng.shuffle(idx)
        for j, i in enumerate(idx):
            folds[j % k].append(i)
    return folds


def pooled(per_fold, beta):
    """Metrics over all held-out decisions, summing counts across folds."""
    tp, fp, fn = (sum(c[i] for c in per_fold) for i in range(3))
    return from_counts(tp, fp, fn, beta)


def tune(rows, fold_sets, args):
    tuned, default, failed, thresholds = [], [], [], []
    for r, folds in enumerate(fold_sets):
        for n, test_idx in enumerate(folds):
            test_set = set(test_idx)
            train = [row for i, row in enumerate(rows) if i not in test_set]
            test = [rows[i] for i in test_idx]
            default.append(counts(test, 0.5))
            t = choose(train, args)
            where = f"repeat {r} fold {n}" if len(fold_sets) > 1 else f"fold {n}"
            if t is None:
                failed.append({"fold": where, "reason": "no threshold met minimums on train"})
                continue
            m = metrics(test, t, args.beta)
            thresholds.append(t)
            tuned.append(counts(test, t))
            if not meets(m, args):
                failed.append({"fold": where, "reason": "held-out below minimums", "threshold": t})
    return {
        "shipped_threshold": choose(rows, args),
        "fold_thresholds": thresholds,
        "default_0.5_heldout": pooled(default, args.beta),
        "tuned_heldout": pooled(tuned, args.beta) if tuned else None,
        "tuned_folds": len(tuned),
        "total_folds": len(default),
        "failed_folds": failed,
    }


def brier(scored):
    return sum((p - y) ** 2 for y, p in scored) / len(scored)


def sigmoid(z):
    z = min(max(z, -30.0), 30.0)
    return 1 / (1 + math.exp(-z))


def logit(p):
    p = min(max(p, EPS), 1 - EPS)
    return math.log(p / (1 - p))


def fit_platt(rows, iters=100, l2=1.0):
    """Logistic regression of label on logit(score) by Newton's method.

    A small L2 prior toward the identity mapping (a=1, b=0) keeps the fit finite
    when the classes are separable.
    """
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


def calibrate(rows, folds):
    raw, scaled = [], []
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


def reliability(scored, per_bin=20):
    scored = sorted(scored, key=lambda r: r[1])
    bins = max(1, len(scored) // per_bin)
    size = math.ceil(len(scored) / bins)
    out = []
    for i in range(0, len(scored), size):
        chunk = scored[i : i + size]
        out.append({
            "n": len(chunk),
            "mean_predicted": sum(p for _, p in chunk) / len(chunk),
            "observed_rate": sum(y for y, _ in chunk) / len(chunk),
        })
    return out


def errors(cases, threshold):
    """False positives and false negatives at the shipped threshold."""
    out = []
    for c in cases:
        flagged = c["s"] >= threshold
        if flagged != (c["y"] == 1):
            out.append({"case_id": c["id"], "split": c["split"], "stratum": c["stratum"],
                        "label": c["y"], "score": c["s"],
                        "kind": "false_positive" if flagged else "false_negative"})
    return sorted(out, key=lambda e: e["score"])


def by_stratum(cases, threshold, beta):
    groups = defaultdict(list)
    for c in cases:
        groups[c["stratum"]].append((c["y"], c["s"]))
    return {name: {**metrics(rows, threshold, beta), "n": len(rows)}
            for name, rows in sorted(groups.items())}


def report(name, cases, args):
    dev = [c for c in cases if c["split"] == "dev"]
    test = [c for c in cases if c["split"] == "test"]
    rows = pairs(dev)
    positives = sum(y for y, _ in rows)
    k = min(args.folds, positives, len(rows) - positives)
    if k < 2:
        return {"model": name, "error": "need at least 2 dev cases of each label"}
    result = {"model": name, "dev_cases": len(rows), "dev_positives": positives,
              "test_cases": len(test), "folds": k, "repeats": args.repeats}
    warnings = []
    if min(positives, len(rows) - positives) < 50:
        warnings.append("fewer than 50 dev cases per label; treat results as noisy")
    if args.min_precision == 0 and args.min_recall == 0:
        warnings.append("no precision or recall minimums set; agree them before shipping")
    if max(s for y, s in rows if y == 0) < min(s for y, s in rows if y == 1):
        warnings.append("dev classes are perfectly separated; expect optimistic results")
    fold_sets = [stratified_folds(rows, k, args.seed + r) for r in range(args.repeats)]
    result.update(tune(rows, fold_sets, args))
    t = result["shipped_threshold"]
    if t is not None and result["tuned_folds"] < result["total_folds"]:
        warnings.append(f"only {result['tuned_folds']} of {result['total_folds']} folds found "
                        "a qualifying threshold; the shipped threshold is not well supported")
    if result["tuned_folds"] < result["total_folds"]:
        warnings.append("default 0.5 covers all folds; tuned covers qualifying folds only")
    ts = result["fold_thresholds"]
    if ts and max(ts) - min(ts) > 0.05:
        warnings.append("fold thresholds vary by more than 0.05; grow the label set")
    if test and t is not None:
        test_rows = pairs(test)
        result["test_at_shipped"] = metrics(test_rows, t, args.beta)
        result["test_at_0.5"] = metrics(test_rows, 0.5, args.beta)
        m = result["test_at_shipped"]
        if not meets(m, args):
            warnings.append("test is below the minimums at the shipped threshold")
    if t is not None and any(c["stratum"] for c in cases):
        scope = test or dev
        result["strata_scope"] = "test" if test else "dev (in-sample)"
        result["strata"] = by_stratum(scope, t, args.beta)
    if args.calibration:
        cal = calibrate(rows, fold_sets[0])
        if test:
            a, b = cal["platt_mapping"]["a"], cal["platt_mapping"]["b"]
            test_rows = pairs(test)
            cal["test_brier_raw"] = brier(test_rows)
            cal["test_brier_platt"] = brier([(y, sigmoid(a * logit(s) + b)) for y, s in test_rows])
        result["calibration"] = cal
    if args.errors and t is not None:
        result["errors"] = errors(dev, t)
    result["warnings"] = warnings
    return result


def fmt(m):
    if not m:
        return "none qualified"
    return f"P {m['precision']:.2f} · R {m['recall']:.2f} · F {m['f']:.2f}"


def print_markdown(results, args):
    rule = "95% lower bounds" if args.conservative else "point estimates"
    print(f"Minimums: precision {args.min_precision}, recall {args.min_recall} ({rule}); "
          f"metric F-beta with beta {args.beta}; seed {args.seed}\n")
    for r in results:
        print(f"## {r['model']}\n")
        if "error" in r:
            print(r["error"] + "\n")
            continue
        for w in r["warnings"]:
            print(f"Warning: {w}")
        if r["warnings"]:
            print()
        t = r["shipped_threshold"]
        print(f"- Dev: {r['dev_cases']} cases ({r['dev_positives']} positive), "
              f"{r['folds']} folds × {r['repeats']} repeats; test: {r['test_cases']} cases")
        print(f"- Default 0.5, dev held-out: {fmt(r['default_0.5_heldout'])}")
        print(f"- Tuned, dev held-out ({r['tuned_folds']} of {r['total_folds']} folds): "
              f"{fmt(r['tuned_heldout'])}")
        print(f"- Shipped threshold: {'none meets minimums' if t is None else f'{t:.4f}'}")
        if r["fold_thresholds"]:
            ts = r["fold_thresholds"]
            print(f"- Fold thresholds: {min(ts):.4f} to {max(ts):.4f}")
        if "test_at_shipped" in r:
            print(f"- Test at shipped threshold: {fmt(r['test_at_shipped'])}; "
                  f"at 0.5: {fmt(r['test_at_0.5'])}")
        failed = r["failed_folds"]
        for f in failed[:10]:
            print(f"- {f['fold']}: {f['reason']}")
        if len(failed) > 10:
            print(f"- … {len(failed) - 10} more failed folds")
        if "calibration" in r:
            c = r["calibration"]
            print(f"- Brier raw {c['brier_raw']:.4f}, Platt held-out "
                  f"{c['brier_platt_heldout']:.4f}: keep {c['keep']}")
            if "test_brier_raw" in c:
                print(f"- Test Brier raw {c['test_brier_raw']:.4f}, Platt {c['test_brier_platt']:.4f}")
            if c["keep"] == "platt":
                pm = c["platt_mapping"]
                print(f"- Platt mapping fitted on all dev: a {pm['a']:.4f}, b {pm['b']:.4f}")
        if r.get("strata"):
            print(f"\nPer stratum at the shipped threshold, {r['strata_scope']}:\n")
            print("| stratum | n | precision | recall |\n| --- | --- | --- | --- |")
            for name, m in r["strata"].items():
                print(f"| {name or '(none)'} | {m['n']} | {m['precision']:.2f} | {m['recall']:.2f} |")
        if r.get("errors"):
            print("\nDev errors at the shipped threshold:\n")
            print("| case_id | stratum | label | score | kind |\n| --- | --- | --- | --- | --- |")
            for e in r["errors"]:
                print(f"| {e['case_id']} | {e['stratum']} | {e['label']} | {e['score']:.4f} "
                      f"| {e['kind']} |")
        print()


def self_test():
    """Check core behaviour on fixed data; return a list of failures."""
    base = dict(min_precision=0.8, min_recall=0.8, conservative=False, beta=1.0, folds=5,
                repeats=1, seed=0, calibration=True, errors=True)
    args = argparse.Namespace(**base)
    fails = []

    def case(i, y, s, split="dev", stratum=""):
        return {"id": str(i), "y": y, "s": s, "split": split, "stratum": stratum}

    shifted = [case(i, int(i < 30), 0.40 if i < 30 else 0.10) for i in range(60)]
    r = report("shifted", shifted, args)
    if r["shipped_threshold"] != 0.25:
        fails.append(f"0.40/0.10 threshold: expected 0.25, got {r['shipped_threshold']}")
    if r["default_0.5_heldout"]["recall"] != 0.0:
        fails.append("0.40/0.10 at 0.5 should catch nothing")
    cal = r["calibration"]
    pm = cal["platt_mapping"]
    if not all(math.isfinite(v) for v in (pm["a"], pm["b"], cal["brier_platt_heldout"])):
        fails.append("Platt fit is not finite on separable data")

    overlap = [case(i, i % 2, 0.5) for i in range(40)]
    if report("overlap", overlap, args)["shipped_threshold"] is not None:
        fails.append("impossible minimums should ship no threshold")

    split = [case(i, int(i < 30), 0.40 if i < 30 else 0.10) for i in range(60)]
    split += [case(100 + i, 1, 0.05, "test", "phone") for i in range(10)]
    r = report("split", split, args)
    if "test_brier_platt" not in r["calibration"] or not r["calibration"]["reliability_platt_heldout"]:
        fails.append("calibration does not report test Brier or Platt reliability")
    if r["dev_cases"] != 60 or r["test_cases"] != 10:
        fails.append("dev and test cases are not kept apart")
    if r["shipped_threshold"] != 0.25 or r.get("test_at_shipped", {}).get("recall") != 0.0:
        fails.append("test cases influenced tuning or were not scored once")

    text = "case_id,label,score,model,question_version\n1,1,0.9,a,v1\n2,0,0.1,a,v2\n3,1,0.8,b,v1\n"
    names = sorted(parse(io.StringIO(text)))
    if names != ["a @ v1", "a @ v2", "b @ v1"]:
        fails.append(f"model and version grouping: got {names}")
    return fails


def main():
    p = argparse.ArgumentParser(
        description="Tune a decision threshold and check calibration from saved scores.")
    p.add_argument("csv", nargs="?")
    p.add_argument("--self-test", action="store_true", help="run built-in checks and exit")
    p.add_argument("--min-precision", type=float, default=0.0)
    p.add_argument("--min-recall", type=float, default=0.0)
    p.add_argument("--conservative", action="store_true",
                   help="require the 95%% lower bound, not the point estimate, to meet minimums")
    p.add_argument("--beta", type=float, default=1.0, help="<1 favors precision, >1 recall")
    p.add_argument("--folds", type=int, default=5)
    p.add_argument("--repeats", type=int, default=1, help="repeat CV with new seeds")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--calibration", action="store_true", help="also test Platt scaling")
    p.add_argument("--errors", action="store_true",
                   help="list dev false positives and negatives at the shipped threshold")
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
    try:
        groups = load(args.csv)
    except (OSError, ValueError) as e:
        sys.exit(f"cannot read {args.csv}: {e}")
    results = [report(name, groups[name], args) for name in sorted(groups)]
    if args.json:
        json.dump(results, sys.stdout, indent=2)
        print()
    else:
        print_markdown(results, args)


if __name__ == "__main__":
    main()
