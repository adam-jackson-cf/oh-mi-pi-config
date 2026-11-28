from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from guardrails.commands.structure_naming.scan_scope import is_vendored_path
from guardrails.commands.structure_naming.tokens import Gram, definition_grams
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
from guardrails.foundation.source.tree import (
    SourceError,
    language_of,
    rule_definitions,
    source_language,
)
from guardrails.subcommand.dispatch import SUBCOMMANDS

RULE = "STRUCT.overlapping-responsibilities"
MIGRATIONS_FOLDER = "migrations"
BUNDLE_MANIFEST = "SKILL.md"
MIN_LINES = 5
MIN_GRAMS = 15
MAX_DOCUMENT_FREQUENCY = 40


@dataclass(frozen=True)
class Definition:
    language: str
    unit: str
    file: str
    name: str
    grams: frozenset[Gram]


@dataclass(frozen=True)
class Overlap:
    score: float
    first: Definition
    second: Definition


def pair_score_cutoffs() -> dict[str, float | None]:
    return {
        language: entry["pair_score_cutoff"]
        for language, entry in rule_definitions(RULE).items()
    }


def measured_cutoffs() -> dict[str, float]:
    return {
        language: float(cutoff)
        for language, cutoff in pair_score_cutoffs().items()
        if cutoff is not None
    }


def unit_of(
    path: str, bundles: set[str], languages: frozenset[str], config: dict[str, Any]
) -> str | None:
    pure = PurePosixPath(path)
    folder = pure.parent
    if (
        language_of(path) not in languages
        or is_test_source(path, config)
        or is_vendored_path(path)
    ):
        return None
    if MIGRATIONS_FOLDER in folder.parts:
        return None
    if any(parent.as_posix() in bundles for parent in (folder, *folder.parents)):
        return None
    return folder.as_posix()


def definitions(unit: str, path: str, source: str) -> list[Definition]:
    try:
        found = definition_grams(path, source)
    except SourceError:
        return []
    language = source_language(path)
    return [
        Definition(language, unit, path, item.name, grams)
        for item, grams in found
        if item.end_line - item.line + 1 >= MIN_LINES and len(grams) >= MIN_GRAMS
    ]


def by_language(found: list[Definition]) -> dict[str, list[Definition]]:
    grouped: dict[str, list[Definition]] = defaultdict(list)
    for item in found:
        grouped[item.language].append(item)
    return dict(grouped)


def similarity(
    first: Definition, second: Definition, rare: dict[int, set[Gram]]
) -> float:
    shared = len(rare[id(first)] & rare[id(second)])
    return shared / max(1, len(first.grams) + len(second.grams) - shared)


def overlaps(
    found: list[Definition], changed: set[str]
) -> dict[tuple[str, str], Overlap]:
    frequency = Counter(gram for item in found for gram in item.grams)
    rare = {
        id(item): {
            gram for gram in item.grams if frequency[gram] <= MAX_DOCUMENT_FREQUENCY
        }
        for item in found
    }
    index: dict[Gram, list[int]] = defaultdict(list)
    for position, item in enumerate(found):
        for gram in rare[id(item)]:
            index[gram].append(position)
    best: dict[tuple[str, str], Overlap] = {}
    for item in found:
        if item.unit not in changed:
            continue
        candidates = {position for gram in rare[id(item)] for position in index[gram]}
        for position in sorted(candidates):
            other = found[position]
            if other.unit == item.unit:
                continue
            key = (min(item.unit, other.unit), max(item.unit, other.unit))
            score = similarity(item, other, rare)
            if key not in best or score > best[key].score:
                best[key] = Overlap(score, item, other)
    return best


def _folder(unit: str) -> str:
    return unit if unit == "." else unit + "/"


def exempt(pair: Overlap, config: dict[str, Any]) -> bool:
    probes = [
        Finding(RULE, _folder(unit), "", "")
        for unit in (pair.first.unit, pair.second.unit)
    ]
    return len(apply_exceptions(probes, config)) < len(probes)


def overlap_findings(
    found: list[Definition],
    changed: dict[str, set[str]],
    cutoffs: dict[str, float],
    config: dict[str, Any],
) -> list[Finding]:
    return [
        Finding(
            RULE,
            _folder(pair.first.unit),
            f"shares code with {_folder(pair.second.unit)}: {pair.first.file} "
            f"{pair.first.name} and {pair.second.file} {pair.second.name} have "
            f"3-gram similarity {pair.score:.3f} at or above {cutoffs[language]}",
            "move the shared logic into one shared mechanism both units use, or "
            "record an owner-approved exceptions entry {rule, path, reason}",
        )
        for language, items in sorted(by_language(found).items())
        for _, pair in sorted(overlaps(items, changed.get(language, set())).items())
        if pair.score >= cutoffs[language] and not exempt(pair, config)
    ]


def head_units(
    repo: Path, head: str, config: dict[str, Any], languages: frozenset[str]
) -> tuple[dict[str, str], dict[str, str]]:
    entries = tree_entries(repo, head)
    excluded = excluded_paths(config)
    bundles = {
        PurePosixPath(path).parent.as_posix()
        for path in entries
        if PurePosixPath(path).name == BUNDLE_MANIFEST
    }
    units = {
        path: unit
        for path, (mode, kind, _, _) in entries.items()
        if kind == "blob" and mode != "120000" and not matches_any(path, excluded)
        if (unit := unit_of(path, bundles, languages, config)) is not None
    }
    return units, {path: entries[path].oid for path in units}


def unit_definitions(
    repo: Path, units: dict[str, str], oids: dict[str, str]
) -> list[Definition]:
    blobs = read_blobs(repo, sorted(set(oids.values())))
    found = []
    for path in sorted(units):
        try:
            source = blobs[oids[path]].decode()
        except UnicodeDecodeError:
            continue
        found.extend(definitions(units[path], path, source))
    return found


def overlap_candidates(
    repo: Path, head: str, language: str, config: dict[str, Any]
) -> list[Overlap]:
    units, oids = head_units(repo, head, config, frozenset({language}))
    found = unit_definitions(repo, units, oids)
    return [pair for _, pair in sorted(overlaps(found, set(units.values())).items())]


def excluded_paths(config: dict[str, Any]) -> list[str]:
    return as_list(checker_settings(config, "check").get("exclude")) + as_list(
        checker_settings(config, "python").get("excluded_paths")
    )


def run(repo: Path, config: dict[str, Any], argv: list[str]) -> list[Finding]:
    parser = argparse.ArgumentParser(
        description=SUBCOMMANDS["responsibility-overlap"].summary
    )
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args(argv)
    cutoffs = measured_cutoffs()
    try:
        units, oids = head_units(repo, args.head, config, frozenset(cutoffs))
        changed: dict[str, set[str]] = defaultdict(set)
        for path in changed_paths(repo, args.base, args.head, "AMR"):
            if path in units:
                changed[source_language(path)].add(units[path])
        found = unit_definitions(repo, units, oids) if changed else []
    except (DiffError, ValueError) as error:
        return [
            Finding(
                RULE,
                ".",
                str(error),
                "provide readable base and head commits in this repository",
            )
        ]
    return overlap_findings(found, changed, cutoffs, config)
