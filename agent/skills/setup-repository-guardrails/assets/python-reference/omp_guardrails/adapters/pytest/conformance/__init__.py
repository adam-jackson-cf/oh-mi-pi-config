"""Run the framework-agnostic conformance cases against the pytest adapter.

``cases.json`` is the spec every adapter must classify; ``bindings.json`` and ``fixtures/`` are
this adapter's implementation of each case. Each case builds a throwaway project (an owned
package ``shop``, a ``tests/`` folder and a ``pytest.ini``), places its fixture, produces the
case's evidence through the adapter and compares the result with the case's ``expect``:

- ``source``: ``lint`` over the placed file; ``finding`` means the case's rule is reported.
- ``run-report``: a guarded pytest run of the case's tier, parsed by the shared
  ``test_evidence.junit`` parser with this adapter's dialect; ``finding`` means a failed or
  errored case names the rule; ``clean`` means every case passed and the report's ``omp.guards``
  and ``omp.tier`` show the tier's guards; an object names fields the single case must have.
- ``collection``: ``tiers`` for T1; ``finding`` means the rule is reported; an object names the
  collected IDs.

Run-report cases point ``HOME`` at the case's ``home`` folder and ``TMPDIR`` at a sibling
folder, so the user-data guard protects a throwaway home, never the real one.
``run_conformance`` returns one pass or fail result per case.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from omp_guardrails.findings import Finding

PACKAGE = "omp_guardrails.adapters.pytest.conformance"
# A hang guard for one case's pytest run, not a tuned budget: every fixture finishes in seconds.
CASE_TIMEOUT_S = 300
PYTEST_INI = "[pytest]\nmarkers =\n    fast: tier 1\n    slow: tier 2\n"
BASE_PROFILE: dict = {
    "public_entry_points": {"shop": "shop.core:Basket"},
    "tiers": {
        "t1": {"selector": "fast", "location": "tests", "command": "pytest -m fast"},
        "t2": {"selector": "slow", "location": "tests", "command": "pytest -m slow"},
        "t3": {"selector": "-m explore", "location": "tests", "command": "pytest -m explore"},
    },
    "layout": {
        "support_dir": "tests",
        "fixtures_dir": "tests",
        "test_file_patterns": ["test_*.py"],
    },
    "house_style": {"version": 1, "exceptions": []},
}
FAILED_OUTCOMES = frozenset({"failed", "error"})


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    rule: str
    passed: bool
    detail: str


def data(name: str) -> dict:
    return json.loads(resources.files(PACKAGE).joinpath(name).read_text(encoding="utf-8"))


def fixture_source(name: str) -> str:
    return resources.files(f"{PACKAGE}.fixtures").joinpath(name).read_text(encoding="utf-8")


def load_cases() -> list[dict]:
    return data("cases.json")["cases"]


def build_project(root: Path, case: dict, binding: dict) -> str:
    project = root / "project"
    (project / "shop").mkdir(parents=True)
    (project / "tests").mkdir()
    (project / "pytest.ini").write_text(PYTEST_INI, encoding="utf-8")
    (project / "shop" / "__init__.py").write_text("", encoding="utf-8")
    (project / "shop" / "core.py").write_text(fixture_source("owned_package.py"), encoding="utf-8")
    fixture = binding.get("fixture") or f"{case['id'].replace('-', '_')}.py"
    placed = "shop/settings.py" if binding.get("place") == "production" else "tests/test_case.py"
    (project / placed).write_text(fixture_source(fixture), encoding="utf-8")
    return placed


def case_profile(binding: dict) -> dict:
    return {**json.loads(json.dumps(BASE_PROFILE)), **binding.get("profile", {})}


def check_source(project: Path, profile: dict, placed: str, case: dict) -> tuple[bool, str]:
    from omp_guardrails.adapters.pytest.lint import lint

    rules = sorted({finding.rule_id for finding in lint(project, profile, [placed])})
    reported = case["rule"] in rules
    return reported == (case["expect"] == "finding"), f"rules reported: {rules or 'none'}"


def run_tier(root: Path, tier: str):
    """Run the project's tests as ``tier`` with the adapter's arguments; return the report."""
    from omp_guardrails.adapters.pytest import tier_arguments
    from omp_guardrails.adapters.pytest.dialect import DIALECT
    from omp_guardrails.adapters.pytest.tiers import package_environment
    from omp_guardrails.test_evidence.junit import ReportError, parse_report

    home, scratch, report = root / "home", root / "tmp", root / "report.xml"
    home.mkdir()
    scratch.mkdir()
    environment = package_environment()
    environment.pop("PYTEST_ADDOPTS", None)
    environment["PYTHONPATH"] = os.pathsep.join([environment["PYTHONPATH"], str(root / "project")])
    environment.update({
        "HOME": str(home),
        "TMPDIR": str(scratch),
        "OMP_CONFORMANCE_LIVE_HOME": str(home),
    })
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        *tier_arguments(tier, str(report)),
        "tests",
    ]
    result = subprocess.run(
        command,
        cwd=root / "project",
        env=environment,
        capture_output=True,
        text=True,
        timeout=CASE_TIMEOUT_S,
        check=False,
    )
    try:
        return parse_report(report, DIALECT), f"pytest exit {result.returncode}"
    except ReportError as error:
        return None, f"{error} (pytest exit {result.returncode}): {result.stdout[-400:]}"


def check_run(root: Path, binding: dict, case: dict) -> tuple[bool, str]:
    from omp_guardrails.adapters.pytest import TIER_GUARDS
    from omp_guardrails.adapters.pytest.dialect import GUARDS_PROPERTY

    tier = binding.get("tier", "t1")
    report, note = run_tier(root, tier)
    if report is None or not report.cases:
        return False, note
    cases = report.cases
    armed = set(report.properties.get(GUARDS_PROPERTY, "").split(","))
    summary = [(item.outcome, item.reason[:120]) for item in cases]
    detail = f"{note}; guards {sorted(armed - {''})}; cases: {summary}"
    expect, rule = case["expect"], case["rule"]
    if expect == "finding":
        flagged = any(item.outcome in FAILED_OUTCOMES and rule in item.reason for item in cases)
        return flagged, detail
    if expect == "clean":
        guarded = set(TIER_GUARDS[tier]) <= armed and report.properties.get("omp.tier") == tier
        clean = all(item.outcome == "passed" and rule not in item.reason for item in cases)
        return clean and guarded, detail
    only = cases[0]
    return len(cases) == 1 and all(getattr(only, k) == v for k, v in expect.items()), detail


def check_collection(project: Path, profile: dict, case: dict) -> tuple[bool, str]:
    from omp_guardrails.adapters.pytest.tiers import list_tiers

    out = project.parent / "collections"
    findings = list_tiers(project, profile, ["t1"], out)
    rules = sorted({finding.rule_id for finding in findings})
    from omp_guardrails.test_evidence.collection import read_ids

    ids = read_ids(out / "t1.txt")
    detail = f"rules reported: {rules or 'none'}; ids: {ids}"
    if case["expect"] == "finding":
        return case["rule"] in rules, detail
    return not findings and ids == case["expect"]["ids"], detail


def run_case(case: dict, binding: dict) -> CaseResult:
    with tempfile.TemporaryDirectory(prefix="omp-conformance-") as scratch:
        root = Path(scratch).resolve()
        placed = build_project(root, case, binding)
        project = root / "project"
        profile = case_profile(binding)
        if case["evidence"] == "source":
            passed, detail = check_source(project, profile, placed, case)
        elif case["evidence"] == "run-report":
            passed, detail = check_run(root, binding, case)
        elif case["evidence"] == "collection":
            passed, detail = check_collection(project, profile, case)
        else:
            passed, detail = False, f"unknown evidence kind {case['evidence']!r}"
    return CaseResult(case["id"], case["rule"], passed, detail)


def run_conformance(case_ids: list[str] | None = None) -> list[CaseResult]:
    """Pass or fail per conformance case (all cases when ``case_ids`` is empty)."""
    bindings = data("bindings.json")["cases"]
    cases = load_cases()
    known = {case["id"] for case in cases}
    unknown = sorted(set(case_ids or []) - known)
    if unknown:
        raise ValueError(f"unknown conformance case(s): {', '.join(unknown)}")
    selected = [case for case in cases if not case_ids or case["id"] in case_ids]
    return [run_case(case, bindings.get(case["id"], {})) for case in selected]


def run_cases(case_ids: list[str]) -> list[Finding]:
    """``omp-guardrails adapter conformance``: one finding per misclassified case."""
    return [
        Finding(
            result.rule,
            f"conformance/{result.case_id}",
            f"the pytest adapter misclassifies this case ({result.detail})",
            "fix the adapter until every conformance case passes",
        )
        for result in run_conformance(case_ids)
        if not result.passed
    ]
