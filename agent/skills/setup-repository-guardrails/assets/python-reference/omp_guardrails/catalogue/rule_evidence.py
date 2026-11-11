"""Rule evidence: the repo's ledger of bespoke checkers against the packaged rule catalogue.

The rule catalogue ships in this package (``omp_guardrails/data/guardrail-rules.json``), so a
repo never tracks a copy of it: the pinned package version decides which rules apply.

A catalogue rule is **proven by the package** when it is ``kind: "tool"`` (a native tool
enforces the configuration init writes) or names a ``reference`` module (an omp-guardrails
checker enforces it). The skill's own test suite proves those once, for every repo. Every other
catalogue rule needs a **repo checker**: code the repo writes because the package does not
implement the rule. Only repo checkers have ledger rows.

``omp-guardrails rule-evidence`` checks the ledger (default ``.omp/rule-coverage.json``)
without running anything:

1. every always-applicable rule that needs a repo checker has exactly one row; a conditional
   one without a row is not adopted;
2. no row names a rule the package proves; such a row is removed, never kept;
3. a row whose ID is not a catalogue rule declares ``"local": true`` (a repo-local rule);
4. each row has the catalogue's evidence kind (``local_ci`` or ``git_hook``; a local row picks
   one), existing ``implementation_paths``, a ``local_runner`` holding ``local_marker``, for
   ``local_ci`` a ``ci_runner`` holding ``ci_marker``, for ``git_hook`` a
   ``hook_install_check`` (``command``, ``diagnostic``), and a ``canary`` whose ``command``
   uses ``{fixture}`` with existing ``passing`` and ``violating`` fixtures and a
   ``diagnostic`` marker.

A repo with no repo checkers needs no ledger file. ``omp-guardrails rule-coverage`` runs the
same check and then executes each row's canary and hook installation check
(``catalogue.rule_coverage``).

Settings (``guardrails.rule_coverage``): ``ledger`` repo path.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from omp_guardrails.findings import Finding
from omp_guardrails.structure.inventory import settings

LEDGER = ".omp/rule-coverage.json"
LEDGER_SCHEMA = 2
LEDGER_RULE = "RULES.coverage-ledger"
PACKAGED_CATALOGUE = "guardrail-rules.json"
EVIDENCE_KINDS = ("local_ci", "git_hook")
MAX_CANARY_TIMEOUT_S = 120
DEFAULT_CANARY_TIMEOUT_S = 60


class LedgerError(ValueError):
    """A ledger row cannot prove its rule."""


@dataclass(frozen=True)
class Proof:
    """One validated ledger row: what to execute to prove the repo checker works."""

    rule_id: str
    canary: dict[str, Any]
    hook_install_check: dict[str, Any] | None


def packaged_catalogue() -> dict[str, Any]:
    """The rule catalogue shipped with this package version."""
    data = resources.files("omp_guardrails.data").joinpath(PACKAGED_CATALOGUE).read_bytes()
    return json.loads(data)


def proven_by_package(rule: dict[str, Any]) -> bool:
    """A native tool or an omp-guardrails module enforces the rule; the package tests prove it."""
    return rule.get("kind") == "tool" or bool(rule.get("reference"))


def repo_checker_rules(catalogue: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Catalogue rules a repo can satisfy only with its own checker, by ID."""
    return {rule["id"]: rule for rule in catalogue["rules"] if not proven_by_package(rule)}


