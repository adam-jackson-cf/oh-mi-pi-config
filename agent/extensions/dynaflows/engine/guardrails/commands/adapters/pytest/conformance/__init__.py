from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Any

from guardrails.commands.adapters.pytest.dialect import NAME
from guardrails.foundation.findings import Finding
from guardrails.host.process import run_text

if TYPE_CHECKING:
    from guardrails.toolkit.test_report.junit import Report

PACKAGE = "guardrails.commands.adapters.pytest.conformance"
CASE_TIMEOUT_S = 300
PYTEST_INI = "[pytest]\nmarkers =\n    fast: tier 1\n    slow: tier 2\n"
BASE_PROFILE: dict[str, Any] = {
    "public_entry_points": {"shop": "shop.core:Basket"},
    "adapters": [
        {
            "name": NAME,
            "tiers": {
                "t1": {"selector": "fast", "location": "tests"},
                "t2": {"selector": "slow", "location": "tests"},
                "t3": {"selector": "-m explore", "location": "tests"},
            },
        }
    ],
    "tiers": {
        "t1": {"command": "pytest -m fast"},
        "t2": {"command": "pytest -m slow"},
        "t3": {"command": "pytest -m explore"},
    },
    "layout": {
        "support_dir": "tests",
        "fixtures_dir": "tests",
        "test_file_patterns": ["test_*.py"],
    },
    "house_style": {"version": 1},
    "guardrails": {"exceptions": []},
}
FAILED_OUTCOMES = frozenset({"failed", "error"})
PREREQUISITE = "GUARDRAILS.adapter-prerequisite"


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    rule: str
    passed: bool
    detail: str


