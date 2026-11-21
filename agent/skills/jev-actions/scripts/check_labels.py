#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, TextIO

USAGE = """\
Reads `cases.jsonl` and `labels.jsonl` as the eval data schemas describe, and
checks the labels a tuning run may count, per question:

- Provenance: an `arbiter` row counts only when it names the arbiter in `by`,
  has a `timestamp`, records `confirmed` as `individual`, and shares its
  arbiter and timestamp with no other case. Every other `arbiter` row is bulk
  and counts as no label.
- Blindness: an `arbiter` or `agent_reviewed` row without `blind` true saw a
  score or an earlier label, and counts as `agent`.
- Version key: with `--version-key QUESTION=KEY`, a row of that question whose
  `question_version` is not KEY counts as no label, as after a meaning change.
- Abstain: a counting label of `abstain` gives its case no label; abstains are
  reported per question.
- Unit coverage: each label has counting labels on `dev` cases from at least
  `--min-units` independent units (default 2); a yes-or-no question needs both.

`--proposals` also counts the latest `agent` row of a case that has no
counting label, to check coverage before labelling spend is approved; its
result never approves tuning.

Exits 0 when every question passes, 1 on a failed check or invalid input, and 2
on a usage error.

Example:
    python3 -B scripts/check_labels.py --cases /abs/cases.jsonl --labels /abs/labels.jsonl
"""

COUNTING = ("outcome", "agent_reviewed", "unknown")
PROVENANCES = frozenset({"arbiter", "agent", *COUNTING})
BLIND_REQUIRED = frozenset({"arbiter", "agent_reviewed"})
BINARY = frozenset({"0", "1"})
ABSTAIN = "abstain"
Result = dict[str, Any]


@dataclass(frozen=True)
class CaseInfo:
    unit: str
    split: str


@dataclass(frozen=True)
class Scope:
    """The questions to report, and each question's current version key."""

    questions: tuple[str, ...] = ()
    versions: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Label:
    line: int
    case_id: str
    question_id: str
    label: str
    by: str
    provenance: str
    timestamp: str
    confirmed: str
    blind: bool = False
    question_version: str = ""


