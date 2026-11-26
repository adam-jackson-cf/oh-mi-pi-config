from __future__ import annotations

import argparse
import json
import os.path
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from guardrails.commands.structure_naming.scan_scope import is_vendored_path, runs
from guardrails.context.changes import (
    DiffError,
    changed_paths,
    read_blobs,
    tree_entries,
)
from guardrails.context.config.house_style import apply_exceptions
from guardrails.context.config.reader import checker_settings
from guardrails.context.config.suite_files import is_test_source
from guardrails.foundation.findings import Finding
from guardrails.foundation.inventory import as_list, matches_any
from guardrails.foundation.source import modules, python
from guardrails.foundation.source.tree import SourceError, line, named, parse

if TYPE_CHECKING:
    from guardrails.foundation.source.tree import SgNode
from guardrails.subcommand.dispatch import SUBCOMMANDS

RULE = "STRUCT.kind-candidates"
EXCEPTION_RULE = "structure-naming/technical_kind_grouping"
KIND_WORDS = (
    "adapters",
    "clients",
    "constants",
    "controllers",
    "dto",
    "entities",
    "enums",
    "exceptions",
    "handlers",
    "helpers",
    "interfaces",
    "manager",
    "managers",
    "middleware",
    "mixins",
    "models",
    "repositories",
    "schemas",
    "serializers",
    "services",
    "types",
    "utils",
    "validators",
)
MAX_SYMBOLS = 12
MAX_IMPORTERS = 3


def forms(word: str) -> set[str]:
    found = {word}
    if word.endswith("ies"):
        found.add(word[:-3] + "y")
    elif word.endswith("y") and len(word) > 2 and word[-2] not in "aeiou":
        found.add(word[:-1] + "ies")
    if word.endswith("es") and word[:-2].endswith(("s", "x", "ch", "sh")):
        found.add(word[:-2])
    elif word.endswith("s") and not word.endswith("ss"):
        found.add(word[:-1])
    if not word.endswith("s"):
        found.add(word + "s")
        if word.endswith(("s", "x", "ch", "sh")):
            found.add(word + "es")
    return found


KIND_FORMS = frozenset(form for word in KIND_WORDS for form in forms(word))


def excluded(path: str, skills: list[str]) -> bool:
    if is_vendored_path(path):
        return True
    folder = os.path.dirname(path)
    return any(
        skill == "" or folder == skill or folder.startswith(skill + "/")
        for skill in skills
    )


def owned(paths: list[str]) -> list[str]:
    skills = sorted({
        os.path.dirname(path) for path in paths if os.path.basename(path) == "SKILL.md"
    })
    return [path for path in paths if not excluded(path, skills)]


def parsed(path: str, source: str) -> SgNode | None:
    """The tree of a Python file; an unparseable file is skipped (None): it proposes no
    candidate and gives no import edges."""
    try:
        root = parse(path, source)
        python.body_docstring(root)
        for node in python.members(root):
            python.body_docstring(node.field("body"))
    except SourceError:
        return None
    return root


def first_line(doc: str) -> str:
    for text in doc.strip().splitlines():
        if text.strip():
            return text.strip()[:200]
    return ""


def signature_of(node: SgNode, lines: list[str]) -> str:
    body = node.field("body")
    statements = [] if body is None else named(body)
    start = line(node) - 1
    end = line(statements[0]) - 1 if statements else start + 1
    if statements and line(statements[0]) - 1 <= start:
        end = start + 1
    text = " ".join(text.strip() for text in lines[start : max(end, start + 1)])
    depth = 0
    for index, character in enumerate(text):
        if character in "([{":
            depth += 1
        elif character in ")]}":
            depth -= 1
        elif character == ":" and depth == 0 and index > 0:
            return text[:index].strip()[:300]
    return text[:300]


def definitions(source: str, root: SgNode | None) -> list[dict[str, str]]:
    if root is None:
        return []
    lines = source.splitlines()
    return [
        {
            "signature": signature_of(node, lines),
            "doc": first_line(python.body_docstring(node.field("body"))),
        }
        for node in python.members(root)
    ]


