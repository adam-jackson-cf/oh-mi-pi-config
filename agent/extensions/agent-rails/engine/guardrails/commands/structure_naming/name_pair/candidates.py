from __future__ import annotations

import argparse
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from guardrails.commands.structure_naming.name_pair.propose import (
    NameIndex,
    Pair,
    Proposer,
)
from guardrails.commands.structure_naming.scan_scope import is_vendored_path, proposed
from guardrails.context.changes import (
    DiffError,
    TreeEntry,
    changed_paths,
    read_blobs,
    tree_entries,
)
from guardrails.context.config.house_style import apply_exceptions
from guardrails.context.config.reader import checker_settings
from guardrails.context.config.suite_files import is_test_source
from guardrails.foundation.findings import Finding
from guardrails.foundation.inventory import as_list, matches_any
from guardrails.foundation.source.tree import language_of
from guardrails.subcommand.dispatch import SUBCOMMANDS

RULE = "STRUCT.name-pairs"
EXCEPTION_RULE = "structure-naming/same_thing"
BUNDLE_MANIFEST = "SKILL.md"
READS = frozenset({"python", "typescript", "go"})
MAX_SOURCE_BYTES = 300_000
USE_SITES = 3
USE_TEXT = 160


@dataclass(frozen=True)
class Site:
    kind: str
    path: str | None = None
    lines: tuple[int, int] = (0, 0)
    value: str | None = None


def site_pattern(name: str, site: Site) -> re.Pattern[str]:
    if site.kind == "string_value":
        quote = "['\"`]"
        return re.compile(quote + re.escape(site.value or "") + quote)
    if site.kind == "member":
        return re.compile(rf"(?<![\w$]){re.escape(name)}(?![\w$])")
    if site.kind == "module":
        return re.compile(rf"(import|from|require|use)\b.*\b{re.escape(name)}\b")
    return re.compile(rf"(?<![\w$]){re.escape(name.split('.')[-1])}(?![\w$])")


IsTest = Callable[[str], bool]


@dataclass(frozen=True)
class Sources:
    """The source files a proposal reads, by path, and which of them are test source."""

    tree: dict[str, str]
    is_test: IsTest


def site_hits(
    sources: Sources, pattern: re.Pattern[str], site: Site
) -> list[tuple[str, int, str]]:
    tree = sources.tree
    hits: list[tuple[str, int, str]] = []
    for path in sorted(tree, key=lambda path: (sources.is_test(path), path)):
        text = tree[path]
        if language_of(path) is None or not pattern.search(text):
            continue
        for number, line in enumerate(text.split("\n"), 1):
            if path == site.path and site.lines[0] <= number <= site.lines[1]:
                continue
            if pattern.search(line):
                hits.append((path, number, line.strip()[:USE_TEXT]))
    return hits


def use_sites(sources: Sources, name: str, site: Site) -> list[str]:
    hits = site_hits(sources, site_pattern(name, site), site)
    chosen: list[tuple[str, int, str]] = []
    for hit in hits:
        if hit[0] not in {item[0] for item in chosen} and len(chosen) < USE_SITES:
            chosen.append(hit)
    for hit in hits:
        if len(chosen) >= USE_SITES:
            break
        if hit not in chosen:
            chosen.append(hit)
    return [f"{path}:{number}: {text}" for path, number, text in chosen[:USE_SITES]]


def described(item: dict[str, Any]) -> dict[str, str]:
    return {"signature": item["sig"], "docstring": item["doc"], "code": item["code"]}


def string_evidence(
    sources: Sources, name: Pair, held: dict[str, Any] | None
) -> dict[str, Any]:
    span = (name["line"], name["line"] + (held["nlines"] - 1 if held else 0))
    return {
        "name": name["name"],
        "kind": "string value",
        "path": name["path"],
        "definition": {
            "signature": held["sig"] if held else "",
            "docstring": "",
            "code": held["code"] if held else "",
        },
        "uses": use_sites(
            sources,
            name["name"],
            Site("string_value", name["path"], span, name["value"]),
        ),
    }


def binding_evidence(sources: Sources, name: Pair, text: str | None) -> dict[str, Any]:
    lines, line = sources.tree[name["path"]].split("\n"), name["line"]
    return {
        "name": name["name"].split(".")[-1],
        "kind": "alias binding",
        "path": name["path"],
        "definition": {
            "signature": text or lines[line - 1].strip(),
            "docstring": "",
            "code": "\n".join(lines[max(0, line - 3) : line + 2]),
        },
        "uses": use_sites(
            sources, name["name"], Site("alias", name["path"], (line, line))
        ),
    }


