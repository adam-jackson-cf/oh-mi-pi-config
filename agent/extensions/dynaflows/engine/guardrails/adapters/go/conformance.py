from __future__ import annotations

import copy
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from guardrails.adapters.go.process import run_command
from guardrails.findings import Finding

FIXTURES = Path(__file__).with_name("fixtures")
CASE_TIMEOUT_S = 300
GO_MOD = "module example.com/project\n\ngo 1.22\n"
TEST_FILE = "tests/case_test.go"
PRODUCTION_FILE = "shop/settings.go"
RUN_SELECTOR = "-run ."
GO_IDS = {"tests/test_case.py::test_fast_total": f"{TEST_FILE}::TestFastTotal"}
RUN_TIERS = {"user-data-write": "t2", "user-data-temporary-home": "t2"}
BASE_PROFILE: dict[str, Any] = {
    "adapters": [
        {
            "name": "go",
            "tiers": {"t1": {"selector": "-run ^TestFastTotal$", "location": "tests"}},
        }
    ],
    "public_entry_points": {"shop": ["PublicAPI"]},
    "tiers": {"t1": {"command": "guardrails adapter go run --tier t1"}},
    "layout": {
        "test_file_patterns": ["*_test.go"],
        "support_dir": "tests",
        "fixtures_dir": "tests",
    },
    "house_style": {"version": 1},
    "guardrails": {"exceptions": []},
}


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    rule: str
    passed: bool
    detail: str


def load_cases() -> list[dict[str, Any]]:
    canonical = resources.files("guardrails.adapters.pytest.conformance").joinpath(
        "cases.json"
    )
    cases: list[dict[str, Any]] = json.loads(canonical.read_text(encoding="utf-8"))[
        "cases"
    ]
    return cases


def fixture_source(case_id: str) -> str:
    return (FIXTURES / f"{case_id}.go.txt").read_text(encoding="utf-8")


def build_project(root: Path, case: dict[str, Any]) -> tuple[Path, str, dict[str, Any]]:
    project = root / "project"
    (project / "shop").mkdir(parents=True)
    (project / "tests").mkdir()
    initialised = run_command(["git", "init", "-q", str(project)], root, os.environ, 30)
    if initialised.returncode:
        raise OSError(f"git init failed: {initialised.stderr.strip()}")
    (project / "go.mod").write_text(GO_MOD, encoding="utf-8")
    (project / "shop/core.go").write_text(
        fixture_source("owned-package"), encoding="utf-8"
    )
    production = case["id"].startswith("production-")
    placed = PRODUCTION_FILE if production else TEST_FILE
    (project / placed).write_text(fixture_source(case["id"]), encoding="utf-8")
    profile = copy.deepcopy(BASE_PROFILE)
    if case["id"] == "approved-static-skip":
        profile["guardrails"]["exceptions"] = [
            {
                "rule": "TEST.no-evasion",
                "path": placed,
                "reason": "Upstream vendor outage",
            }
        ]
    if production:
        profile["public_entry_points"][placed] = ["Region"]
    return project, placed, profile


def check_source(
    project: Path, profile: dict[str, Any], placed: str, case: dict[str, Any]
) -> tuple[bool, str]:
    from guardrails.adapters.go.lint import lint

    rules = sorted({finding.rule_id for finding in lint(project, profile, [placed])})
    return (case["rule"] in rules) == (
        case["expect"] == "finding"
    ), f"rules reported: {rules}"


def check_static_cases(
    project: Path, placed: str, case: dict[str, Any]
) -> tuple[bool, str]:
    from guardrails.adapters.go.cases import collect
    from guardrails.diffs.growth import Snapshot, growth_findings

    collected = collect(placed, (project / placed).read_text(encoding="utf-8"))
    findings = growth_findings(
        Snapshot({}, {}, {}), Snapshot({}, dict(collected.behaviours), {})
    )
    reported = any(finding.rule_id == case["rule"] for finding in findings)
    return reported == (
        case["expect"] == "finding"
    ), f"behaviours: {dict(collected.behaviours)}"


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
        "OMP_CONFORMANCE_LIVE_HOME": str(home),
    }


