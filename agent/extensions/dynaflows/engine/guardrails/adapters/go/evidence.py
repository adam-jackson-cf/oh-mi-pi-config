from __future__ import annotations

from pathlib import Path

from guardrails.adapters.go.dialect import DIALECT, GUARDS_PROPERTY
from guardrails.adapters.go.run import TIER_GUARDS
from guardrails.findings import Finding
from guardrails.test_evidence.junit import Report, ReportError, parse_report

PROPAGATION = "TEST.failure-propagation"
GUARD_RULES = {
    "fast-tier": "TEST.fast-tier-isolation",
    "user-data": "TEST.user-data-guard",
}


def load_report(report: Path) -> Report:
    return parse_report(report, DIALECT)


def report_finding(report: Path, error: Exception) -> Finding:
    return Finding(
        PROPAGATION,
        report.as_posix(),
        f"run report is missing or invalid: {error}",
        "rerun the tier with guardrails adapter go run; a missing report never passes",
    )


def guard_evidence(report: Path, tier: str) -> list[Finding]:
    try:
        parsed = load_report(report)
    except ReportError as error:
        return [report_finding(report, error)]
    recorded = parsed.properties.get("omp.tier", "")
    if recorded != tier:
        return [
            report_finding(
                report, ValueError(f"omp.tier is {recorded!r}, not {tier!r}")
            )
        ]
    armed = set(filter(None, parsed.properties.get(GUARDS_PROPERTY, "").split(",")))
    return [
        Finding(
            GUARD_RULES[guard],
            report.as_posix(),
            f"tier {tier} ran without the {guard} guard",
            f"run tier {tier} with guardrails adapter go run --tier {tier}",
        )
        for guard in TIER_GUARDS[tier]
        if guard not in armed
    ]
