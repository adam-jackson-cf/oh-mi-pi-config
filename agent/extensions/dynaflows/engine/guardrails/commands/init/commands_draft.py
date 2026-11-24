"""Draft the config's commands section from what the repo already runs."""

from __future__ import annotations

import shlex
from typing import Any

from guardrails.commands.init.discovery import Discovery

COMMIT_TARGETS = ("gate-commit", "pre-commit", "check")
CI_TARGETS = ("gate-ci", "ci", "check")


def make_command(targets: list[str], wanted: tuple[str, ...]) -> list[str] | None:
    chosen = next((name for name in wanted if name in targets), None)
    return ["make", chosen] if chosen else None


def drafted_commands(
    found: Discovery, commit: str | None, ci: str | None
) -> dict[str, list[str]]:
    commands: dict[str, list[str]] = {}
    commit_argv = (
        shlex.split(commit)
        if commit
        else make_command(found.make_targets, COMMIT_TARGETS)
    )
    if commit_argv is None and "pre-commit" in found.hook_managers:
        commit_argv = ["pre-commit", "run", "--all-files"]
    ci_argv = shlex.split(ci) if ci else make_command(found.make_targets, CI_TARGETS)
    if commit_argv:
        commands["commit"] = commit_argv
    if ci_argv:
        commands["ci"] = ci_argv
    return commands


def planned_commands(
    existing: object, found: Discovery, commit: str | None, ci: str | None
) -> tuple[dict[str, Any], list[str]]:
    """Existing commands are kept; discovery and the flags fill the absent ones."""
    planned = dict(existing) if isinstance(existing, dict) else {}
    for event, argv in drafted_commands(found, commit, ci).items():
        planned.setdefault(event, argv)
    lines = []
    if "commit" not in planned:
        lines.append(
            "required: no commands.commit found; pass --commit-command or fill the draft"
        )
    if "ci" not in planned:
        lines.append(
            "confirm: no commands.ci found; pass --ci-command or fill the draft"
        )
    return planned, lines
