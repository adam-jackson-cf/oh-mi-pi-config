"""Install the tracked pre-commit hook that runs ``.omp/gate.json`` ``checks.commit``.

Init installs ``.githooks/pre-commit`` and sets ``core.hooksPath`` only when the repo has no
hook of its own. It never replaces an installed hook, a hook manager's configuration or a
different ``core.hooksPath``; those cases are reported for the owner to wire by hand.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from omp_guardrails.findings import Finding

RULE = "GATE.pre-commit"
HOOKS_DIR = ".githooks"
HOOK_PATH = f"{HOOKS_DIR}/pre-commit"
MANAGER_FILES = (
    ".pre-commit-config.yaml",
    ".pre-commit-config.yml",
    "lefthook.yml",
    "lefthook.yaml",
    ".lefthook.yml",
    ".husky",
)
HOOK_TEXT = """#!/usr/bin/env bash
# Runs .omp/gate.json checks.commit. Written by omp-guardrails init.
# Enable once per clone: git config core.hooksPath .githooks
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
exec python3 - <<'PY'
import json, os, sys
argv = json.load(open(".omp/gate.json")).get("checks", {}).get("commit")
if not isinstance(argv, list) or not argv or not all(isinstance(a, str) and a for a in argv):
    sys.exit("pre-commit: .omp/gate.json checks.commit must be a nonempty argv array")
os.execvp(argv[0], argv)
PY
"""


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run git in ``repo`` without inheriting a calling hook's repository variables."""
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
        timeout=30,
    )


def finding(message: str, fix: str) -> Finding:
    return Finding(RULE, HOOK_PATH, message, fix)


def is_executable_file(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def install_hook(repo: Path, changes: list[str]) -> list[Finding]:
    """Install the hook when nothing else is wired; report what blocks it otherwise."""
    resolved = git(repo, "rev-parse", "--git-path", "hooks/pre-commit")
    if resolved.returncode:
        return [finding("the target is not a git repository", "run init inside a git checkout")]
    active = repo / resolved.stdout.strip()
    tracked = repo / HOOK_PATH
    if is_executable_file(active):
        if active.resolve() != tracked.resolve():
            changes.append(f"kept the installed pre-commit hook {resolved.stdout.strip()}")
        return []
    blocker = install_blocker(repo, tracked)
    if blocker:
        return [blocker]
    write_hook(tracked, changes)
    if git(repo, "config", "--get", "core.hooksPath").stdout.strip() != HOOKS_DIR:
        git(repo, "config", "core.hooksPath", HOOKS_DIR)
        changes.append(f"set git config core.hooksPath {HOOKS_DIR}")
    return []


def install_blocker(repo: Path, tracked: Path) -> Finding | None:
    managers = [name for name in MANAGER_FILES if (repo / name).exists()]
    if managers:
        return finding(
            f"hook manager configuration {managers} exists but no pre-commit hook is installed",
            "install the manager's hook and point it at checks.commit",
        )
    hooks_path = git(repo, "config", "--get", "core.hooksPath").stdout.strip()
    if hooks_path and hooks_path != HOOKS_DIR:
        return finding(
            f"core.hooksPath is {hooks_path!r} and holds no executable pre-commit hook",
            f"add a pre-commit hook there or set core.hooksPath to {HOOKS_DIR}",
        )
    if tracked.exists() and tracked.read_text(encoding="utf-8") != HOOK_TEXT:
        return finding(
            f"{HOOK_PATH} exists with other content",
            "make it run checks.commit and executable, then set core.hooksPath",
        )
    return None


def write_hook(tracked: Path, changes: list[str]) -> None:
    if not tracked.exists():
        tracked.parent.mkdir(parents=True, exist_ok=True)
        tracked.write_text(HOOK_TEXT, encoding="utf-8")
        tracked.chmod(0o755)
        changes.append(f"wrote {HOOK_PATH}")
    elif not os.access(tracked, os.X_OK):
        tracked.chmod(tracked.stat().st_mode | 0o755)
        changes.append(f"made {HOOK_PATH} executable")
