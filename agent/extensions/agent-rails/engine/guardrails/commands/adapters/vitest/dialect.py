from __future__ import annotations

import json
from pathlib import Path

from guardrails.commands.adapters import contract
from guardrails.foundation.findings import Finding
from guardrails.test_report.junit import Dialect

DIALECT = Dialect(name="vitest")
RERUN = "rerun Vitest with the adapter's junit arguments"


def junit_arguments(tier: str, report_path: str) -> list[str]:
    if tier not in contract.TIER_GUARDS:
        raise ValueError(f"unknown Vitest tier {tier!r}")
    report = Path(report_path).resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    entry = report.with_name(report.name + ".reporter.mjs")
    reporter = Path(__file__).with_name("reporter.ts").resolve().as_uri()
    options = json.dumps({"tier": tier, "report": str(report)})
    entry.write_text(
        f"import Reporter from {json.dumps(reporter)};\n"
        f"export default class extends Reporter {{ constructor() {{ super({options}); }} }}\n",
        encoding="utf-8",
    )
    return [
        "--reporter",
        str(entry),
        "--configLoader",
        "runner",
        "--no-cache",
        "--no-fsModuleCache",
    ]


def guard_evidence(report: Path, tier: str) -> list[Finding]:
    return contract.guard_evidence(report, tier, DIALECT, RERUN)
