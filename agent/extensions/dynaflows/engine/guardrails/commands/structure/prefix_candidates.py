from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import re
import sys
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path, PurePosixPath
from typing import Any

from guardrails.commands.structure.import_graph import import_config
from guardrails.context.changes import DiffError, git, parse_range, range_finding
from guardrails.context.config.profile import ProfileError, load_profile
from guardrails.context.config.suite_files import is_test_file
from guardrails.foundation.findings import Finding
from guardrails.foundation.inventory import as_list, matches_any, settings
from guardrails.toolkit.dispatch import SUBCOMMANDS

RULE = "PREFIX.candidates"


def _added_or_moved(repo: Path, base: str, head: str) -> set[str]:
    tokens = git(
        repo, "diff", "--name-status", "-z", "--find-renames=50%", base, head, "--"
    ).split("\0")
    found: set[str] = set()
    index = 0
    while index < len(tokens) and tokens[index]:
        status = tokens[index][0]
        if status in {"R", "C"}:
            if status == "R":
                found.add(tokens[index + 2])
            index += 3
        else:
            if status == "A":
                found.add(tokens[index + 1])
            index += 2
    return found


def _owner_module(imported: str, modules: dict[str, str]) -> str | None:
    while imported:
        if imported in modules:
            return modules[imported]
        imported = imported.rpartition(".")[0]
    return None


def _importers(members: list[str], edges: set[tuple[str, str]]) -> dict[str, list[str]]:
    inside = set(members)
    return {
        member: sorted(
            source
            for source, target in edges
            if target == member and source not in inside
        )
        for member in members
    }


SKIPPED_FOLDERS = frozenset({
    "__pycache__",
    "build",
    "dist",
    "examples",
    "node_modules",
    "third_party",
    "vendor",
})
TEST_FOLDERS = frozenset({"test", "tests"})
MODIFIERS = frozenset({
    "abstract",
    "base",
    "common",
    "core",
    "default",
    "generic",
    "get",
    "helper",
    "helpers",
    "main",
    "my",
    "new",
    "old",
    "run",
    "set",
    "simple",
    "test",
    "tests",
    "tmp",
    "util",
    "utils",
})
BUNDLE_MANIFEST = "SKILL.md"
MIN_PREFIX = 3


