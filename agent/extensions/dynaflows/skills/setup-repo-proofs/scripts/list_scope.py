#!/usr/bin/env python3
"""List the Git-owned files one rollout scope covers, for dry runs and scoped gates.

Usage:
  python3 scripts/list_scope.py --repo <dir> whole [--exclude <glob> ...]
  python3 scripts/list_scope.py --repo <dir> changed --base <ref> [--new-paths] [--exclude <glob> ...]
  python3 scripts/list_scope.py --repo <dir> paths <path> [<path> ...] [--exclude <glob> ...]
  python3 scripts/list_scope.py --self-test

whole: every file from `git ls-files --cached --others --exclude-standard`.
changed: files added, copied, modified or renamed since the merge base of <base> and HEAD,
plus staged, unstaged and untracked work; --new-paths keeps only added and renamed paths.
paths: owned files equal to or under each repo-relative path.
--exclude drops matching files (fnmatch, repeatable; `dir/**` also drops the entry `dir` itself, such as a submodule);
name only generated, vendored or fixture paths with origin evidence. A tracked symlink resolving outside the repo fails
the run; an untracked one is skipped with a notice on stderr.
Prints one repo-relative POSIX path per line, sorted. Reads Git state only; writes nothing.
Exit 0: listed (possibly empty). Exit 2: not a Git work tree, unknown base ref, bad path or
a tracked symlink resolving outside the repo.
"""

from __future__ import annotations

import argparse
import fnmatch
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath


class ScopeError(ValueError):
    """Invalid repository, ref or path."""


def git(repo: Path, *args: str) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if result.returncode != 0:
        raise ScopeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return [line for line in result.stdout.splitlines() if line]


def owned(repo: Path) -> set[str]:
    files = git(repo, "ls-files", "--cached", "--others", "--exclude-standard")
    return {name for name in files if (repo / name).exists() or (repo / name).is_symlink()}


def changed(repo: Path, base: str, *, new_paths: bool) -> set[str]:
    git(repo, "rev-parse", "--verify", f"{base}^{{commit}}")
    merge_base = git(repo, "merge-base", base, "HEAD")[0]
    status_filter = "AR" if new_paths else "ACMR"
    names = set(
        git(repo, "diff", "--name-only", f"--diff-filter={status_filter}", merge_base)
    )
    names |= set(git(repo, "ls-files", "--others", "--exclude-standard"))
    return names


def under(paths: list[str], name: str) -> bool:
    item = PurePosixPath(name)
    return any(item == PurePosixPath(p) or PurePosixPath(p) in item.parents for p in paths)


def select(repo: Path, mode: str, *, base: str | None, paths: list[str], new_paths: bool,
           excludes: list[str]) -> list[str]:
    if git(repo, "rev-parse", "--is-inside-work-tree") != ["true"]:
        raise ScopeError("not a Git work tree")
    files = owned(repo)
    if mode == "changed":
        files &= changed(repo, base or "", new_paths=new_paths)
    elif mode == "paths":
        for item in paths:
            if PurePosixPath(item).is_absolute() or ".." in PurePosixPath(item).parts:
                raise ScopeError(f"path must be repo-relative: {item}")
        files = {name for name in files if under([p.rstrip("/") for p in paths], name)}
    kept = sorted(
        name for name in files
        if not any(fnmatch.fnmatchcase(name, g) or fnmatch.fnmatchcase(name + "/", g) for g in excludes)
    )
    root = repo.resolve()
    tracked = set(git(repo, "ls-files", "--cached"))
    outside = [name for name in kept
               if (repo / name).is_symlink() and not (repo / name).resolve().is_relative_to(root)]
    for name in outside:
        if name in tracked:
            raise ScopeError(f"symlink resolves outside the repo: {name}")
        print(f"scope: skipped untracked symlink outside the repo: {name}", file=sys.stderr)
    return [name for name in kept if name not in outside]


