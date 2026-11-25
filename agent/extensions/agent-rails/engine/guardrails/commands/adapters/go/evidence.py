from __future__ import annotations

from pathlib import Path

from guardrails.commands.adapters import contract
from guardrails.commands.adapters.go.dialect import DIALECT
from guardrails.foundation.findings import Finding
from guardrails.test_report.junit import Report, parse_report

RERUN = "rerun the tier with guardrails adapter go run"


def load_report(report: Path) -> Report:
    return parse_report(report, DIALECT)


def report_finding(report: Path, error: Exception) -> Finding:
    return contract.report_finding(report, error, RERUN)


def guard_evidence(report: Path, tier: str) -> list[Finding]:
    return contract.guard_evidence(
        report,
        tier,
        DIALECT,
        f"run tier {tier} with guardrails adapter go run --tier {tier}",
    )