def check_run(
    root: Path,
    project: Path,
    profile: dict[str, Any],
    case: dict[str, Any],
    kept: dict[str, str],
) -> tuple[bool, str]:
    from guardrails.adapters.go.evidence import guard_evidence, load_report
    from guardrails.adapters.go.run import run_tier
    from guardrails.test_evidence.junit import ReportError

    tier = RUN_TIERS.get(case["id"], "t1")
    profile["adapters"][0]["tiers"] = {
        tier: {"selector": RUN_SELECTOR, "location": "tests"}
    }
    report = root / "evidence" / "report.xml"
    code = run_tier(project, profile, tier, report, run_environment(root, kept))
    try:
        parsed = load_report(report)
    except ReportError as error:
        return False, f"{error}; go exit {code}"
    cases = parsed.cases
    detail = f"go exit {code}; cases: {[(item.outcome, item.reason[:160]) for item in cases]}"
    expected, rule = case["expect"], case["rule"]
    if expected == "finding":
        return any(
            item.outcome in {"failed", "error"} and rule in item.reason
            for item in cases
        ), detail
    if expected == "clean":
        guarded = guard_evidence(report, tier) == []
        clean = bool(cases) and all(
            item.outcome == "passed" and rule not in item.reason for item in cases
        )
        return clean and guarded and code == 0, detail
    return len(cases) == 1 and all(
        getattr(cases[0], key) == value for key, value in expected.items()
    ), detail


def check_collection(
    root: Path, project: Path, profile: dict[str, Any], case: dict[str, Any]
) -> tuple[bool, str]:
    from guardrails.adapters.go.tiers import list_tiers
    from guardrails.test_evidence.collection import read_ids

    out = root / "collections"
    findings = list_tiers(project, profile, ["t1"], out)
    rules = [finding.rule_id for finding in findings]
    ids = read_ids(out / "t1.txt")
    if case["expect"] == "finding":
        return case["rule"] in rules, f"rules: {rules}; ids: {ids}"
    expected = [GO_IDS[item] for item in case["expect"]["ids"]]
    return not findings and ids == expected, f"rules: {rules}; ids: {ids}"


def run_conformance(
    case_ids: list[str] | None = None, *, profile: dict[str, Any] | None = None
) -> list[CaseResult]:
    from guardrails.adapters.go.run import kept_go_environment

    cases = load_cases()
    unknown = sorted(set(case_ids or []) - {case["id"] for case in cases})
    if unknown:
        raise ValueError(f"unknown conformance case(s): {', '.join(unknown)}")
    selected = [case for case in cases if not case_ids or case["id"] in case_ids]
    configured = (profile or {}).get("guardrails", {}).get("adapter_go", {})
    try:
        kept = kept_go_environment({"guardrails": {"adapter_go": configured}})
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return [
            CaseResult(case["id"], case["rule"], False, str(error)) for case in selected
        ]
    toolchain = {
        **configured,
        "command": [str(Path(kept.pop("GOROOT")) / "bin" / "go")],
    }
    results = []
    for case in selected:
        try:
            with tempfile.TemporaryDirectory(prefix="omp-go-conformance-") as scratch:
                root = Path(scratch).resolve()
                project, placed, bound = build_project(root, case)
                bound["guardrails"]["adapter_go"] = toolchain
                kind = case["evidence"]
                if kind == "source":
                    passed, detail = check_source(project, bound, placed, case)
                elif kind == "static-cases":
                    passed, detail = check_static_cases(project, placed, case)
                elif kind == "run-report":
                    passed, detail = check_run(root, project, bound, case, kept)
                elif kind == "collection":
                    passed, detail = check_collection(root, project, bound, case)
                else:
                    passed, detail = False, f"unsupported evidence kind: {kind}"
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            passed, detail = False, str(error)
        results.append(CaseResult(case["id"], case["rule"], passed, detail))
    return results


def run_cases(
    case_ids: list[str], *, profile: dict[str, Any] | None = None
) -> list[Finding]:
    return [
        Finding(
            result.rule,
            f"conformance/{result.case_id}",
            f"the Go adapter misclassifies this case ({result.detail})",
            "fix the Go adapter until every canonical case passes",
        )
        for result in run_conformance(case_ids, profile=profile)
        if not result.passed
    ]
