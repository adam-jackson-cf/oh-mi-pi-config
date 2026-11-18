"""Bounded head-only structure evidence: --base SHA --head SHA.

Reads Git objects, never checkout files or imports target code. Evidence limits
explicitly report omissions; missing relationships are not evidence of absence.
An optional record supplies documented exclusions and Python module roots.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import subprocess
from collections import Counter
from dataclasses import asdict
from itertools import combinations
from pathlib import Path, PurePosixPath

from omp_guardrails.findings import Finding
from omp_guardrails.structure.inventory import as_list, matches_any, settings
from omp_guardrails.structure.prefix_candidates import _groups, _module_names
from omp_guardrails.structure.rename_residue import (
    DefinedNames, GitError, Rename, git, residue_findings, retired_terms, words,
)

CAPS = {
    "tree_files": 4096, "unit_files": 256,
    "python_file_bytes": 262144, "python_total_bytes": 8388608,
    "symbols_per_file": 128, "imports_per_file": 128,
    "docstring_chars": 512, "context_units": 24, "context_files": 64,
    "edges": 512, "term_owners": 128, "owner_paths": 24,
    "clusters": 32, "history_commits": 128, "change_rows": 256,
    "state_bytes": 48000, "limit_rows": 24, "limit_text_chars": 512,
}


class TopLevelNames(DefinedNames):
    """Keep module control-flow bindings, but not class or function locals."""

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.names.append(node.name)


def compact_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _bounded(state: dict) -> dict:
    """Keep the schema while dropping largest evidence collections to fit bytes."""
    limits = state["evidence_limits"]
    for field in ("truncations", "parse_errors", "excluded_paths"):
        rows = limits[field]
        limits[field] = [
            {key: value[:CAPS["limit_text_chars"]] if isinstance(value, str) else value
             for key, value in row.items()} if isinstance(row, dict)
            else row[:CAPS["limit_text_chars"]]
            for row in rows[:CAPS["limit_rows"]]
        ]
        limits[field + "_omitted_rows"] = max(0, len(rows) - CAPS["limit_rows"])
    while len(compact_json(limits).encode()) > CAPS["state_bytes"] // 3:
        field = max(("truncations", "parse_errors", "excluded_paths"),
                    key=lambda name: len(compact_json(limits[name]).encode()))
        rows = limits[field]
        if not rows:
            break
        retained = len(rows) // 2
        limits[field] = rows[:retained]
        limits[field + "_omitted_rows"] += len(rows) - retained
    cuts: dict[str, int] = {}
    limits["state_budget_omissions"] = cuts
    while len(compact_json(state).encode()) > CAPS["state_bytes"]:
        candidates = []
        for field, value in state.items():
            if field in {"folder_path", "evidence_limits"}:
                continue
            if field in {"import_edges", "change"}:
                candidates.extend((len(compact_json(rows).encode()), field, key)
                                  for key, rows in value.items() if rows)
            elif value:
                candidates.append((len(compact_json(value).encode()), field, None))
        if not candidates:
            raise ValueError("unit metadata exceeds structure state byte cap")
        _, field, key = max(candidates, key=lambda item: (item[0], item[1], item[2] or ""))
        container = state if key is None else state[field]
        name = field if key is None else key
        value = container[name]
        retained = len(value) // 2
        container[name] = (value[:retained] if isinstance(value, list)
                           else {k: value[k] for k in sorted(value)[:retained]})
        label = field if key is None else field + "." + key
        cuts[label] = cuts.get(label, 0) + len(value) - retained
    return state

def _git_input(repo: Path, args: list[str], data: bytes) -> bytes:
    try:
        result = subprocess.run(["git", "-C", str(repo), *args], input=data,
                                capture_output=True, check=False, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GitError(str(error)) from error
    if result.returncode:
        raise GitError(result.stderr.decode(errors="replace").strip() or "git batch read failed")
    return result.stdout



def _inside(path: str, folder: str) -> bool:
    return folder == "." or path == folder or path.startswith(folder + "/")


def _commit(repo: Path, revision: str) -> str:
    return git(repo, "rev-parse", "--verify", "--end-of-options",
               revision + "^{commit}").decode().strip()


def _changes(repo: Path, base: str, head: str) -> tuple[list[Rename], set[str]]:
    tokens = git(repo, "diff", "--no-ext-diff", "--no-textconv", "--name-status", "-z",
                 "--find-renames=50%", "-l0", base, head, "--").decode().split("\0")
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


class Snapshot:
    def __init__(self, repo: Path, base: str, head: str, profile: dict,
                 candidate_base: str | None = None) -> None:
        self.repo, self.profile = repo, profile
        self.base, self.head = _commit(repo, base), _commit(repo, head)
        self.candidate_base = (
            git(repo, "rev-parse", "--verify", "--end-of-options", candidate_base + "^{tree}").decode().strip()
            if candidate_base else self.base
        )
        self.truncations: list[dict] = []
        self.parse_errors: list[dict] = []
        self.global_truncations = 0
        excluded = (as_list(settings(profile, "check").get("exclude"))
                    + as_list(settings(profile, "python").get("excluded_paths")))
        self.excluded = excluded
        entries = git(repo, "ls-tree", "-r", "-z", self.head).split(b"\0")
        objects = {}
        for entry in entries:
            if not entry:
                continue
            metadata, raw_path = entry.split(b"\t", 1)
            mode, kind, oid = metadata.decode().split()
            path = raw_path.decode()
            if kind == "blob" and mode != "120000" and not matches_any(path, excluded):
                objects[path] = oid
        self.owned_paths = set(objects)
        self.paths = self.cap(sorted(objects), "tree_files", "tree")
        self.renames, self.added = _changes(repo, self.base, self.head)
        _, self.candidate_added = _changes(repo, self.candidate_base, self.head)
        self.sources = self.read_sources(objects)
        self.trees: dict[str, ast.Module] = {}
        self.symbols: dict[str, list[str]] = {}
        self.docs: dict[str, dict[str, str]] = {}
        self.constants: dict[str, dict[str, str]] = {}
        for path, source in self.sources.items():
            try:
                tree = ast.parse(source, filename=path)
            except (SyntaxError, ValueError, UnicodeError) as error:
                self.parse_errors.append({"path": path, "error": str(error)})
                continue
            self.trees[path] = tree
            visitor = TopLevelNames()
            visitor.visit(tree)
            names, docs, constants = visitor.names, {}, {}
            module_doc = ast.get_docstring(tree)
            if module_doc:
                docs["<module>"] = self.doc(module_doc.split("\n\n", 1)[0], path)
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    doc = ast.get_docstring(node)
                    if doc:
                        docs[node.name] = self.doc(doc.splitlines()[0], path)
                elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    bindings = [item.id for target in targets for item in ast.walk(target)
                                if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Store)]
                    value = node.value
                    if isinstance(value, ast.Constant) and isinstance(value.value, str) and len(value.value) <= 80:
                        constants.update({name: value.value for name in bindings if name.isupper()})
            self.symbols[path] = self.cap(sorted(set(names)), "symbols_per_file", path)
            doc_names = self.cap(sorted(docs), "symbols_per_file", path + ":docstrings")
            self.docs[path] = {name: docs[name] for name in doc_names}
            constant_names = self.cap(sorted(constants), "symbols_per_file", path + ":constants")
            self.constants[path] = {name: constants[name] for name in constant_names}
        self.edges = self.imports()
        self.clusters = _groups(self.paths, self.candidate_added, profile)
        self.global_truncations = len(self.truncations)

    def read_sources(self, objects: dict[str, str]) -> dict[str, bytes]:
        paths = [path for path in self.paths if path.endswith(".py")]
        if not paths:
            return {}
        oids = sorted({objects[path] for path in paths})
        sizes_output = _git_input(
            self.repo, ["cat-file", "--batch-check=%(objectname) %(objectsize)"],
            "".join(oid + "\n" for oid in oids).encode(),
        )
        sizes = {oid: int(size) for oid, size in
                 (row.split() for row in sizes_output.decode().splitlines())}
        selected, total = [], 0
        for path in paths:
            size = sizes[objects[path]]
            if size > CAPS["python_file_bytes"] or total + size > CAPS["python_total_bytes"]:
                self.truncations.append({"field": "python_source", "path": path,
                                         "omitted_bytes": size})
                continue
            selected.append(path)
            total += size
        requested = sorted({objects[path] for path in selected})
        if not requested:
            return {}
        output = _git_input(self.repo, ["cat-file", "--batch"],
                            "".join(oid + "\n" for oid in requested).encode())
        blobs, offset = {}, 0
        for expected in requested:
            end = output.index(b"\n", offset)
            oid, kind, raw_size = output[offset:end].decode().split()
            size = int(raw_size)
            if oid != expected or kind != "blob":
                raise GitError("unexpected Git batch object")
            offset = end + 1
            blobs[oid] = output[offset:offset + size]
            offset += size + 1
        return {path: blobs[objects[path]] for path in selected}

    def cap(self, values: list, cap: str, field: str) -> list:
        limit = CAPS[cap]
        if len(values) > limit:
            self.truncations.append({"field": field, "limit": limit,
                                     "omitted": len(values) - limit})
        return values[:limit]

    def doc(self, value: str, path: str) -> str:
        if len(value) > CAPS["docstring_chars"]:
            self.truncations.append({"field": "docstrings", "path": path,
                                     "omitted_chars": len(value) - CAPS["docstring_chars"]})
        return value[:CAPS["docstring_chars"]]

    def imports(self) -> list[dict]:
        aliases = {path: _module_names(path, self.profile) for path in self.paths if path.endswith(".py")}
        modules: dict[str, list[str]] = {}
        for path, names in aliases.items():
            for name in names:
                modules.setdefault(name, []).append(path)
        edges = []
        for path, tree in self.trees.items():
            module = aliases[path][0]
            parent = module if PurePosixPath(path).stem == "__init__" else module.rpartition(".")[0]
            imports = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    destination = node.module or ""
                    if node.level:
                        try:
                            destination = importlib.util.resolve_name("." * node.level + destination, parent)
                        except (ImportError, ValueError):
                            destination = "." * node.level + destination
                    imports.extend(destination if alias.name == "*" else destination + "." + alias.name
                                   for alias in node.names)
            for imported in self.cap(sorted(set(imports)), "imports_per_file", path + ":imports"):
                target, resolved = imported, []
                while target:
                    if target in modules:
                        resolved = sorted(p for p in modules[target] if p != path)
                        break
                    target = target.rpartition(".")[0]
                edges.append({"from": path, "module": imported, "to": resolved})
        return edges

    def context(self, folder: str) -> dict:
        paths = self.cap([p for p in self.paths if p == folder or str(PurePosixPath(p).parent) == folder],
                         "context_files", folder + ":context_files")
        return {"unit_path": folder, "folder_path": folder, "files": paths,
                "top_level_symbols": {p: self.symbols[p] for p in paths if p in self.symbols}}

    def cluster(self, cluster: dict) -> dict:
        result: dict = dict(cluster, eligible=True)
        result["members"] = self.cap(cluster["members"], "unit_files", "cluster.members")
        members = set(result["members"])
        result["added_or_moved"] = sorted(members.intersection(cluster["added_or_moved"]))
        pairs = {(edge["from"], target) for edge in self.edges for target in edge["to"]}
        result["internal_edges"] = self.cap([
            {"source": source, "target": target} for source, target in sorted(pairs)
            if source in members and target in members], "edges", "cluster.internal_edges")
        result["importers"] = {member: self.cap(sorted(source for source, target in pairs
                                                      if target == member and source not in members),
                                                 "edges", "cluster.importers:" + member)
                               for member in sorted(members)}
        history = git(self.repo, "log", f"--max-count={CAPS['history_commits'] + 1}",
                      "--format=%x1e", "--name-only", "-z", self.head, "--", *sorted(members)).decode()
        commits = [row for row in history.split("\x1e") if row.strip("\0\n")]
        commits = self.cap(commits, "history_commits", "cluster.co_change.history")
        counts: Counter = Counter()
        for row in commits:
            changed = {p.strip("\n") for p in row.split("\0")} & members
            counts.update(combinations(sorted(changed), 2))
        result["co_change"] = self.cap([{"members": list(pair), "count": count}
                                           for pair, count in sorted(counts.items())],
                                          "edges", "cluster.co_change")
        return result

    def unit(self, folder: str) -> dict:
        start = len(self.truncations)
        paths = self.cap([p for p in self.paths if _inside(p, folder)], "unit_files", folder + ":files")
        selected = set(paths)
        parent = str(PurePosixPath(folder).parent)
        sibling_names = sorted({PurePosixPath(p).relative_to(parent).parts[0]
                                for p in self.paths if _inside(p, parent)})
        siblings = [(PurePosixPath(parent) / name).as_posix() for name in sibling_names]
        siblings = self.cap([p for p in siblings if p != folder] if folder != "." else [],
                            "context_units", "sibling_context")
        renames = [r for r in self.renames if _inside(r.old, folder) or _inside(r.new, folder)]
        related = sorted({str(PurePosixPath(p).parent) for r in renames for p in (r.old, r.new)} - {folder})
        related = self.cap(related, "context_units", "related_context")
        terms = retired_terms(self.renames, self.paths)
        terms = [term for term in terms if _inside(term.scope, folder) or _inside(folder, term.scope)]
        files = {p: self.sources.get(p, b"") for p in self.paths}
        residue = [asdict(f) for f in residue_findings(self.renames, files) if _inside(f.path, folder)]
        unit_words = {word for p in paths for part in PurePosixPath(p).parts for word in words(PurePosixPath(part).stem)}
        unit_words.update(word for p in paths for name in self.symbols.get(p, []) for word in words(name))
        owners: dict[str, set[str]] = {}
        for path in self.paths:
            if _inside(path, folder):
                continue
            for owner in (*PurePosixPath(path).parents, PurePosixPath(path)):
                name = owner.stem if owner == PurePosixPath(path) else owner.name
                if name.lower() in unit_words:
                    owners.setdefault(name.lower(), set()).add(owner.as_posix())
        owner_terms = self.cap(sorted(owners), "term_owners", "term_owners")
        edge_groups = {"inbound": [], "outbound": [], "internal": []}
        for edge in self.edges:
            source_inside = edge["from"] in selected
            targets_inside = any(p in selected for p in edge["to"])
            if source_inside:
                kind = "internal" if edge["to"] and all(p in selected for p in edge["to"]) else "outbound"
                edge_groups[kind].append(edge)
            elif targets_inside:
                edge_groups["inbound"].append(edge)
        state = {
            "folder_path": folder, "files": paths,
            "top_level_symbols": {p: self.symbols[p] for p in paths if p in self.symbols},
            "import_edges": {kind: self.cap(rows, "edges", "import_edges." + kind) for kind, rows in edge_groups.items()},
            "sibling_context": [self.context(p) for p in siblings],
            "related_context": [self.context(p) for p in related],
            "docstrings": {p: self.docs[p] for p in paths if self.docs.get(p)},
            "constant_values": {p: self.constants[p] for p in paths if self.constants.get(p)},
            "term_owners": {term: self.cap(sorted(owners[term]), "owner_paths", "term_owners." + term) for term in owner_terms},
            "change": {"renames": self.cap([asdict(r) for r in renames], "change_rows", "change.renames"),
                       "retired_terms": self.cap([asdict(t) for t in terms], "change_rows", "change.retired_terms"),
                       "residue": self.cap(residue, "change_rows", "change.residue")},
            "cluster": [self.cluster(c) for c in self.cap([c for c in self.clusters if c["folder"] == folder], "clusters", "cluster")],
        }
        state["evidence_limits"] = {
            "base": self.base, "head": self.head, "candidate_base": self.candidate_base,
            "scope": "owned committed regular blobs; recursive unit, direct-file contexts; Python AST only",
            "excluded_paths": self.excluded, "caps": dict(CAPS),
            "truncations": self.truncations[:self.global_truncations] + self.truncations[start:],
            "parse_errors": self.parse_errors,
            "import_resolution": "static module roots; unresolved or external imports have empty to; no runtime or star expansion",
            "co_change": "bounded head history, current member paths only; no rename following",
        }
        return _bounded(state)



def extract_unit(repo: Path, base: str, head: str, unit: str, profile: dict | None = None,
                 *, candidate_base: str | None = None) -> dict:
    """Extract any head folder; candidate_base may select a past introduction window."""
    folder = PurePosixPath(unit).as_posix()
    if PurePosixPath(folder).is_absolute() or ".." in PurePosixPath(folder).parts:
        raise ValueError("unit must be a repository-relative folder")
    snapshot = Snapshot(repo, base, head, profile or {}, candidate_base)
    snapshot.global_truncations = len(snapshot.truncations)
    return snapshot.unit(folder)


def extract(repo: Path, base: str, head: str, profile: dict | None = None) -> dict[str, dict]:
    """Extract destination folders of added/renamed/moved owned files only."""
    snapshot = Snapshot(repo, base, head, profile or {})
    folders = sorted({str(PurePosixPath(p).parent) for p in snapshot.added if p in snapshot.owned_paths})
    snapshot.global_truncations = len(snapshot.truncations)
    return {folder: snapshot.unit(folder) for folder in folders}


def run(repo: Path, profile: dict, argv: list[str]) -> list[Finding]:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args(argv)
    try:
        result = extract(repo, args.base, args.head, profile)
    except (GitError, UnicodeError, ValueError) as error:
        return [Finding("STRUCTURE.state", ".", str(error), "provide readable base and head commits")]
    print(compact_json(result))
    return []
