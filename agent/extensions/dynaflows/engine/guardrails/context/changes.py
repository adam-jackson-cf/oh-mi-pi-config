from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

from guardrails.foundation.findings import Finding
from guardrails.host.process import run_bytes

HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


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


def git(repo: Path, *args: str) -> str:
    try:
        result = run_bytes(["git", "-C", str(repo), *args], check=False)
    except OSError as error:
        raise DiffError(f"cannot run git: {error}") from error
    if result.returncode != 0:
        message = result.stderr.decode(errors="replace").strip()
        raise DiffError(message or f"git {' '.join(args)} failed")
    return result.stdout.decode(errors="replace")


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
