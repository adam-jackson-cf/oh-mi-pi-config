from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

from guardrails.commands.test_evidence.collection import DUPLICATE_ID_FIX, EMPTY_FIX
from guardrails.context.config.reader import approved_targets
from guardrails.foundation.findings import Finding
from guardrails.test_report.junit import (
    EXIT_CODE_PROPERTY,
    FAILING,
    Case,
    Report,
    ReportError,
    UsageError,
    as_int,
    display_path,
    load_run_report,
    report_options,
)

RULE = "TEST.failure-propagation"
FAIL_FIX = (
    "fix the failing check; a failed, errored or missing check never counts as passing"
)
TRUNCATED_FIX = "rerun the tier to completion and keep the whole report"
EXIT_FIX = "fix the runner failure outside the test cases, then rerun the tier"
EVASION = "TEST.no-evasion"
SKIP_FIX = (
    "make the test run and pass, or ask the owner for a skip approval (approvals kind "
    "skip, target the test id or an fnmatch pattern) with their reason"
)
RETRY_FIX = "fix the flake's cause so it passes on the first run; never rely on retries"


def approved(test_id: str, config: dict[str, Any]) -> bool:
    return any(
        fnmatchcase(test_id, pattern) for pattern in approved_targets(config, "skip")
    )


def report_findings(report: Report, config: dict[str, Any]) -> list[Finding]:
    if not report.cases:
        return [
            Finding(RULE, report.source, "run report holds no test cases", EMPTY_FIX)
        ]
    found = []
    by_id: dict[str, list[Any]] = {}
    for case in report.cases:
        by_id.setdefault(case.id, []).append(case)
    for test_id, cases in by_id.items():
        path = cases[-1].file or report.source
        final = cases[-1]
        earlier_failed = any(case.outcome in {"failed", "error"} for case in cases[:-1])
        if final.outcome == "passed" and (final.retries > 0 or earlier_failed):
            attempts = final.retries + len(cases) - 1
            found.append(
                Finding(
                    EVASION,
                    path,
                    f"{test_id} passed only after {attempts} retries",
                    RETRY_FIX,
                )
            )
        elif len(cases) > 1:
            found.append(
                Finding(
                    RULE,
                    path,
                    f"{test_id} is reported {len(cases)} times",
                    DUPLICATE_ID_FIX,
                )
            )
        if final.outcome in {"skipped", "xfail"} and not approved(test_id, config):
            reason = f" ({final.reason})" if final.reason else ""
            found.append(
                Finding(
                    EVASION,
                    path,
                    f"{test_id} is {final.outcome} without approval{reason}",
                    SKIP_FIX,
                )
            )
    return found


def final_cases(report: Report) -> list[Case]:
    return list({case.id: case for case in report.cases}.values())


def outcome_findings(report: Report) -> list[Finding]:
    found = []
    for case in final_cases(report):
        if case.outcome in FAILING:
            kind = (
                "errored (infrastructure or collection)"
                if case.outcome == "error"
                else "failed"
            )
            reason = f": {case.reason}" if case.reason else ""
            found.append(
                Finding(
                    RULE,
                    case.file or report.source,
                    f"{case.id} {kind}{reason}",
                    FAIL_FIX,
                )
            )
    return found


def truncation_findings(report: Report) -> list[Finding]:
    found = []
    held = {
        "tests": len(report.cases),
        "failures": sum(case.outcome == "failed" for case in report.cases),
        "errors": sum(case.outcome == "error" for case in report.cases),
    }
    for key, declared in sorted(report.declared.items()):
        if declared > held[key]:
            found.append(
                Finding(
                    RULE,
                    report.source,
                    f"suites declare {declared} {key} but the report holds {held[key]}",
                    TRUNCATED_FIX,
                )
            )
    return found


def exit_findings(report: Report, exit_code: int | None) -> list[Finding]:
    if exit_code is None:
        return []
    failing = any(case.outcome in FAILING for case in final_cases(report))
    if exit_code != 0 and not failing:
        return [
            Finding(
                RULE,
                report.source,
                f"runner exited {exit_code} although no reported case failed",
                EXIT_FIX,
            )
        ]
    if exit_code == 0 and failing:
        return [
            Finding(
                RULE,
                report.source,
                "runner exited 0 although a reported case failed",
                "make the runner's exit code reflect failures",
            )
        ]
    return []


def evaluate(
    report: Report, config: dict[str, Any], exit_code: int | None
) -> list[Finding]:
    if exit_code is None:
        exit_code = as_int(report.properties.get(EXIT_CODE_PROPERTY))
    if exit_code is None and report.requires_exit_code:
        return [
            Finding(
                RULE,
                report.source,
                "this runner's report omits failures outside the test cases, such as a test "
                "file that fails to load, and no exit code was given",
                "pass the runner's exit code with --exit-code",
            )
        ]
    return [
        *outcome_findings(report),
        *truncation_findings(report),
        *exit_findings(report, exit_code),
        *report_findings(report, config),
    ]


def run(repo: Path, config: dict[str, Any], argv: list[str]) -> list[Finding]:
    try:
        options = report_options(repo, argv)
        report = load_run_report(repo, config, options)
    except UsageError as error:
        return [Finding(RULE, "report-check", f"usage: {error}", "fix the arguments")]
    except ReportError as error:
        path = display_path(repo, options.junit, "report-check")
        return [Finding(RULE, path, str(error), FAIL_FIX)]
    return evaluate(report, config, options.exit_code)
