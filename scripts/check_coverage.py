from __future__ import annotations

import argparse
import fnmatch
import json
import subprocess
import sys
import tomllib
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

GIT_TIMEOUT_S = 30
TEST_ROOT = "tests/"
SCRIPT_SUFFIXES = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")
DECLARATION_SUFFIXES = (".d.ts", ".d.mts", ".d.cts")
COMMENT_STARTS = ("//", "/*", "*")


@dataclass
class Totals:
    covered_lines: int = 0
    lines: int = 0
    covered_branches: int = 0
    branches: int = 0


@dataclass
class LineHits:
    lines: dict[str, set[int]] = field(default_factory=dict)
    hit: dict[str, set[int]] = field(default_factory=dict)

    def add(self, path: str, line: int, count: int) -> None:
        self.lines.setdefault(path, set()).add(line)
        if count > 0:
            self.hit.setdefault(path, set()).add(line)


@dataclass(frozen=True)
class OwnedScope:
    python_roots: tuple[str, ...]
    omit: tuple[str, ...]

    def excludes(self, path: str) -> bool:
        return path.startswith(TEST_ROOT) or any(
            fnmatch.fnmatch(path, pattern) for pattern in self.omit
        )

    def python_files(self, tracked: Iterable[str]) -> set[str]:
        return {
            path
            for path in tracked
            if path.endswith(".py")
            and any(path.startswith(f"{root}/") for root in self.python_roots)
            and not self.excludes(path)
        }

    def script_files(self, tracked: Iterable[str]) -> set[str]:
        return {
            path
            for path in tracked
            if path.endswith(SCRIPT_SUFFIXES)
            and not path.endswith(DECLARATION_SUFFIXES)
            and not self.excludes(path)
        }


class UnmeasuredPythonError(ValueError):
    def __init__(self, paths: list[str]) -> None:
        super().__init__(
            "owned Python files absent from the coverage report: " + ", ".join(paths)
        )


def read_scope(pyproject: Path) -> OwnedScope:
    run = tomllib.loads(pyproject.read_text(encoding="utf-8"))["tool"]["coverage"][
        "run"
    ]
    return OwnedScope(tuple(run["source"]), tuple(run["omit"]))


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


def python_totals(report: Path, owned: set[str]) -> Totals:
    files = json.loads(report.read_text(encoding="utf-8"))["files"]
    missing = sorted(owned - set(files))
    if missing:
        raise UnmeasuredPythonError(missing)
    totals = Totals()
    for path in owned:
        summary = files[path]["summary"]
        totals.covered_lines += int(summary["covered_lines"])
        totals.lines += int(summary["num_statements"])
        totals.covered_branches += int(summary["covered_branches"])
        totals.branches += int(summary["num_branches"])
    return totals


def code_lines(text: str) -> int:
    return sum(
        1
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith(COMMENT_STARTS)
    )


def typescript_totals(
    hits: LineHits, owned: set[str], read_text: Callable[[str], str]
) -> tuple[Totals, list[str]]:
    unloaded = sorted(owned - set(hits.lines))
    loaded = owned & set(hits.lines)
    return (
        Totals(
            sum(len(hits.hit.get(path, set())) for path in loaded),
            sum(len(hits.lines[path]) for path in loaded)
            + sum(code_lines(read_text(path)) for path in unloaded),
        ),
        unloaded,
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


def check(options: argparse.Namespace, root: Path, tracked: set[str]) -> int:
    scope = read_scope(root / "pyproject.toml")
    hits = LineHits()
    try:
        python = python_totals(options.python_json, scope.python_files(tracked))
        for report in options.lcov:
            read_lcov(report, hits)
    except (OSError, ValueError, KeyError) as error:
        print(f"Coverage report missing or unreadable: {error}", file=sys.stderr)
        return 2
    script, unloaded = typescript_totals(
        hits,
        scope.script_files(tracked),
        lambda path: (root / path).read_text(encoding="utf-8"),
    )
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
    if unloaded:
        print(
            f"{len(unloaded)} owned TypeScript and JavaScript files no test loads "
            f"count as uncovered: {', '.join(unloaded)}"
        )
    if lines < options.line_floor or branches < options.branch_floor:
        print("Coverage is below its floor.", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parents[1]
    return check(parse(argv), root, tracked_files(root))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
