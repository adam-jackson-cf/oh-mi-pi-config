from __future__ import annotations

import argparse
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from guardrails.structure.inventory import InventoryError, normalized_path, repo_paths


@dataclass
class Scope:
    paths: list[str]
    existing_paths: set[str] | None
    inventory: list[str]
    tree: str | None = None


def git(repo: Path, *args: str, data: bytes = b"") -> bytes:
    try:
        result = subprocess.run(["git", "-C", str(repo), *args], input=data, capture_output=True,
                                check=False, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise InventoryError(str(error)) from error
    if result.returncode:
        raise InventoryError(result.stderr.decode(errors="replace").strip())
    return result.stdout


def tree_paths(repo: Path, tree: str) -> list[str]:
    return [path for path in git(repo, "ls-tree", "-r", "--name-only", "-z", tree).decode().split("\0") if path]


def additions(repo: Path, base: str, head: str) -> list[str]:
    rows = git(repo, "diff", "--no-ext-diff", "--no-textconv", "--name-status", "-z",
               "--find-renames=50%", "-l0", "--diff-filter=AR", base, head, "--").decode().split("\0")
    paths = []
    index = 0
    while index < len(rows) and rows[index]:
        renamed = rows[index].startswith("R")
        paths.append(rows[index + (2 if renamed else 1)])
        index += 3 if renamed else 2
    return sorted(set(paths))


def _expand(repo: Path, requested: list[str], inventory: list[str]) -> list[str]:
    found = set()
    for raw in requested:
        if raw == "." or Path(raw) == repo:
            found.update(inventory)
            continue
        path = normalized_path(repo, raw)
        if path is None:
            found.add(raw)
            continue
        descendants = [item for item in inventory if item == path or item.startswith(path + "/")]
        found.update(descendants or [path])
    return sorted(found)


def select(repo: Path, argv: list[str]) -> Scope:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--event", choices=("edit", "staged"))
    parser.add_argument("--base")
    parser.add_argument("--head")
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args(argv)
    if args.base or args.head:
        if not (args.base and args.head) or args.event or args.all or args.paths:
            raise InventoryError("--base and --head require a range without paths or other scope options")
        head = git(repo, "rev-parse", "--verify", "--end-of-options", args.head + "^{commit}").decode().strip()
        base = git(repo, "merge-base", args.base, head).decode().strip()
        return Scope(additions(repo, base, head), set(tree_paths(repo, base)), tree_paths(repo, head), head)
    if args.all or (not args.paths and not args.event):
        inventory = repo_paths(repo)
        return Scope(_expand(repo, args.paths, inventory) if args.paths else inventory, None, inventory)
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "HEAD"],
                          capture_output=True, check=False, timeout=60)
    base = head.stdout.decode().strip() if head.returncode == 0 else git(repo, "mktree").decode().strip()
    existing = set(tree_paths(repo, base))
    if args.event == "staged":
        tree = git(repo, "write-tree").decode().strip()
        return Scope(additions(repo, base, tree), existing, tree_paths(repo, tree), tree)
    inventory = repo_paths(repo)
    candidates = _expand(repo, args.paths, inventory) if args.paths else inventory
    paths = []
    for raw in candidates:
        path = normalized_path(repo, raw)
        if path is None:
            paths.append(raw)
        elif path not in existing and path in inventory and (repo / path).exists():
            paths.append(path)
    return Scope(paths, existing, inventory)


@contextmanager
def checkout(repo: Path, scope: Scope):
    if scope.tree is None:
        yield repo
        return
    with tempfile.TemporaryDirectory(prefix="omp-path-snapshot-") as scratch:
        root = Path(scratch)
        entries = git(repo, "ls-tree", "-r", "-z", scope.tree).split(b"\0")
        files = []
        for entry in entries:
            if not entry:
                continue
            metadata, raw_path = entry.split(b"\t", 1)
            mode, kind, oid = metadata.decode().split()
            raw = raw_path.decode()
            if kind != "blob" or mode == "120000":
                continue
            if normalized_path(root, raw) != raw:
                raise InventoryError(f"unsafe snapshot path: {raw}")
            files.append((raw, mode, oid))
        if files:
            output = git(repo, "cat-file", "--batch",
                         data="".join(oid + "\n" for _, _, oid in files).encode())
            offset = 0
            for raw, mode, expected in files:
                end = output.index(b"\n", offset)
                oid, kind, raw_size = output[offset:end].decode().split()
                if oid != expected or kind != "blob":
                    raise InventoryError("unexpected snapshot Git object")
                offset = end + 1
                size = int(raw_size)
                path = root / raw
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(output[offset:offset + size])
                path.chmod(int(mode, 8) & 0o777)
                offset += size + 1
        yield root


def restrict(findings, paths: list[str], existing_paths: set[str] | None):
    if existing_paths is None:
        return findings
    existing = {parent.as_posix() for path in existing_paths for parent in Path(path).parents}
    existing.update(existing_paths)
    return [finding for finding in findings
            if finding.path.rstrip("/") not in existing
            and any(path == finding.path.rstrip("/") or path.startswith(finding.path.rstrip("/") + "/")
                    for path in paths)]
