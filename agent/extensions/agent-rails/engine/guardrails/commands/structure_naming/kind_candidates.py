from __future__ import annotations

import argparse
import json
import os.path
from collections.abc import Collection
from pathlib import Path
from typing import TYPE_CHECKING, Any

from guardrails.commands.structure_naming import importers, outline
from guardrails.commands.structure_naming.scan_scope import (
    checked,
    is_vendored_path,
    language_list,
    runs,
)
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
from guardrails.foundation.source.tree import SourceError, language_of, parse

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
READS = frozenset({"python", "typescript", "go"})
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


Parsed = tuple["SgNode", outline.Outline]


def parsed(path: str, source: str) -> Parsed | None:
    """The tree and outline of a source file; an unparseable file is skipped (None): it
    proposes no candidate and gives no import edges."""
    try:
        root = parse(path, source)
        return root, outline.outline(path, root, source)
    except SourceError:
        return None


class TreeModel:
    def __init__(
        self,
        paths: list[str],
        sources: dict[str, str],
        config: dict[str, Any],
        languages: Collection[str],
    ) -> None:
        self.sources = sources
        self.config = config
        self.files = sorted(path for path in paths if runs(path, languages))
        self.read = {
            path: found
            for path in self.files
            if (found := parsed(path, sources.get(path, ""))) is not None
        }
        roots = {path: found[0] for path, found in self.read.items()}
        self.graph = importers.graph(self.files, sources, roots)

    def eligible(self) -> list[str]:
        return [
            path
            for path in self.files
            if not outline.folder_entry(path)
            and os.path.dirname(path)
            and os.path.dirname(path).split("/")[-1].lower() in KIND_FORMS
            and path in self.read
            and self.read[path][1].entries
        ]

    def state(self, path: str) -> dict[str, Any]:
        found = self.read[path][1]
        symbols = [
            item.signature + (f"  # {item.doc}" if item.doc else "")
            for item in found.entries[:MAX_SYMBOLS]
        ]
        folder = os.path.dirname(path)
        chosen = sorted(
            self.graph.get(path, []),
            key=lambda item: (
                is_test_source(item.path, self.config),
                os.path.dirname(item.path) == folder,
                item.path,
            ),
        )[:MAX_IMPORTERS]
        return {
            "file": {"path": path, "docstring": found.doc, "symbols": symbols},
            "folder": folder,
            "importers": [{"path": item.path, "import": item.text} for item in chosen],
        }


def model_of(
    tree: dict[str, str], config: dict[str, Any], languages: Collection[str]
) -> TreeModel:
    paths = owned(sorted(tree))
    return TreeModel(paths, {path: tree[path] for path in paths}, config, languages)


def head_tree(
    repo: Path, head: str, excluded_paths: list[str], languages: Collection[str]
) -> dict[str, str]:
    """The owned paths of a head commit, with the text of the source files of the
    languages and of the files their imports resolve through."""
    entries = {
        path: entry
        for path, entry in tree_entries(repo, head).items()
        if not matches_any(path, excluded_paths)
    }
    paths = owned(sorted(entries))
    resolving = importers.manifests(languages)
    want = [
        (path, entries[path].oid)
        for path in paths
        if (runs(path, languages) or os.path.basename(path) in resolving)
        and entries[path].kind == "blob"
    ]
    found = dict.fromkeys(paths, "")
    blobs = read_blobs(repo, sorted({oid for _, oid in want}))
    for path, oid in want:
        found[path] = blobs[oid].decode("utf-8", "replace")
    return found


def exempt(path: str, config: dict[str, Any]) -> bool:
    return not apply_exceptions([Finding(EXCEPTION_RULE, path, "", "")], config)


def kind_candidates(
    tree: dict[str, str],
    changed: set[str],
    config: dict[str, Any],
    languages: Collection[str],
) -> list[dict[str, Any]]:
    """The changed kind-folder files of the languages asked for, each with its language: the
    kernel asks for the languages its question has settings for, a measurement for any
    language this proposer reads."""
    model = model_of(tree, config, checked(languages, READS))
    return [
        {"path": path, "language": language_of(path), "state": model.state(path)}
        for path in model.eligible()
        if path in changed and not exempt(path, config)
    ]


def run(repo: Path, config: dict[str, Any], argv: list[str]) -> list[Finding]:
    parser = argparse.ArgumentParser(description=SUBCOMMANDS["kind-candidates"].summary)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--languages", required=True, type=language_list)
    args = parser.parse_args(argv)
    excluded_paths = as_list(
        checker_settings(config, "check").get("exclude")
    ) + as_list(checker_settings(config, "python").get("excluded_paths"))
    try:
        languages = checked(args.languages, READS)
        tree = head_tree(repo, args.head, excluded_paths, languages)
        found = kind_candidates(
            tree, changed_paths(repo, args.base, args.head, "AMR"), config, languages
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