def evidence(
    sources: Sources,
    name: Pair,
    text: str | None = None,
    held: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if name["kind"] == "string_value":
        return string_evidence(sources, name, held)
    if name["kind"] == "alias":
        return binding_evidence(sources, name, text)
    if name["kind"] == "external":
        return {
            "name": name["name"],
            "kind": "name not defined in this repository",
            "path": None,
            "definition": None,
            "uses": use_sites(sources, name["name"], Site("external")),
        }
    item = name.get("_def")
    if item is None:
        return {
            "name": name["name"],
            "kind": name["kind"],
            "path": name["path"],
            "definition": None,
            "uses": [],
        }
    if name["kind"] == "member":
        return {
            "name": name["name"],
            "kind": f"member of {item['kind']} {item['name']}",
            "path": item["path"],
            "definition": described(item),
            "uses": use_sites(sources, name["name"], Site("member", item["path"])),
        }
    site = Site(item["kind"], item["path"], (item["line"], item["end"]))
    return {
        "name": item["name"],
        "kind": item["kind"],
        "path": item["path"],
        "definition": described(item),
        "uses": use_sites(sources, item["name"], site),
    }


def pair_state(sources: Sources, pair: dict[str, Any]) -> dict[str, Any]:
    first, second = pair["a"], pair["b"]
    text = (pair.get("alias") or {}).get("text")
    held = first.get("_def") if second["kind"] == "string_value" else None
    return {
        "first": evidence(sources, first, text=text),
        "second": evidence(sources, second, held=held),
    }


def pair_id(pair: dict[str, Any]) -> str:
    return "<>".join(
        f"{name.get('path') or ''}::{name['name']}" for name in (pair["a"], pair["b"])
    )


def exempt(pair: dict[str, Any], config: dict[str, Any]) -> bool:
    paths = {name.get("path") for name in (pair["a"], pair["b"])} - {None}
    probes = [Finding(EXCEPTION_RULE, str(path), "", "") for path in sorted(paths)]
    return len(apply_exceptions(probes, config)) < len(probes)


def owned_sources(entries: dict[str, TreeEntry], excluded: list[str]) -> list[str]:
    bundles = {
        PurePosixPath(path).parent.as_posix()
        for path in entries
        if PurePosixPath(path).name == BUNDLE_MANIFEST
    }

    def bundled(path: str) -> bool:
        return any(root == "." or path.startswith(root + "/") for root in bundles)

    return sorted(
        path
        for path, (mode, kind, _, size) in entries.items()
        if kind == "blob"
        and mode != "120000"
        and language_of(path) is not None
        and not is_vendored_path(path)
        and not bundled(path)
        and size.isdigit()
        and int(size) < MAX_SOURCE_BYTES
        and not matches_any(path, excluded)
    )


def head_tree(repo: Path, head: str, excluded: list[str]) -> dict[str, str]:
    entries = tree_entries(repo, head)
    paths = owned_sources(entries, excluded)
    blobs = read_blobs(repo, sorted({entries[path].oid for path in paths}))
    return {path: blobs[entries[path].oid].decode("utf-8", "replace") for path in paths}


def name_pair_candidates(
    tree: dict[str, str], changed: set[str], config: dict[str, Any]
) -> list[dict[str, Any]]:
    sources = Sources(tree, lambda path: is_test_source(path, config))
    index = NameIndex(tree, sources.is_test, proposed(READS))
    found = []
    for pair in Proposer(index).propose():
        paths = {pair["a"].get("path"), pair["b"].get("path")}
        if not paths & changed or exempt(pair, config):
            continue
        found.append({
            "id": pair_id(pair),
            "proposer_score": pair["proposer_score"],
            "features": pair["features"],
            "state": pair_state(sources, pair),
        })
    return sorted(found, key=lambda item: item["id"])


def run(repo: Path, config: dict[str, Any], argv: list[str]) -> list[Finding]:
    parser = argparse.ArgumentParser(description=SUBCOMMANDS["name-pairs"].summary)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args(argv)
    excluded = as_list(checker_settings(config, "check").get("exclude")) + as_list(
        checker_settings(config, "python").get("excluded_paths")
    )
    try:
        tree = head_tree(repo, args.head, excluded)
        pairs = name_pair_candidates(
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
    print(json.dumps({"pairs": pairs}, sort_keys=True))
    return []
