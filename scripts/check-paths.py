#!/usr/bin/env python3
"""Check the source allowlist and test layout for changed repository paths."""
import os
from pathlib import Path
import subprocess
import sys


def git(repo, *args, **kwargs):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, **kwargs)


def main(paths):
    root = subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
    repo = Path(root).resolve()
    if not paths:
        tracked = git(repo, "ls-files", "-z")
        tracked.check_returncode()
        paths = [os.fsdecode(path) for path in tracked.stdout.split(b"\0") if path]

    existing = []
    for path in paths:
        # Resolve directory aliases (such as macOS /var) but not a symlink file's target.
        candidate = repo / path
        absolute = candidate.parent.resolve() / candidate.name
        if absolute.exists() or absolute.is_symlink():
            existing.append(os.path.relpath(absolute, repo))
    existing = list(dict.fromkeys(existing))
    if not existing:
        return 0

    # Git excludes tracked paths by default, including force-added ignored files.
    ignored = git(repo, "check-ignore", "-z", "--stdin",
                  input=b"".join(os.fsencode(path) + b"\0" for path in existing))
    if ignored.returncode not in (0, 1):
        ignored.check_returncode()
    ignored_paths = {os.fsdecode(path) for path in ignored.stdout.split(b"\0") if path}

    errors = []
    for path in existing:
        if path in ignored_paths:
            errors.append(f"{path}: ignored by the .gitignore allowlist; "
                          f"add a !{path} re-include or move it under a tracked folder")
        file = Path(path)
        if not (repo / file).is_file() and not (repo / file).is_symlink():
            continue
        is_test = file.name.endswith(".test.ts") or (
            file.name.startswith("test_") and file.suffix == ".py")
        if is_test and file.parent != Path("tests"):
            errors.append(f"{path}: move test files directly under tests/")
        elif file.parts[0] == "tests" and file.suffix in (".ts", ".py") and not is_test:
            pattern = "*.test.ts" if file.suffix == ".ts" else "test_*.py"
            errors.append(f"{path}: rename test files to {pattern} and place directly under tests/")

    for error in errors:
        print(error)
    return int(bool(errors))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
