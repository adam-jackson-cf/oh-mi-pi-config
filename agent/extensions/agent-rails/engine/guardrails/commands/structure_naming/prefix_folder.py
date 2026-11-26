from __future__ import annotations

import argparse
import re
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from itertools import combinations
from pathlib import Path, PurePosixPath
from typing import Any

from guardrails.commands.structure_naming.scan_scope import (
    is_test_path,
    is_vendored_path,
)
from guardrails.context.changes import (
    DiffError,
    changed_paths,
    git,
    read_blobs,
    tree_entries,
)
from guardrails.context.config.house_style import apply_exceptions
from guardrails.context.config.reader import checker_settings
from guardrails.context.config.suite_files import is_test_file
from guardrails.foundation.findings import Finding
from guardrails.foundation.inventory import as_list, matches_any
from guardrails.foundation.source import modules, python, tree, typescript
from guardrails.subcommand.dispatch import SUBCOMMANDS

RULE = "STRUCT.prefix-folder"
HISTORY_COMMITS = 200
MAX_SOURCE_BYTES = 400_000
FEATURES = ("import_edge", "shared_reader", "co_change", "prefix_is_domain_word")
SCRIPT_CLASSES = frozenset({"class_declaration", "class"})
SCRIPT_NAMED_TYPES = {
    "javascript": SCRIPT_CLASSES,
    "typescript": SCRIPT_CLASSES
    | {"abstract_class_declaration", "interface_declaration", "type_alias_declaration"},
}


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


def script_name_tokens(stem: str) -> list[str]:
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", stem)
    return [token for token in re.split(r"[\s_\-.]+", text.lower()) if token]


def stem_tokens(language: str, stem: str) -> list[str]:
    return name_tokens(stem) if language == "python" else script_name_tokens(stem)


def is_member(path: str, language: str) -> bool:
    if language == "python":
        return PurePosixPath(path).suffix == ".py"
    return tree.language_of(path) == language


def owned(paths: list[str], language: str) -> list[str]:
    bundles = {
        PurePosixPath(path).parent.as_posix()
        for path in paths
        if PurePosixPath(path).name == BUNDLE_MANIFEST
    }

    def skipped(path: str) -> bool:
        if is_vendored_path(path):
            return True
        parent = PurePosixPath(path).parent.as_posix()
        return any(
            root == "." or parent == root or parent.startswith(root + "/")
            for root in bundles
        )

    return [path for path in paths if is_member(path, language) and not skipped(path)]


def groups(
    paths: list[str], added: set[str], config: dict[str, Any], language: str
) -> list[dict[str, Any]]:
    siblings: dict[str, list[str]] = defaultdict(list)
    for path in owned(paths, language):
        if PurePosixPath(path).name != modules.package_marker() and not (
            is_test_file(path, config) or is_test_path(path)
        ):
            siblings[PurePosixPath(path).parent.as_posix()].append(path)
    found: list[dict[str, Any]] = []
    touched = {PurePosixPath(path).parent.as_posix() for path in added}
    for directory in sorted(touched):
        name = PurePosixPath(directory).name
        own = (
            {singular(token) for token in stem_tokens(language, name)}
            if directory != "."
            else set()
        )
        by_prefix: dict[str, list[str]] = defaultdict(list)
        for path in siblings.get(directory, []):
            tokens = stem_tokens(language, PurePosixPath(path).stem)
            if tokens:
                by_prefix[tokens[0]].append(path)
        found.extend(
            {
                "folder": directory,
                "prefix": prefix,
                "members": sorted(members),
                "added_or_moved": sorted(added.intersection(members)),
            }
            for prefix, members in sorted(by_prefix.items())
            if len(members) >= 2
            and len(prefix) >= MIN_PREFIX
            and prefix not in MODIFIERS
            and singular(prefix) not in own
            and added.intersection(members)
        )
    return found


@cache
def contracts() -> dict[str, dict[str, Any]]:
    return tree.rule_definitions(RULE)


def measured() -> list[str]:
    return [
        language
        for language, entry in contracts().items()
        if entry["weights"] is not None and entry["cluster_score_cutoff"] is not None
    ]


def parsed(path: str, source: str | None) -> tree.SgNode | None:
    """The tree of a member's source; a file that does not decode, parse, or (for Python)
    hold docstrings CPython can read is skipped: it gives no import edges or words."""
    if source is None:
        return None
    try:
        root = tree.parse(path, source)
        if tree.language_of(path) == "python":
            docstrings(root)
    except tree.SourceError:
        return None
    return root


def camel_words(name: str) -> list[str]:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)
    return [word for word in re.split(r"[\s_\-.]+", text.lower()) if word]


