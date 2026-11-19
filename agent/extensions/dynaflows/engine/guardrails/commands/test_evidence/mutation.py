from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from guardrails.context.changes import (
    DiffError,
    changed_files,
    line_changes,
    parse_range,
    range_finding,
)
from guardrails.foundation.findings import Finding

RULE = "TEST.mutation-sensitivity"
VERSION = 1
PASSING = frozenset({"killed", "timeout"})
STATUSES = frozenset({"killed", "survived", "no_tests", "timeout", "incomplete"})
FIX = {
    "survived": "add an outcome or property case that fails when this code changes behaviour",
    "no_tests": "add a test that runs this code through its public entry point",
    "incomplete": "rerun the mutation tool until every mutant of the changed code has a result",
}


class ReportError(Exception):
    pass


@dataclass(frozen=True)
class Mutant:
    id: str
    path: str
    start_line: int
    end_line: int
    status: str


def load_report(path: Path) -> list[Mutant]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ReportError(f"cannot read {path}: {error}") from error
    if not isinstance(data, dict) or data.get("version") != VERSION:
        raise ReportError(f"{path} must be a version {VERSION} mutation report")
    entries = data.get("mutants")
    if not isinstance(entries, list):
        raise ReportError(f"{path} has no mutants list")
    return [mutant(entry, path) for entry in entries]


def mutant(entry: object, path: Path) -> Mutant:
    if not isinstance(entry, dict):
        raise ReportError(f"{path}: every mutant must be an object")
    start, end = entry.get("start_line"), entry.get("end_line")
    status = entry.get("status")
    if (
        not isinstance(entry.get("id"), str)
        or not isinstance(entry.get("path"), str)
        or not isinstance(start, int)
        or not isinstance(end, int)
        or start > end
        or status not in STATUSES
    ):
        raise ReportError(f"{path}: mutant {entry!r} does not follow the report schema")
    return Mutant(entry["id"], entry["path"], start, end, status)


def options(argv: list[str]) -> Path:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--report", type=Path)
    known, _ = parser.parse_known_args(argv)
    if known.report is None:
        raise ReportError("pass --report FILE naming the normalised mutation report")
    report: Path = known.report
    return report


def check(mutants: list[Mutant], added: dict[str, set[int]]) -> list[Finding]:
    found = []
    for item in mutants:
        lines = added.get(item.path, set())
        in_scope = any(item.start_line <= line <= item.end_line for line in lines)
        if not in_scope or item.status in PASSING:
            continue
        found.append(
            Finding(
                RULE,
                item.path,
                f"lines {item.start_line}-{item.end_line}: mutant {item.id} {item.status} "
                "in changed code",
                FIX[item.status],
            )
        )
    return found


def run(repo: Path, profile: dict[str, Any], argv: list[str]) -> list[Finding]:
    del profile
    try:
        report = options(argv)
        mutants = load_report(report if report.is_absolute() else repo / report)
    except ReportError as error:
        return [
            Finding(
                RULE, "mutation report", str(error), "run the adapter's mutation-report"
            )
        ]
    try:
        diff = parse_range(argv)
        added = {
            change.path: {
                number for number, _ in line_changes(repo, diff, change.path).added
            }
            for change in changed_files(repo, diff)
            if change.status != "D"
        }
    except DiffError as error:
        return [range_finding(RULE, error)]
    return check(mutants, added)
