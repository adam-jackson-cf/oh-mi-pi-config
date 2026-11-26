from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

from guardrails.commands.adapters import conformance
from guardrails.commands.adapters.vitest.tiers import command
from guardrails.foundation.findings import Finding
from guardrails.host.process import run_bytes, run_text

FIXTURES = Path(__file__).with_name("fixtures")
COMPANIONS = {"test": "tests/case.test.ts", "neighbour": "shop/neighbour.ts"}
INSTALL_REMEDY = "install the target's pinned dependencies, including vitest, then run conformance again"
BASE_CONFIG: dict[str, Any] = {
    "tests": {
        "adapters": [
            {
                "name": "vitest",
                "tiers": {
                    "t1": {
                        "location": "tests",
                        "selector": "--testNamePattern test_fast_total",
                    }
                },
            }
        ],
        "public_entry_points": {"shop": "shop/core.ts:publicApi"},
        "tiers": {"t1": {"command": "vitest run"}},
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


def native_installation(repo: Path, config: dict[str, Any]) -> tuple[list[str], Path]:
    root = repo.resolve()
    modules = next(
        (
            path / "node_modules"
            for path in [root, *root.parents]
            if (path / "node_modules" / "vitest" / "vitest.mjs").is_file()
        ),
        None,
    )
    if modules is None:
        raise ValueError(
            "Vitest is not installed locally; install the target's pinned dependencies before conformance"
        )
    return command(modules.parent, config), modules


def build_project(
    root: Path, case: dict[str, Any], modules: Path | None
) -> conformance.Project:
    project = root / "project"
    (project / "shop").mkdir(parents=True)
    (project / "tests").mkdir()
    run_bytes(["git", "init", "-q", str(project)], check=True, timeout=30)
    (project / "package.json").write_text('{"type":"module"}\n', encoding="utf-8")
    if modules is not None:
        (project / "node_modules").symlink_to(modules, target_is_directory=True)
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
    from guardrails.commands.adapters.vitest.lint import lint

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
    from guardrails.commands.adapters.vitest.dialect import (
        DIALECT,
        guard_evidence,
        junit_arguments,
    )
    from guardrails.test_report.junit import ReportError, parse_report

    tier = "t2" if case["id"].startswith("user-data-") else "t1"
    report = project.root / "report.xml"
    home = project.root / "live-home"
    home.mkdir()
    environment = {
        **os.environ,
        "HOME": str(home),
        "USERPROFILE": str(home),
        "GUARDRAILS_CONFORMANCE_LIVE_HOME": str(home),
    }
    result = run_text(
        [*command, "run", *junit_arguments(tier, str(report)), "tests/case.test.ts"],
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
    detail = f"Vitest exit {result.returncode}; cases: {[(item.outcome, item.reason) for item in parsed.cases]}"
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
    from guardrails.commands.adapters.vitest.tiers import list_tiers
    from guardrails.test_report.collect_list import read_ids

    project.config["checkers"] = {"adapter_vitest": {"command": command}}
    out = project.root / "collections"
    reported = list_tiers(project.path, project.config, ["t1"], out)
    ids = read_ids(out / "t1.txt")
    expected = [
        item.replace("tests/test_case.py", "tests/case.test.ts")
        for item in ([] if case["expect"] == "finding" else case["expect"]["ids"])
    ]
    return conformance.collection_verdict(case, reported, ids, expected)


def harness(repo: Path, config: dict[str, Any]) -> conformance.Harness:
    try:
        command, modules = native_installation(repo, config)
        missing = None
    except (OSError, ValueError) as error:
        command, modules = [], None
        missing = conformance.Prerequisite(str(error), INSTALL_REMEDY)
    return conformance.Harness(
        label="the Vitest adapter",
        prefix="guardrails-vitest-conformance-",
        build=lambda root, case: build_project(root, case, modules),
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
    case_ids: list[str],
    *,
    repo: Path,
    config: dict[str, Any],
) -> list[conformance.CaseResult]:
    selected = conformance.select_cases(case_ids)
    return conformance.run_cases(harness(repo, config), selected)


def run_cases(
    case_ids: list[str],
    *,
    repo: Path,
    config: dict[str, Any],
) -> list[Finding]:
    chosen = harness(repo, config)
    results = conformance.run_cases(chosen, conformance.select_cases(case_ids))
    return conformance.findings(chosen, results)
