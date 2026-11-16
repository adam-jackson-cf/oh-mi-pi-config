"""Normalise mutmut results into the shared mutation report (adapter item 5).

The pytest adapter's mutation tool is mutmut, pinned by this package's ``mutation`` extra. After
``mutmut run`` in the repo root, ``omp-guardrails adapter mutation-report --out FILE`` reads
mutmut's per-file results (``mutants/<source path>.meta``: ``exit_code_by_key`` maps each mutant
name to its test-run exit code) and writes the version 1 report that
``omp-guardrails mutation-check`` reads.

A mutant name is ``<module>.x_<function>__mutmut_<n>`` or
``<module>.xǁ<class>ǁ<method>__mutmut_<n>``. Its span is the mutated function's lines in the
source file, decorators included, found with ``ast``. Exit codes map as mutmut maps them:
1 and 3 killed; 0 survived; 5 and 33 no tests; 24, 36, 152, 255 and -24 timeout; 37 (caught by
type check), -9 and -11 killed; anything else, including no result yet, is incomplete.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from omp_guardrails.findings import Finding

TOOL = "mutmut"
RULE = "TEST.mutation-sensitivity"
RESULTS_DIR = "mutants"
META_SUFFIX = ".meta"
CLASS_SEPARATOR = "ǁ"
NAME = re.compile(
    r"(?:^|\.)x(?:_(?P<function>[^.ǁ]+)|ǁ(?P<cls>[^ǁ]+)ǁ(?P<method>[^ǁ]+))__mutmut_\d+$"
)
STATUS_BY_EXIT = {
    1: "killed",
    3: "killed",
    37: "killed",
    -9: "killed",
    -11: "killed",
    0: "survived",
    5: "no_tests",
    33: "no_tests",
    24: "timeout",
    -24: "timeout",
    36: "timeout",
    152: "timeout",
    255: "timeout",
}


class MutmutError(Exception):
    """mutmut's results cannot be read or matched to the source."""


def status(exit_code: object) -> str:
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        return "incomplete"
    return STATUS_BY_EXIT.get(exit_code, "incomplete")


def spans(source: str) -> dict[tuple[str | None, str], tuple[int, int]]:
    """``(class or None, function) -> (first line, last line)`` for mutmut's mutation units."""
    tree = ast.parse(source)
    found: dict[tuple[str | None, str], tuple[int, int]] = {}
    functions = (ast.FunctionDef, ast.AsyncFunctionDef)
    for node in tree.body:
        if isinstance(node, functions):
            found[(None, node.name)] = span(node)
        elif isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, functions):
                    found[(node.name, child.name)] = span(child)
    return found


def span(node: ast.AST) -> tuple[int, int]:
    decorators = getattr(node, "decorator_list", [])
    start = min([node.lineno, *(item.lineno for item in decorators)])
    return start, node.end_lineno or node.lineno


def unit(name: str) -> tuple[str | None, str]:
    match = NAME.search(name)
    if not match:
        raise MutmutError(f"unrecognised mutmut mutant name {name!r}")
    if match.group("function"):
        return None, match.group("function")
    return match.group("cls"), match.group("method")


def file_mutants(repo: Path, meta: Path) -> list[dict]:
    results = repo / RESULTS_DIR
    source_path = meta.relative_to(results).as_posix().removesuffix(META_SUFFIX)
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
        source = (repo / source_path).read_text(encoding="utf-8")
        found = spans(source)
    except (OSError, ValueError, SyntaxError) as error:
        raise MutmutError(f"cannot read {meta} or its source {source_path}: {error}") from error
    by_key = data.get("exit_code_by_key") if isinstance(data, dict) else None
    if not isinstance(by_key, dict):
        raise MutmutError(f"{meta} has no exit_code_by_key")
    mutants = []
    for name, exit_code in sorted(by_key.items()):
        key = unit(name)
        if key not in found:
            raise MutmutError(f"{source_path} has no function for mutant {name}")
        start, end = found[key]
        mutants.append({
            "id": name,
            "path": source_path,
            "start_line": start,
            "end_line": end,
            "status": status(exit_code),
        })
    return mutants


def normalise(repo: Path) -> dict:
    results = repo / RESULTS_DIR
    metas = sorted(results.rglob(f"*.py{META_SUFFIX}")) if results.is_dir() else []
    if not metas:
        raise MutmutError(f"no mutmut results under {RESULTS_DIR}/; run mutmut first")
    mutants = [item for meta in metas for item in file_mutants(repo, meta)]
    return {"version": 1, "tool": TOOL, "mutants": mutants}


def write_report(repo: Path, out: Path) -> list[Finding]:
    try:
        report = normalise(repo)
    except MutmutError as error:
        return [Finding(RULE, RESULTS_DIR, str(error), "run mutmut in the repo root, then retry")]
    target = out if out.is_absolute() else repo / out
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"mutation report: {len(report['mutants'])} mutants -> {out}")
    return []
