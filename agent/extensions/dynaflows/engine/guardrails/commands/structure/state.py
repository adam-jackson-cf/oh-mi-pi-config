from __future__ import annotations

import argparse
import ast
import dataclasses
import importlib.util
import json
import subprocess
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from itertools import combinations
from pathlib import Path, PurePosixPath
from typing import Any

from guardrails.commands.structure.prefix_candidates import _groups, _module_names
from guardrails.commands.structure.rename_residue import (
    DefinedNames,
    GitError,
    Rename,
    git,
    residue_findings,
    retired_terms,
    words,
)
from guardrails.foundation.findings import Finding
from guardrails.foundation.inventory import as_list, matches_any, settings
from guardrails.host.process import run_bytes
from guardrails.toolkit.dispatch import SUBCOMMANDS

DIRECTORY_QUESTIONS = frozenset({
    "technical_kind_grouping",
    "folder_name_coverage",
    "overlapping_responsibilities",
    "sibling_naming_pattern",
})
FILE_QUESTIONS = frozenset({
    "one_name_per_concept",
    "runtime_data_in_code",
    "entry_point_placement",
})
CAPS = {
    "tree_files": 4096,
    "unit_files": 256,
    "python_file_bytes": 262144,
    "python_total_bytes": 8388608,
    "symbols_per_file": 128,
    "imports_per_file": 128,
    "docstring_chars": 512,
    "context_units": 24,
    "context_files": 64,
    "edges": 512,
    "term_owners": 128,
    "owner_paths": 24,
    "clusters": 32,
    "history_commits": 128,
    "change_rows": 256,
    "state_bytes": 48000,
    "limit_rows": 24,
    "limit_text_chars": 512,
}


def _path_words(path: str) -> set[str]:
    return {
        word
        for part in PurePosixPath(path).parts
        for word in words(PurePosixPath(part).stem)
    }


def _names_words(names: list[str]) -> set[str]:
    return {word for name in names for word in words(name)}


class TopLevelNames(DefinedNames):
    descend_classes = False


def compact_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _clipped_row(row: Any) -> Any:
    if not isinstance(row, dict):
        return row[: CAPS["limit_text_chars"]]
    return {
        key: value[: CAPS["limit_text_chars"]] if isinstance(value, str) else value
        for key, value in row.items()
    }


def _field_candidates(
    field: str, value: dict[str, Any]
) -> list[tuple[int, str, str | None]]:
    return [
        (len(compact_json(rows).encode()), field, key)
        for key, rows in value.items()
        if rows
    ]


def _cut_candidates(state: dict[str, Any]) -> list[tuple[int, str, str | None]]:
    candidates: list[tuple[int, str, str | None]] = []
    for field, value in state.items():
        if field in {"folder_path", "evidence_limits"}:
            continue
        if field in {"import_edges", "change"}:
            candidates.extend(_field_candidates(field, value))
        elif value:
            candidates.append((len(compact_json(value).encode()), field, None))
    return candidates


def _bounded(state: dict[str, Any]) -> dict[str, Any]:
    limits = state["evidence_limits"]
    for field in ("truncations", "parse_errors", "excluded_paths"):
        rows = limits[field]
        limits[field] = [_clipped_row(row) for row in rows[: CAPS["limit_rows"]]]
        limits[field + "_omitted_rows"] = max(0, len(rows) - CAPS["limit_rows"])
    while len(compact_json(limits).encode()) > CAPS["state_bytes"] // 3:
        field = max(
            ("truncations", "parse_errors", "excluded_paths"),
            key=lambda name: len(compact_json(limits[name]).encode()),
        )
        rows = limits[field]
        if not rows:
            break
        retained = len(rows) // 2
        limits[field] = rows[:retained]
        limits[field + "_omitted_rows"] += len(rows) - retained
    cuts: dict[str, int] = dict(limits.get("state_budget_omissions", {}))
    limits["state_budget_omissions"] = cuts
    while len(compact_json(state).encode()) > CAPS["state_bytes"]:
        candidates = _cut_candidates(state)
        if not candidates:
            raise ValueError("unit metadata exceeds structure state byte cap")
        _, field, key = max(
            candidates, key=lambda item: (item[0], item[1], item[2] or "")
        )
        container = state if key is None else state[field]
        name = field if key is None else key
        value = container[name]
        retained = len(value) // 2
        container[name] = (
            value[:retained]
            if isinstance(value, list)
            else {k: value[k] for k in sorted(value)[:retained]}
        )
        label = field if key is None else field + "." + key
        cuts[label] = cuts.get(label, 0) + len(value) - retained
    return state