def line_at(lines: list[str], number: int) -> str:
    return lines[number - 1].strip()[:240] if 0 < number <= len(lines) else ""


class TreeModel:
    def __init__(
        self, paths: list[str], sources: dict[str, str], config: dict[str, Any]
    ) -> None:
        self.sources = sources
        self.config = config
        self.python = sorted(path for path in paths if runs(path))
        self.trees = {path: parsed(path, sources.get(path, "")) for path in self.python}
        index = modules.PythonIndex(self.python)
        self.graph: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for importer in self.python:
            root = self.trees[importer]
            if root is None:
                continue
            lines = sources.get(importer, "").splitlines()
            seen: set[str] = set()
            for item in python.imports(root):
                for target in index.resolve(importer, item):
                    if target not in seen:
                        seen.add(target)
                        self.graph[target].append((importer, line_at(lines, item.line)))

    def eligible(self) -> list[str]:
        return [
            path
            for path in self.python
            if os.path.basename(path) != modules.package_marker()
            and os.path.dirname(path)
            and os.path.dirname(path).split("/")[-1].lower() in KIND_FORMS
            and definitions(self.sources.get(path, ""), self.trees[path])
        ]

    def state(self, path: str) -> dict[str, Any]:
        source = self.sources.get(path, "")
        root = self.trees[path]
        symbols = [
            item["signature"] + (f"  # {item['doc']}" if item["doc"] else "")
            for item in definitions(source, root)[:MAX_SYMBOLS]
        ]
        folder = os.path.dirname(path)
        importers = sorted(
            self.graph.get(path, []),
            key=lambda item: (
                is_test_source(item[0], self.config),
                os.path.dirname(item[0]) == folder,
                item[0],
            ),
        )[:MAX_IMPORTERS]
        doc = python.body_docstring(root).strip()[:600] if root is not None else ""
        return {
            "file": {"path": path, "docstring": doc, "symbols": symbols},
            "folder": folder,
            "importers": [
                {"path": importer, "import": text} for importer, text in importers
            ],
        }


def model_of(tree: dict[str, str], config: dict[str, Any]) -> TreeModel:
    paths = owned(sorted(tree))
    return TreeModel(paths, {path: tree[path] for path in paths if runs(path)}, config)


def head_tree(repo: Path, head: str, excluded_paths: list[str]) -> dict[str, str]:
    entries = {
        path: entry
        for path, entry in tree_entries(repo, head).items()
        if not matches_any(path, excluded_paths)
    }
    paths = owned(sorted(entries))
    want = [
        (path, entries[path].oid)
        for path in paths
        if runs(path) and entries[path].kind == "blob"
    ]
    found = dict.fromkeys(paths, "")
    blobs = read_blobs(repo, sorted({oid for _, oid in want}))
    for path, oid in want:
        found[path] = blobs[oid].decode("utf-8", "replace")
    return found


def exempt(path: str, config: dict[str, Any]) -> bool:
    return not apply_exceptions([Finding(EXCEPTION_RULE, path, "", "")], config)


def kind_candidates(
    tree: dict[str, str], changed: set[str], config: dict[str, Any]
) -> list[dict[str, Any]]:
    model = model_of(tree, config)
    return [
        {"path": path, "state": model.state(path)}
        for path in model.eligible()
        if path in changed and not exempt(path, config)
    ]


def run(repo: Path, config: dict[str, Any], argv: list[str]) -> list[Finding]:
    parser = argparse.ArgumentParser(description=SUBCOMMANDS["kind-candidates"].summary)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args(argv)
    excluded_paths = as_list(
        checker_settings(config, "check").get("exclude")
    ) + as_list(checker_settings(config, "python").get("excluded_paths"))
    try:
        tree = head_tree(repo, args.head, excluded_paths)
        found = kind_candidates(
            tree, changed_paths(repo, args.base, args.head, "AMR"), config
        )
    except (DiffError, ValueError) as error:
        return [
            Finding(
                RULE,
                ".",
                str(error),
                "provide readable base and head commits in this repository",
            )
        ]
    print(json.dumps({"candidates": found}, sort_keys=True))
    return []