def read_jsonl(f: TextIO, name: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for n, text in enumerate(f, start=1):
        if not text.strip():
            continue
        try:
            row = json.loads(text)
        except json.JSONDecodeError as e:
            sys.exit(f"{name} line {n}: invalid JSON: {e.msg}")
        if not isinstance(row, dict):
            sys.exit(f"{name} line {n}: expected a JSON object")
        rows.append({**row, "_line": n})
    return rows


def text_field(row: dict[str, Any], key: str, name: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        sys.exit(f"{name} line {row['_line']}: {key} must be a non-empty string")
    return value.strip()


def parse_cases(rows: list[dict[str, Any]]) -> dict[str, CaseInfo]:
    cases: dict[str, CaseInfo] = {}
    for row in rows:
        case_id = text_field(row, "case_id", "cases.jsonl")
        split = text_field(row, "split", "cases.jsonl").lower()
        if split not in ("dev", "test"):
            sys.exit(f"cases.jsonl line {row['_line']}: split must be dev or test")
        if case_id in cases:
            sys.exit(f"cases.jsonl line {row['_line']}: case_id {case_id!r} repeats")
        cases[case_id] = CaseInfo(text_field(row, "unit", "cases.jsonl"), split)
    return cases


def label_value(row: dict[str, Any]) -> str:
    value = row.get("label")
    if isinstance(value, bool) or not isinstance(value, int | str) or value == "":
        sys.exit(
            f"labels.jsonl line {row['_line']}: label must be 1, 0, abstain or an option key"
        )
    return str(value)


def optional_text(row: dict[str, Any], key: str) -> str:
    value = row.get(key)
    return value.strip() if isinstance(value, str) else ""


def parse_labels(rows: list[dict[str, Any]], cases: dict[str, CaseInfo]) -> list[Label]:
    labels: list[Label] = []
    for row in rows:
        case_id = text_field(row, "case_id", "labels.jsonl")
        if case_id not in cases:
            sys.exit(
                f"labels.jsonl line {row['_line']}: case_id {case_id!r} is not in cases.jsonl"
            )
        provenance = text_field(row, "provenance", "labels.jsonl")
        if provenance not in PROVENANCES:
            sys.exit(
                f"labels.jsonl line {row['_line']}: unknown provenance {provenance!r}"
            )
        labels.append(
            Label(
                line=row["_line"],
                case_id=case_id,
                question_id=text_field(row, "question_id", "labels.jsonl"),
                label=label_value(row),
                by=optional_text(row, "by"),
                provenance=provenance,
                timestamp=optional_text(row, "timestamp"),
                confirmed=optional_text(row, "confirmed"),
                blind=row.get("blind") is True,
                question_version=optional_text(row, "question_version"),
            )
        )
    return labels


def own_fault(row: Label) -> str | None:
    if not row.by:
        return "no arbiter in by"
    if not row.timestamp:
        return "no timestamp"
    if row.confirmed != "individual":
        return "confirmed is not individual"
    return None


def stale_rows(labels: list[Label], versions: dict[str, str]) -> dict[int, str]:
    """Map the line of each row made under an older version key to its reason."""
    stale: dict[int, str] = {}
    for row in labels:
        key = versions.get(row.question_id)
        if key is not None and row.question_version != key:
            made = row.question_version or "no version key"
            stale[row.line] = f"made under {made}, not the current {key}"
    return stale


def bulk_rows(labels: list[Label]) -> dict[int, str]:
    """Map the line of each arbiter row that may not count to its reason."""
    arbiter = [row for row in labels if row.provenance == "arbiter"]
    faults = {row.line: fault for row in arbiter if (fault := own_fault(row))}
    batches: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in arbiter:
        if row.line not in faults:
            batches[row.by, row.timestamp].add(row.case_id)
    for row in arbiter:
        shared = len(batches.get((row.by, row.timestamp), set())) - 1
        if row.line not in faults and shared > 0:
            faults[row.line] = f"arbiter and timestamp shared with {shared} other cases"
    return faults


def sighted_rows(labels: list[Label], dropped: dict[int, str]) -> dict[int, str]:
    """Map the line of each arbiter or agreement row that was not blind to its reason."""
    return {
        row.line: f"{row.provenance} row is not blind"
        for row in labels
        if row.line not in dropped
        and row.provenance in BLIND_REQUIRED
        and not row.blind
    }


def row_kind(row: Label, sighted: dict[int, str]) -> str:
    if row.line in sighted:
        return "agent"
    return "counting" if row.provenance in COUNTING else row.provenance


def counting_labels(
    labels: list[Label],
    dropped: dict[int, str],
    sighted: dict[int, str],
    proposals: bool,
) -> dict[tuple[str, str], Label]:
    """Pick each case's counting label per question; the latest row of a kind wins."""
    latest: dict[tuple[str, str, str], Label] = {}
    for row in labels:
        if row.line in dropped:
            continue
        latest[row.question_id, row.case_id, row_kind(row, sighted)] = row
    kinds = ["arbiter", "counting", *(["agent"] if proposals else [])]
    chosen: dict[tuple[str, str], Label] = {}
    for question_id, case_id in sorted({(q, c) for q, c, _ in latest}):
        for kind in kinds:
            if (question_id, case_id, kind) in latest:
                chosen[question_id, case_id] = latest[question_id, case_id, kind]
                break
    return chosen


def required_labels(values: set[str]) -> set[str]:
    return set(BINARY) if values <= BINARY else values


def question_report(
    question_id: str,
    chosen: list[Label],
    cases: dict[str, CaseInfo],
    min_units: int,
    sighted: dict[int, str],
) -> Result:
    rows = [row for row in chosen if row.label != ABSTAIN]
    abstain = sorted(row.case_id for row in chosen if row.label == ABSTAIN)
    dev = [row for row in rows if cases[row.case_id].split == "dev"]
    units: dict[str, set[str]] = defaultdict(set)
    totals: dict[str, int] = defaultdict(int)
    provenance: dict[str, int] = defaultdict(int)
    for row in dev:
        units[row.label].add(cases[row.case_id].unit)
        totals[row.label] += 1
        provenance["agent" if row.line in sighted else row.provenance] += 1
    need = required_labels({row.label for row in rows})
    reasons = [
        f"label {label} has dev cases from {len(units[label])} units; need {min_units}"
        for label in sorted(need)
        if len(units[label]) < min_units
    ]
    return {
        "question_id": question_id,
        "labels": {
            label: {"dev_cases": totals[label], "dev_units": len(units[label])}
            for label in sorted(need)
        },
        "provenance": dict(sorted(provenance.items())),
        "abstain": abstain,
        "passes": not reasons,
        "reasons": reasons,
        "counting": {
            row.case_id: {
                "label": row.label,
                "provenance": "agent" if row.line in sighted else row.provenance,
            }
            for row in rows
        },
    }


def audit(
    cases: dict[str, CaseInfo],
    labels: list[Label],
    min_units: int,
    *,
    proposals: bool = False,
    scope: Scope | None = None,
) -> Result:
    scope = scope or Scope()
    versions = scope.versions
    dropped = stale_rows(labels, versions)
    dropped.update(
        (line, reason)
        for line, reason in bulk_rows(labels).items()
        if line not in dropped
    )
    sighted = sighted_rows(labels, dropped)
    chosen = counting_labels(labels, dropped, sighted, proposals)
    grouped: dict[str, list[Label]] = defaultdict(list)
    for (question_id, _), row in chosen.items():
        grouped[question_id].append(row)
    names = list(scope.questions) or sorted({row.question_id for row in labels})
    reports = [question_report(q, grouped[q], cases, min_units, sighted) for q in names]
    for report in reports:
        if not report["counting"]:
            report["passes"] = False
            report["reasons"].insert(0, "no counting labels")
    by_line = {row.line: row for row in labels}
    return {
        "min_units": min_units,
        "proposals": proposals,
        "version_keys": dict(sorted(versions.items())),
        "passes": all(r["passes"] for r in reports) and bool(reports),
        "questions": reports,
        "not_counted": row_list(dropped, by_line),
        "counted_as_agent": row_list(sighted, by_line),
    }


def row_list(reasons: dict[int, str], by_line: dict[int, Label]) -> list[Result]:
    return [
        {
            "line": line,
            "case_id": by_line[line].case_id,
            "question_id": by_line[line].question_id,
            "reason": reason,
        }
        for line, reason in sorted(reasons.items())
    ]


def load(cases_path: str, labels_path: str) -> tuple[dict[str, CaseInfo], list[Label]]:
    with open(cases_path, encoding="utf-8") as f:
        cases = parse_cases(read_jsonl(f, "cases.jsonl"))
    with open(labels_path, encoding="utf-8") as f:
        return cases, parse_labels(read_jsonl(f, "labels.jsonl"), cases)


def print_markdown(result: Result) -> None:
    print(
        f"Minimum dev units per label: {result['min_units']}; agent proposals counted: "
        f"{'yes, so this result never approves tuning' if result['proposals'] else 'no'}\n"
    )
    for r in result["questions"]:
        print(f"## {r['question_id']}\n")
        for label, n in r["labels"].items():
            print(
                f"- Label {label}: {n['dev_cases']} dev cases from {n['dev_units']} units"
            )
        mix = ", ".join(f"{k} {v}" for k, v in r["provenance"].items()) or "none"
        print(f"- Counting provenance: {mix}")
        print(f"- Abstain: {len(r['abstain'])} cases")
        for reason in r["reasons"]:
            print(f"- FAIL: {reason}")
        print(f"- Result: {'pass' if r['passes'] else 'fail'}\n")
    for key, title in (
        ("not_counted", "Rows not counted"),
        ("counted_as_agent", "Rows counted as agent"),
    ):
        if not result[key]:
            continue
        print(f"{title}:\n")
        print("| line | case_id | question_id | reason |\n| --- | --- | --- | --- |")
        for row in result[key]:
            print(
                f"| {row['line']} | {row['case_id']} | {row['question_id']} | {row['reason']} |"
            )
        print()


def main() -> None:
    p = argparse.ArgumentParser(
        description="Check label provenance and unit coverage before tuning.",
        epilog=USAGE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--cases", required=True)
    p.add_argument("--labels", required=True)
    p.add_argument("--question", action="append", help="check only this question ID")
    p.add_argument("--min-units", type=int, default=2)
    p.add_argument(
        "--version-key",
        action="append",
        default=[],
        metavar="QUESTION=KEY",
        help="count only this question's labels made under KEY",
    )
    p.add_argument(
        "--proposals",
        action="store_true",
        help="also count agent proposals, to plan labelling; never approves tuning",
    )
    p.add_argument("--json", action="store_true")
    args = p.parse_args()
    if args.min_units < 2:
        p.error("--min-units must be at least 2")
    versions: dict[str, str] = {}
    for pair in args.version_key:
        question_id, sep, key = pair.partition("=")
        if not sep or not question_id.strip() or not key.strip():
            p.error(f"--version-key must be QUESTION=KEY, got {pair!r}")
        versions[question_id.strip()] = key.strip()
    try:
        cases, labels = load(args.cases, args.labels)
    except (OSError, UnicodeDecodeError) as e:
        sys.exit(f"cannot read eval files: {e}")
    result = audit(
        cases,
        labels,
        args.min_units,
        proposals=args.proposals,
        scope=Scope(tuple(args.question or ()), versions),
    )
    if args.json:
        json.dump(result, sys.stdout, indent=2)
        print()
    else:
        print_markdown(result)
    sys.exit(0 if result["passes"] else 1)


if __name__ == "__main__":
    main()
