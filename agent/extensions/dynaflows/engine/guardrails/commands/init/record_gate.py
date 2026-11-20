from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from guardrails.commands.init.discovery import Discovery
from guardrails.foundation.findings import Finding

GATE_FILE = "gate.json"
GATE_PATH = "record:gate.json"
SCHEMA_VERSION = 2
RULE = "GATE.local-ci-parity"
GATE_FIELDS = frozenset({
    "schemaVersion",
    "checks",
    "agentMerge",
    "ci",
    "dynaflows",
})
CHECK_FIELDS = frozenset({"commit", "ci", "format", "profile"})
COMMIT_TARGETS = ("gate-commit", "pre-commit", "check")
CI_TARGETS = ("gate-ci", "ci", "check")
GITHUB_RUNNER = "github-actions"
DEFAULT_CONTEXT = "gate"


class GateError(Exception):
    pass


def load_gate(record: Path) -> dict[str, Any] | None:
    path = record / GATE_FILE
    if not path.exists():
        return None
    try:
        gate = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise GateError(f"{GATE_PATH} cannot be read: {error}") from error
    if not isinstance(gate, dict) or gate.get("schemaVersion") != SCHEMA_VERSION:
        raise GateError(f"{GATE_PATH} must use schemaVersion {SCHEMA_VERSION}")
    checks = gate.get("checks", {})
    unknown = sorted(set(gate) - GATE_FIELDS)
    if isinstance(checks, dict):
        unknown += sorted(f"checks.{key}" for key in set(checks) - CHECK_FIELDS)
    if unknown:
        raise GateError(
            f"{GATE_PATH}: unknown field {', '.join(unknown)} must be removed"
        )
    return gate


def make_command(targets: list[str], wanted: tuple[str, ...]) -> list[str] | None:
    chosen = next((name for name in wanted if name in targets), None)
    return ["make", chosen] if chosen else None


def drafted_checks(
    found: Discovery, commit: str | None, ci: str | None
) -> dict[str, list[str]]:
    checks: dict[str, list[str]] = {}
    commit_argv = (
        shlex.split(commit)
        if commit
        else make_command(found.make_targets, COMMIT_TARGETS)
    )
    if commit_argv is None and "pre-commit" in found.hook_managers:
        commit_argv = ["pre-commit", "run", "--all-files"]
    ci_argv = shlex.split(ci) if ci else make_command(found.make_targets, CI_TARGETS)
    if commit_argv:
        checks["commit"] = commit_argv
    if ci_argv:
        checks["ci"] = ci_argv
    return checks


def drafted_ci(
    found: Discovery, required_context: str | None, runner: str | None
) -> dict[str, Any] | None:
    context = required_context
    if context is None and found.required_checks and len(found.required_checks) == 1:
        context = found.required_checks[0]
    names = {name for job in found.ci_jobs for name in (job.job_id, job.name)}
    if context is None and DEFAULT_CONTEXT in names:
        context = DEFAULT_CONTEXT
    if runner is None and context is not None and context in names:
        runner = GITHUB_RUNNER
    if not context or not runner:
        return None
    return {"requiredContext": context, "runner": runner}


@dataclass(frozen=True)
class GateAnswers:
    commit: str | None
    ci_command: str | None
    required_context: str | None
    runner: str | None
    dynaflows: bool = False


def planned_gate(
    gate: dict[str, Any] | None, found: Discovery, answers: GateAnswers
) -> tuple[dict[str, Any], list[str]]:
    planned: dict[str, Any] = dict(gate) if gate else {"schemaVersion": SCHEMA_VERSION}
    declared = planned.get("checks")
    checks = dict(declared) if isinstance(declared, dict) else {}
    for event, argv in drafted_checks(
        found, answers.commit, answers.ci_command
    ).items():
        checks.setdefault(event, argv)
    planned["checks"] = checks
    ci = drafted_ci(found, answers.required_context, answers.runner)
    if ci:
        planned.setdefault("ci", ci)
    planned.setdefault("dynaflows", {"enabled": answers.dynaflows})
    return planned, confirmations(planned)


def confirmations(gate: dict[str, Any]) -> list[str]:
    lines = []
    checks: dict[str, Any] = (
        gate["checks"] if isinstance(gate.get("checks"), dict) else {}
    )
    if "commit" not in checks:
        lines.append(
            f"required: no checks.commit found; pass --commit-command or edit {GATE_PATH}"
        )
    if "ci" not in checks:
        lines.append(
            f"confirm: no checks.ci found; pass --ci-command or edit {GATE_PATH}"
        )
    if "ci" not in gate:
        lines.append(
            f"confirm: no ci found; pass --required-context and --runner or edit {GATE_PATH}"
        )
    if "agentMerge" not in gate:
        lines.append(f"confirm: agentMerge is the owner's to set in {GATE_PATH}")
    if gate.get("dynaflows") == {"enabled": False}:
        lines.append(
            f"confirm: dynaflows is off in {GATE_PATH}; it turns on when the owner asks for it in a "
            "session (for example `enable $dynaflows`)"
        )
    return lines


def render(gate: dict[str, Any]) -> str:
    return json.dumps(gate, indent=2) + "\n"


def gate_finding(error: GateError) -> Finding:
    return Finding(
        RULE,
        GATE_PATH,
        str(error),
        "repair the record's gate.json, then run init again",
    )
