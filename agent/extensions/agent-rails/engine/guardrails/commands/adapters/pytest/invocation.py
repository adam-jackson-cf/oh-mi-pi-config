from __future__ import annotations

from guardrails.commands.adapters.contract import TIER_GUARDS
from guardrails.commands.adapters.pytest.dialect import junit_arguments

PLUGIN = "guardrails.commands.adapters.pytest.plugin"


def tier_arguments(tier: str, report_path: str) -> list[str]:
    guards = ",".join(TIER_GUARDS[tier])
    return [
        "-p",
        PLUGIN,
        f"--guardrails-guard={guards}",
        f"--guardrails-tier={tier}",
        *junit_arguments(report_path),
    ]