def words_of(texts: list[str], names: list[str]) -> set[str]:
    return {
        singular(word) for text in texts for word in re.findall(r"[a-z]+", text.lower())
    } | {singular(word) for name in names for word in camel_words(name)}


def type_names(root: tree.SgNode, kinds: frozenset[str]) -> list[str]:
    return [
        name.text()
        for node in tree.of_kinds(root, kinds)
        if (name := node.field("name")) is not None
    ]


def docstrings(root: tree.SgNode) -> list[str]:
    return [python.docstring_text(statement) for statement in python.docstrings(root)]


def domain_words(path: str, root: tree.SgNode | None) -> set[str]:
    if root is None:
        return set()
    if tree.language_of(path) == "python":
        return words_of(
            docstrings(root), type_names(root, frozenset({"class_definition"}))
        )
    comments = [node.text() for node in root.find_all(kind="comment")]
    kinds = SCRIPT_NAMED_TYPES[
        "javascript" if tree.grammar(path) == "javascript" else "typescript"
    ]
    return words_of(comments, type_names(root, kinds))


class Links:
    def __init__(self, edges: dict[str, set[str]]) -> None:
        self.edges = edges
        self.importers: dict[str, set[str]] = defaultdict(set)
        for source, targets in edges.items():
            for target in targets:
                self.importers[target].add(source)


def python_links(trees: dict[str, tree.SgNode | None]) -> Links:
    index = modules.PythonIndex(trees)
    return Links({
        path: {
            target
            for item in python.imports(root)
            for target in index.resolve(path, item)
        }
        for path, root in sorted(trees.items())
        if root is not None
    })


def script_links(
    trees: dict[str, tree.SgNode | None], configs: dict[str, modules.TsConfig]
) -> Links:
    edges: dict[str, set[str]] = {}
    for path in sorted(trees):
        root = trees[path]
        if root is None:
            continue
        found = modules.nearest(path, modules.TSCONFIG, configs)
        config = configs[found] if found is not None else None
        edges[path] = {
            target
            for item in typescript.imports(root)
            if (
                target := modules.resolve_specifier(path, item.specifier, trees, config)
            )
            is not None
        } - {path}
    return Links(edges)


@dataclass(frozen=True)
class Evidence:
    graph: Links
    words: dict[str, set[str]]
    history: list[set[str]]


def pair_features(
    evidence: Evidence, prefix: str, first: str, second: str
) -> dict[str, float]:
    graph = evidence.graph
    edge = second in graph.edges.get(first, set()) or first in graph.edges.get(
        second, set()
    )
    readers = (
        graph.importers.get(first, set()) & graph.importers.get(second, set())
    ) - {
        first,
        second,
    }
    both = sum(1 for commit in evidence.history if first in commit and second in commit)
    either = sum(
        1 for commit in evidence.history if first in commit or second in commit
    )
    word = singular(prefix)
    return {
        "import_edge": float(edge),
        "shared_reader": float(bool(readers)),
        "co_change": both / either if either else 0.0,
        "prefix_is_domain_word": float(
            word in evidence.words.get(first, set())
            or word in evidence.words.get(second, set())
        ),
    }


def pair_score(features: dict[str, float], weights: dict[str, float]) -> float:
    capped = {**features, "co_change": min(features["co_change"], 1.0)}
    return round(sum(weights[name] * capped[name] for name in FEATURES), 4)


def scored(
    cluster: dict[str, Any], evidence: Evidence, weights: dict[str, float]
) -> dict[str, Any]:
    best: dict[str, Any] = {"score": -1.0}
    for first, second in combinations(cluster["members"], 2):
        features = pair_features(evidence, cluster["prefix"], first, second)
        score = pair_score(features, weights)
        if score > best["score"]:
            best = {"score": score, "pair": [first, second], "features": features}
    return {**cluster, **best}


def head_paths(
    repo: Path, head: str, excluded: list[str]
) -> tuple[list[str], dict[str, Any]]:
    entries = tree_entries(repo, head)
    return sorted(path for path in entries if not matches_any(path, excluded)), entries


def sources(
    repo: Path, paths: list[str], entries: dict[str, Any], language: str
) -> dict[str, str | None]:
    members = owned(paths, language)
    tracked = set(paths)
    configs = {
        found
        for path in members
        if language != "python"
        and (found := modules.nearest(path, modules.TSCONFIG, tracked)) is not None
    }
    files = [
        path
        for path in [*members, *sorted(configs)]
        if entries[path].kind == "blob" and entries[path].mode != "120000"
    ]
    blobs = read_blobs(repo, sorted({entries[path].oid for path in files}))
    texts: dict[str, str | None] = {}
    for path in files:
        data = blobs.get(entries[path].oid, b"")
        texts[path] = decoded(path, data) if len(data) < MAX_SOURCE_BYTES else ""
    return texts