def data(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(
        resources.files(PACKAGE).joinpath(name).read_text(encoding="utf-8")
    )
    return loaded


def fixture_source(name: str) -> str:
    return (
        resources
        .files(f"{PACKAGE}.fixtures")
        .joinpath(name)
        .read_text(encoding="utf-8")
    )


def load_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = data("cases.json")["cases"]
    return cases


def build_project(root: Path, case: dict[str, Any], binding: dict[str, Any]) -> str:
    project = root / "project"
    (project / "shop").mkdir(parents=True)
    (project / "tests").mkdir()
    (project / "pytest.ini").write_text(PYTEST_INI, encoding="utf-8")
    (project / "shop" / "__init__.py").write_text("", encoding="utf-8")
    (project / "shop" / "core.py").write_text(
        fixture_source("owned_package.py"), encoding="utf-8"
    )
    fixture = binding.get("fixture") or f"{case['id'].replace('-', '_')}.py"
    placed = (
        "shop/settings.py"
        if binding.get("place") == "production"
        else "tests/test_case.py"
    )
    (project / placed).write_text(fixture_source(fixture), encoding="utf-8")
    return placed


def case_profile(binding: dict[str, Any]) -> dict[str, Any]:
    return {**json.loads(json.dumps(BASE_PROFILE)), **binding.get("profile", {})}


def check_source(
    project: Path, profile: dict[str, Any], placed: str, case: dict[str, Any]
) -> tuple[bool, str]:
    from guardrails.commands.adapters.pytest.lint import lint

    rules = sorted({finding.rule_id for finding in lint(project, profile, [placed])})
    reported = case["rule"] in rules
    return reported == (
        case["expect"] == "finding"
    ), f"rules reported: {rules or 'none'}"


def check_static_cases(
    project: Path, placed: str, case: dict[str, Any]
) -> tuple[bool, str]:
    from guardrails.commands.adapters.pytest.cases import collect
    from guardrails.toolkit.growth import Snapshot, growth_findings

    collected = collect(placed, (project / placed).read_text(encoding="utf-8"))
    head = Snapshot({}, dict(collected.behaviours), {})
    findings = growth_findings(Snapshot({}, {}, {}), head)
    reported = any(finding.rule_id == case["rule"] for finding in findings)
    detail = f"behaviours: {dict(collected.behaviours)}"
    return reported == (case["expect"] == "finding"), detail


def run_tier(root: Path, tier: str) -> tuple[Report | None, str]:
    from guardrails.commands.adapters.pytest.dialect import DIALECT
    from guardrails.commands.adapters.pytest.invocation import tier_arguments
    from guardrails.commands.adapters.pytest.tiers import package_environment
    from guardrails.toolkit.test_report.junit import ReportError, parse_report

    home, scratch, report = root / "home", root / "tmp", root / "report.xml"
    home.mkdir()
    scratch.mkdir()
    framework_prefix = NAME.upper() + "_"
    environment = {
        key: value
        for key, value in package_environment().items()
        if not key.startswith(framework_prefix)
    }
    environment["PYTHONPATH"] = os.pathsep.join([
        environment["PYTHONPATH"],
        str(root / "project"),
    ])
    environment.update({
        "HOME": str(home),
        "TMPDIR": str(scratch),
        "OMP_CONFORMANCE_LIVE_HOME": str(home),
    })
    command = [
        sys.executable,
        "-m",
        NAME,
        "-q",
        "-p",
        "no:cacheprovider",
        *tier_arguments(tier, str(report)),
        "tests",
    ]
    result = run_text(
        command,
        cwd=root / "project",
        env=environment,
        timeout=CASE_TIMEOUT_S,
        check=False,
    )
    try:
        return parse_report(report, DIALECT), f"pytest exit {result.returncode}"
    except ReportError as error:
        return (
            None,
            f"{error} (pytest exit {result.returncode}): {result.stdout[-400:]}",
        )


def check_run(
    root: Path, binding: dict[str, Any], case: dict[str, Any]
) -> tuple[bool, str]:
    from guardrails.commands.adapters.pytest.dialect import GUARDS_PROPERTY
    from guardrails.commands.adapters.pytest.invocation import TIER_GUARDS

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
        flagged = any(
            item.outcome in FAILED_OUTCOMES and rule in item.reason for item in cases
        )
        return flagged, detail
    if expect == "clean":
        guarded = (
            set(TIER_GUARDS[tier]) <= armed
            and report.properties.get("omp.tier") == tier
        )
        clean = all(
            item.outcome == "passed" and rule not in item.reason for item in cases
        )
        return clean and guarded, detail
    only = cases[0]
    return len(cases) == 1 and all(
        getattr(only, k) == v for k, v in expect.items()
    ), detail


def check_collection(
    project: Path, profile: dict[str, Any], case: dict[str, Any]
) -> tuple[bool, str]:
    from guardrails.commands.adapters.pytest.tiers import list_tiers

    out = project.parent / "collections"
    findings = list_tiers(project, profile, ["t1"], out)
    rules = sorted({finding.rule_id for finding in findings})
    from guardrails.toolkit.test_report.collection import read_ids

    ids = read_ids(out / "t1.txt")
    detail = f"rules reported: {rules or 'none'}; ids: {ids}"
    if case["expect"] == "finding":
        return case["rule"] in rules, detail
    return not findings and ids == case["expect"]["ids"], detail


def run_case(case: dict[str, Any], binding: dict[str, Any]) -> CaseResult:
    with tempfile.TemporaryDirectory(prefix="omp-conformance-") as scratch:
        root = Path(scratch).resolve()
        placed = build_project(root, case, binding)
        project = root / "project"
        profile = case_profile(binding)
        if case["evidence"] == "source":
            passed, detail = check_source(project, profile, placed, case)
        elif case["evidence"] == "static-cases":
            passed, detail = check_static_cases(project, placed, case)
        elif case["evidence"] == "run-report":
            passed, detail = check_run(root, binding, case)
        elif case["evidence"] == "collection":
            passed, detail = check_collection(project, profile, case)
        else:
            passed, detail = False, f"unknown evidence kind {case['evidence']!r}"
    return CaseResult(case["id"], case["rule"], passed, detail)


def run_conformance(case_ids: list[str] | None = None) -> list[CaseResult]:
    bindings = data("bindings.json")["cases"]
    cases = load_cases()
    known = {case["id"] for case in cases}
    unknown = sorted(set(case_ids or []) - known)
    if unknown:
        raise ValueError(f"unknown conformance case(s): {', '.join(unknown)}")
    selected = [case for case in cases if not case_ids or case["id"] in case_ids]
    return [run_case(case, bindings.get(case["id"], {})) for case in selected]


def run_cases(case_ids: list[str]) -> list[Finding]:
    if importlib.util.find_spec(NAME) is None:
        return [
            Finding(
                PREREQUISITE,
                "conformance",
                f"the pytest extra is not installed for {sys.executable}, "
                "so the run-report cases cannot start",
                "run guardrails from an environment that installs guardrails[pytest]",
            )
        ]
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
