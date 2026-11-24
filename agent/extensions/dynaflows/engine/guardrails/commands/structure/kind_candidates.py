from __future__ import annotations

import argparse
import ast
import json
import os.path
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from guardrails.commands.structure.name_pairs import changed_paths
from guardrails.commands.structure.rename_residue import GitError, git
from guardrails.context.config.house_style import apply_exceptions
from guardrails.foundation.findings import Finding
from guardrails.foundation.inventory import as_list, matches_any, settings
from guardrails.host.process import run_bytes
from guardrails.toolkit.dispatch import SUBCOMMANDS

RULE = "STRUCTURE.kind-candidates"
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
EXCLUDED_DIRS = frozenset({
    "__pycache__",
    "build",
    "dist",
    "examples",
    "node_modules",
    "third_party",
    "vendor",
})
TEST_DIRS = frozenset({"spec", "specs", "test", "testing", "tests"})
MAX_SYMBOLS = 12
MAX_IMPORTERS = 3
DEFINITION = re.compile(r"(?:async\s+)?(def|class)\s+([A-Za-z_]\w*)")
Import = tuple[int, int, str, list[str], str]


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
    parts = path.split("/")
    if any(part.startswith(".") for part in parts):
        return True
    if any(part in EXCLUDED_DIRS for part in parts[:-1]):
        return True
    folder = "/".join(parts[:-1])
    return any(
        skill == "" or folder == skill or folder.startswith(skill + "/")
        for skill in skills
    )


def owned(paths: list[str]) -> list[str]:
    skills = sorted({
        os.path.dirname(path) for path in paths if os.path.basename(path) == "SKILL.md"
    })
    return [path for path in paths if not excluded(path, skills)]


def parsed(source: str) -> ast.Module | None:
    try:
        return ast.parse(source)
    except (SyntaxError, ValueError):
        return None


def first_line(doc: str | None) -> str:
    for line in (doc or "").strip().splitlines():
        if line.strip():
            return line.strip()[:200]
    return ""


def signature_of(node: ast.stmt, lines: list[str]) -> str:
    body = getattr(node, "body", [])
    start = node.lineno - 1
    end = body[0].lineno - 1 if body else start + 1
    if body and body[0].lineno - 1 <= start:
        end = start + 1
    text = " ".join(line.strip() for line in lines[start : max(end, start + 1)])
    depth = 0
    for index, character in enumerate(text):
        if character in "([{":
            depth += 1
        elif character in ")]}":
            depth -= 1
        elif character == ":" and depth == 0 and index > 0:
            return text[:index].strip()[:300]
    return text[:300]


def definitions(source: str, tree: ast.Module | None) -> list[dict[str, str]]:
    if tree is None:
        return [
            {"signature": line.rstrip().rstrip(":"), "doc": ""}
            for line in source.splitlines()
            if DEFINITION.match(line)
        ]
    lines = source.splitlines()
    return [
        {
            "signature": signature_of(node, lines),
            "doc": first_line(ast.get_docstring(node)),
        }
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]


def line_at(lines: list[str], number: int) -> str:
    return lines[number - 1].strip()[:240] if 0 < number <= len(lines) else ""


