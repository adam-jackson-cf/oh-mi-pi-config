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
WORDS = re.compile(r"[A-Z]+(?=[A-Z][a-z]|[^A-Za-z]|$)|[A-Z]?[a-z]+|[0-9]+")


def _words(name: str) -> tuple[str, ...]:
    return tuple(word.lower() for word in WORDS.findall(name))


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


def _prefix_members(paths: list[str]) -> dict[tuple[str, ...], list[str]]:
    prefixes: dict[tuple[str, ...], list[str]] = defaultdict(list)
    for path in paths:
        words = _words(PurePosixPath(path).stem)
        for length in range(1, len(words) + 1):
            prefixes[words[:length]].append(path)
    return prefixes


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


def _groups(
    paths: list[str], added: set[str], profile: dict[str, Any]
) -> list[dict[str, Any]]:
    folders = {
        parent.as_posix() for path in paths for parent in PurePosixPath(path).parents
    }
    siblings: dict[str, list[str]] = defaultdict(list)
    for path in paths:
        part = PurePosixPath(path)
        if (
            part.suffix == ".py"
            and part.stem != "__init__"
            and not is_test_file(path, profile)
        ):
            siblings[part.parent.as_posix()].append(path)
    clusters: list[dict[str, Any]] = []
    touched = {PurePosixPath(path).parent.as_posix() for path in added}
    for folder in sorted(touched):
        prefixes = _prefix_members(siblings.get(folder, []))
        clusters.extend(
            {
                "folder": folder,
                "prefix": "_".join(words),
                "members": sorted(members),
                "added_or_moved": sorted(added.intersection(members)),
            }
            for words, members in sorted(prefixes.items())
            if _longest_new_prefix(folder, words, members, prefixes, added)
            and (PurePosixPath(folder) / "_".join(words)).as_posix() not in folders
        )
    return clusters


def _longest_new_prefix(
    folder: str,
    words: tuple[str, ...],
    members: list[str],
    prefixes: dict[tuple[str, ...], list[str]],
    added: set[str],
) -> bool:
    if len(members) < 2 or not added.intersection(members):
        return False
    if any(
        len(other) == len(words) + 1 and other[:-1] == words and group == members
        for other, group in prefixes.items()
    ):
        return False
    return words != _words(PurePosixPath(folder).name)


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
