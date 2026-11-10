"""GATE.pre-commit: the installed hook runs ``checks.commit`` and its failure blocks the commit.

``omp-guardrails hook-check`` works with any hook manager or a native hook:

1. It asks git where this checkout's hook lives (``git rev-parse --git-path hooks/<hook>``,
   which honours ``core.hooksPath``) and requires an executable file there.
2. It checks the wiring lines the profile declares (``required_text``).
3. It copies the hook into a disposable repository (a tracked hooks folder keeps its relative
   ``core.hooksPath``), writes a ``.omp/gate.json`` whose ``checks.commit`` exits 0 and then 17,
   and commits each time. The first commit must succeed and the second must be blocked.
   The caller's checkout is never modified.

Settings (``guardrails.hook_installation``), all optional:

- ``hook``: hook name, default ``pre-commit``;
- ``required_text``: ``{"<repo file>": ["<substring>", ...]}``, e.g. the manager config entry;
- ``copy``: repo files the hook needs at run time (manager config, scripts it calls);
- ``scaffold``: ``{"<path>": "<text>"}`` stub files the hook's command needs in the copy;
- ``unset_env``: variable names removed from the commit's environment;
- ``timeout_s``: wall limit for each git or hook child (default 120).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from omp_guardrails.catalogue.scratch import (
    ScratchError,
    clean_environment,
    init_repository,
    inside,
    output_tail,
    run_bounded,
    stage_all,
    write_files,
)
from omp_guardrails.config.profile import PROFILE_PATH
from omp_guardrails.findings import Finding
from omp_guardrails.structure.inventory import as_list, settings

RULE = "GATE.pre-commit"
GATE_PATH = ".omp/gate.json"
BLOCKING_STATUS = 17
DEFAULT_TIMEOUT_S = 120


class HookError(Exception):
    """The installed hook is absent, miswired or does not propagate failure."""


def hook_location(repo: Path, hook: str, timeout: float) -> tuple[Path, str | None]:
    """The installed hook file, plus its repo-relative folder when it is tracked in the tree."""
    result = run_bounded(
        ["git", "-C", str(repo), "rev-parse", "--git-path", f"hooks/{hook}"],
        repo,
        timeout=timeout,
    )
    if result.returncode:
        raise HookError(f"git cannot locate the hook: {output_tail(result)}")
    path = (repo / result.stdout.strip()).resolve()
    if not path.is_file() or not os.access(path, os.X_OK):
        raise HookError(f"no executable {hook} hook is installed at {path.name}")
    root = repo.resolve()
    if path.is_relative_to(root) and ".git" not in path.relative_to(root).parts:
        return path, path.parent.relative_to(root).as_posix()
    return path, None


def wiring_problems(repo: Path, required: object) -> list[Finding]:
    findings = []
    for relative, needles in (required if isinstance(required, dict) else {}).items():
        try:
            text = (repo / relative).read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            findings.append(Finding(RULE, relative, "hook wiring file is missing", "restore it"))
            continue
        findings.extend(
            Finding(RULE, relative, f"hook wiring lacks {needle!r}", "restore the gate entry")
            for needle in as_list(needles)
            if needle not in text
        )
    return findings


def install_copy(repo: Path, scratch: Path, options: dict, hook: tuple[Path, str | None]) -> None:
    """Copy the hook, its runtime files and the profile into the disposable repository."""
    path, tracked_folder = hook
    for relative in [*as_list(options.get("copy")), PROFILE_PATH]:
        source = repo / relative
        if source.is_file():
            target = inside(scratch, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        elif relative == PROFILE_PATH:
            # The gate command only parses the profile; an empty one lets it start.
            write_files(scratch, {PROFILE_PATH: "---\n{}\n---\n"})
        else:
            raise HookError(f"hook_installation.copy names a missing file: {relative}")
    scaffold = options.get("scaffold")
    write_files(scratch, scaffold if isinstance(scaffold, dict) else {})
    if tracked_folder is None:
        target = scratch / ".git/hooks" / path.name
    else:
        target = inside(scratch, f"{tracked_folder}/{path.name}")
        configure = ["git", "-C", str(scratch), "config", "core.hooksPath", tracked_folder]
        if run_bounded(configure, scratch, timeout=30).returncode:
            raise HookError("cannot set core.hooksPath in the disposable repository")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target)


def commit_with_status(scratch: Path, status: int, env: dict, timeout: float) -> int:
    gate = {
        "schemaVersion": 2,
        "checks": {"commit": [sys.executable, "-c", f"raise SystemExit({status})"]},
    }
    (scratch / GATE_PATH).parent.mkdir(parents=True, exist_ok=True)
    (scratch / GATE_PATH).write_text(json.dumps(gate) + "\n", encoding="utf-8")
    stage_all(scratch, timeout=timeout)
    message = f"test: hook propagation canary {status}"
    result = run_bounded(
        ["git", "-C", str(scratch), "commit", "-q", "-m", message],
        scratch,
        timeout=timeout,
        env=env,
    )
    return result.returncode


def prove_propagation(repo: Path, options: dict, hook: tuple[Path, str | None]) -> None:
    timeout = options.get("timeout_s", DEFAULT_TIMEOUT_S)
    env = clean_environment(as_list(options.get("unset_env")))
    with tempfile.TemporaryDirectory(prefix="omp-guardrails-hook-") as directory:
        scratch = Path(directory).resolve()
        init_repository(scratch, timeout=timeout)
        install_copy(repo, scratch, options, hook)
        if commit_with_status(scratch, 0, env, timeout) != 0:
            raise HookError("a passing checks.commit still blocked the commit")
        if commit_with_status(scratch, BLOCKING_STATUS, env, timeout) == 0:
            raise HookError("a failing checks.commit did not block the commit")


def run(repo: Path, profile: dict, argv: list[str]) -> list[Finding]:
    del argv
    options = settings(profile, "hook_installation")
    name = options.get("hook", "pre-commit")
    timeout = options.get("timeout_s", DEFAULT_TIMEOUT_S)
    try:
        hook = hook_location(repo, name, timeout)
        findings = wiring_problems(repo, options.get("required_text"))
        if not findings:
            prove_propagation(repo, options, hook)
    except (HookError, ScratchError, OSError, subprocess.TimeoutExpired) as error:
        return [
            Finding(
                RULE,
                f".git/hooks/{name}",
                str(error),
                "install the tracked hook so it runs 'omp-guardrails gate commit' and "
                "propagates its exit status",
            )
        ]
    return findings
