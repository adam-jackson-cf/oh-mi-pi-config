"""Record the dynaflows contract version in the record's ``gate.json`` ``dynaflowsContract``.

The current version has one source: the top-level ``dynaflowsContract: N`` line of the harness's
``agent/agent-rails.yml`` (``omp_guardrails.harness.RAILS_CONFIG``). Workflows and rails refuse
a record below it.

Init writes the current version when the record lacks it and raises a lower one, as the last
step after every other write succeeded: init has just redrafted the record in the current
format. Init never lowers a version; a higher one means the record was drafted by a newer
harness, which the report states.
"""

from __future__ import annotations

import re
from pathlib import Path

from omp_guardrails import harness
from omp_guardrails.init.record_gate import GATE_PATH, GateError

FIELD = "dynaflowsContract"
LINE = re.compile(r"^dynaflowsContract:\s*([0-9]+)\s*(?:#.*)?$", re.MULTILINE)


class ContractError(Exception):
    """The current contract version cannot be determined."""


def is_version(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def current(source: Path | None = None) -> int:
    """The version ``source`` (default: the harness's agent-rails.yml) declares."""
    source = source or harness.RAILS_CONFIG
    try:
        text = source.read_text(encoding="utf-8")
    except OSError:
        text = None
    match = LINE.search(text) if text is not None else None
    if match is None or not is_version(int(match.group(1))):
        where = "has no top-level dynaflowsContract line" if text is not None else "is not present"
        raise ContractError(
            f"cannot find the current dynaflows contract: {source} {where}; run omp-guardrails "
            "from its skill folder in the harness"
        )
    return int(match.group(1))


def recorded(gate: dict) -> int | None:
    if FIELD not in gate:
        return None
    value = gate[FIELD]
    if not is_version(value):
        raise GateError(f"{GATE_PATH} {FIELD} must be a positive integer, got {value!r}")
    return value


def planned(gate: dict, version: int) -> tuple[dict, str | None]:
    """The gate with the contract applied, and a report note when it is left alone."""
    stored = recorded(gate)
    if stored is None or stored < version:
        return {**gate, FIELD: version}, None
    if stored > version:
        return gate, (
            f"{FIELD}: kept v{stored}; the record expects a newer dynaflows contract than"
            f" v{version}; update the harness"
        )
    return gate, None
