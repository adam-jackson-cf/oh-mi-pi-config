"""Draft the record's ``gate.json`` from what the repository already runs.

The record's gate has today's gate schema (``schemaVersion`` 2): ``checks.commit``,
``checks.ci``, ``pathCheck``, ``testGlobs``, ``ci`` (``requiredContext``: the status context or
check run that must be green on the exact head; ``runner``: a role the machine's runner config
resolves), ``agentMerge`` and ``dynaflows`` (``{"enabled": bool}``).
Every command is the repository's own, run in the checkout; the harness checks
(``omp-guardrails check``) are not gate commands, because the rails run them from the harness.

Init fills only absent fields and never replaces a recorded value:

- ``checks.commit`` and ``checks.ci`` from ``--commit-command`` and ``--ci-command``, else the
  first make target in ``COMMIT_TARGETS`` or ``CI_TARGETS``; a repo with only a pre-commit
  config gets ``pre-commit run --all-files`` for the commit;
- ``pathCheck`` from a ``check-paths`` make target;
- ``ci`` from ``--required-context`` and ``--runner``, else the single required check on the
  default branch (or a CI job named ``gate``), with ``github-actions`` as the runner when a
  GitHub workflow job reports that context;
- ``dynaflows`` as ``{"enabled": false}``: the owner turns dynaflows on after confirming.

``agentMerge``, ``testGlobs`` and every approval are the owner's to write. A gate that still
declares the retired ``ciHost`` is refused.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

from omp_guardrails.findings import Finding
from omp_guardrails.init.discovery import Discovery

GATE_FILE = "gate.json"
# How findings and the report name the record's gate; it is not a repository path.
GATE_PATH = "record:gate.json"
SCHEMA_VERSION = 2
RULE = "GATE.local-ci-parity"
RETIRED_CI_HOST = "ciHost is retired; declare ci.requiredContext and ci.runner"
COMMIT_TARGETS = ("gate-commit", "pre-commit", "check")
CI_TARGETS = ("gate-ci", "ci", "check")
PATH_TARGET = "check-paths"
GITHUB_RUNNER = "github-actions"
DEFAULT_CONTEXT = "gate"


class GateError(Exception):
    """The record's gate cannot be extended safely."""


def load_gate(record: Path) -> dict | None:
    path = record / GATE_FILE
    if not path.exists():
        return None
    try:
        gate = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise GateError(f"{GATE_PATH} cannot be read: {error}") from error
    if not isinstance(gate, dict) or gate.get("schemaVersion") != SCHEMA_VERSION:
        raise GateError(f"{GATE_PATH} must use schemaVersion {SCHEMA_VERSION}")
    if "ciHost" in gate:
        raise GateError(f"{GATE_PATH}: {RETIRED_CI_HOST}")
    return gate


def make_command(targets: list[str], wanted: tuple[str, ...]) -> list[str] | None:
    chosen = next((name for name in wanted if name in targets), None)
    return ["make", chosen] if chosen else None


def drafted_checks(
    found: Discovery, commit: str | None, ci: str | None
) -> dict[str, list[str]]:
    checks: dict[str, list[str]] = {}
    commit_argv = shlex.split(commit) if commit else make_command(found.make_targets, COMMIT_TARGETS)
    if commit_argv is None and "pre-commit" in found.hook_managers:
        commit_argv = ["pre-commit", "run", "--all-files"]
    ci_argv = shlex.split(ci) if ci else make_command(found.make_targets, CI_TARGETS)
    if commit_argv:
        checks["commit"] = commit_argv
    if ci_argv:
        checks["ci"] = ci_argv
    return checks


def drafted_ci(found: Discovery, required_context: str | None, runner: str | None) -> dict | None:
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


def planned_gate(
    gate: dict | None,
    found: Discovery,
    *,
    commit: str | None,
    ci_command: str | None,
    required_context: str | None,
    runner: str | None,
) -> tuple[dict, list[str]]:
    """The gate with absent fields drafted, and the values the owner still has to confirm."""
    planned = dict(gate) if gate else {"schemaVersion": SCHEMA_VERSION}
    declared = planned.get("checks")
    checks = dict(declared) if isinstance(declared, dict) else {}
    for event, argv in drafted_checks(found, commit, ci_command).items():
        checks.setdefault(event, argv)
    planned["checks"] = checks
    if PATH_TARGET in found.make_targets:
        planned.setdefault("pathCheck", ["make", PATH_TARGET])
    ci = drafted_ci(found, required_context, runner)
    if ci:
        planned.setdefault("ci", ci)
    planned.setdefault("dynaflows", {"enabled": False})
    return planned, confirmations(planned)


def confirmations(gate: dict) -> list[str]:
    lines = []
    checks = gate.get("checks") if isinstance(gate.get("checks"), dict) else {}
    for event, flag in (("commit", "--commit-command"), ("ci", "--ci-command")):
        if event not in checks:
            lines.append(f"confirm: no checks.{event} found; pass {flag} or edit {GATE_PATH}")
    if "ci" not in gate:
        lines.append(
            f"confirm: no ci found; pass --required-context and --runner or edit {GATE_PATH}"
        )
    for key in ("testGlobs", "agentMerge"):
        if key not in gate:
            lines.append(f"confirm: {key} is the owner's to set in {GATE_PATH}")
    if gate.get("dynaflows") == {"enabled": False}:
        lines.append(f"confirm: dynaflows stays off until the owner sets it on in {GATE_PATH}")
    return lines


def render(gate: dict) -> str:
    return json.dumps(gate, indent=2) + "\n"


def gate_finding(error: GateError) -> Finding:
    return Finding(RULE, GATE_PATH, str(error), "repair the record's gate.json, then run init again")