def singular(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def name_tokens(stem: str) -> list[str]:
    return [token for token in re.split(r"[_\-]+", stem.lower()) if token]


def owned_python(paths: list[str]) -> list[str]:
    bundles = {
        PurePosixPath(path).parent.as_posix()
        for path in paths
        if PurePosixPath(path).name == BUNDLE_MANIFEST
    }

    def skipped(path: str) -> bool:
        folders = PurePosixPath(path).parts[:-1]
        if any(part.startswith(".") or part in SKIPPED_FOLDERS for part in folders):
            return True
        parent = PurePosixPath(path).parent.as_posix()
        return any(
            root == "." or parent == root or parent.startswith(root + "/")
            for root in bundles
        )

    return [path for path in paths if path.endswith(".py") and not skipped(path)]


def _test_like(path: str, profile: dict[str, Any]) -> bool:
    pure = PurePosixPath(path)
    return (
        is_test_file(path, profile)
        or pure.name == "conftest.py"
        or bool(TEST_FOLDERS.intersection(pure.parts[:-1]))
    )


def _groups(
    paths: list[str], added: set[str], profile: dict[str, Any]
) -> list[dict[str, Any]]:
    siblings: dict[str, list[str]] = defaultdict(list)
    for path in owned_python(paths):
        if PurePosixPath(path).name != "__init__.py" and not _test_like(path, profile):
            siblings[PurePosixPath(path).parent.as_posix()].append(path)
    clusters: list[dict[str, Any]] = []
    touched = {PurePosixPath(path).parent.as_posix() for path in added}
    for folder in sorted(touched):
        name = PurePosixPath(folder).name
        own = (
            {singular(token) for token in name_tokens(name)} if folder != "." else set()
        )
        groups: dict[str, list[str]] = defaultdict(list)
        for path in siblings.get(folder, []):
            tokens = name_tokens(PurePosixPath(path).stem)
            if tokens:
                groups[tokens[0]].append(path)
        clusters.extend(
            {
                "folder": folder,
                "prefix": prefix,
                "members": sorted(members),
                "added_or_moved": sorted(added.intersection(members)),
            }
            for prefix, members in sorted(groups.items())
            if len(members) >= 2
            and len(prefix) >= MIN_PREFIX
            and prefix not in MODIFIERS
            and singular(prefix) not in own
            and added.intersection(members)
        )
    return clusters


def _module_names(path: str, profile: dict[str, Any]) -> list[str]:
    direction, _ = import_config(profile)
    root, package = direction.get("source_root"), direction.get("package")
    names = []
    if isinstance(root, str) and isinstance(package, str):
        source = PurePosixPath(root)
        if PurePosixPath(path).is_relative_to(source):
            relative = PurePosixPath(path).relative_to(source).with_suffix("")
            names.append(".".join([package, *relative.parts]).removesuffix(".__init__"))
    for root in [*as_list(settings(profile, "python").get("source_roots")), "."]:
        source = PurePosixPath(root)
        if PurePosixPath(path).is_relative_to(source):
            relative = PurePosixPath(path).relative_to(source).with_suffix("")
            names.append(".".join(relative.parts).removesuffix(".__init__"))
    return list(dict.fromkeys(names))


def _imported_names(tree: ast.Module, path: str, parent: str) -> list[str]:
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            destination = node.module or ""
            if node.level:
                try:
                    destination = importlib.util.resolve_name(
                        "." * node.level + destination, parent
                    )
                except ImportError as error:
                    raise DiffError(f"{path}: {error}") from error
            imports.extend(
                destination if alias.name == "*" else f"{destination}.{alias.name}"
                for alias in node.names
            )
    return imports


def _import_edges(
    repo: Path, head: str, paths: list[str], profile: dict[str, Any]
) -> set[tuple[str, str]]:
    names = {
        path: _module_names(path, profile) for path in paths if path.endswith(".py")
    }
    modules = {name: path for path, aliases in names.items() for name in aliases}
    edges = set()
    for path, aliases in names.items():
        text = git(repo, "show", f"{head}:{path}")
        tree = ast.parse(text, filename=path)
        module = aliases[0]
        parent = (
            module
            if PurePosixPath(path).stem == "__init__"
            else module.rpartition(".")[0]
        )
        for imported in _imported_names(tree, path, parent):
            target = _owner_module(imported, modules)
            if target is not None and target != path:
                edges.add((path, target))
    return edges


def _co_change(repo: Path, head: str, members: set[str]) -> Counter[tuple[str, str]]:
    counts: Counter[tuple[str, str]] = Counter()
    history = git(
        repo, "log", "--format=%x1e", "--name-only", "-z", head, "--", *sorted(members)
    )
    for commit in history.split("\x1e"):
        changed = {path.strip("\n") for path in commit.split("\0")} & members
        counts.update(combinations(sorted(changed), 2))
    return counts


def candidates(
    repo: Path, profile: dict[str, Any], base: str, head: str = "HEAD"
) -> list[dict[str, Any]]:
    added = _added_or_moved(repo, base, head)
    if not added:
        return []
    excluded = as_list(settings(profile, "check").get("exclude")) + as_list(
        settings(profile, "python").get("excluded_paths")
    )
    paths = sorted(
        path
        for path in git(repo, "ls-tree", "-r", "--name-only", "-z", head).split("\0")
        if path and not matches_any(path, excluded)
    )
    clusters = _groups(paths, added, profile)
    if not clusters:
        return []
    edges = _import_edges(repo, head, paths, profile)
    counts = _co_change(
        repo, head, {member for cluster in clusters for member in cluster["members"]}
    )
    for cluster in clusters:
        members = set(cluster["members"])
        cluster["internal_edges"] = [
            {"source": source, "target": target}
            for source, target in sorted(edges)
            if source in members and target in members
        ]
        cluster["importers"] = _importers(cluster["members"], edges)
        cluster["co_change"] = [
            {"members": [left, right], "count": counts[left, right]}
            for left, right in combinations(cluster["members"], 2)
            if counts[left, right]
        ]
    return clusters


def run(repo: Path, profile: dict[str, Any], argv: list[str]) -> list[Finding]:
    try:
        diff = parse_range(argv)
        result = candidates(repo, profile, diff.base, diff.head)
    except (DiffError, SyntaxError, ValueError) as error:
        return [range_finding(RULE, DiffError(str(error)))]
    print(json.dumps(result, sort_keys=True))
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=SUBCOMMANDS["prefix-candidates"].summary
    )
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", default="HEAD")
    args = parser.parse_args(argv)
    try:
        profile = load_profile(args.record)
    except ProfileError as error:
        print(str(error), file=sys.stderr)
        return 2
    findings = run(args.repo, profile, ["--base", args.base, "--head", args.head])
    for finding in findings:
        print(f"{finding.rule_id}: {finding.message}", file=sys.stderr)
    return 2 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
