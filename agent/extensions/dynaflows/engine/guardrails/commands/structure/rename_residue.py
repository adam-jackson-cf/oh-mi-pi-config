from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from guardrails.context.config.repo_config import ConfigError, load_config
from guardrails.foundation.findings import Finding, exit_code, format_findings
from guardrails.foundation.inventory import as_list, matches_any, settings
from guardrails.host.process import run_bytes
from guardrails.toolkit.dispatch import SUBCOMMANDS

RULE = "RENAME.residue"
RENAME_THRESHOLD = "50%"


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


class DefinedNames(ast.NodeVisitor):
    descend_classes = True
    non_defining_nodes = (
        ast.Import,
        ast.ImportFrom,
        ast.Lambda,
        ast.ListComp,
        ast.SetComp,
        ast.DictComp,
        ast.GeneratorExp,
    )

    def __init__(self) -> None:
        self.names: list[str] = []

    def visit(self, node: ast.AST) -> None:
        if not isinstance(node, self.non_defining_nodes):
            super().visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.names.append(node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.names.append(node.name)
        if self.descend_classes:
            for statement in node.body:
                self.visit(statement)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Store):
            self.names.append(node.id)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.names.append(node.name)
        for statement in node.body:
            self.visit(statement)


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
        identifiers = []
        if path.endswith(".py"):
            try:
                tree = ast.parse(source, filename=path)
            except (SyntaxError, ValueError, UnicodeError) as error:
                found.append(
                    Finding(
                        RULE,
                        path,
                        f"cannot parse head Python file: {error}",
                        "repair the Python source so rename residue can be checked",
                    )
                )
                continue
            visitor = DefinedNames()
            visitor.visit(tree)
            identifiers = list(dict.fromkeys(visitor.names))
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
                    "in one change, with no alias for the old name (naming.md)",
                    f"Rename it to use `{term.new}`.",
                )
            )
    return found


class GitError(Exception):
    pass


def git(repo: Path, *args: str) -> bytes:
    try:
        result = run_bytes(["git", "-C", str(repo), *args], check=False)
    except OSError as error:
        raise GitError(str(error)) from error
    if result.returncode:
        raise GitError(
            result.stderr.decode(errors="replace").strip() or "git command failed"
        )
    return result.stdout


def commit_snapshot(
    repo: Path, config: dict[str, Any], base: str, head: str
) -> tuple[list[Rename], dict[str, bytes]]:
    base_id = (
        git(repo, "rev-parse", "--verify", "--end-of-options", base + "^{commit}")
        .decode()
        .strip()
    )
    head_id = (
        git(repo, "rev-parse", "--verify", "--end-of-options", head + "^{commit}")
        .decode()
        .strip()
    )
    rows = (
        git(
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
        )
        .decode()
        .split("\0")
    )
    renames = [
        Rename(rows[index + 1], rows[index + 2]) for index in range(0, len(rows) - 1, 3)
    ]
    exclusions = as_list(settings(config, "check").get("exclude"))
    paths = (
        git(repo, "ls-tree", "-r", "--name-only", "-z", head_id).decode().split("\0")
    )
    owned_paths = [path for path in paths if path and not matches_any(path, exclusions)]
    terms = retired_terms(renames, owned_paths)
    files = {}
    for path in owned_paths:
        scoped = any(
            path == term.scope or (term.directory and path.startswith(term.scope + "/"))
            for term in terms
        )
        files[path] = (
            git(repo, "show", f"{head_id}:{path}")
            if scoped and path.endswith(".py")
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
    except (GitError, UnicodeError) as error:
        return [
            Finding(
                RULE,
                ".",
                str(error),
                "provide readable base and head commits in this repository",
            )
        ]
    return residue_findings(renames, files)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=SUBCOMMANDS["rename-residue"].summary)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args(argv)
    repo = args.repo.resolve()
    try:
        config = load_config(repo)
    except ConfigError as error:
        parser.error(str(error))
    found = run(repo, config, ["--base", args.base, "--head", args.head])
    rendered = format_findings(found)
    if rendered:
        print(rendered)
    return exit_code(found)


if __name__ == "__main__":
    sys.exit(main())
