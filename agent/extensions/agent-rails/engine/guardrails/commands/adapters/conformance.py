"""The shared conformance run: framework-neutral cases every adapter must classify as stated."""

from __future__ import annotations

import json
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from guardrails.checker.growth import CollectedFile, Snapshot, growth_findings
from guardrails.foundation import conventions
from guardrails.foundation.findings import Finding
from guardrails.test_report.junit import FAILING, Case

CASES_FILE = "conformance-cases.json"
CASE_TIMEOUT_S = 300
# What a case can raise, from its fixture, its adapter or the framework: each fails only
# its own case.
CASE_ERRORS: tuple[type[Exception], ...] = (
    OSError,
    ValueError,
    LookupError,
    TypeError,
    AttributeError,
    RuntimeError,
    ImportError,
    subprocess.SubprocessError,
)
PREREQUISITE = "GUARDRAILS.adapter-prerequisite"
# Evidence kinds that start the framework's own runner; the other kinds read source only.
RUNTIME_EVIDENCE = frozenset({"run-report", "collection"})
COMPANION_SEPARATOR = "--"
APPROVED_SKIP = "approved-static-skip"
# Cases that run another case's fixture unchanged, judged under a different config or evidence
# kind. The pytest adapter names its fixtures in data/pytest-conformance-bindings.json instead.
REUSED_FIXTURES = {
    APPROVED_SKIP: "unapproved-static-skip",
    "expected-failure-recorded": "expected-failure-marker",
}
Verdict = tuple[bool, str]


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    rule: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class Project:
    """One case's scratch folder, with the fixture placed in its project/ folder."""

    root: Path
    placed: str
    config: dict[str, Any]

    @property
    def path(self) -> Path:
        return self.root / "project"


Check = Callable[[Project, dict[str, Any]], Verdict]


@dataclass(frozen=True)
class Prerequisite:
    """Why the framework's runner cannot start here, and how to provide it."""

    problem: str
    remedy: str


@dataclass(frozen=True)
class Harness:
    """How one adapter builds a case's project and judges each evidence kind. With a
    missing prerequisite the runtime cases do not run, and the run reports it once."""

    label: str
    prefix: str
    build: Callable[[Path, dict[str, Any]], Project]
    checks: Mapping[str, Check]
    missing: Prerequisite | None = None


def load_cases() -> list[dict[str, Any]]:
    text = (conventions.DATA_FOLDER / CASES_FILE).read_text("utf-8")
    cases: list[dict[str, Any]] = json.loads(text)["cases"]
    return cases


def select_cases(case_ids: Sequence[str]) -> list[dict[str, Any]]:
    cases = load_cases()
    unknown = sorted(set(case_ids) - {case["id"] for case in cases})
    if unknown:
        raise ValueError(f"unknown conformance case(s): {', '.join(unknown)}")
    return [case for case in cases if not case_ids or case["id"] in case_ids]


def fixture_case(case_id: str) -> str:
    """The case whose fixture file this case runs."""
    return REUSED_FIXTURES.get(case_id, case_id)


def companions(case: dict[str, Any]) -> list[str]:
    """The roles of the further files a case places beside its fixture: "test" (a test
    file in the adapter's test folder) and "neighbour" (a second owned production
    module)."""
    return list(case.get("companions", []))


def companion_fixture(case: dict[str, Any], role: str) -> str:
    """The fixture name of a case's companion file in one role."""
    return f"{fixture_case(case['id'])}{COMPANION_SEPARATOR}{role}"


def place_companions(
    project: Path,
    case: dict[str, Any],
    paths: Mapping[str, str],
    source: Callable[[str], str],
) -> None:
    """Write each companion of a case at the adapter's path for its role."""
    for role in companions(case):
        target = project / paths[role]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source(companion_fixture(case, role)), encoding="utf-8")


def blocked(harness: Harness, case: dict[str, Any]) -> bool:
    return harness.missing is not None and case["evidence"] in RUNTIME_EVIDENCE


def run_case(harness: Harness, case: dict[str, Any]) -> CaseResult:
    kind = case["evidence"]
    if harness.missing is not None and blocked(harness, case):
        return CaseResult(
            case["id"], case["rule"], False, f"not run: {harness.missing.problem}"
        )
    try:
        with tempfile.TemporaryDirectory(prefix=harness.prefix) as scratch:
            project = harness.build(Path(scratch).resolve(), case)
            check = harness.checks.get(kind)
            if check is None:
                passed, detail = False, f"unsupported evidence kind: {kind}"
            else:
                passed, detail = check(project, case)
    except CASE_ERRORS as error:
        passed, detail = False, f"{type(error).__name__}: {error}"
    return CaseResult(case["id"], case["rule"], passed, detail)


def run_cases(harness: Harness, cases: list[dict[str, Any]]) -> list[CaseResult]:
    return [run_case(harness, case) for case in cases]


def findings(harness: Harness, results: list[CaseResult]) -> list[Finding]:
    """The missing prerequisite once, then each case that ran and failed."""
    runtime = {case["id"] for case in load_cases() if blocked(harness, case)}
    found = []
    if harness.missing is not None:
        found.append(
            Finding(
                PREREQUISITE,
                "conformance",
                f"{harness.missing.problem}, so the run-report and collection cases "
                "cannot start",
                harness.missing.remedy,
            )
        )
    found.extend(
        Finding(
            result.rule,
            f"conformance/{result.case_id}",
            f"{harness.label} misclassifies this case ({result.detail})",
            f"fix {harness.label} until every conformance case passes",
        )
        for result in results
        if not result.passed and result.case_id not in runtime
    )
    return found


def case_exceptions(case: dict[str, Any], placed: str) -> list[dict[str, str]]:
    """The owner-approved exception the approved-static-skip case runs under."""
    if case["id"] != APPROVED_SKIP:
        return []
    return [
        {
            "rule": "TEST.no-evasion",
            "path": placed,
            "reason": "Upstream vendor outage",
            "approvedBy": "conformance-owner",
            "approvedAt": "2026-01-01T00:00:00Z",
        }
    ]


def source_verdict(case: dict[str, Any], reported: list[Finding]) -> Verdict:
    rules = sorted({finding.rule_id for finding in reported})
    flagged = case["rule"] in rules
    return flagged == (
        case["expect"] == "finding"
    ), f"rules reported: {rules or 'none'}"


def static_verdict(case: dict[str, Any], collected: CollectedFile) -> Verdict:
    behaviours = dict(collected.behaviours)
    reported = growth_findings(Snapshot({}, {}, {}), Snapshot({}, behaviours, {}))
    flagged = any(finding.rule_id == case["rule"] for finding in reported)
    return flagged == (case["expect"] == "finding"), f"behaviours: {behaviours}"


def collection_verdict(
    case: dict[str, Any],
    reported: list[Finding],
    ids: list[str] | None,
    expected: list[str],
) -> Verdict:
    rules = sorted({finding.rule_id for finding in reported})
    detail = f"rules reported: {rules or 'none'}; ids: {ids}"
    if case["expect"] == "finding":
        return case["rule"] in rules, detail
    return not reported and ids == expected, detail


def flagged(cases: Sequence[Case], rule: str) -> bool:
    return any(item.outcome in FAILING and rule in item.reason for item in cases)


def matches_record(cases: Sequence[Case], expected: dict[str, Any]) -> bool:
    return len(cases) == 1 and all(
        getattr(cases[0], key) == value for key, value in expected.items()
    )
