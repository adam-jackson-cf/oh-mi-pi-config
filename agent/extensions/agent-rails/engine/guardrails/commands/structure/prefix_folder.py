from __future__ import annotations

import argparse
import ast
import re
from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations
from os.path import commonprefix
from pathlib import Path, PurePosixPath
from typing import Any

from guardrails.commands.structure.rename_residue import GitError, git
from guardrails.context.config.house_style import apply_exceptions
from guardrails.context.config.suite_files import is_test_file
from guardrails.foundation.findings import Finding, catalogue_rules
from guardrails.foundation.inventory import as_list, matches_any, settings
from guardrails.host.process import run_bytes
from guardrails.toolkit.dispatch import SUBCOMMANDS

RULE = "STRUCT.prefix-folder"
HISTORY_COMMITS = 200
MAX_SOURCE_BYTES = 400_000
FEATURES = ("import_edge", "shared_reader", "co_change", "prefix_is_domain_word")
DOCUMENTED = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
Import = tuple[int, list[str], list[str]]


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


def added_or_moved(repo: Path, base: str, head: str) -> set[str]:
    tokens = (
        git(repo, "diff", "--name-status", "-z", "--find-renames=50%", base, head, "--")
        .decode(errors="replace")
        .split("\0")
    )
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


def is_test_like(path: str, config: dict[str, Any]) -> bool:
    pure = PurePosixPath(path)
    return (
        is_test_file(path, config)
        or pure.name == "conftest.py"
        or bool(TEST_FOLDERS.intersection(pure.parts[:-1]))
    )


def groups(
    paths: list[str], added: set[str], config: dict[str, Any]
) -> list[dict[str, Any]]:
    siblings: dict[str, list[str]] = defaultdict(list)
    for path in owned_python(paths):
        if PurePosixPath(path).name != "__init__.py" and not is_test_like(path, config):
            siblings[PurePosixPath(path).parent.as_posix()].append(path)
    found: list[dict[str, Any]] = []
    touched = {PurePosixPath(path).parent.as_posix() for path in added}
    for directory in sorted(touched):
        name = PurePosixPath(directory).name
        own = (
            {singular(token) for token in name_tokens(name)}
            if directory != "."
            else set()
        )
        by_prefix: dict[str, list[str]] = defaultdict(list)
        for path in siblings.get(directory, []):
            tokens = name_tokens(PurePosixPath(path).stem)
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


def contract() -> dict[str, Any]:
    rule = next(item for item in catalogue_rules() if item["id"] == RULE)
    found: dict[str, Any] = rule["contract"]
    return found


def parsed(source: str) -> ast.Module | None:
    try:
        return ast.parse(source)
    except (SyntaxError, ValueError):
        return None


def camel_words(name: str) -> list[str]:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)
    return [word for word in re.split(r"[\s_\-.]+", text.lower()) if word]


