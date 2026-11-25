"""What every test-framework adapter shares: tier guards, evidence output and guard evidence."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from guardrails.foundation.findings import Finding
from guardrails.foundation.inventory import settings
from guardrails.toolkit.test_report.junit import (
    GUARDS_PROPERTY,
    TIER_PROPERTY,
    Dialect,
    ReportError,
    parse_report,
)

FAST_TIER = "fast-tier"
USER_DATA = "user-data"
GUARDS = (FAST_TIER, USER_DATA)
GUARD_RULES = {
    FAST_TIER: "TEST.fast-tier-isolation",
    USER_DATA: "TEST.user-data-guard",
}
TIER_GUARDS = {
    "t1": (FAST_TIER, USER_DATA),
    "t2": (USER_DATA,),
    "t3": (USER_DATA,),
}
OUTPUT_RULE = "GUARDRAILS.output-outside-repo"
PROPAGATION = "TEST.failure-propagation"


def output_finding(repo: Path, out: object) -> Finding | None:
    target = Path(str(out)).expanduser()
    target = (target if target.is_absolute() else repo / target).resolve()
    if not target.is_relative_to(repo.resolve()):
        return None
    return Finding(
        OUTPUT_RULE,
        str(out),
        "adapter evidence must be outside the repository",
        "choose an external temporary directory",
    )


def report_finding(report: Path, error: Exception, rerun: str) -> Finding:
    return Finding(
        PROPAGATION,
        report.as_posix(),
        f"run report is missing or invalid: {error}",
        f"{rerun}; a missing report never passes",
    )


def guard_evidence(
    report: Path, tier: str, dialect: Dialect, rerun: str
) -> list[Finding]:
    """Findings for a tier report that does not record the tier and every guard it needs."""
    if tier not in TIER_GUARDS:
        raise ValueError(f"unknown {dialect.name} tier {tier!r}")
    try:
        parsed = parse_report(report, dialect)
    except ReportError as error:
        return [report_finding(report, error, rerun)]
    recorded = parsed.properties.get(TIER_PROPERTY, "")
    if recorded != tier:
        problem = ValueError(f"{TIER_PROPERTY} is {recorded!r}, not {tier!r}")
        return [report_finding(report, problem, rerun)]
    armed = set(filter(None, parsed.properties.get(GUARDS_PROPERTY, "").split(",")))
    return [
        Finding(
            GUARD_RULES[guard],
            report.as_posix(),
            f"tier {tier} ran without the {guard} guard",
            rerun,
        )
        for guard in TIER_GUARDS[tier]
        if guard not in armed
    ]


def collect_timeout(config: dict[str, Any], section: str) -> float:
    """checkers.<section>.collect_timeout_s: seconds a tier collection may take, 120 by default."""
    timeout = settings(config, section).get("collect_timeout_s", 120)
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise ValueError(
            f"{section}.collect_timeout_s must be a positive finite number"
        )
    return float(timeout)
