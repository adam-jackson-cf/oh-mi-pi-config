from __future__ import annotations

import argparse
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from guardrails.context.changes import DiffError, git, git_bytes
from guardrails.context.config.reader import checker_settings
from guardrails.foundation.findings import Finding
from guardrails.foundation.inventory import as_list, matches_any
from guardrails.foundation.source import go, typescript
from guardrails.foundation.source.tree import (
    SourceError,
    grammar,
    language_of,
    named,
    parse,
)
from guardrails.subcommand.dispatch import SUBCOMMANDS

if TYPE_CHECKING:
    from ast_grep_py import SgNode

RULE = "RENAME.residue"
RENAME_THRESHOLD = "50%"
NON_DEFINING = frozenset({
    "import_statement",
    "import_from_statement",
    "future_import_statement",
    "lambda",
    "list_comprehension",
    "set_comprehension",
    "dictionary_comprehension",
    "generator_expression",
})
DEFINING = frozenset({"function_definition", "class_definition"})
TARGET_FIELDS = {
    "assignment": "left",
    "augmented_assignment": "left",
    "for_statement": "left",
    "named_expression": "name",
    "type_alias_statement": "left",
    "as_pattern": "alias",
}
TARGET_PATTERNS = frozenset({
    "pattern_list",
    "tuple_pattern",
    "list_pattern",
    "list_splat_pattern",
    "tuple",
    "list",
    "list_splat",
    "parenthesized_expression",
    "as_pattern_target",
    "type",
    "generic_type",
})


@dataclass(frozen=True)
class Rename:
    old: str
    new: str


@dataclass(frozen=True)
class RetiredTerm:
    old: str
    new: str
    scope: str
    directory: bool
    rename: Rename


def words(name: str) -> tuple[str, ...]:
    name = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    return tuple(re.findall(r"[a-z0-9]+", name.lower()))


def contains(sequence: tuple[str, ...], term: tuple[str, ...]) -> bool:
    return bool(term) and any(
        sequence[index : index + len(term)] == term
        for index in range(len(sequence) - len(term) + 1)
    )


def retired_terms(renames: list[Rename], owned_paths: list[str]) -> list[RetiredTerm]:
    retained = {
        words(segment)
        for raw_path in owned_paths
        for segment in (
            *PurePosixPath(raw_path).parts[:-1],
            PurePosixPath(raw_path).stem,
        )
    }
    found = [term for rename in renames for term in rename_terms(rename)]
    unique: dict[tuple[tuple[str, ...], tuple[str, ...], str, bool], RetiredTerm] = {}
    for term in found:
        if words(term.old) in retained:
            continue
        key = (words(term.old), words(term.new), term.scope, term.directory)
        unique.setdefault(key, term)
    return list(unique.values())


def rename_terms(rename: Rename) -> list[RetiredTerm]:
    found = []

    old = PurePosixPath(rename.old)
    new = PurePosixPath(rename.new)
    old_dirs, new_dirs = old.parts[:-1], new.parts[:-1]
    shared = 0
    while shared < min(len(old_dirs), len(new_dirs)):
        if old_dirs[shared] != new_dirs[shared]:
            break
        shared += 1
    remaining_dirs = new_dirs[shared:]
    directory_words = tuple(word for part in remaining_dirs for word in words(part))
    new_parts = (*new_dirs, new.stem)
    for depth in range(shared, len(old_dirs)):
        segment = old_dirs[depth]
        if contains(directory_words, words(segment)):
            continue
        replacement = new_parts[min(depth, len(new_parts) - 1)]
        directory = depth < len(new_dirs)
        scope = "/".join(new.parts[: depth + 1]) if directory else rename.new
        found.append(RetiredTerm(segment, replacement, scope, directory, rename))
    if not contains((*directory_words, *words(new.stem)), words(old.stem)):
        found.append(RetiredTerm(old.stem, new.stem, rename.new, False, rename))
    return found


def bound(target: SgNode, found: list[str]) -> None:
    if target.kind() == "identifier":
        found.append(target.text())
    elif target.kind() in TARGET_PATTERNS:
        for item in named(target):
            bound(item, found)
    else:
        defined(target, found)


def visited(node: SgNode) -> list[SgNode]:
    kind, parent = node.kind(), node.parent()
    if kind == "function_definition":
        return []
    if kind in {"class_definition", "decorated_definition"}:
        inner = node.field("body" if kind == "class_definition" else "definition")
        return [] if inner is None else [inner]
    children = named(node)
    if kind == "case_clause":
        return [child for child in children if child.kind() != "case_pattern"]
    if kind == "except_clause":
        return [child for child in children if child.kind() in {"as_pattern", "block"}]
    if kind == "as_pattern" and parent is not None and parent.kind() == "except_clause":
        return [child for child in children if child.kind() == "as_pattern_target"]
    return children