def domain_words(tree: ast.Module | None) -> set[str]:
    if tree is None:
        return set()
    docs = [
        ast.get_docstring(node) or ""
        for node in ast.walk(tree)
        if isinstance(node, DOCUMENTED)
    ]
    classes = [node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
    return {
        singular(word) for doc in docs for word in re.findall(r"[a-z]+", doc.lower())
    } | {singular(word) for name in classes for word in camel_words(name)}


def imports(tree: ast.Module) -> list[Import]:
    found: list[Import] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((0, alias.name.split("."), []) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module.split(".") if node.module else []
            names = [alias.name for alias in node.names if alias.name != "*"]
            found.append((node.level, module, names))
    return found


def folder(path: str) -> str:
    parent = PurePosixPath(path).parent.as_posix()
    return "" if parent == "." else parent


def marker(directory: str) -> str:
    return f"{directory}/__init__.py" if directory else "__init__.py"


class ImportGraph:
    def __init__(self, trees: dict[str, ast.Module | None]) -> None:
        self.files = set(trees)
        self.by_suffix: dict[tuple[str, ...], list[str]] = defaultdict(list)
        for path in sorted(trees):
            parts = path[:-3].split("/")
            if parts[-1] == "__init__":
                parts = parts[:-1]
            for index in range(len(parts)):
                self.by_suffix[tuple(parts[index:])].append(path)
        self.edges: dict[str, set[str]] = {}
        for path in sorted(trees):
            tree = trees[path]
            if tree is None:
                continue
            targets: set[str] = set()
            for level, module, names in imports(tree):
                targets.update(self.resolve(level, module, names, path))
            self.edges[path] = targets - {path}
        self.importers: dict[str, set[str]] = defaultdict(set)
        for source, targets in self.edges.items():
            for target in targets:
                self.importers[target].add(source)

    def top_level(self, path: str) -> bool:
        package = PurePosixPath(path).name == "__init__.py"
        directory = folder(folder(path)) if package else folder(path)
        return marker(directory) not in self.files

    def absolute(self, parts: list[str], importer: str) -> str | None:
        found = self.by_suffix.get(tuple(parts), [])
        if len(parts) == 1:
            found = [
                path
                for path in found
                if folder(path) == folder(importer) or self.top_level(path)
            ]
        if len(found) <= 1:
            return found[0] if found else None
        same = [path for path in found if folder(path) == folder(importer)]
        if len(same) == 1:
            return same[0]
        return max(found, key=lambda path: (len(commonprefix([path, importer])), path))

    def relative(
        self, level: int, module: list[str], names: list[str], importer: str
    ) -> list[str]:
        base = folder(importer).split("/") if folder(importer) else []
        if level - 1 > len(base):
            return []
        base = base[: len(base) - (level - 1)]

        def find(parts: list[str]) -> str | None:
            stem = "/".join(base + parts)
            for candidate in (
                stem + ".py",
                (stem + "/" if stem else "") + "__init__.py",
            ):
                if candidate in self.files:
                    return candidate
            return None

        found = [target for name in names if (target := find(module + [name]))]
        owner = find(module) if module else None
        return found + ([owner] if owner else [])

    def resolve(
        self, level: int, module: list[str], names: list[str], importer: str
    ) -> list[str]:
        if level:
            return self.relative(level, module, names, importer)
        found = [
            target
            for name in names
            if (target := self.absolute(module + [name], importer))
        ]
        for length in range(len(module), 0, -1):
            target = self.absolute(module[:length], importer)
            if target:
                found.append(target)
                break
        return found


@dataclass(frozen=True)
class Evidence:
    graph: ImportGraph
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


def sources(
    repo: Path, head: str, excluded: list[str]
) -> tuple[list[str], dict[str, str]]:
    entries = {}
    for entry in git(repo, "ls-tree", "-r", "-l", "-z", head).split(b"\0"):
        if entry:
            metadata, raw = entry.split(b"\t", 1)
            entries[raw.decode()] = metadata.decode().split()
    paths = sorted(path for path in entries if not matches_any(path, excluded))
    owned = [
        path
        for path in owned_python(paths)
        if entries[path][1] == "blob" and entries[path][0] != "120000"
    ]
    oids = sorted({entries[path][2] for path in owned})
    blobs = read_blobs(repo, oids)
    texts = {}
    for path in owned:
        data = blobs.get(entries[path][2], b"")
        texts[path] = (
            data.decode("utf-8", "replace") if len(data) < MAX_SOURCE_BYTES else ""
        )
    return paths, texts


def read_blobs(repo: Path, oids: list[str]) -> dict[str, bytes]:
    if not oids:
        return {}
    result = run_bytes(
        ["git", "-C", str(repo), "cat-file", "--batch"],
        data="".join(oid + "\n" for oid in oids).encode(),
        check=False,
        timeout=120,
    )
    if result.returncode:
        raise GitError(result.stderr.decode(errors="replace").strip())
    blobs, output, offset = {}, result.stdout, 0
    for oid in oids:
        end = output.index(b"\n", offset)
        size = int(output[offset:end].split()[2])
        blobs[oid] = output[end + 1 : end + 1 + size]
        offset = end + 2 + size
    return blobs


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
    ).decode()
    return [
        {line for line in chunk.splitlines() if line.strip()}
        for chunk in log.split("\x1e")[1:]
    ]


def evidence_of(texts: dict[str, str], commits: list[set[str]]) -> Evidence:
    trees = {path: parsed(text) for path, text in texts.items()}
    return Evidence(
        ImportGraph(trees),
        {path: domain_words(tree) for path, tree in trees.items()},
        commits,
    )


def clusters(
    paths: list[str],
    added: set[str],
    evidence: Evidence,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    weights = contract()["weights"]
    return [
        scored(cluster, evidence, weights) for cluster in groups(paths, added, config)
    ]


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
    excluded = as_list(settings(config, "check").get("exclude")) + as_list(
        settings(config, "python").get("excluded_paths")
    )
    try:
        added = added_or_moved(repo, args.base, args.head)
        paths, texts = sources(repo, args.head, excluded)
        found = clusters(
            paths, added, evidence_of(texts, history(repo, args.head)), config
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
    cutoff = float(contract()["cluster_score_cutoff"])
    return [
        finding(cluster, cutoff)
        for cluster in found
        if cluster["score"] >= cutoff and not exempt(cluster, config)
    ]
