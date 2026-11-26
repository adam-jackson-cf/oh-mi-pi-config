from __future__ import annotations

import argparse
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from guardrails.foundation.findings import Finding
from guardrails.host.process import run_bytes

HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")
BLOB_TIMEOUT_S = 120


class DiffError(Exception):
    pass


@dataclass(frozen=True)
class DiffRange:
    base: str
    head: str


@dataclass(frozen=True)
class Change:
    status: str
    path: str
    old_path: str | None = None

    @property
    def paths(self) -> tuple[str, ...]:
        return (self.path,) if self.old_path is None else (self.old_path, self.path)


@dataclass(frozen=True)
class LineChanges:
    added: tuple[tuple[int, str], ...]
    removed: tuple[tuple[int, str], ...]


def parse_range(argv: list[str]) -> DiffRange:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--base")
    parser.add_argument("--head", default="HEAD")
    known, _ = parser.parse_known_args(argv)
    if not known.base:
        raise DiffError(
            "pass --base <ref> (and optionally --head <ref>) to name the diff range"
        )
    return DiffRange(known.base, known.head)


def git_bytes(
    repo: Path, *args: str, data: bytes | None = None, timeout: float | None = None
) -> bytes:
    try:
        result = run_bytes(
            ["git", "-C", str(repo), *args], data=data, check=False, timeout=timeout
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DiffError(f"cannot run git: {error}") from error
    if result.returncode != 0:
        message = result.stderr.decode(errors="replace").strip()
        raise DiffError(message or f"git {' '.join(args)} failed")
    return result.stdout


def git(repo: Path, *args: str) -> str:
    return git_bytes(repo, *args).decode(errors="replace")


class TreeEntry(NamedTuple):
    mode: str
    kind: str
    oid: str
    size: str


def tree_entries(repo: Path, head: str) -> dict[str, TreeEntry]:
    """Every path in head's tree with its mode, object type, object id and size
    (`git ls-tree -r -l`; the size is "-" for a submodule), in git's order."""
    entries = {}
    for entry in git(repo, "ls-tree", "-r", "-l", "-z", head).split("\0"):
        if entry:
            metadata, path = entry.split("\t", 1)
            entries[path] = TreeEntry(*metadata.split())
    return entries


def read_blobs(repo: Path, oids: list[str]) -> dict[str, bytes]:
    """Each blob's content by object id, read in one `git cat-file --batch` call."""
    if not oids:
        return {}
    output = git_bytes(
        repo,
        "cat-file",
        "--batch",
        data="".join(oid + "\n" for oid in oids).encode(),
        timeout=BLOB_TIMEOUT_S,
    )
    blobs, offset = {}, 0
    for oid in oids:
        end = output.index(b"\n", offset)
        size = int(output[offset:end].split()[2])
        blobs[oid] = output[end + 1 : end + 1 + size]
        offset = end + 2 + size
    return blobs


def changed_paths(repo: Path, base: str, head: str, statuses: str) -> set[str]:
    """Head-side paths of base..head changes whose status is in `statuses` (git
    --diff-filter letters); a rename counts as its new path."""
    tokens = git(
        repo,
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--name-status",
        "-z",
        "--find-renames=50%",
        "-l0",
        f"--diff-filter={statuses}",
        base,
        head,
        "--",
    ).split("\0")
    found, index = set(), 0
    while index < len(tokens) and tokens[index]:
        renamed = tokens[index][0] in {"R", "C"}
        found.add(tokens[index + (2 if renamed else 1)])
        index += 3 if renamed else 2
    return found


def changed_files(repo: Path, diff: DiffRange) -> list[Change]:
    return name_changes(repo, diff.base, diff.head)


def name_changes(repo: Path, *revisions: str) -> list[Change]:
    output = git(repo, "diff", "--name-status", "-z", "-M", *revisions, "--")
    tokens = [token for token in output.split("\0") if token]
    changes: list[Change] = []
    index = 0
    while index < len(tokens):
        status = tokens[index][0]
        if status in {"R", "C"}:
            changes.append(Change(status, tokens[index + 2], tokens[index + 1]))
            index += 3
        else:
            changes.append(Change(status, tokens[index + 1]))
            index += 2
    return changes


def hunks(repo: Path, diff: DiffRange, *paths: str) -> list[LineChanges]:
    output = git(
        repo,
        "diff",
        "-U0",
        "-M",
        "--no-color",
        "--no-ext-diff",
        diff.base,
        diff.head,
        "--",
        *paths,
    )
    found: list[LineChanges] = []
    added: list[tuple[int, str]] = []
    removed: list[tuple[int, str]] = []
    old_line = new_line = 0
    in_hunk = False
    for line in output.splitlines():
        header = HUNK.match(line)
        if header:
            if in_hunk:
                found.append(LineChanges(tuple(added), tuple(removed)))
            added, removed = [], []
            old_line, new_line = int(header.group(1)), int(header.group(2))
            in_hunk = True
        elif not in_hunk or line.startswith("\\"):
            continue
        elif line.startswith("+"):
            added.append((new_line, line[1:]))
            new_line += 1
        elif line.startswith("-"):
            removed.append((old_line, line[1:]))
            old_line += 1
    if in_hunk:
        found.append(LineChanges(tuple(added), tuple(removed)))
    return found


def line_changes(repo: Path, diff: DiffRange, *paths: str) -> LineChanges:
    parts = hunks(repo, diff, *paths)
    return LineChanges(
        tuple(line for part in parts for line in part.added),
        tuple(line for part in parts for line in part.removed),
    )


def commit_base(repo: Path) -> str:
    try:
        return git(repo, "rev-parse", "--verify", "HEAD").strip()
    except DiffError:
        return git(repo, "hash-object", "-t", "tree", "/dev/null").strip()


def staged_range(repo: Path) -> DiffRange:
    return DiffRange(commit_base(repo), git(repo, "write-tree").strip())


def file_at(repo: Path, ref: str, path: str) -> str | None:
    try:
        return git(repo, "show", f"{ref}:{path}")
    except DiffError:
        return None


def range_finding(rule: str, error: DiffError) -> Finding:
    return Finding(rule, ".", str(error), "name a valid --base/--head commit range")