def _git_input(repo: Path, args: list[str], data: bytes) -> bytes:
    try:
        result = run_bytes(
            ["git", "-C", str(repo), *args], data=data, check=False, timeout=60
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GitError(str(error)) from error
    if result.returncode:
        raise GitError(
            result.stderr.decode(errors="replace").strip() or "git batch read failed"
        )
    return result.stdout


def _inside(path: str, folder: str) -> bool:
    return folder == "." or path == folder or path.startswith(folder + "/")


def _commit(repo: Path, revision: str) -> str:
    return (
        git(repo, "rev-parse", "--verify", "--end-of-options", revision + "^{commit}")
        .decode()
        .strip()
    )


def _changes(repo: Path, base: str, head: str) -> tuple[list[Rename], set[str]]:
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
            base,
            head,
            "--",
        )
        .decode()
        .split("\0")
    )
    renames, added = [], set()
    index = 0
    while index < len(tokens) and tokens[index]:
        status = tokens[index][0]
        if status in {"R", "C"}:
            if status == "R":
                renames.append(Rename(tokens[index + 1], tokens[index + 2]))
                added.add(tokens[index + 2])
            index += 3
        else:
            if status == "A":
                added.add(tokens[index + 1])
            index += 2
    return renames, added


def _owned_blobs(repo: Path, head: str, excluded: list[str]) -> dict[str, str]:
    objects = {}
    for entry in git(repo, "ls-tree", "-r", "-z", head).split(b"\0"):
        if not entry:
            continue
        metadata, raw_path = entry.split(b"\t", 1)
        mode, kind, oid = metadata.decode().split()
        path = raw_path.decode()
        if kind == "blob" and mode != "120000" and not matches_any(path, excluded):
            objects[path] = oid
    return objects


def _stored_names(targets: Sequence[ast.expr]) -> list[str]:
    names: list[str] = []
    for target in targets:
        names.extend(
            item.id
            for item in ast.walk(target)
            if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Store)
        )
    return names


def _string_constants(tree: ast.Module) -> dict[str, str]:
    constants: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            bindings = _stored_names(targets)
            value = node.value
            if (
                isinstance(value, ast.Constant)
                and isinstance(value.value, str)
                and len(value.value) <= 80
            ):
                constants.update({
                    name: value.value for name in bindings if name.isupper()
                })
    return constants


def _imports(tree: ast.Module, parent: str) -> list[str]:
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
                except (ImportError, ValueError):
                    destination = "." * node.level + destination
            imports.extend(
                destination if alias.name == "*" else destination + "." + alias.name
                for alias in node.names
            )
    return imports


def _resolved(imported: str, modules: dict[str, list[str]], path: str) -> list[str]:
    target = imported
    while target:
        if target in modules:
            return sorted(p for p in modules[target] if p != path)
        target = target.rpartition(".")[0]
    return []


@dataclass(frozen=True)
class CommitWindow:
    base: str
    head: str
    candidate_base: str | None = None


@dataclass
class EvidenceLog:
    truncations: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    parse_errors: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    global_truncations: int = 0


@dataclass(frozen=True)
class Changes:
    renames: list[Rename]
    added: set[str]
    candidate_added: set[str]


@dataclass
class SourceIndex:
    sources: dict[str, bytes]
    trees: dict[str, ast.Module] = dataclasses.field(default_factory=dict)
    symbols: dict[str, list[str]] = dataclasses.field(default_factory=dict)
    docs: dict[str, dict[str, str]] = dataclasses.field(default_factory=dict)
    constants: dict[str, dict[str, str]] = dataclasses.field(default_factory=dict)


