from __future__ import annotations

import importlib.util
import json
import os
import sys
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Any

from guardrails.commands.adapters import conformance, contract
from guardrails.commands.adapters.pytest.dialect import NAME
from guardrails.foundation.findings import Finding
from guardrails.host.process import run_text
from guardrails.toolkit.test_report.junit import TIER_PROPERTY

if TYPE_CHECKING:
    from guardrails.toolkit.test_report.junit import Report

PACKAGE = "guardrails.commands.adapters.pytest.conformance"
PYTEST_INI = "[pytest]\nmarkers =\n    fast: tier 1\n    slow: tier 2\n"
BASE_CONFIG: dict[str, Any] = {
    "tests": {
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
    },
    "houseStyle": {"version": 1},
    "checkers": {},
    "exceptions": [],
}
PREREQUISITE = "GUARDRAILS.adapter-prerequisite"


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


def case_config(binding: dict[str, Any]) -> dict[str, Any]:
    return {**json.loads(json.dumps(BASE_CONFIG)), **binding.get("config", {})}


def check_source(
    project: conformance.Project, case: dict[str, Any]
) -> conformance.Verdict:
    from guardrails.commands.adapters.pytest.lint import lint

    reported = lint(project.path, project.config, [project.placed])
    return conformance.source_verdict(case, reported)


def check_static_cases(
    project: conformance.Project, case: dict[str, Any]
) -> conformance.Verdict:
    from guardrails.commands.adapters.pytest.cases import collect

    text = (project.path / project.placed).read_text(encoding="utf-8")
    return conformance.static_verdict(case, collect(project.placed, text))


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
        "GUARDRAILS_CONFORMANCE_LIVE_HOME": str(home),
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
        timeout=conformance.CASE_TIMEOUT_S,
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
    project: conformance.Project, case: dict[str, Any]
) -> conformance.Verdict:
    from guardrails.toolkit.test_report.junit import GUARDS_PROPERTY

    tier = binding(case).get("tier", "t1")
    report, note = run_tier(project.root, tier)
    if report is None or not report.cases:
        return False, note
    cases = report.cases
    armed = set(report.properties.get(GUARDS_PROPERTY, "").split(","))
    summary = [(item.outcome, item.reason[:120]) for item in cases]
    detail = f"{note}; guards {sorted(armed - {''})}; cases: {summary}"
    expect, rule = case["expect"], case["rule"]
    if expect == "finding":
        return conformance.flagged(cases, rule), detail
    if expect == "clean":
        guarded = (
            set(contract.TIER_GUARDS[tier]) <= armed
            and report.properties.get(TIER_PROPERTY) == tier
        )
        clean = all(
            item.outcome == "passed" and rule not in item.reason for item in cases
        )
        return clean and guarded, detail
    return conformance.matches_record(cases, expect), detail


def check_collection(
    project: conformance.Project, case: dict[str, Any]
) -> conformance.Verdict:
    from guardrails.commands.adapters.pytest.tiers import list_tiers
    from guardrails.toolkit.test_report.collection import read_ids

    out = project.root / "collections"
    reported = list_tiers(project.path, project.config, ["t1"], out)
    ids = read_ids(out / "t1.txt")
    expected = [] if case["expect"] == "finding" else case["expect"]["ids"]
    return conformance.collection_verdict(case, reported, ids, expected)


def binding(case: dict[str, Any]) -> dict[str, Any]:
    found: dict[str, Any] = data("bindings.json")["cases"].get(case["id"], {})
    return found


def build_case(root: Path, case: dict[str, Any]) -> conformance.Project:
    bound = binding(case)
    return conformance.Project(
        root, build_project(root, case, bound), case_config(bound)
    )


HARNESS = conformance.Harness(
    label="the pytest adapter",
    prefix="guardrails-conformance-",
    build=build_case,
    checks={
        "source": check_source,
        "static-cases": check_static_cases,
        "run-report": check_run,
        "collection": check_collection,
    },
    errors=(),
)


def run_conformance(case_ids: list[str]) -> list[conformance.CaseResult]:
    return conformance.run_cases(HARNESS, conformance.select_cases(case_ids))


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
    return conformance.findings(run_conformance(case_ids), HARNESS.label)
