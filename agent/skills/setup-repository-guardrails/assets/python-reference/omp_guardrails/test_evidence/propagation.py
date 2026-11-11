"""TEST.failure-propagation over a run report: missing, failed or infra-failed runs never pass.

``omp-guardrails report-check --junit PATH [--exit-code N]`` fails when:

1. the report is missing, unreadable or not JUnit XML;
2. a case failed or errored (an error is an infrastructure or collection failure);
3. the report is truncated: its suites declare more tests, failures or errors than it holds;
4. the runner exit code (``--exit-code``, else the ``omp.exit_code`` property) is non-zero
   while every case passed, which is a failure outside the test cases (setup, collection,
   empty selection such as pytest exit 5 or vitest "no tests", or a crashed runner);
5. the exit code is zero while a case failed: the runner masked a failure.

It also applies the run-report hygiene in ``collection.report_findings``: empty reports,
duplicate IDs, unapproved skips or xfails, and retries to green. It never reads test timing:
the commit and CI gates both run it, and only the commit gate runs ``budget-check``.
"""

from __future__ import annotations

from pathlib import Path

from omp_guardrails.findings import Finding
from omp_guardrails.test_evidence.collection import report_findings
from omp_guardrails.test_evidence.junit import (
    FAILING,
    Case,
    Report,
    ReportError,
    UsageError,
    display_path,
    as_int,
    load_run_report,
    report_options,
)

RULE = "TEST.failure-propagation"
FAIL_FIX = "fix the failing check; a failed, errored or missing check never counts as passing"
TRUNCATED_FIX = "rerun the tier to completion and keep the whole report"
EXIT_FIX = "fix the runner failure outside the test cases, then rerun the tier"


def final_cases(report: Report) -> list[Case]:
    """The last result per test ID; earlier attempts are judged as retries in collection."""
    return list({case.id: case for case in report.cases}.values())


def outcome_findings(report: Report) -> list[Finding]:
    found = []
    for case in final_cases(report):
        if case.outcome in FAILING:
            kind = "errored (infrastructure or collection)" if case.outcome == "error" else "failed"
            reason = f": {case.reason}" if case.reason else ""
            found.append(Finding(RULE, case.file or report.source, f"{case.id} {kind}{reason}", FAIL_FIX))
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


def evaluate(report: Report, profile: dict, exit_code: int | None) -> list[Finding]:
    if exit_code is None:
        exit_code = as_int(report.properties.get("omp.exit_code"))
    return [
        *outcome_findings(report),
        *truncation_findings(report),
        *exit_findings(report, exit_code),
        *report_findings(report, profile),
    ]


def run(repo: Path, profile: dict, argv: list[str]) -> list[Finding]:
    try:
        options = report_options(repo, argv)
        report = load_run_report(repo, profile, options)
    except UsageError as error:
        return [Finding(RULE, "report-check", f"usage: {error}", "fix the arguments")]
    except ReportError as error:
        path = display_path(repo, options.junit, "report-check")
        return [Finding(RULE, path, str(error), FAIL_FIX)]
    return evaluate(report, profile, options.exit_code)
