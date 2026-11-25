from __future__ import annotations

import copy
import os
import subprocess
from pathlib import Path
from typing import Any

from guardrails.commands.adapters import conformance
from guardrails.foundation.findings import Finding
from guardrails.host.process import run_text

FIXTURES = Path(__file__).with_name("fixtures")
GO_MOD = "module example.com/project\n\ngo 1.22\n"
TEST_FILE = "tests/case_test.go"
PRODUCTION_FILE = "shop/settings.go"
RUN_SELECTOR = "-run ."
GO_IDS = {"tests/test_case.py::test_fast_total": f"{TEST_FILE}::TestFastTotal"}
RUN_TIERS = {"user-data-write": "t2", "user-data-temporary-home": "t2"}
BASE_CONFIG: dict[str, Any] = {
    "tests": {
        "adapters": [
            {
                "name": "go",
                "tiers": {
                    "t1": {"selector": "-run ^TestFastTotal$", "location": "tests"}
                },
            }
        ],
        "public_entry_points": {"shop": ["PublicAPI"]},
        "tiers": {"t1": {"command": "guardrails adapter go run --tier t1"}},
        "layout": {
            "test_file_patterns": ["*_test.go"],
            "support_dir": "tests",
            "fixtures_dir": "tests",
        },
    },
    "houseStyle": {"version": 1},
    "checkers": {},
    "exceptions": [],
}


def fixture_source(case_id: str) -> str:
    return (FIXTURES / f"{case_id}.go.txt").read_text(encoding="utf-8")


def build_project(root: Path, case: dict[str, Any]) -> conformance.Project:
    project = root / "project"
    (project / "shop").mkdir(parents=True)
    (project / "tests").mkdir()
    initialised = run_text(
        ["git", "init", "-q", str(project)],
        cwd=root,
        env=os.environ,
        timeout=30,
        check=False,
    )
    if initialised.returncode:
        raise OSError(f"git init failed: {initialised.stderr.strip()}")
    (project / "go.mod").write_text(GO_MOD, encoding="utf-8")
    (project / "shop/core.go").write_text(
        fixture_source("owned-package"), encoding="utf-8"
    )
    production = case["id"].startswith("production-")
    placed = PRODUCTION_FILE if production else TEST_FILE
    (project / placed).write_text(
        fixture_source(conformance.fixture_case(case["id"])), encoding="utf-8"
    )
    config = copy.deepcopy(BASE_CONFIG)
    config["exceptions"] = conformance.case_exceptions(case, placed)
    if production:
        config["tests"]["public_entry_points"][placed] = ["Region"]
    return conformance.Project(root, placed, config)


def check_source(
    project: conformance.Project, case: dict[str, Any]
) -> conformance.Verdict:
    from guardrails.commands.adapters.go.lint import lint

    reported = lint(project.path, project.config, [project.placed])
    return conformance.source_verdict(case, reported)


def check_static_cases(
    project: conformance.Project, case: dict[str, Any]
) -> conformance.Verdict:
    from guardrails.commands.adapters.go.cases import collect

    text = (project.path / project.placed).read_text(encoding="utf-8")
    return conformance.static_verdict(case, collect(project.placed, text))


def run_environment(root: Path, kept: dict[str, str]) -> dict[str, str]:
    home, scratch = root / "home", root / "tmp"
    home.mkdir()
    scratch.mkdir()
    return {
        **os.environ,
        **kept,
        "HOME": str(home),
        "TMPDIR": str(scratch),
        "GOTOOLCHAIN": "local",
        "GUARDRAILS_CONFORMANCE_LIVE_HOME": str(home),
    }


def check_run(
    project: conformance.Project, case: dict[str, Any], kept: dict[str, str]
) -> conformance.Verdict:
    from guardrails.commands.adapters.go.evidence import guard_evidence, load_report
    from guardrails.commands.adapters.go.run import run_tier
    from guardrails.test_report.junit import ReportError

    tier = RUN_TIERS.get(case["id"], "t1")
    config = project.config
    config["tests"]["adapters"][0]["tiers"] = {
        tier: {"selector": RUN_SELECTOR, "location": "tests"}
    }
    report = project.root / "evidence" / "report.xml"
    environment = run_environment(project.root, kept)
    code = run_tier(project.path, config, tier, report, environment)
    try:
        parsed = load_report(report)
    except ReportError as error:
        return False, f"{error}; go exit {code}"
    cases = parsed.cases
    detail = f"go exit {code}; cases: {[(item.outcome, item.reason[:160]) for item in cases]}"
    expected, rule = case["expect"], case["rule"]
    if expected == "finding":
        return conformance.flagged(cases, rule), detail
    if expected == "clean":
        guarded = guard_evidence(report, tier) == []
        clean = bool(cases) and all(
            item.outcome == "passed" and rule not in item.reason for item in cases
        )
        return clean and guarded and code == 0, detail
    return conformance.matches_record(cases, expected), detail


def check_collection(
    project: conformance.Project, case: dict[str, Any]
) -> conformance.Verdict:
    from guardrails.commands.adapters.go.tiers import list_tiers
    from guardrails.test_report.collect_list import read_ids

    out = project.root / "collections"
    reported = list_tiers(project.path, project.config, ["t1"], out)
    ids = read_ids(out / "t1.txt")
    expected = (
        []
        if case["expect"] == "finding"
        else [GO_IDS[item] for item in case["expect"]["ids"]]
    )
    return conformance.collection_verdict(case, reported, ids, expected)


def run_conformance(
    case_ids: list[str] | None = None, *, config: dict[str, Any] | None = None
) -> list[conformance.CaseResult]:
    from guardrails.commands.adapters.go.run import kept_go_environment

    selected = conformance.select_cases(case_ids or [])
    configured = (config or {}).get("checkers", {}).get("adapter_go", {})
    try:
        kept = kept_go_environment({"checkers": {"adapter_go": configured}})
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return conformance.failed_cases(selected, str(error))
    toolchain = {
        **configured,
        "command": [str(Path(kept.pop("GOROOT")) / "bin" / "go")],
    }

    def build(root: Path, case: dict[str, Any]) -> conformance.Project:
        project = build_project(root, case)
        project.config["checkers"]["adapter_go"] = toolchain
        return project

    harness = conformance.Harness(
        label="the Go adapter",
        prefix="guardrails-go-conformance-",
        build=build,
        checks={
            "source": check_source,
            "static-cases": check_static_cases,
            "run-report": lambda project, case: check_run(project, case, kept),
            "collection": check_collection,
        },
        errors=(OSError, ValueError, subprocess.SubprocessError),
    )
    return conformance.run_cases(harness, selected)


def run_cases(
    case_ids: list[str], *, config: dict[str, Any] | None = None
) -> list[Finding]:
    return conformance.findings(
        run_conformance(case_ids, config=config), "the Go adapter"
    )