def target_path(repo: Path, value: object, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise LedgerError(f"{label} needs a repo-relative path")
    if Path(value).is_absolute():
        raise LedgerError(f"{label} must be repo-relative, not {value}")
    resolved = (repo / value).resolve()
    if not resolved.is_relative_to(repo.resolve()) or not resolved.is_file():
        raise LedgerError(f"{label} names a missing or outside file: {value}")
    return resolved


def required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LedgerError(f"{label} is missing")
    return value


def command_argv(value: object, label: str) -> list[str]:
    valid = isinstance(value, list) and bool(value)
    if not valid or not all(isinstance(part, str) and part.strip() for part in value):
        raise LedgerError(f"{label} must be a non-empty argv")
    return value


def check_runner(repo: Path, row: dict[str, Any], path_key: str, marker_key: str) -> None:
    path = target_path(repo, row.get(path_key), path_key)
    marker = required_text(row.get(marker_key), marker_key)
    if marker not in path.read_text(encoding="utf-8"):
        raise LedgerError(f"{path_key} does not invoke the checker ({marker_key} not found)")


def check_canary(repo: Path, value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LedgerError("canary is missing")
    command = command_argv(value.get("command"), "canary command")
    if not any("{fixture}" in part for part in command):
        raise LedgerError("canary command must use {fixture}")
    target_path(repo, value.get("passing"), "canary passing fixture")
    target_path(repo, value.get("violating"), "canary violating fixture")
    required_text(value.get("diagnostic"), "canary diagnostic")
    timeout = value.get("timeout_seconds", DEFAULT_CANARY_TIMEOUT_S)
    if type(timeout) is not int or not 1 <= timeout <= MAX_CANARY_TIMEOUT_S:
        raise LedgerError(f"canary timeout_seconds must be 1 to {MAX_CANARY_TIMEOUT_S}")
    return value


def check_hook(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LedgerError("hook_install_check is missing")
    command_argv(value.get("command"), "hook_install_check command")
    required_text(value.get("diagnostic"), "hook_install_check diagnostic")
    return value


def proof(repo: Path, row: dict[str, Any], kind: str) -> Proof:
    """Validate a repo-checker row; raise LedgerError naming its first defect."""
    if row.get("evidence_kind", "local_ci") != kind:
        raise LedgerError(f"ledger evidence kind differs from the catalogue; use {kind}")
    if kind not in EVIDENCE_KINDS:
        raise LedgerError(f"evidence kind {kind!r} is not one of {list(EVIDENCE_KINDS)}")
    paths = row.get("implementation_paths")
    if not isinstance(paths, list) or not paths:
        raise LedgerError("implementation_paths is missing")
    for item in paths:
        target_path(repo, item, "implementation_paths")
    check_runner(repo, row, "local_runner", "local_marker")
    hook = None
    if kind == "local_ci":
        check_runner(repo, row, "ci_runner", "ci_marker")
    else:
        hook = check_hook(row.get("hook_install_check"))
    return Proof(row["id"], check_canary(repo, row.get("canary")), hook)


def row_kind(row: dict[str, Any], rules: dict[str, dict[str, Any]]) -> str:
    """The evidence kind a row must prove: the catalogue's, or a local row's own choice."""
    rule = rules.get(row["id"])
    source = row if rule is None else rule
    return str(source.get("evidence_kind", "local_ci"))


def grouped_rows(ledger: object) -> dict[str, list[dict[str, Any]]]:
    rows = ledger.get("rules") if isinstance(ledger, dict) else None
    if not isinstance(ledger, dict) or ledger.get("schema_version") != LEDGER_SCHEMA:
        rows = None
    if not isinstance(rows, list):
        raise ValueError(f"ledger needs schema_version {LEDGER_SCHEMA} and a rules list")
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            raise ValueError("every ledger row needs a string id")
        grouped.setdefault(row["id"], []).append(row)
    return grouped


def row_defect(
    rule_id: str, rows: list[dict[str, Any]], rules: dict[str, dict[str, Any]]
) -> str | None:
    """Why a set of rows sharing one ID cannot stand, before their evidence is read."""
    if len(rows) != 1:
        return "has duplicate ledger rows; keep exactly one"
    rule = rules.get(rule_id)
    if rule is not None and proven_by_package(rule):
        return "is proven by the pinned omp-guardrails package; remove its ledger row"
    if rule is None and rows[0].get("local") is not True:
        return 'is not a catalogue rule; mark a repo-local rule with "local": true'
    return None


def missing_rows(catalogue: dict[str, Any], rows: dict, path: str) -> list[Finding]:
    return [
        Finding(
            rule_id,
            path,
            "needs a repo checker and has no ledger row",
            "implement the checker and record its evidence row",
        )
        for rule_id, rule in repo_checker_rules(catalogue).items()
        if rule_id not in rows and rule.get("applicability") == "always"
    ]


def ledger_findings(
    repo: Path, catalogue: dict[str, Any], ledger: object, path: str
) -> tuple[list[Finding], list[Proof]]:
    """Findings for missing or invalid rows, and the proofs of the valid ones."""
    rules = {rule["id"]: rule for rule in catalogue["rules"]}
    rows = grouped_rows(ledger)
    findings = missing_rows(catalogue, rows, path)
    proofs: list[Proof] = []
    for rule_id, entries in sorted(rows.items()):
        try:
            defect = row_defect(rule_id, entries, rules)
            if defect:
                raise LedgerError(defect)
            proofs.append(proof(repo, entries[0], row_kind(entries[0], rules)))
        except (LedgerError, OSError, UnicodeError) as error:
            findings.append(Finding(rule_id, path, str(error), "repair the ledger row"))
    return findings, proofs


def ledger_path(profile: dict) -> str:
    return str(settings(profile, "rule_coverage").get("ledger", LEDGER))


def check(repo: Path, profile: dict) -> tuple[list[Finding], list[Proof]]:
    """Validate the ledger against the packaged catalogue; an absent ledger has no rows."""
    path = ledger_path(profile)
    try:
        file = repo / path
        ledger = (
            json.loads(file.read_bytes())
            if file.is_file()
            else {"schema_version": LEDGER_SCHEMA, "rules": []}
        )
        return ledger_findings(repo, packaged_catalogue(), ledger, path)
    except (OSError, ValueError, KeyError, TypeError) as error:
        finding = Finding(
            LEDGER_RULE,
            path,
            f"evidence cannot be read: {error}",
            f"write a schema_version {LEDGER_SCHEMA} ledger of repo-checker rows",
        )
        return [finding], []


def run(repo: Path, profile: dict, argv: list[str]) -> list[Finding]:
    return check(repo, profile)[0]
