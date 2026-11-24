from __future__ import annotations

import argparse
import ast
import io
import re
import tokenize
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from guardrails.commands.structure.rename_residue import GitError, git
from guardrails.context.config.house_style import apply_exceptions
from guardrails.foundation.findings import Finding, catalogue_rules
from guardrails.foundation.inventory import as_list, matches_any, settings
from guardrails.host.process import run_bytes
from guardrails.toolkit.dispatch import SUBCOMMANDS

RULE = "STRUCT.overlapping-responsibilities"
SKIPPED_FOLDERS = frozenset({
    "__pycache__",
    "build",
    "dist",
    "example",
    "examples",
    "fixture",
    "fixtures",
    "migrations",
    "node_modules",
    "site-packages",
    "test",
    "testing",
    "tests",
    "third_party",
    "vendor",
})
BUNDLE_MANIFEST = "SKILL.md"
TEST_PREFIX = "test_"
MIN_LINES = 5
MIN_GRAMS = 15
MAX_DOCUMENT_FREQUENCY = 40
TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\S")
DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
DOCUMENTED = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)

Gram = tuple[str, str, str]
Span = tuple[tuple[int, int], tuple[int, int]]


@dataclass(frozen=True)
class Definition:
    unit: str
    file: str
    name: str
    grams: frozenset[Gram]


@dataclass(frozen=True)
class Overlap:
    score: float
    first: Definition
    second: Definition


def pair_score_cutoff() -> float:
    rule = next(item for item in catalogue_rules() if item["id"] == RULE)
    return float(rule["contract"]["pair_score_cutoff"])


def unit_of(path: str, bundles: set[str]) -> str | None:
    pure = PurePosixPath(path)
    folder = pure.parent
    if pure.suffix != ".py" or pure.name.startswith(TEST_PREFIX):
        return None
    if any(part in SKIPPED_FOLDERS or part.startswith(".") for part in folder.parts):
        return None
    if any(parent.as_posix() in bundles for parent in (folder, *folder.parents)):
        return None
    return folder.as_posix()


def _docstring_spans(tree: ast.Module) -> list[Span]:
    spans: list[Span] = []
    for node in ast.walk(tree):
        if not isinstance(node, DOCUMENTED) or not node.body:
            continue
        first = node.body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
            and first.end_lineno is not None
            and first.end_col_offset is not None
        ):
            spans.append((
                (first.lineno, first.col_offset),
                (first.end_lineno, first.end_col_offset),
            ))
    return spans


def _comment_spans(source: str, lines: list[str]) -> list[Span]:
    spans: list[Span] = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            (row, start), (_, end) = token.start, token.end
            text = lines[row - 1]
            spans.append((
                (row, len(text[:start].encode())),
                (row, len(text[:end].encode())),
            ))
    return spans


def cleaned_lines(source: str, tree: ast.Module) -> list[bytearray]:
    lines = source.splitlines(keepends=True)
    encoded = [bytearray(line.encode()) for line in lines]
    for (first_row, first_col), (last_row, last_col) in [
        *_docstring_spans(tree),
        *_comment_spans(source, lines),
    ]:
        for row in range(first_row, last_row + 1):
            line = encoded[row - 1]
            start = first_col if row == first_row else 0
            end = last_col if row == last_row else len(line.rstrip(b"\r\n"))
            line[start:end] = b" " * (end - start)
    return encoded


def _segment(lines: list[bytearray], node: ast.stmt) -> str:
    first, last = node.lineno, node.end_lineno or node.lineno
    rows = [bytes(line) for line in lines[first - 1 : last]]
    rows[-1] = rows[-1][: node.end_col_offset]
    rows[0] = rows[0][node.col_offset :]
    return b"".join(rows).decode(errors="replace")


def _named_nodes(tree: ast.Module) -> list[tuple[str, ast.stmt]]:
    nodes: list[tuple[str, ast.stmt]] = []
    for node in tree.body:
        if isinstance(node, DEFINITIONS):
            nodes.append((node.name, node))
        elif isinstance(node, ast.ClassDef):
            nodes.append((node.name, node))
            nodes.extend(
                (f"{node.name}.{method.name}", method)
                for method in node.body
                if isinstance(method, DEFINITIONS)
            )
    return nodes


def definitions(unit: str, path: str, source: str) -> list[Definition]:
    try:
        tree = ast.parse(source, filename=path)
        lines = cleaned_lines(source, tree)
    except (SyntaxError, ValueError, tokenize.TokenError):
        return []
    found = []
    for name, node in _named_nodes(tree):
        last = node.end_lineno or node.lineno
        if last - node.lineno + 1 < MIN_LINES:
            continue
        tokens = TOKEN.findall(_segment(lines, node))
        grams = frozenset(zip(tokens, tokens[1:], tokens[2:], strict=False))
        if len(grams) >= MIN_GRAMS:
            found.append(Definition(unit, path, name, grams))
    return found


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