def defined(node: SgNode, found: list[str]) -> None:
    kind = node.kind()
    if kind in NON_DEFINING:
        return
    name = node.field("name")
    if kind in DEFINING and name is not None:
        found.append(name.text())
    target = node.field(TARGET_FIELDS[kind]) if kind in TARGET_FIELDS else None
    for child in visited(node):
        if child == target:
            bound(child, found)
        else:
            defined(child, found)


def python_names(root: SgNode) -> list[str]:
    found: list[str] = []
    defined(root, found)
    return list(dict.fromkeys(found))


IDENTIFIERS: dict[str, Callable[[SgNode], list[str]]] = {
    "python": python_names,
    "go": go.identifiers,
}


def residue_findings(
    renames: list[Rename], head_files: dict[str, bytes]
) -> list[Finding]:
    terms = retired_terms(renames, list(head_files))
    found: list[Finding] = []
    for path, source in sorted(head_files.items()):
        scoped = [
            term
            for term in terms
            if path == term.scope
            or (term.directory and path.startswith(term.scope + "/"))
        ]
        if not scoped:
            continue
        identifiers: list[str] = []
        language = language_of(path)
        if language is not None:
            try:
                root = parse(path, source.decode())
            except (SourceError, UnicodeError) as error:
                found.append(
                    Finding(
                        RULE,
                        path,
                        f"cannot read head source: {error}",
                        "repair the source so rename residue can be checked",
                    )
                )
                continue
            identifiers = (
                typescript.identifiers(root, grammar(path))
                if language == "typescript"
                else IDENTIFIERS[language](root)
            )
        found.extend(term_findings(path, scoped, identifiers))
    return list(dict.fromkeys(found))


def term_leftovers(
    term: RetiredTerm, identifiers: list[str], path_names: tuple[str, ...]
) -> list[str]:
    retired = words(term.old)
    leftovers = [name for name in identifiers if contains(words(name), retired)]
    path_leftovers = [part for part in path_names if contains(words(part), retired)]
    if path_leftovers:
        leftovers.insert(0, path_leftovers[0])
    return leftovers


def term_findings(
    path: str, scoped: list[RetiredTerm], identifiers: list[str]
) -> list[Finding]:
    parts = PurePosixPath(path).parts
    path_names = (*parts[:-1], PurePosixPath(path).stem)
    found = []
    for term in scoped:
        for identifier in term_leftovers(term, identifiers, path_names):
            found.append(
                Finding(
                    RULE,
                    path,
                    f"defines `{identifier}`, which still uses `{term.old}` after this change "
                    f"renamed `{term.old}` to `{term.new}` "
                    f"(`{term.rename.old}` → `{term.rename.new}`). A rename changes every use "
                    "in one change, with no alias for the old name (structure-and-naming.md)",
                    f"Rename it to use `{term.new}`.",
                )
            )
    return found


def commit_snapshot(
    repo: Path, config: dict[str, Any], base: str, head: str
) -> tuple[list[Rename], dict[str, bytes]]:
    base_id = git(
        repo, "rev-parse", "--verify", "--end-of-options", base + "^{commit}"
    ).strip()
    head_id = git(
        repo, "rev-parse", "--verify", "--end-of-options", head + "^{commit}"
    ).strip()
    rows = git(
        repo,
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--name-status",
        "-z",
        f"--find-renames={RENAME_THRESHOLD}",
        "-l0",
        "--diff-filter=R",
        base_id,
        head_id,
        "--",
    ).split("\0")
    renames = [
        Rename(rows[index + 1], rows[index + 2]) for index in range(0, len(rows) - 1, 3)
    ]
    exclusions = as_list(checker_settings(config, "check").get("exclude"))
    paths = git(repo, "ls-tree", "-r", "--name-only", "-z", head_id).split("\0")
    owned_paths = [path for path in paths if path and not matches_any(path, exclusions)]
    terms = retired_terms(renames, owned_paths)
    files = {}
    for path in owned_paths:
        scoped = any(
            path == term.scope or (term.directory and path.startswith(term.scope + "/"))
            for term in terms
        )
        files[path] = (
            git_bytes(repo, "show", f"{head_id}:{path}")
            if scoped and language_of(path) is not None
            else b""
        )
    return renames, files


def run(repo: Path, config: dict[str, Any], argv: list[str]) -> list[Finding]:
    parser = argparse.ArgumentParser(description=SUBCOMMANDS["rename-residue"].summary)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args(argv)
    try:
        renames, files = commit_snapshot(repo, config, args.base, args.head)
    except (DiffError, UnicodeError) as error:
        return [
            Finding(
                RULE,
                ".",
                str(error),
                "provide readable base and head commits in this repository",
            )
        ]
    return residue_findings(renames, files)
