from __future__ import annotations

from guardrails.commands.adapters.pytest.dialect import junit_arguments

PLUGIN = "guardrails.commands.adapters.pytest.plugin"
TIER_GUARDS = {
    "t1": ("fast-tier", "user-data"),
    "t2": ("user-data",),
    "t3": ("user-data",),
}


def tier_arguments(tier: str, report_path: str) -> list[str]:
    guards = ",".join(TIER_GUARDS[tier])
    return [
        "-p",
        PLUGIN,
        f"--omp-guard={guards}",
        f"--omp-tier={tier}",
        *junit_arguments(report_path),
    ]
