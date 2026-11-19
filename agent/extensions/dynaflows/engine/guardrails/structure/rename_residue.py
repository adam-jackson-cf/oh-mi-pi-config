"""RENAME.residue: retired whole names in owned files under renamed paths.

Rename detection is pinned at 50% similarity, independent of Git's rename-limit setting.
Only paths and Python names defined at module/class scope count, never imports or text.
The record's documented ``guardrails.check.exclude`` paths are not owned input.
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from guardrails.config.profile import ProfileError, load_profile
from guardrails.findings import Finding, exit_code, format_findings
from guardrails.structure.inventory import as_list, matches_any, settings

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
    """Split snake, kebab and camel names without treating substrings as words."""
    name = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    return tuple(re.findall(r"[a-z0-9]+", name.lower()))


def contains(sequence: tuple[str, ...], term: tuple[str, ...]) -> bool:
    return bool(term) and any(
        sequence[index : index + len(term)] == term
        for index in range(len(sequence) - len(term) + 1)
    )


def retired_terms(renames: list[Rename], owned_paths: list[str]) -> list[RetiredTerm]:
    """Retire whole old segments only when no owned head path still names that owner."""
    retained = {
        words(segment)
        for raw_path in owned_paths
        for segment in (
            *PurePosixPath(raw_path).parts[:-1], PurePosixPath(raw_path).stem
        )
    }
    found = []
    for rename in renames:
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
    unique = {}
    for term in found:
        if words(term.old) in retained:
            continue
        key = (words(term.old), words(term.new), term.scope, term.directory)
        unique.setdefault(key, term)
    return list(unique.values())


class DefinedNames(ast.NodeVisitor):
    """Collect module/class bindings, stopping before function-local scopes."""

    def __init__(self) -> None:
        self.names: list[str] = []

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.names.append(node.name)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.names.append(node.name)
        for statement in node.body:
            self.visit(statement)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Store):
            self.names.append(node.id)

    def visit_Import(self, node: ast.Import) -> None:
        pass

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        pass

    def visit_Lambda(self, node: ast.Lambda) -> None:
        pass

    def visit_ListComp(
        self, node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp
    ) -> None:
        pass

    visit_SetComp = visit_ListComp
    visit_DictComp = visit_ListComp
    visit_GeneratorExp = visit_ListComp

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.names.append(node.name)
        for statement in node.body:
            self.visit(statement)


def residue_findings(renames: list[Rename], head_files: dict[str, bytes]) -> list[Finding]:
    """Inspect an owned head snapshot; non-Python values need not contain file bytes."""
    terms = retired_terms(renames, list(head_files))
    found: list[Finding] = []
    for path, source in sorted(head_files.items()):
        scoped = [
            term for term in terms
            if path == term.scope or (term.directory and path.startswith(term.scope + "/"))
        ]
        if not scoped:
            continue
        identifiers = []
        if path.endswith(".py"):
            try:
                tree = ast.parse(source, filename=path)
            except (SyntaxError, ValueError, UnicodeError) as error:
                found.append(Finding(RULE, path, f"cannot parse head Python file: {error}",
                                     "repair the Python source so rename residue can be checked"))
                continue
            visitor = DefinedNames()
            visitor.visit(tree)
            identifiers = list(dict.fromkeys(visitor.names))
        parts = PurePosixPath(path).parts
        path_names = (*parts[:-1], PurePosixPath(path).stem)
        for term in scoped:
            retired = words(term.old)
            leftovers = [name for name in identifiers if contains(words(name), retired)]
            path_leftovers = [part for part in path_names if contains(words(part), retired)]
            if path_leftovers:
                leftovers.insert(0, path_leftovers[0])
            for identifier in leftovers:
                found.append(Finding(
                    RULE, path,
                    f"defines `{identifier}`, which still uses `{term.old}` after this change "
                    f"renamed `{term.old}` to `{term.new}` "
                    f"(`{term.rename.old}` → `{term.rename.new}`). A rename changes every use "
                    "in one change, with no alias for the old name (naming.md)",
                    f"Rename it to use `{term.new}`.",
                ))
    return list(dict.fromkeys(found))


class GitError(Exception):
    """The requested commit snapshot could not be read."""


def git(repo: Path, *args: str) -> bytes:
    try:
        result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False)
    except OSError as error:
        raise GitError(str(error)) from error
    if result.returncode:
        raise GitError(result.stderr.decode(errors="replace").strip() or "git command failed")
    return result.stdout


def commit_snapshot(repo: Path, profile: dict, base: str, head: str) -> tuple[list[Rename], dict[str, bytes]]:
    base_id = git(repo, "rev-parse", "--verify", "--end-of-options", base + "^{commit}").decode().strip()
    head_id = git(repo, "rev-parse", "--verify", "--end-of-options", head + "^{commit}").decode().strip()
    rows = git(repo, "diff", "--no-ext-diff", "--no-textconv", "--name-status", "-z",
               f"--find-renames={RENAME_THRESHOLD}", "-l0", "--diff-filter=R", base_id, head_id, "--").decode().split("\0")
    renames = [Rename(rows[index + 1], rows[index + 2]) for index in range(0, len(rows) - 1, 3)]
    exclusions = as_list(settings(profile, "check").get("exclude"))
    # Read the committed tree, not an index/worktree that may differ from --head.
    paths = git(repo, "ls-tree", "-r", "--name-only", "-z", head_id).decode().split("\0")
    owned_paths = [path for path in paths if path and not matches_any(path, exclusions)]
    terms = retired_terms(renames, owned_paths)
    files = {}
    for path in owned_paths:
        scoped = any(
            path == term.scope or (term.directory and path.startswith(term.scope + "/"))
            for term in terms
        )
        files[path] = git(repo, "show", f"{head_id}:{path}") if scoped and path.endswith(".py") else b""
    return renames, files


def run(repo: Path, profile: dict, argv: list[str]) -> list[Finding]:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args(argv)
    try:
        renames, files = commit_snapshot(repo, profile, args.base, args.head)
    except (GitError, UnicodeError) as error:
        return [Finding(RULE, ".", str(error), "provide readable base and head commits in this repository")]
    return residue_findings(renames, files)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args(argv)
    repo, record = args.repo.resolve(), args.record.resolve()
    if record.is_relative_to(repo):
        parser.error("--record must be outside the repository")
    try:
        profile = load_profile(record)
    except ProfileError as error:
        parser.error(str(error))
    found = run(repo, profile, ["--base", args.base, "--head", args.head])
    rendered = format_findings(found)
    if rendered:
        print(rendered)
    return exit_code(found)


if __name__ == "__main__":
    sys.exit(main())
