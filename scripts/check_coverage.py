"""Coverage floors over T1 and T2 for the owned code the test runs load.

Usage::

    check_coverage.py --python-json FILE --lcov FILE [--lcov FILE ...]
                      --line-floor PERCENT --branch-floor PERCENT

- Python: coverage.py's JSON report (``coverage json``) over the combined data of both tiers.
  pyproject.toml's ``[tool.coverage.run]`` names the owned roots and measures branches.
- TypeScript: one Bun lcov report per tier run. A line counts as covered when any run hit it.
  Only tracked files outside ``tests/`` count. Bun records no branch data.

The line total joins Python statements and TypeScript lines; the branch total is Python's.
A total below its floor exits 1; a missing or unreadable report exits 2.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

GIT_TIMEOUT_S = 30
TEST_ROOT = "tests/"


@dataclass
class Totals:
    covered_lines: int = 0
    lines: int = 0
    covered_branches: int = 0
    branches: int = 0


@dataclass
class LineHits:
    """Per file, every instrumented line and the lines any run hit."""

    lines: dict[str, set[int]] = field(default_factory=dict)
    hit: dict[str, set[int]] = field(default_factory=dict)

    def add(self, path: str, line: int, count: int) -> None:
        self.lines.setdefault(path, set()).add(line)
        if count > 0:
            self.hit.setdefault(path, set()).add(line)


def tracked_files(root: Path) -> set[str]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_S,
        check=True,
    )
    return set(filter(None, result.stdout.split("\0")))


def read_lcov(path: Path, hits: LineHits) -> None:
    current = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("SF:"):
            current = line[3:]
        elif line.startswith("DA:") and current:
            number, count = line[3:].split(",")[:2]
            hits.add(current, int(number), int(count))


def python_totals(report: Path) -> Totals:
    totals = json.loads(report.read_text(encoding="utf-8"))["totals"]
    return Totals(
        int(totals["covered_lines"]),
        int(totals["num_statements"]),
        int(totals["covered_branches"]),
        int(totals["num_branches"]),
    )


def typescript_totals(hits: LineHits, owned: set[str]) -> Totals:
    files = [
        path for path in hits.lines if path in owned and not path.startswith(TEST_ROOT)
    ]
    return Totals(
        sum(len(hits.hit.get(path, set())) for path in files),
        sum(len(hits.lines[path]) for path in files),
    )


def percent(covered: int, total: int) -> float:
    return 100.0 if total == 0 else 100.0 * covered / total


def parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check the T1 and T2 coverage floors.")
    parser.add_argument("--python-json", type=Path, required=True)
    parser.add_argument("--lcov", type=Path, action="append", required=True)
    parser.add_argument("--line-floor", type=float, required=True)
    parser.add_argument("--branch-floor", type=float, required=True)
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    options = parse(argv)
    root = Path(__file__).resolve().parents[1]
    hits = LineHits()
    try:
        python = python_totals(options.python_json)
        for report in options.lcov:
            read_lcov(report, hits)
    except (OSError, ValueError, KeyError) as error:
        print(f"Coverage report missing or unreadable: {error}", file=sys.stderr)
        return 2
    script = typescript_totals(hits, tracked_files(root))
    lines = percent(
        python.covered_lines + script.covered_lines, python.lines + script.lines
    )
    branches = percent(python.covered_branches, python.branches)
    print(
        f"Coverage: lines {lines:.2f}% (floor {options.line_floor:g}%; Python "
        f"{percent(python.covered_lines, python.lines):.2f}%, TypeScript "
        f"{percent(script.covered_lines, script.lines):.2f}%), "
        f"Python branches {branches:.2f}% (floor {options.branch_floor:g}%)."
    )
    if lines < options.line_floor or branches < options.branch_floor:
        print("Coverage is below its floor.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
