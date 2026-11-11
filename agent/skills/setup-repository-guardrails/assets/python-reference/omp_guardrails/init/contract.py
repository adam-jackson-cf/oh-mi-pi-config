"""Record the dynaflows contract version in ``.omp/gate.json`` ``dynaflowsContract``.

The current version has one source: the top-level ``dynaflowsContract: N`` line of
``agent/agent-rails.yml`` in the omp config this package ships in. Init reads it relative to
the package's skill folder when that file exists (a run from the skill folder). An installed
copy of the package has no such file, so the caller passes ``--dynaflows-contract N``.

Every mode writes the current version when the gate lacks the field. ``--adopt`` and
``--extend`` raise a lower recorded version, as the last step after every other write
succeeded. Init never lowers a version: a higher recorded version means the repo expects a newer
omp, which the report states.
"""

from __future__ import annotations

import re
from pathlib import Path

from omp_guardrails.init.gate_config import GATE_PATH, GateError

FIELD = "dynaflowsContract"
FLAG = "--dynaflows-contract"
# init/ -> omp_guardrails/ -> python-reference/ -> assets/ -> setup-repository-guardrails/
# -> skills/ -> agent/
SOURCE = Path(__file__).resolve().parents[6] / "agent-rails.yml"
LINE = re.compile(r"^dynaflowsContract:\s*([0-9]+)\s*(?:#.*)?$", re.MULTILINE)
RAISING_MODES = frozenset({"adopt", "extend"})


class ContractError(Exception):
    """The current contract version cannot be determined."""


def is_version(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def current(flag: int | None, source: Path | None = None) -> int:
    """``flag`` when given, else the version ``source`` (default ``SOURCE``) declares."""
    if flag is not None:
        if not is_version(flag):
            raise ContractError(f"{FLAG} must be a positive integer, got {flag}")
        return flag
    source = source or SOURCE
    try:
        text = source.read_text(encoding="utf-8")
    except OSError:
        text = None
    match = LINE.search(text) if text is not None else None
    if match is None or not is_version(int(match.group(1))):
        where = "has no top-level dynaflowsContract line" if text is not None else "is not present"
        raise ContractError(
            f"cannot find the current dynaflows contract: {source} {where}; "
            f"pass {FLAG} N with the version of the omp install the repo uses"
        )
    return int(match.group(1))


def recorded(gate: dict) -> int | None:
    if FIELD not in gate:
        return None
    value = gate[FIELD]
    if not is_version(value):
        raise GateError(f"{GATE_PATH} {FIELD} must be a positive integer, got {value!r}")
    return value


def planned(gate: dict, version: int, mode: str) -> tuple[dict, str | None]:
    """The gate with the contract applied, and a report note when it is left alone."""
    stored = recorded(gate)
    if stored is None or (stored < version and mode in RAISING_MODES):
        return {**gate, FIELD: version}, None
    if stored < version:
        return gate, (
            f"{FIELD}: kept v{stored} (current v{version}); run init --extend to raise it"
        )
    if stored > version:
        return gate, (
            f"{FIELD}: kept v{stored}; the repo expects a newer dynaflows contract than"
            f" v{version}; update omp"
        )
    return gate, None
