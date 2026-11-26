from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from guardrails.foundation.findings import Finding
from guardrails.foundation.source.python import defined, last_token
from guardrails.foundation.source.tree import end_line, line, named, parse

if TYPE_CHECKING:
    from ast_grep_py import SgNode

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
    pass


def status(exit_code: object) -> str:
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        return "incomplete"
    return STATUS_BY_EXIT.get(exit_code, "incomplete")


def spans(path: str, source: str) -> dict[tuple[str | None, str], tuple[int, int]]:
    found: dict[tuple[str | None, str], tuple[int, int]] = {}
    for statement in named(parse(path, source)):
        node = defined(statement)
        name = node.field("name")
        body = node.field("body")
        if name is None or body is None:
            continue
        if node.kind() == "function_definition":
            found[(None, name.text())] = span(statement, node)
        elif node.kind() == "class_definition":
            for member in named(body):
                method = defined(member)
                method_name = method.field("name")
                if method.kind() == "function_definition" and method_name is not None:
                    found[(name.text(), method_name.text())] = span(member, method)
    return found


def span(statement: SgNode, node: SgNode) -> tuple[int, int]:
    return line(statement), end_line(last_token(node))


def unit(name: str) -> tuple[str | None, str]:
    match = NAME.search(name)
    if not match:
        raise MutmutError(f"unrecognised mutmut mutant name {name!r}")
    if match.group("function"):
        return None, match.group("function")
    return match.group("cls"), match.group("method")


def file_mutants(repo: Path, meta: Path) -> list[dict[str, Any]]:
    results = repo / RESULTS_DIR
    source_path = meta.relative_to(results).as_posix().removesuffix(META_SUFFIX)
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
        source = (repo / source_path).read_text(encoding="utf-8")
        found = spans(source_path, source)
    except (OSError, ValueError) as error:
        raise MutmutError(
            f"cannot read {meta} or its source {source_path}: {error}"
        ) from error
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


def normalise(repo: Path) -> dict[str, Any]:
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
        return [
            Finding(
                RULE, RESULTS_DIR, str(error), "run mutmut in the repo root, then retry"
            )
        ]
    target = out if out.is_absolute() else repo / out
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"mutation report: {len(report['mutants'])} mutants -> {out}")
    return []