def decoded(path: str, data: bytes) -> str | None:
    try:
        return tree.decode(path, data)
    except tree.SourceError:
        return None


def history(repo: Path, head: str) -> list[set[str]]:
    log = git(
        repo,
        "log",
        "--first-parent",
        "--diff-merges=first-parent",
        "-n",
        str(HISTORY_COMMITS),
        "--name-only",
        "--format=%x1e",
        head,
    )
    return [
        {line for line in chunk.splitlines() if line.strip()}
        for chunk in log.split("\x1e")[1:]
    ]


def evidence_of(
    language: str, texts: Mapping[str, str | None], commits: list[set[str]]
) -> Evidence:
    configs = {
        path: modules.tsconfig(path, text or "")
        for path, text in texts.items()
        if PurePosixPath(path).name == modules.TSCONFIG
    }
    trees = {
        path: parsed(path, text) for path, text in texts.items() if path not in configs
    }
    graph = (
        python_links(trees) if language == "python" else script_links(trees, configs)
    )
    return Evidence(
        graph,
        {path: domain_words(path, root) for path, root in trees.items()},
        commits,
    )


def clusters(
    paths: list[str],
    added: set[str],
    evidence: Evidence,
    config: dict[str, Any],
    language: str,
) -> list[dict[str, Any]]:
    weights = contracts()[language]["weights"]
    return [
        scored(cluster, evidence, weights)
        for cluster in groups(paths, added, config, language)
    ]


def cluster_candidates(
    repo: Path, head: str, language: str, config: dict[str, Any]
) -> list[dict[str, Any]]:
    """Every prefix cluster of a language with its pair features; none for a language
    structure-languages.json lists the rule as not applying to."""
    if language in tree.not_applicable(RULE):
        return []
    if language not in contracts():
        raise ValueError(f"{RULE} has no definition for {language}")
    paths, entries = head_paths(repo, head, excluded_paths(config))
    evidence = evidence_of(
        language, sources(repo, paths, entries, language), history(repo, head)
    )
    return [
        {
            "folder": cluster["folder"],
            "prefix": cluster["prefix"],
            "members": cluster["members"],
            "pairs": [
                {
                    "pair": [first, second],
                    "features": pair_features(
                        evidence, cluster["prefix"], first, second
                    ),
                }
                for first, second in combinations(cluster["members"], 2)
            ],
        }
        for cluster in groups(paths, set(paths), config, language)
    ]


def excluded_paths(config: dict[str, Any]) -> list[str]:
    return as_list(checker_settings(config, "check").get("exclude")) + as_list(
        checker_settings(config, "python").get("excluded_paths")
    )


def _folder_path(cluster: dict[str, Any]) -> str:
    name = str(cluster["folder"])
    return name if name == "." else name + "/"


def exempt(cluster: dict[str, Any], config: dict[str, Any]) -> bool:
    probes = [
        Finding(RULE, path, "", "")
        for path in [_folder_path(cluster), *cluster["members"]]
    ]
    return len(apply_exceptions(probes, config)) < len(probes)


def finding(cluster: dict[str, Any], cutoff: float) -> Finding:
    first, second = cluster["pair"]
    facts = ", ".join(
        f"{name} {value:g}" for name, value in cluster["features"].items()
    )
    return Finding(
        RULE,
        _folder_path(cluster),
        f"files sharing the prefix `{cluster['prefix']}` implement one concept: "
        f"{', '.join(cluster['members'])}; score {cluster['score']:.2f} at or above "
        f"{cutoff} from {first} and {second} ({facts})",
        f"move them into a `{cluster['prefix']}/` folder named for the concept, or record "
        "an owner-approved exceptions entry {rule, path, reason} on the folder "
        "or a member",
    )


def run(repo: Path, config: dict[str, Any], argv: list[str]) -> list[Finding]:
    parser = argparse.ArgumentParser(description=SUBCOMMANDS["prefix-folder"].summary)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args(argv)
    found: list[tuple[dict[str, Any], float]] = []
    try:
        added = changed_paths(repo, args.base, args.head, "AR")
        paths, entries = head_paths(repo, args.head, excluded_paths(config))
        commits = history(repo, args.head)
        for language in measured():
            texts = sources(repo, paths, entries, language)
            cutoff = float(contracts()[language]["cluster_score_cutoff"])
            evidence = evidence_of(language, texts, commits)
            found.extend(
                (cluster, cutoff)
                for cluster in clusters(paths, added, evidence, config, language)
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
    return [
        finding(cluster, cutoff)
        for cluster, cutoff in found
        if cluster["score"] >= cutoff and not exempt(cluster, config)
    ]
