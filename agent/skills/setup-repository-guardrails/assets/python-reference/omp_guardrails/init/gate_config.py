"""Write ``.omp/gate.json`` in the schema agent rails accepts today (``schemaVersion: 2``).

Accepted fields (``agent/extensions/agent-rails.ts`` ``gateSchema``): ``checks.commit``,
``checks.ci``, ``pathCheck``, ``pathCheckTimeoutMs``, ``testGlobs``, ``agentMerge`` and
``ciHost``. Init writes only ``checks``, ``pathCheck`` and ``ciHost``, and only where they are
absent. ``ciHost`` is passed through as given; agent rails validates its value.

Adopt mode keeps a repo's own commit and CI checks and runs the reference checks after them in
one argv, so both results show on every run (shadow comparison)::

    ["sh", "-c", 'status=0; "$@" || status=$?; <reference check> || status=$?; exit $status',
     "omp-guardrails-shadow", <existing argv>...]

The ``omp-guardrails-shadow`` script name marks a composed argv, so a second run leaves it alone.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

from omp_guardrails.findings import Finding

GATE_PATH = ".omp/gate.json"
SCHEMA_VERSION = 2
SHADOW_NAME = "omp-guardrails-shadow"
RULE = "GATE.local-ci-parity"


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


def planned_gate(
    gate: dict | None,
    *,
    adopt: bool,
    reference: list[str],
    checks: list[str],
    ci_host: str | None,
) -> dict:
    """The gate after init: absent fields filled, adopt checks composed, nothing removed."""
    planned = dict(gate) if gate else {"schemaVersion": SCHEMA_VERSION}
    declared = planned.get("checks")
    current = dict(declared) if isinstance(declared, dict) else {}
    for event in ("commit", "ci"):
        argv = current.get(event)
        if argv is None:
            current[event] = reference_argv(reference, checks)
        elif adopt and not runs_reference(argv, reference):
            current[event] = shadow_argv(argv, reference, checks)
    planned["checks"] = current
    planned.setdefault("pathCheck", [*reference, "path-check"])
    if ci_host:
        planned.setdefault("ciHost", ci_host)
    return planned


def render(gate: dict) -> str:
    return json.dumps(gate, indent=2) + "\n"


def ci_host_note(gate: dict | None, ci_host: str | None) -> str | None:
    recorded = gate.get("ciHost") if gate else None
    if ci_host and recorded is not None and recorded != ci_host:
        return f"{GATE_PATH} keeps ciHost {recorded!r}; --ci-host {ci_host!r} was not applied"
    return None


def gate_finding(error: GateError) -> Finding:
    return Finding(RULE, GATE_PATH, str(error), "repair the gate file, then run init again")