def _self_test() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        repo.mkdir()
        git(repo, "init", "-q", "-b", "main")
        git(repo, "config", "user.email", "t@example.com")
        git(repo, "config", "user.name", "t")
        for name in ("src/a.py", "src/b.py", "vendor/lib.py", "docs/x.md"):
            (repo / name).parent.mkdir(parents=True, exist_ok=True)
            (repo / name).write_text("x\n", encoding="utf-8")
        (repo / ".gitignore").write_text("build/\n", encoding="utf-8")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "base")
        git(repo, "checkout", "-q", "-b", "work")
        (repo / "src/a.py").write_text("y\n", encoding="utf-8")
        (repo / "src/new.py").write_text("n\n", encoding="utf-8")
        (repo / "build").mkdir()
        (repo / "build/out.py").write_text("o\n", encoding="utf-8")
        checks = [
            (select(repo, "whole", base=None, paths=[], new_paths=False,
                    excludes=["vendor/*"]),
             [".gitignore", "docs/x.md", "src/a.py", "src/b.py", "src/new.py"]),
            (select(repo, "changed", base="main", paths=[], new_paths=False, excludes=[]),
             ["src/a.py", "src/new.py"]),
            (select(repo, "changed", base="main", paths=[], new_paths=True, excludes=[]),
             ["src/new.py"]),
            (select(repo, "paths", base=None, paths=["src/"], new_paths=False, excludes=[]),
             ["src/a.py", "src/b.py", "src/new.py"]),
        ]
        (repo / "out-link").symlink_to(Path(tmp))
        (repo / "tool-link").symlink_to(Path(tmp))
        git(repo, "add", "out-link")
        git(repo, "commit", "-q", "-m", "link")
        (repo / "gitlink").write_text("stands in for a submodule entry\n", encoding="utf-8")
        checks.append(
            (select(repo, "whole", base=None, paths=[], new_paths=False,
                    excludes=["vendor/**", "gitlink/**", "out-link"]),
             [".gitignore", "docs/x.md", "src/a.py", "src/b.py", "src/new.py"]))
        for got, expected in checks:
            if got != expected:
                print(f"self-test: expected {expected}, got {got}", file=sys.stderr)
                return 1
        for mode, base, paths in (("changed", "missing-ref", []), ("paths", None, ["../x"]),
                                  ("whole", None, [])):
            try:
                select(repo, mode, base=base, paths=paths, new_paths=False, excludes=[])
            except ScopeError:
                continue
            print(f"self-test: {mode} accepted an invalid input", file=sys.stderr)
            return 1
        try:
            select(Path(tmp), "whole", base=None, paths=[], new_paths=False, excludes=[])
        except ScopeError:
            pass
        else:
            print("self-test: non-repository accepted", file=sys.stderr)
            return 1
    print("self-test ok")
    return 0


def main(argv: list[str] | None = None) -> int:
    if (argv if argv is not None else sys.argv[1:]) == ["--self-test"]:
        return _self_test()
    parser = argparse.ArgumentParser(description="List the files one rollout scope covers.")
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--self-test", action="store_true",
                        help="run built-in fixture tests (use alone) and exit")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--exclude", action="append", default=[])
    sub = parser.add_subparsers(dest="mode", required=True)
    sub.add_parser("whole", parents=[common])
    changed_parser = sub.add_parser("changed", parents=[common])
    changed_parser.add_argument("--base", required=True)
    changed_parser.add_argument("--new-paths", action="store_true")
    paths_parser = sub.add_parser("paths", parents=[common])
    paths_parser.add_argument("paths", nargs="+")
    args = parser.parse_args(argv)
    try:
        files = select(
            args.repo,
            args.mode,
            base=getattr(args, "base", None),
            paths=getattr(args, "paths", []),
            new_paths=getattr(args, "new_paths", False),
            excludes=args.exclude,
        )
    except (ScopeError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"scope FAIL: {exc}", file=sys.stderr)
        return 2
    for name in files:
        print(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
