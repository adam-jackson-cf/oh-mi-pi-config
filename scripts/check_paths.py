#!/usr/bin/env python3
"""Check the source allowlist and test layout for changed repository paths."""

import os
import subprocess
import sys
from pathlib import Path


def git(
    repo: Path, *args: str, stdin: bytes | None = None
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, input=stdin, check=False
    )


def repo_root() -> Path:
    root = subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True)
    return Path(root.strip()).resolve()


def existing_paths(repo: Path, paths: list[str]) -> list[str]:
    """Repo-relative forms of the given paths that exist inside the repository, without duplicates.

    With no paths, every tracked path is checked.
    """
    base = Path.cwd()
    if not paths:
        tracked = git(repo, "ls-files", "-z")
        tracked.check_returncode()
        paths = [os.fsdecode(path) for path in tracked.stdout.split(b"\0") if path]
        base = repo
    existing = []
    for path in paths:
        # Resolve directory aliases (such as macOS /var) but not a symlink file's target.
        candidate = base / path
        absolute = candidate.parent.resolve() / candidate.name
        if absolute.is_relative_to(repo) and (
            absolute.exists() or absolute.is_symlink()
        ):
            existing.append(os.path.relpath(absolute, repo))
    return list(dict.fromkeys(existing))


def ignored_paths(repo: Path, paths: list[str]) -> set[str]:
    """The paths the .gitignore allowlist ignores."""
    # Git excludes tracked paths by default, including force-added ignored files.
    ignored = git(
        repo,
        "check-ignore",
        "-z",
        "--stdin",
        stdin=b"".join(os.fsencode(path) + b"\0" for path in paths),
    )
    if ignored.returncode not in {0, 1}:
        ignored.check_returncode()
    return {os.fsdecode(path) for path in ignored.stdout.split(b"\0") if path}


def layout_error(repo: Path, path: str) -> str | None:
    """Why the file at ``path`` breaks the test layout, or None."""
    file = Path(path)
    if not (repo / file).is_file() and not (repo / file).is_symlink():
        return None
    is_test = file.name.endswith(".test.ts") or (
        file.name.startswith("test_") and file.suffix == ".py"
    )
    if is_test and file.parent != Path("tests"):
        return f"{path}: move test files directly under tests/"
    if file.parts[0] == "tests" and file.suffix in {".ts", ".py"} and not is_test:
        pattern = "*.test.ts" if file.suffix == ".ts" else "test_*.py"
        return f"{path}: rename test files to {pattern} and place directly under tests/"
    return None


def main(paths: list[str]) -> int:
    repo = repo_root()
    existing = existing_paths(repo, paths)
    if not existing:
        return 0
    ignored = ignored_paths(repo, existing)
    errors = []
    for path in existing:
        if path in ignored:
            errors.append(
                f"{path}: ignored by the .gitignore allowlist; "
                f"add a !{path} re-include or move it under a tracked folder"
            )
        error = layout_error(repo, path)
        if error:
            errors.append(error)

    for error in errors:
        print(error)
    return int(bool(errors))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
