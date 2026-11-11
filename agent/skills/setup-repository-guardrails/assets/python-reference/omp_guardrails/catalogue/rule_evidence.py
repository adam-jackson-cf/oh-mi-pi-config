"""Rule evidence: the tracked catalogue snapshot and a fail-closed adoption ledger.

``omp-guardrails rule-evidence [--reference CATALOGUE]`` checks that:

1. the repo tracks the catalogue snapshot (default ``.omp/guardrail-rules.json``);
2. the snapshot is byte-identical to the reference catalogue. The reference is the
   ``--reference`` file, else ``omp_guardrails/data/guardrail-rules.json`` when the installed
   package ships one; with neither, only the snapshot is checked;
3. the ledger (default ``.omp/rule-coverage.json``) has exactly one row per catalogue rule, no
   unknown rows, the catalogue's evidence kind, ``status: implemented``, or ``not_applicable``
   only for a conditional rule with ``user_approval: approved``, a reason, an evidence path and
   an evidence marker.

Each ledger defect is reported under the rule ID it concerns. The skill's
``validate_rule_coverage.py`` then executes the recorded canaries; this check only decides
whether the recorded decisions are complete.

Settings (``guardrails.rule_evidence``): ``snapshot`` and ``ledger`` repo paths.
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

from omp_guardrails.findings import Finding
from omp_guardrails.structure.inventory import settings

SNAPSHOT = ".omp/guardrail-rules.json"
LEDGER = ".omp/rule-coverage.json"
SNAPSHOT_RULE = "RULES.catalogue-snapshot"
PACKAGED_CATALOGUE = "guardrail-rules.json"


def reference_bytes(argv: list[str]) -> bytes | None:
    """The reference catalogue named on the command line, else the packaged one, else none."""
    if "--reference" in argv:
        index = argv.index("--reference")
        if index + 1 >= len(argv):
            raise ValueError("--reference needs a catalogue path")
        return Path(argv[index + 1]).expanduser().read_bytes()
    packaged = resources.files("omp_guardrails.data").joinpath(PACKAGED_CATALOGUE)
    return packaged.read_bytes() if packaged.is_file() else None


def compare_catalogue(snapshot: bytes | None, reference: bytes | None, path: str) -> list[Finding]:
    if snapshot is None:
        return [
            Finding(
                SNAPSHOT_RULE,
                path,
                "catalogue snapshot is missing",
                "track a byte-for-byte copy of the skill's guardrail-rules.json",
            )
        ]
    if reference is not None and snapshot != reference:
        return [
            Finding(
                SNAPSHOT_RULE,
                path,
                "catalogue snapshot differs from the reference catalogue",
                "copy the pinned skill catalogue byte-for-byte, then update the ledger",
            )
        ]
    return []


def row_defect(rule: dict[str, Any], entries: list[dict[str, Any]]) -> str | None:
    if not entries:
        return "has no ledger row; add its evidence row"
    if len(entries) != 1:
        return "has duplicate ledger rows; keep exactly one"
    row = entries[0]
    kind = rule.get("evidence_kind", "local_ci")
    if row.get("evidence_kind", "local_ci") != kind:
        return f"ledger evidence kind differs from the catalogue; use {kind}"
    if row.get("status") == "not_applicable":
        approved = (
            rule.get("applicability") == "conditional"
            and row.get("user_approval") == "approved"
            and all(row.get(key) for key in ("reason", "evidence_path", "evidence_marker"))
        )
        return None if approved else "exclusion is unapproved or lacks conditional evidence"
    if row.get("status") != "implemented":
        return "ledger row is not implemented; implement and prove enforcement"
    return None


def ledger_findings(catalogue: dict[str, Any], ledger: dict[str, Any], path: str) -> list[Finding]:
    """Complete, unambiguous and approved adoption decisions; no I/O."""
    rows: dict[str, list[dict[str, Any]]] = {}
    for row in ledger["rules"]:
        rows.setdefault(row["id"], []).append(row)
    findings = []
    for rule in catalogue["rules"]:
        defect = row_defect(rule, rows.pop(rule["id"], []))
        if defect:
            findings.append(Finding(rule["id"], path, defect, "record approved evidence"))
    findings.extend(
        Finding(rule_id, path, "is not a catalogue rule", "remove the non-catalogue row")
        for rule_id in sorted(rows)
    )
    return findings


def run(repo: Path, profile: dict, argv: list[str]) -> list[Finding]:
    options = settings(profile, "rule_evidence")
    snapshot_path = options.get("snapshot", SNAPSHOT)
    ledger_path = options.get("ledger", LEDGER)
    snapshot_file = repo / snapshot_path
    try:
        snapshot = snapshot_file.read_bytes() if snapshot_file.is_file() else None
        findings = compare_catalogue(snapshot, reference_bytes(argv), snapshot_path)
        if findings or snapshot is None:
            return findings
        ledger = json.loads((repo / ledger_path).read_bytes())
        return ledger_findings(json.loads(snapshot), ledger, ledger_path)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        return [
            Finding(
                SNAPSHOT_RULE,
                ledger_path,
                f"evidence cannot be read: {error}",
                "repair the catalogue snapshot and ledger schema",
            )
        ]
