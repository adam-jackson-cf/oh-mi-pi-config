from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

from guardrails.commands.adapters import conformance
from guardrails.foundation.findings import Finding
from guardrails.host.process import run_bytes, run_text

FIXTURES = Path(__file__).with_name("fixtures")
COMPANIONS = {"test": "tests/case.test.ts", "neighbour": "shop/neighbour.ts"}
RUNNER_REMEDY = "install Bun, or set checkers.adapter_bun_test.command to its binary"
BASE_CONFIG: dict[str, Any] = {
    "tests": {
        "adapters": [
            {
                "name": "bun_test",
                "tiers": {
                    "t1": {
                        "location": "tests",
                        "selector": "--test-name-pattern test_fast_total",
                    }
                },
            }
        ],
        "public_entry_points": {"shop": "shop/core.ts:publicApi"},
        "tiers": {"t1": {"command": "bun test"}},
        "layout": {
            "test_file_patterns": ["*.test.ts"],
            "support_dir": "tests",
            "fixtures_dir": "tests",
        },
    },
    "houseStyle": {"version": 1},
    "checkers": {},
    "exceptions": [],
}


def fixture_source(case_id: str) -> str:
    return (FIXTURES / f"{case_id}.ts.txt").read_text(encoding="utf-8")


def build_project(root: Path, case: dict[str, Any]) -> conformance.Project:
    project = root / "project"
    (project / "shop").mkdir(parents=True)
    (project / "tests").mkdir()
    run_bytes(["git", "init", "-q", str(project)], check=True, timeout=30)
    (project / "package.json").write_text('{"type":"module"}\n', encoding="utf-8")
    (project / "shop/core.ts").write_text(
        fixture_source("owned-package"), encoding="utf-8"
    )
    production = case["id"].startswith("production-")
    placed = "shop/settings.ts" if production else "tests/case.test.ts"
    (project / placed).write_text(
        fixture_source(conformance.fixture_case(case["id"])), encoding="utf-8"
    )
    conformance.place_companions(project, case, COMPANIONS, fixture_source)
    config = copy.deepcopy(BASE_CONFIG)
    config["exceptions"] = conformance.case_exceptions(case, placed)
    return conformance.Project(root, placed, config)


def check_source(
    project: conformance.Project, case: dict[str, Any]
) -> conformance.Verdict:
    from guardrails.commands.adapters.bun_test.lint import lint

    reported = lint(project.path, project.config, [project.placed])
    return conformance.source_verdict(case, reported)


def check_static_cases(
    project: conformance.Project, case: dict[str, Any]
) -> conformance.Verdict:
    from guardrails.commands.adapters.javascript.cases import collect

    text = (project.path / project.placed).read_text(encoding="utf-8")
    return conformance.static_verdict(case, collect(project.placed, text))


def check_run(
    project: conformance.Project, case: dict[str, Any], command: list[str]
) -> conformance.Verdict:
    from guardrails.commands.adapters.bun_test.dialect import (
        DIALECT,
        guard_evidence,
        junit_arguments,
    )
    from guardrails.test_report.junit import ReportError, parse_report

    tier = "t2" if case["id"].startswith("user-data-") else "t1"
    report = project.root / "evidence" / "report.xml"
    home = project.root / "live-home"
    home.mkdir()
    environment = {
        **os.environ,
        "HOME": str(home),
        "USERPROFILE": str(home),
        "GUARDRAILS_CONFORMANCE_LIVE_HOME": str(home),
    }
    result = run_text(
        [*command, "test", *junit_arguments(tier, str(report)), "tests/case.test.ts"],
        cwd=project.path,
        env=environment,
        timeout=conformance.CASE_TIMEOUT_S,
        check=False,
    )
    try:
        parsed = parse_report(report, DIALECT)
    except ReportError as error:
        return (
            False,
            f"{error}; exit {result.returncode}: {(result.stdout + result.stderr)[-1200:]}",
        )
    detail = (
        f"bun exit {result.returncode}; cases: "
        f"{[(item.outcome, item.reason) for item in parsed.cases]}"
    )
    if guard_evidence(report, tier):
        return False, f"guard evidence missing; {detail}"
    expected = case["expect"]
    if expected == "finding":
        return conformance.flagged(
            parsed.cases, case["rule"]
        ) and result.returncode != 0, detail
    if expected == "clean":
        return bool(parsed.cases) and result.returncode == 0 and all(
            item.outcome == "passed" for item in parsed.cases
        ), detail
    return conformance.matches_record(parsed.cases, expected), detail


def check_collection(
    project: conformance.Project, case: dict[str, Any], command: list[str]
) -> conformance.Verdict:
    from guardrails.commands.adapters.bun_test.tiers import list_tiers
    from guardrails.test_report.collect_list import read_ids

    project.config["checkers"] = {"adapter_bun_test": {"command": command}}
    out = project.root / "collections"
    reported = list_tiers(project.path, project.config, ["t1"], out)
    ids = read_ids(out / "t1.txt")
    expected = [
        item.replace("tests/test_case.py", "tests/case.test.ts")
        for item in ([] if case["expect"] == "finding" else case["expect"]["ids"])
    ]
    return conformance.collection_verdict(case, reported, ids, expected)


def harness(config: dict[str, Any]) -> conformance.Harness:
    from guardrails.commands.adapters.bun_test.tiers import command as runner_command

    try:
        command, missing = runner_command(config), None
    except ValueError as error:
        command, missing = [], conformance.Prerequisite(str(error), RUNNER_REMEDY)
    return conformance.Harness(
        label="the Bun adapter",
        prefix="guardrails-bun-conformance-",
        build=build_project,
        checks={
            "source": check_source,
            "static-cases": check_static_cases,
            "run-report": lambda project, case: check_run(project, case, command),
            "collection": lambda project, case: check_collection(
                project, case, command
            ),
        },
        missing=missing,
    )


def run_conformance(
    case_ids: list[str] | None = None, *, config: dict[str, Any] | None = None
) -> list[conformance.CaseResult]:
    selected = conformance.select_cases(case_ids or [])
    return conformance.run_cases(harness(config or {}), selected)


def run_cases(
    case_ids: list[str], *, config: dict[str, Any] | None = None
) -> list[Finding]:
    chosen = harness(config or {})
    results = conformance.run_cases(chosen, conformance.select_cases(case_ids))
    return conformance.findings(chosen, results)
