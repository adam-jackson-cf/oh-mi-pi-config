"""External Vitest reporting and the shared evidence dialect.

Only the external evidence directory is written. The reporter is added to the native
configuration, rather than replacing it, so project selection and setup remain native.
"""
from __future__ import annotations

import json
from pathlib import Path

from guardrails.findings import Finding
from guardrails.test_evidence.junit import Dialect, ReportError, parse_report

DIALECT = Dialect(name="vitest", source_suffix=".ts")
GUARDS_PROPERTY = "omp.guards"
TIER_GUARDS = {"t1": ("fast-tier", "user-data"), "t2": ("user-data",), "t3": ("user-data",)}
GUARD_RULES = {"fast-tier": "TEST.fast-tier-isolation", "user-data": "TEST.user-data-guard"}


def junit_arguments(tier: str, report_path: str) -> list[str]:
    """Return CLI arguments; materialise the reporter entry beside the external report.

    Callers must supply a report outside the target tree, as for every evidence writer.
    Native ``--config`` arguments are preserved. No installed package is modified.
    """
    if tier not in TIER_GUARDS:
        raise ValueError(f"unknown Vitest tier {tier!r}")
    report = Path(report_path).resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    entry = report.with_name(report.name + ".reporter.mjs")
    reporter = Path(__file__).with_name("reporter.mjs").resolve().as_uri()
    options = json.dumps({"tier": tier, "report": str(report)})
    entry.write_text(
        f"import Reporter from {json.dumps(reporter)};\n"
        f"export default class extends Reporter {{ constructor() {{ super({options}); }} }}\n",
        encoding="utf-8",
    )
    return ["--reporter", str(entry), "--configLoader", "runner", "--no-cache", "--no-fsModuleCache"]


def guard_evidence(report: Path, tier: str) -> list[Finding]:
    """Reject absent evidence or a run which did not actually arm every tier guard."""
    if tier not in TIER_GUARDS:
        raise ValueError(f"unknown Vitest tier {tier!r}")
    try:
        parsed = parse_report(report, DIALECT)
        if parsed.properties.get("omp.tier") != tier:
            raise ReportError(f"report does not record tier {tier}")
    except ReportError as error:
        return [Finding("TEST.failure-propagation", str(report), str(error), "rerun Vitest with the adapter's junit arguments")]
    armed = set(parsed.properties.get(GUARDS_PROPERTY, "").split(","))
    return [
        Finding(GUARD_RULES[guard], str(report), f"tier {tier} ran without the {guard} guard", "rerun Vitest with the adapter's junit arguments")
        for guard in TIER_GUARDS[tier] if guard not in armed
    ]
