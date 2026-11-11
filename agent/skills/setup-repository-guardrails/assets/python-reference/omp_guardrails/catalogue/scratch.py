"""Disposable git checkouts and bounded child processes for the catalogue checkers.

Checks that prove behaviour (hook propagation, canaries) never touch the caller's checkout:
they build a throwaway repository, and every child runs without the caller's ``GIT_*``
variables, which a hook exports and which would otherwise redirect git into the caller's index.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterable
from pathlib import Path

IDENTITY = (
    ("user.name", "Guardrail Canary"),
    ("user.email", "guardrail-canary@example.invalid"),
    ("commit.gpgsign", "false"),
)


class ScratchError(Exception):
    """A disposable checkout could not be prepared."""


def clean_environment(unset: Iterable[str] = ()) -> dict[str, str]:
    """The current environment without git context and without the named variables."""
    removed = set(unset)
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_") and key not in removed
    }


def run_bounded(
    argv: list[str],
    cwd: Path,
    *,
    timeout: float,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run one child with captured text output, a timeout and no inherited git context."""
    return subprocess.run(
        argv,
        cwd=cwd,
        env=clean_environment() if env is None else env,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def output_tail(result: subprocess.CompletedProcess[str], lines: int = 6) -> str:
    """The last lines of a child's combined output, for a one-line diagnostic."""
    text = f"{result.stdout or ''}\n{result.stderr or ''}".strip()
    return " | ".join(text.splitlines()[-lines:]) if text else "no output"


def init_repository(root: Path, *, timeout: float) -> None:
    """Create an empty repository with a fixed, unsigned commit identity."""
    steps = [["git", "init", "-q", str(root)]]
    steps += [["git", "-C", str(root), "config", key, value] for key, value in IDENTITY]
    for step in steps:
        result = run_bounded(step, root, timeout=timeout)
        if result.returncode:
            raise ScratchError(f"{' '.join(step[:4])} failed: {output_tail(result)}")


def stage_all(root: Path, *, timeout: float) -> None:
    result = run_bounded(["git", "-C", str(root), "add", "-A"], root, timeout=timeout)
    if result.returncode:
        raise ScratchError(f"git add failed: {output_tail(result)}")


def inside(root: Path, relative: str) -> Path:
    """Resolve a fixture path inside ``root``; refuse absolute paths and traversal."""
    target = (root / relative).resolve()
    if Path(relative).is_absolute() or not target.is_relative_to(root.resolve()):
        raise ScratchError(f"path {relative!r} escapes the scratch checkout")
    return target


def write_files(root: Path, files: dict[str, str]) -> None:
    for relative, text in files.items():
        target = inside(root, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