def imports_of(source: str, tree: ast.Module | None) -> list[Import]:
    if tree is None:
        return []
    lines = source.splitlines()
    found: list[Import] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(
                (node.lineno, 0, alias.name, [], line_at(lines, node.lineno))
                for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            names = [alias.name for alias in node.names]
            found.append((
                node.lineno,
                node.level or 0,
                node.module or "",
                names,
                line_at(lines, node.lineno),
            ))
    return found


def test_path(path: str) -> bool:
    parts = path.split("/")
    name = parts[-1]
    return (
        any(part in TEST_DIRS for part in parts[:-1])
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name == "conftest.py"
    )


class TreeModel:
    def __init__(self, paths: list[str], sources: dict[str, str]) -> None:
        self.sources = sources
        self.python = sorted(path for path in paths if path.endswith(".py"))
        self.packages = {
            os.path.dirname(path)
            for path in paths
            if os.path.basename(path) == "__init__.py"
        }
        self.trees = {path: parsed(sources.get(path, "")) for path in self.python}
        self.modules: dict[str, set[str]] = defaultdict(set)
        for path in self.python:
            parts = path[:-3].split("/")
            if parts[-1] == "__init__":
                parts = parts[:-1]
            for index in range(len(parts)):
                parent = "/".join(path.split("/")[:index])
                if index == 0 or parent not in self.packages:
                    self.modules[".".join(parts[index:])].add(path)
        self.graph: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for importer in self.python:
            seen: set[str] = set()
            for _, level, module, names, text in imports_of(
                sources.get(importer, ""), self.trees[importer]
            ):
                for target in sorted(self.resolve(importer, level, module, names)):
                    if target != importer and target not in seen:
                        seen.add(target)
                        self.graph[target].append((importer, text))

    def relative(
        self, importer: str, level: int, module: str, names: list[str]
    ) -> set[str]:
        base = os.path.dirname(importer).split("/") if os.path.dirname(importer) else []
        if level > 1:
            base = base[: len(base) - (level - 1)] if level - 1 <= len(base) else []
        stem = "/".join(base + (module.split(".") if module else []))
        candidates = [stem] + [f"{stem}/{name}" if stem else name for name in names]
        return {
            target
            for candidate in candidates
            for target in (candidate + ".py", candidate + "/__init__.py")
            if target in self.sources or target in self.python
        }

    def resolve(
        self, importer: str, level: int, module: str, names: list[str]
    ) -> set[str]:
        if level:
            return self.relative(importer, level, module, names)
        targets: set[str] = set()
        for name in [module] + (
            [f"{module}.{item}" for item in names] if names else []
        ):
            hits = self.modules.get(name, set())
            if len(hits) > 1:
                common = {
                    hit: len(
                        os.path.commonprefix([hit.split("/"), importer.split("/")])
                    )
                    for hit in hits
                }
                best = max(common.values())
                hits = {hit for hit, size in common.items() if size == best}
            targets |= hits
        return targets

    def eligible(self) -> list[str]:
        return [
            path
            for path in self.python
            if os.path.basename(path) != "__init__.py"
            and os.path.dirname(path)
            and os.path.dirname(path).split("/")[-1].lower() in KIND_FORMS
            and definitions(self.sources.get(path, ""), self.trees[path])
        ]

    def state(self, path: str) -> dict[str, Any]:
        source = self.sources.get(path, "")
        tree = self.trees[path]
        symbols = [
            item["signature"] + (f"  # {item['doc']}" if item["doc"] else "")
            for item in definitions(source, tree)[:MAX_SYMBOLS]
        ]
        folder = os.path.dirname(path)
        importers = sorted(
            self.graph.get(path, []),
            key=lambda item: (
                test_path(item[0]),
                os.path.dirname(item[0]) == folder,
                item[0],
            ),
        )[:MAX_IMPORTERS]
        doc = (ast.get_docstring(tree) or "").strip()[:600] if tree is not None else ""
        return {
            "file": {"path": path, "docstring": doc, "symbols": symbols},
            "folder": folder,
            "importers": [
                {"path": importer, "import": text} for importer, text in importers
            ],
        }


def model_of(tree: dict[str, str]) -> TreeModel:
    paths = owned(sorted(tree))
    return TreeModel(
        paths, {path: tree[path] for path in paths if path.endswith(".py")}
    )


def head_tree(repo: Path, head: str, excluded_paths: list[str]) -> dict[str, str]:
    listing = git(repo, "ls-tree", "-r", "-z", head).split(b"\0")
    entries = {}
    for entry in listing:
        if entry:
            metadata, raw = entry.split(b"\t", 1)
            path = raw.decode("utf-8", "replace")
            if not matches_any(path, excluded_paths):
                entries[path] = metadata.decode().split()
    paths = owned(sorted(entries))
    want = [
        (path, entries[path][2])
        for path in paths
        if path.endswith(".py") and entries[path][1] == "blob"
    ]
    tree = dict.fromkeys(paths, "")
    if not want:
        return tree
    result = run_bytes(
        ["git", "-C", str(repo), "cat-file", "--batch"],
        data="".join(oid + "\n" for _, oid in want).encode(),
        check=False,
        timeout=120,
    )
    if result.returncode:
        raise GitError(result.stderr.decode(errors="replace").strip())
    output, offset = result.stdout, 0
    for path, _ in want:
        end = output.index(b"\n", offset)
        length = int(output[offset:end].split()[2])
        tree[path] = output[end + 1 : end + 1 + length].decode("utf-8", "replace")
        offset = end + 2 + length
    return tree


def exempt(path: str, profile: dict[str, Any]) -> bool:
    return not apply_exceptions([Finding(EXCEPTION_RULE, path, "", "")], profile)


def kind_candidates(
    tree: dict[str, str], changed: set[str], profile: dict[str, Any]
) -> list[dict[str, Any]]:
    model = model_of(tree)
    return [
        {"path": path, "state": model.state(path)}
        for path in model.eligible()
        if path in changed and not exempt(path, profile)
    ]


def run(repo: Path, profile: dict[str, Any], argv: list[str]) -> list[Finding]:
    parser = argparse.ArgumentParser(description=SUBCOMMANDS["kind-candidates"].summary)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args(argv)
    excluded_paths = as_list(settings(profile, "check").get("exclude")) + as_list(
        settings(profile, "python").get("excluded_paths")
    )
    try:
        tree = head_tree(repo, args.head, excluded_paths)
        found = kind_candidates(
            tree, changed_paths(repo, args.base, args.head), profile
        )
    except (GitError, ValueError) as error:
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
