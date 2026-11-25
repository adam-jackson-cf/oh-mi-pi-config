from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from guardrails.commands.structure.name_extract import (
    EXCLUDED_FOLDER,
    SOURCE_SUFFIXES,
    TEST_PATH,
)
from guardrails.commands.structure.name_propose import NameIndex, Pair, Proposer
from guardrails.commands.structure.rename_residue import GitError, git
from guardrails.context.config.house_style import apply_exceptions
from guardrails.foundation.findings import Finding
from guardrails.foundation.inventory import as_list, matches_any, settings
from guardrails.host.process import run_bytes
from guardrails.toolkit.dispatch import SUBCOMMANDS

RULE = "STRUCTURE.name-pairs"
EXCEPTION_RULE = "structure-naming/same_thing"
BUNDLE_MANIFEST = "SKILL.md"
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


def site_hits(
    tree: dict[str, str], pattern: re.Pattern[str], site: Site
) -> list[tuple[str, int, str]]:
    hits: list[tuple[str, int, str]] = []
    for path in sorted(tree, key=lambda path: (bool(TEST_PATH.search(path)), path)):
        text = tree[path]
        if not path.endswith(SOURCE_SUFFIXES) or not pattern.search(text):
            continue
        for number, line in enumerate(text.split("\n"), 1):
            if path == site.path and site.lines[0] <= number <= site.lines[1]:
                continue
            if pattern.search(line):
                hits.append((path, number, line.strip()[:USE_TEXT]))
    return hits


def use_sites(tree: dict[str, str], name: str, site: Site) -> list[str]:
    hits = site_hits(tree, site_pattern(name, site), site)
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
    tree: dict[str, str], name: Pair, held: dict[str, Any] | None
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
            tree, name["name"], Site("string_value", name["path"], span, name["value"])
        ),
    }


def binding_evidence(
    tree: dict[str, str], name: Pair, text: str | None
) -> dict[str, Any]:
    lines, line = tree[name["path"]].split("\n"), name["line"]
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
            tree, name["name"], Site("alias", name["path"], (line, line))
        ),
    }


def evidence(
    tree: dict[str, str],
    name: Pair,
    text: str | None = None,
    held: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if name["kind"] == "string_value":
        return string_evidence(tree, name, held)
    if name["kind"] == "alias":
        return binding_evidence(tree, name, text)
    if name["kind"] == "external":
        return {
            "name": name["name"],
            "kind": "name not defined in this repository",
            "path": None,
            "definition": None,
            "uses": use_sites(tree, name["name"], Site("external")),
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
            "uses": use_sites(tree, name["name"], Site("member", item["path"])),
        }
    site = Site(item["kind"], item["path"], (item["line"], item["end"]))
    return {
        "name": item["name"],
        "kind": item["kind"],
        "path": item["path"],
        "definition": described(item),
        "uses": use_sites(tree, item["name"], site),
    }


def pair_state(tree: dict[str, str], pair: dict[str, Any]) -> dict[str, Any]:
    first, second = pair["a"], pair["b"]
    text = (pair.get("alias") or {}).get("text")
    held = first.get("_def") if second["kind"] == "string_value" else None
    return {
        "first": evidence(tree, first, text=text),
        "second": evidence(tree, second, held=held),
    }


def pair_id(pair: dict[str, Any]) -> str:
    return "<>".join(
        f"{name.get('path') or ''}::{name['name']}" for name in (pair["a"], pair["b"])
    )


def exempt(pair: dict[str, Any], config: dict[str, Any]) -> bool:
    paths = {name.get("path") for name in (pair["a"], pair["b"])} - {None}
    probes = [Finding(EXCEPTION_RULE, str(path), "", "") for path in sorted(paths)]
    return len(apply_exceptions(probes, config)) < len(probes)


def owned_sources(entries: dict[str, list[str]], excluded: list[str]) -> list[str]:
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
        and path.endswith(SOURCE_SUFFIXES)
        and not EXCLUDED_FOLDER.search(PurePosixPath(path).parent.as_posix() + "/")
        and not bundled(path)
        and size.isdigit()
        and int(size) < MAX_SOURCE_BYTES
        and not matches_any(path, excluded)
    )


def head_tree(repo: Path, head: str, excluded: list[str]) -> dict[str, str]:
    entries = {}
    for entry in git(repo, "ls-tree", "-r", "-l", "-z", head).split(b"\0"):
        if entry:
            metadata, raw = entry.split(b"\t", 1)
            entries[raw.decode()] = metadata.decode().split()
    paths = owned_sources(entries, excluded)
    oids = sorted({entries[path][2] for path in paths})
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
    return {path: blobs[entries[path][2]].decode("utf-8", "replace") for path in paths}


def changed_paths(repo: Path, base: str, head: str) -> set[str]:
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
    found, index = set(), 0
    while index < len(tokens) and tokens[index]:
        renamed = tokens[index][0] in {"R", "C"}
        found.add(tokens[index + (2 if renamed else 1)])
        index += 3 if renamed else 2
    return found


def name_pairs(
    tree: dict[str, str], changed: set[str], config: dict[str, Any]
) -> list[dict[str, Any]]:
    found = []
    for pair in Proposer(NameIndex(tree)).propose():
        paths = {pair["a"].get("path"), pair["b"].get("path")}
        if not paths & changed or exempt(pair, config):
            continue
        found.append({
            "id": pair_id(pair),
            "det_score": pair["det_score"],
            "features": pair["features"],
            "state": pair_state(tree, pair),
        })
    return sorted(found, key=lambda item: item["id"])


def run(repo: Path, config: dict[str, Any], argv: list[str]) -> list[Finding]:
    parser = argparse.ArgumentParser(description=SUBCOMMANDS["name-pairs"].summary)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args(argv)
    excluded = as_list(settings(config, "check").get("exclude")) + as_list(
        settings(config, "python").get("excluded_paths")
    )
    try:
        tree = head_tree(repo, args.head, excluded)
        pairs = name_pairs(tree, changed_paths(repo, args.base, args.head), config)
    except (GitError, ValueError) as error:
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
