"""Run every canonical adapter case over frozen, real TypeScript fixtures.

Runtime cases execute native Vitest, never a simulated runner. Dependencies are taken
from the supplied target's installation; absent dependencies fail closed without fetching.
"""

from __future__ import annotations

import copy
import json
import os
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from guardrails.foundation.findings import Finding
from guardrails.host.process import run_bytes, run_text

CASE_TIMEOUT_S = 300
FIXTURES = Path(__file__).with_name("fixtures")
BASE_PROFILE: dict[str, Any] = {
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
    "public_entry_points": {"shop/core.ts": ["publicApi"]},
    "tiers": {"t1": {"command": "vitest run"}},
    "layout": {
        "test_file_patterns": ["*.test.ts"],
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
    canonical = resources.files(
        "guardrails.commands.adapters.pytest.conformance"
    ).joinpath("cases.json")
    cases: list[dict[str, Any]] = json.loads(canonical.read_text(encoding="utf-8"))[
        "cases"
    ]
    return cases


def fixture_source(case_id: str) -> str:
    return (FIXTURES / f"{case_id}.ts.txt").read_text(encoding="utf-8")


def native_installation(
    repo: Path | None, profile: dict[str, Any] | None
) -> tuple[list[str], Path]:
    """Use only a local installation, including an explicitly configured command."""
    root = (repo or Path.cwd()).resolve()
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
    settings = (profile or {}).get("guardrails", {}).get("adapter_vitest", {})
    command = settings.get("command")
    if command:
        argv = shlex.split(command) if isinstance(command, str) else list(command)
        if not argv:
            raise ValueError("guardrails.adapter_vitest.command must not be empty")
    else:
        node = shutil.which("node")
        if not node:
            raise ValueError(
                "Node is unavailable; activate the target's pinned Node before conformance"
            )
        argv = [node, str(modules / "vitest" / "vitest.mjs")]
    return argv, modules


def build_project(
    root: Path, case: dict[str, Any], modules: Path | None
) -> tuple[Path, str, dict[str, Any]]:
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
        profile["public_entry_points"][placed] = ["publicApi"]
    return project, placed, profile


def check_source(
    project: Path, profile: dict[str, Any], placed: str, case: dict[str, Any]
) -> tuple[bool, str]:
    from guardrails.commands.adapters.vitest.lint import lint

    rules = sorted({finding.rule_id for finding in lint(project, profile, [placed])})
    return (case["rule"] in rules) == (
        case["expect"] == "finding"
    ), f"rules reported: {rules}"


def check_static_cases(
    project: Path, placed: str, case: dict[str, Any]
) -> tuple[bool, str]:
    from guardrails.commands.adapters.vitest.cases import collect
    from guardrails.toolkit.growth import Snapshot, growth_findings

    collected = collect(placed, (project / placed).read_text(encoding="utf-8"))
    findings = growth_findings(
        Snapshot({}, {}, {}), Snapshot({}, dict(collected.behaviours), {})
    )
    reported = any(finding.rule_id == case["rule"] for finding in findings)
    return reported == (
        case["expect"] == "finding"
    ), f"behaviours: {dict(collected.behaviours)}"


def check_run(
    root: Path, project: Path, case: dict[str, Any], command: list[str]
) -> tuple[bool, str]:
    from guardrails.commands.adapters.vitest.dialect import (
        DIALECT,
        guard_evidence,
        junit_arguments,
    )
    from guardrails.toolkit.test_report.junit import ReportError, parse_report

    tier = "t2" if case["id"].startswith("user-data-") else "t1"
    report = root / "report.xml"
    home = root / "live-home"
    home.mkdir()
    environment = {
        **os.environ,
        "HOME": str(home),
        "USERPROFILE": str(home),
        "OMP_CONFORMANCE_LIVE_HOME": str(home),
    }
    result = run_text(
        [*command, "run", *junit_arguments(tier, str(report)), "tests/case.test.ts"],
        cwd=project,
        env=environment,
        timeout=CASE_TIMEOUT_S,
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
        flagged = any(
            item.outcome in {"failed", "error"} and case["rule"] in item.reason
            for item in parsed.cases
        )
        return flagged and result.returncode != 0, detail
    if expected == "clean":
        return bool(parsed.cases) and result.returncode == 0 and all(
            item.outcome == "passed" for item in parsed.cases
        ), detail
    return len(parsed.cases) == 1 and all(
        getattr(parsed.cases[0], key) == value for key, value in expected.items()
    ), detail


def check_collection(
    root: Path,
    project: Path,
    profile: dict[str, Any],
    case: dict[str, Any],
    command: list[str],
) -> tuple[bool, str]:
    from guardrails.commands.adapters.vitest.tiers import list_tiers
    from guardrails.toolkit.test_report.collection import read_ids

    profile["guardrails"] = {"adapter_vitest": {"command": command}}
    out = root / "collections"
    findings = list_tiers(project, profile, ["t1"], out)
    rules = [finding.rule_id for finding in findings]
    ids = read_ids(out / "t1.txt")
    if case["expect"] == "finding":
        return case["rule"] in rules, f"rules: {rules}; ids: {ids}"
    # Canonical IDs encode pytest's file spelling; bind the same case to the TS file.
    expected = [
        item.replace("tests/test_case.py", "tests/case.test.ts")
        for item in case["expect"]["ids"]
    ]
    return not findings and ids == expected, f"rules: {rules}; ids: {ids}"


def run_conformance(
    case_ids: list[str] | None = None,
    *,
    repo: Path | None = None,
    profile: dict[str, Any] | None = None,
) -> list[CaseResult]:
    cases = load_cases()
    unknown = sorted(set(case_ids or []) - {case["id"] for case in cases})
    if unknown:
        raise ValueError(f"unknown conformance case(s): {', '.join(unknown)}")
    selected = [case for case in cases if not case_ids or case["id"] in case_ids]
    try:
        command, modules = native_installation(repo, profile)
    except (OSError, ValueError) as error:
        return [
            CaseResult(case["id"], case["rule"], False, str(error)) for case in selected
        ]
    results = []
    for case in selected:
        try:
            with tempfile.TemporaryDirectory(
                prefix="omp-vitest-conformance-"
            ) as scratch:
                root = Path(scratch).resolve()
                project, placed, bound_profile = build_project(root, case, modules)
                kind = case["evidence"]
                if kind == "source":
                    passed, detail = check_source(project, bound_profile, placed, case)
                elif kind == "static-cases":
                    passed, detail = check_static_cases(project, placed, case)
                elif kind == "run-report":
                    passed, detail = check_run(root, project, case, command)
                elif kind == "collection":
                    passed, detail = check_collection(
                        root, project, bound_profile, case, command
                    )
                else:
                    passed, detail = False, f"unsupported evidence kind: {kind}"
        except (
            OSError,
            ValueError,
            ModuleNotFoundError,
            subprocess.SubprocessError,
        ) as error:
            passed, detail = False, str(error)
        results.append(CaseResult(case["id"], case["rule"], passed, detail))
    return results


def run_cases(
    case_ids: list[str],
    *,
    repo: Path | None = None,
    profile: dict[str, Any] | None = None,
) -> list[Finding]:
    return [
        Finding(
            result.rule,
            f"conformance/{result.case_id}",
            f"the Vitest adapter misclassifies this case ({result.detail})",
            "fix the Vitest adapter until every canonical case passes",
        )
        for result in run_conformance(case_ids, repo=repo, profile=profile)
        if not result.passed
    ]