def _directories(repo: Path, revision: str) -> set[str]:
    listing = git(repo, "ls-tree", "-r", "--name-only", "-z", revision).decode()
    return {
        parent.as_posix()
        for path in listing.split("\0")
        if path
        for parent in PurePosixPath(path).parents
    }


def _tree(repo: Path, revision: str) -> str:
    return (
        git(repo, "rev-parse", "--verify", "--end-of-options", revision + "^{tree}")
        .decode()
        .strip()
    )


class Snapshot:
    def __init__(
        self, repo: Path, window: CommitWindow, profile: dict[str, Any]
    ) -> None:
        self.repo, self.profile = repo, profile
        head = _commit(repo, window.head)
        base = (
            git(repo, "merge-base", _commit(repo, window.base), head).decode().strip()
        )
        candidate_base = (
            _tree(repo, window.candidate_base) if window.candidate_base else base
        )
        self.window = CommitWindow(base, head, candidate_base)
        self.existing_directories = _directories(repo, base)
        self.log = EvidenceLog()
        self.excluded = as_list(settings(profile, "check").get("exclude")) + as_list(
            settings(profile, "python").get("excluded_paths")
        )
        objects = _owned_blobs(repo, head, self.excluded)
        self.owned_paths = set(objects)
        self.paths = self.cap(sorted(objects), "tree_files", "tree")
        renames, added = _changes(repo, base, head)
        _, candidate_added = _changes(repo, candidate_base, head)
        self.changes = Changes(renames, added, candidate_added)
        self.parsed = SourceIndex(self.read_sources(objects))
        for path, source in self.parsed.sources.items():
            self.index(path, source)
        self.edges = self.imports()
        self.clusters = _groups(self.paths, candidate_added, profile)
        self.log.global_truncations = len(self.log.truncations)

    def index(self, path: str, source: bytes) -> None:
        try:
            tree = ast.parse(source, filename=path)
        except (SyntaxError, ValueError, UnicodeError) as error:
            self.log.parse_errors.append({"path": path, "error": str(error)})
            return
        self.parsed.trees[path] = tree
        visitor = TopLevelNames()
        visitor.visit(tree)
        docs: dict[str, str] = {}
        module_doc = ast.get_docstring(tree)
        if module_doc:
            docs["<module>"] = self.doc(module_doc.split("\n\n", 1)[0], path)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                doc = ast.get_docstring(node)
                if doc:
                    docs[node.name] = self.doc(doc.splitlines()[0], path)
        constants = _string_constants(tree)
        self.parsed.symbols[path] = self.cap(
            sorted(set(visitor.names)), "symbols_per_file", path
        )
        doc_names = self.cap(sorted(docs), "symbols_per_file", path + ":docstrings")
        self.parsed.docs[path] = {name: docs[name] for name in doc_names}
        constant_names = self.cap(
            sorted(constants), "symbols_per_file", path + ":constants"
        )
        self.parsed.constants[path] = {name: constants[name] for name in constant_names}

    def read_sources(self, objects: dict[str, str]) -> dict[str, bytes]:
        paths = [path for path in self.paths if path.endswith(".py")]
        if not paths:
            return {}
        oids = sorted({objects[path] for path in paths})
        sizes_output = _git_input(
            self.repo,
            ["cat-file", "--batch-check=%(objectname) %(objectsize)"],
            "".join(oid + "\n" for oid in oids).encode(),
        )
        sizes = {
            oid: int(size)
            for oid, size in (row.split() for row in sizes_output.decode().splitlines())
        }
        selected, total = [], 0
        for path in paths:
            size = sizes[objects[path]]
            if (
                size > CAPS["python_file_bytes"]
                or total + size > CAPS["python_total_bytes"]
            ):
                self.log.truncations.append({
                    "field": "python_source",
                    "path": path,
                    "omitted_bytes": size,
                })
                continue
            selected.append(path)
            total += size
        requested = sorted({objects[path] for path in selected})
        if not requested:
            return {}
        output = _git_input(
            self.repo,
            ["cat-file", "--batch"],
            "".join(oid + "\n" for oid in requested).encode(),
        )
        blobs, offset = {}, 0
        for expected in requested:
            end = output.index(b"\n", offset)
            oid, kind, raw_size = output[offset:end].decode().split()
            size = int(raw_size)
            if oid != expected or kind != "blob":
                raise GitError("unexpected Git batch object")
            offset = end + 1
            blobs[oid] = output[offset : offset + size]
            offset += size + 1
        return {path: blobs[objects[path]] for path in selected}

    def cap(self, values: list[Any], cap: str, field: str) -> list[Any]:
        limit = CAPS[cap]
        if len(values) > limit:
            self.log.truncations.append({
                "field": field,
                "limit": limit,
                "omitted": len(values) - limit,
            })
        return values[:limit]

    def doc(self, value: str, path: str) -> str:
        if len(value) > CAPS["docstring_chars"]:
            self.log.truncations.append({
                "field": "docstrings",
                "path": path,
                "omitted_chars": len(value) - CAPS["docstring_chars"],
            })
        return value[: CAPS["docstring_chars"]]

    def imports(self) -> list[dict[str, Any]]:
        aliases = {
            path: _module_names(path, self.profile)
            for path in self.paths
            if path.endswith(".py")
        }
        modules: dict[str, list[str]] = {}
        for path, names in aliases.items():
            for name in names:
                modules.setdefault(name, []).append(path)
        edges: list[dict[str, Any]] = []
        for path, tree in self.parsed.trees.items():
            module = aliases[path][0]
            parent = (
                module
                if PurePosixPath(path).stem == "__init__"
                else module.rpartition(".")[0]
            )
            imports = _imports(tree, parent)
            edges.extend(
                {
                    "from": path,
                    "module": imported,
                    "to": _resolved(imported, modules, path),
                }
                for imported in self.cap(
                    sorted(set(imports)), "imports_per_file", path + ":imports"
                )
            )
        return edges

    def context(self, folder: str) -> dict[str, Any]:
        paths = self.cap(
            [
                p
                for p in self.paths
                if p == folder or str(PurePosixPath(p).parent) == folder
            ],
            "context_files",
            folder + ":context_files",
        )
        return {
            "unit_path": folder,
            "folder_path": folder,
            "files": paths,
            "top_level_symbols": {
                p: self.parsed.symbols[p] for p in paths if p in self.parsed.symbols
            },
        }

    def cluster(self, cluster: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = dict(cluster, eligible=True)
        result["members"] = self.cap(
            cluster["members"], "unit_files", "cluster.members"
        )
        members = set(result["members"])
        result["added_or_moved"] = sorted(
            members.intersection(cluster["added_or_moved"])
        )
        pairs = {(edge["from"], target) for edge in self.edges for target in edge["to"]}
        result["internal_edges"] = self.cap(
            [
                {"source": source, "target": target}
                for source, target in sorted(pairs)
                if source in members and target in members
            ],
            "edges",
            "cluster.internal_edges",
        )
        result["importers"] = {
            member: self.cap(
                sorted(
                    source
                    for source, target in pairs
                    if target == member and source not in members
                ),
                "edges",
                "cluster.importers:" + member,
            )
            for member in sorted(members)
        }
        history = git(
            self.repo,
            "log",
            f"--max-count={CAPS['history_commits'] + 1}",
            "--format=%x1e",
            "--name-only",
            "-z",
            self.window.head,
            "--",
            *sorted(members),
        ).decode()
        commits = [row for row in history.split("\x1e") if row.strip("\0\n")]
        commits = self.cap(commits, "history_commits", "cluster.co_change.history")
        counts: Counter[tuple[str, str]] = Counter()
        for row in commits:
            changed = {p.strip("\n") for p in row.split("\0")} & members
            counts.update(combinations(sorted(changed), 2))
        result["co_change"] = self.cap(
            [
                {"members": list(pair), "count": count}
                for pair, count in sorted(counts.items())
            ],
            "edges",
            "cluster.co_change",
        )
        return result

    def unit(self, folder: str) -> dict[str, Any]:
        start = len(self.log.truncations)
        paths = self.cap(
            [p for p in self.paths if _inside(p, folder)],
            "unit_files",
            folder + ":files",
        )
        selected = set(paths)
        siblings = self.cap(self.siblings(folder), "context_units", "sibling_context")
        renames = [
            r
            for r in self.changes.renames
            if _inside(r.old, folder) or _inside(r.new, folder)
        ]
        related = sorted(
            {str(PurePosixPath(p).parent) for r in renames for p in (r.old, r.new)}
            - {folder}
        )
        related = self.cap(related, "context_units", "related_context")
        terms = retired_terms(self.changes.renames, self.paths)
        terms = [
            term
            for term in terms
            if _inside(term.scope, folder) or _inside(folder, term.scope)
        ]
        files = {p: self.parsed.sources.get(p, b"") for p in self.paths}
        residue = [
            asdict(f)
            for f in residue_findings(self.changes.renames, files)
            if _inside(f.path, folder)
        ]
        owners = self.term_owners(folder, paths)
        owner_terms = self.cap(sorted(owners), "term_owners", "term_owners")
        edge_groups = self.edge_groups(selected)
        state = {
            "folder_path": folder,
            "files": paths,
            "introduced_paths": self.cap(
                sorted(selected & self.changes.added), "change_rows", "introduced_paths"
            ),
            "introduced_directories": self.cap(
                sorted({
                    parent.as_posix()
                    for path in selected & self.changes.added
                    for parent in PurePosixPath(path).parents
                    if parent.as_posix() not in self.existing_directories
                    and parent.as_posix() != "."
                }),
                "change_rows",
                "introduced_directories",
            ),
            "top_level_symbols": {
                p: self.parsed.symbols[p] for p in paths if p in self.parsed.symbols
            },
            "import_edges": {
                kind: self.cap(rows, "edges", "import_edges." + kind)
                for kind, rows in edge_groups.items()
            },
            "sibling_context": [self.context(p) for p in siblings],
            "related_context": [self.context(p) for p in related],
            "docstrings": {
                p: self.parsed.docs[p] for p in paths if self.parsed.docs.get(p)
            },
            "constant_values": {
                p: self.parsed.constants[p]
                for p in paths
                if self.parsed.constants.get(p)
            },
            "term_owners": {
                term: self.cap(
                    sorted(owners[term]), "owner_paths", "term_owners." + term
                )
                for term in owner_terms
            },
            "change": {
                "renames": self.cap(
                    [asdict(r) for r in renames], "change_rows", "change.renames"
                ),
                "retired_terms": self.cap(
                    [asdict(t) for t in terms], "change_rows", "change.retired_terms"
                ),
                "residue": self.cap(residue, "change_rows", "change.residue"),
            },
            "cluster": [
                self.cluster(c)
                for c in self.cap(
                    [c for c in self.clusters if c["folder"] == folder],
                    "clusters",
                    "cluster",
                )
            ],
        }
        state["evidence_limits"] = {
            "base": self.window.base,
            "head": self.window.head,
            "candidate_base": self.window.candidate_base,
            "scope": "owned committed regular blobs; recursive unit, direct-file contexts; Python AST only",
            "excluded_paths": self.excluded,
            "caps": dict(CAPS),
            "truncations": self.log.truncations[: self.log.global_truncations]
            + self.log.truncations[start:],
            "parse_errors": self.log.parse_errors,
            "import_resolution": "static module roots; unresolved or external imports have empty to; no runtime or star expansion",
            "co_change": "bounded head history, current member paths only; no rename following",
        }
        return _bounded(state)

    def siblings(self, folder: str) -> list[str]:
        if folder == ".":
            return []
        parent = str(PurePosixPath(folder).parent)
        sibling_names = sorted({
            PurePosixPath(p).relative_to(parent).parts[0]
            for p in self.paths
            if _inside(p, parent)
        })
        siblings = [(PurePosixPath(parent) / name).as_posix() for name in sibling_names]
        return [p for p in siblings if p != folder]

    def term_owners(self, folder: str, paths: list[str]) -> dict[str, set[str]]:
        unit_words: set[str] = set()
        for p in paths:
            unit_words.update(_path_words(p))
            unit_words.update(_names_words(self.parsed.symbols.get(p, [])))
        owners: dict[str, set[str]] = {}
        for path in self.paths:
            if _inside(path, folder):
                continue
            for owner in (*PurePosixPath(path).parents, PurePosixPath(path)):
                name = owner.stem if owner == PurePosixPath(path) else owner.name
                if name.lower() in unit_words:
                    owners.setdefault(name.lower(), set()).add(owner.as_posix())
        return owners

    def edge_groups(self, selected: set[str]) -> dict[str, list[dict[str, Any]]]:
        groups: dict[str, list[dict[str, Any]]] = {
            "inbound": [],
            "outbound": [],
            "internal": [],
        }
        for edge in self.edges:
            if edge["from"] in selected:
                kind = (
                    "internal"
                    if edge["to"] and all(p in selected for p in edge["to"])
                    else "outbound"
                )
                groups[kind].append(edge)
            elif any(p in selected for p in edge["to"]):
                groups["inbound"].append(edge)
        return groups

    def judgment_unit(self, folder: str) -> dict[str, Any]:
        state = self.unit(folder)
        introduced = set(state["introduced_paths"])
        legacy = [path for path in state["files"] if path not in introduced]
        if legacy:
            state["related_context"].append({
                "unit_path": folder,
                "folder_path": folder,
                "files": legacy,
                "top_level_symbols": {
                    path: self.parsed.symbols[path]
                    for path in legacy
                    if path in self.parsed.symbols
                },
            })
        state["files"] = sorted(introduced)
        for field in ("top_level_symbols", "docstrings", "constant_values"):
            state[field] = {
                path: value
                for path, value in state[field].items()
                if path in introduced
            }
        state["import_edges"] = {
            kind: [
                edge
                for edge in edges
                if edge["from"] in introduced or introduced.intersection(edge["to"])
            ]
            for kind, edges in state["import_edges"].items()
        }
        state["cluster"] = [
            cluster
            for cluster in state["cluster"]
            if cluster["members"] and set(cluster["members"]).issubset(introduced)
        ]
        questions = set(FILE_QUESTIONS) if introduced else set()
        if folder in state["introduced_directories"]:
            questions.update(DIRECTORY_QUESTIONS)
        if state["cluster"]:
            questions.add("prefix_should_be_folder")
        state["judgment_questions"] = sorted(questions)
        return _bounded(state)


def extract_unit(
    repo: Path, window: CommitWindow, unit: str, profile: dict[str, Any] | None = None
) -> dict[str, Any]:
    folder = PurePosixPath(unit).as_posix()
    if PurePosixPath(folder).is_absolute() or ".." in PurePosixPath(folder).parts:
        raise ValueError("unit must be a repository-relative folder")
    return Snapshot(repo, window, profile or {}).unit(folder)


def extract(
    repo: Path, base: str, head: str, profile: dict[str, Any] | None = None
) -> dict[str, dict[str, Any]]:
    snapshot = Snapshot(repo, CommitWindow(base, head), profile or {})
    introduced = snapshot.changes.added & snapshot.owned_paths
    folders = {str(PurePosixPath(path).parent) for path in introduced}
    folders.update(
        parent.as_posix()
        for path in introduced
        for parent in PurePosixPath(path).parents
        if parent.as_posix() not in snapshot.existing_directories
        and parent.as_posix() != "."
    )
    snapshot.log.global_truncations = len(snapshot.log.truncations)
    return {folder: snapshot.judgment_unit(folder) for folder in sorted(folders)}


def run(repo: Path, profile: dict[str, Any], argv: list[str]) -> list[Finding]:
    parser = argparse.ArgumentParser(description=SUBCOMMANDS["structure-state"].summary)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--unit")
    parser.add_argument("--candidate-base")
    args = parser.parse_args(argv)
    try:
        if args.unit is None:
            result = extract(repo, args.base, args.head, profile)
        else:
            window = CommitWindow(args.base, args.head, args.candidate_base)
            result = {args.unit: extract_unit(repo, window, args.unit, profile)}
    except (GitError, UnicodeError, ValueError) as error:
        return [
            Finding(
                "STRUCTURE.state",
                ".",
                str(error),
                "provide readable base and head commits",
            )
        ]
    print(compact_json(result))
    return []