def exempt(pair: Overlap, profile: dict[str, Any]) -> bool:
    probes = [
        Finding(RULE, _folder(unit), "", "")
        for unit in (pair.first.unit, pair.second.unit)
    ]
    return len(apply_exceptions(probes, profile)) < len(probes)


def overlap_findings(
    found: list[Definition], changed: set[str], cutoff: float, profile: dict[str, Any]
) -> list[Finding]:
    return [
        Finding(
            RULE,
            _folder(pair.first.unit),
            f"shares code with {_folder(pair.second.unit)}: {pair.first.file} "
            f"{pair.first.name} and {pair.second.file} {pair.second.name} have "
            f"3-gram similarity {pair.score:.3f} at or above {cutoff}",
            "move the shared logic into one shared mechanism both units use, or "
            "record an owner-approved guardrails.exceptions entry {rule, path, reason}",
        )
        for _, pair in sorted(overlaps(found, changed).items())
        if pair.score >= cutoff and not exempt(pair, profile)
    ]


def _changed_paths(repo: Path, base: str, head: str) -> list[str]:
    tokens = (
        git(
            repo,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--name-status",
            "-z",
            "--find-renames=50%",
            "-l0",
            "--diff-filter=AMR",
            base,
            head,
            "--",
        )
        .decode()
        .split("\0")
    )
    paths, index = [], 0
    while index < len(tokens) and tokens[index]:
        renamed = tokens[index][0] in {"R", "C"}
        paths.append(tokens[index + (2 if renamed else 1)])
        index += 3 if renamed else 2
    return paths


def _blobs(repo: Path, oids: list[str]) -> dict[str, bytes]:
    if not oids:
        return {}
    result = run_bytes(
        ["git", "-C", str(repo), "cat-file", "--batch"],
        data="".join(oid + "\n" for oid in oids).encode(),
        check=False,
        timeout=60,
    )
    if result.returncode:
        raise GitError(result.stderr.decode(errors="replace").strip())
    output, offset, blobs = result.stdout, 0, {}
    for oid in oids:
        end = output.index(b"\n", offset)
        size = int(output[offset:end].split()[2])
        blobs[oid] = output[end + 1 : end + 1 + size]
        offset = end + 2 + size
    return blobs


def head_units(
    repo: Path, head: str, excluded: list[str]
) -> tuple[dict[str, str], dict[str, str]]:
    entries = {}
    for entry in git(repo, "ls-tree", "-r", "-z", head).split(b"\0"):
        if entry:
            metadata, raw = entry.split(b"\t", 1)
            entries[raw.decode()] = metadata.decode().split()
    bundles = {
        PurePosixPath(path).parent.as_posix()
        for path in entries
        if PurePosixPath(path).name == BUNDLE_MANIFEST
    }
    units = {
        path: unit
        for path, (mode, kind, _) in entries.items()
        if kind == "blob" and mode != "120000" and not matches_any(path, excluded)
        if (unit := unit_of(path, bundles)) is not None
    }
    return units, {path: entries[path][2] for path in units}


def unit_definitions(
    repo: Path, units: dict[str, str], oids: dict[str, str]
) -> list[Definition]:
    blobs = _blobs(repo, sorted(set(oids.values())))
    found = []
    for path in sorted(units):
        try:
            source = blobs[oids[path]].decode()
        except UnicodeDecodeError:
            continue
        found.extend(definitions(units[path], path, source))
    return found


def run(repo: Path, profile: dict[str, Any], argv: list[str]) -> list[Finding]:
    parser = argparse.ArgumentParser(
        description=SUBCOMMANDS["responsibility-overlap"].summary
    )
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args(argv)
    excluded = as_list(settings(profile, "check").get("exclude")) + as_list(
        settings(profile, "python").get("excluded_paths")
    )
    try:
        units, oids = head_units(repo, args.head, excluded)
        changed = {
            units[path]
            for path in _changed_paths(repo, args.base, args.head)
            if path in units
        }
        found = unit_definitions(repo, units, oids) if changed else []
    except (GitError, ValueError) as error:
        return [
            Finding(
                RULE,
                ".",
                str(error),
                "provide readable base and head commits in this repository",
            )
        ]
    return overlap_findings(found, changed, pair_score_cutoff(), profile)
