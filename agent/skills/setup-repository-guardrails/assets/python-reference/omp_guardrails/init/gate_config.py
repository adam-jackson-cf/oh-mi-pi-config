"""Write ``.omp/gate.json`` in the schema agent rails accepts today (``schemaVersion: 2``).

Accepted fields (``agent/extensions/agent-rails.ts`` ``gateSchema``): ``checks.commit``,
``checks.ci``, ``checks.format``, ``checks.profile``, ``pathCheck``, ``pathCheckTimeoutMs``,
``testGlobs``, ``agentMerge``, ``ci`` (``requiredContext``: the status context or check run
that must be green on the exact head; ``runner``: a role the machine's runner config resolves)
and ``dynaflowsContract``. Init writes only ``checks.commit``, ``checks.ci``, ``checks.profile``,
``pathCheck`` and ``ci``, and only where they are absent; ``contract`` writes
``dynaflowsContract``. A gate that still declares the retired ``ciHost`` is refused.

Adopt mode keeps a repo's own commit and CI checks and runs the reference checks after them in
one argv, so both results show on every run (shadow comparison)::

    ["sh", "-c", 'status=0; "$@" || status=$?; <reference check> || status=$?; exit $status',
     "omp-guardrails-shadow", <existing argv>...]

The ``omp-guardrails-shadow`` script name marks a composed argv. A later adopt run reads which
reference checks an argv already runs and adds only the requested checks it is missing, so a
second run with the same checks leaves the gate alone.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

from omp_guardrails.catalogue.gate_runner import RETIRED_CI_HOST
from omp_guardrails.findings import Finding

GATE_PATH = ".omp/gate.json"
SCHEMA_VERSION = 2
SHADOW_NAME = "omp-guardrails-shadow"
RULE = "GATE.local-ci-parity"
DEFAULT_CONTEXT = "gate"


class GateError(Exception):
    """The existing gate file cannot be extended safely."""


def load_gate(repo: Path) -> dict | None:
    path = repo / GATE_PATH
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


def reference_argv(reference: list[str], checks: list[str]) -> list[str]:
    """One argv running each reference subcommand; every one runs, any failure fails."""
    if len(checks) == 1:
        return [*reference, checks[0]]
    return ["sh", "-c", script(reference, checks, include_existing=False), SHADOW_NAME]


def script(reference: list[str], checks: list[str], *, include_existing: bool) -> str:
    steps = ['"$@"'] if include_existing else []
    steps.extend(shlex.join([*reference, check]) for check in checks)
    runs = "; ".join(f"{step} || status=$?" for step in steps)
    return f"status=0; {runs}; exit $status"


def shadow_argv(existing: list[str], reference: list[str], checks: list[str]) -> list[str]:
    return ["sh", "-c", script(reference, checks, include_existing=True), SHADOW_NAME, *existing]


def runs_reference(argv: object, reference: list[str]) -> bool:
    if not isinstance(argv, list):
        return False
    composed = argv[:2] == ["sh", "-c"] and argv[3:4] == [SHADOW_NAME]
    return composed or argv[: len(reference)] == reference


def reference_parts(argv: list, reference: list[str]) -> tuple[list[str], list]:
    """The reference subcommands an argv already runs, and the repo's own command it wraps.

    A plain ``[*reference, check]`` runs one check and wraps nothing; a composed argv runs the
    checks named in its script and wraps ``argv[4:]``; any other argv is the repo's own command.
    """
    if argv[:2] == ["sh", "-c"] and argv[3:4] == [SHADOW_NAME] and isinstance(argv[2], str):
        steps = argv[2].split(" || status=$?")[:-1]
        found = []
        for step in steps:
            try:
                words = shlex.split(step.rsplit("; ", 1)[-1])
            except ValueError:
                continue
            if words[: len(reference)] == reference and len(words) == len(reference) + 1:
                found.append(words[-1])
        return found, list(argv[4:])
    if argv[: len(reference)] == reference and len(argv) == len(reference) + 1:
        return [argv[-1]], []
    return [], list(argv)


def adopted_argv(argv: list, reference: list[str], checks: list[str]) -> list:
    """``argv`` running every requested check: missing checks are added, nothing is removed."""
    present, existing = reference_parts(argv, reference)
    missing = [check for check in checks if check not in present]
    if not missing:
        return argv
    wanted = [*present, *missing]
    if existing:
        return shadow_argv(existing, reference, wanted)
    return reference_argv(reference, wanted)


def planned_gate(
    gate: dict | None,
    *,
    adopt: bool,
    reference: list[str],
    checks: list[str],
    ci: dict | None,
) -> dict:
    """The gate after init: absent fields filled, adopt checks composed, nothing removed."""
    planned = dict(gate) if gate else {"schemaVersion": SCHEMA_VERSION}
    declared = planned.get("checks")
    current = dict(declared) if isinstance(declared, dict) else {}
    for event in ("commit", "ci"):
        argv = current.get(event)
        if argv is None:
            current[event] = reference_argv(reference, checks)
        elif adopt and isinstance(argv, list):
            current[event] = adopted_argv(argv, reference, checks)
    current.setdefault("profile", [*reference, "profile-check"])
    planned["checks"] = current
    planned.setdefault("pathCheck", [*reference, "path-check"])
    if ci:
        planned.setdefault("ci", ci)
    return planned


def render(gate: dict) -> str:
    return json.dumps(gate, indent=2) + "\n"


def requested_ci(runner: str | None, required_context: str) -> dict | None:
    """The ``ci`` object the flags ask for; none without a runner role."""
    if not runner:
        return None
    return {"requiredContext": required_context, "runner": runner}


def ci_note(gate: dict | None, ci: dict | None) -> str | None:
    recorded = gate.get("ci") if gate else None
    if ci and recorded is not None and recorded != ci:
        return f"{GATE_PATH} keeps ci {json.dumps(recorded)}; {json.dumps(ci)} was not applied"
    return None


def gate_finding(error: GateError) -> Finding:
    return Finding(RULE, GATE_PATH, str(error), "repair the gate file, then run init again")
