from __future__ import annotations

import hashlib
import platform
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from guardrails.context.config.reader import approved_targets, tests_settings
from guardrails.foundation.findings import Finding
from guardrails.host.process import run_text
from guardrails.test_report.junit import (
    REVISION_PROPERTY,
    RUNTIME_PROPERTY,
    TIER_PROPERTY,
    WALL_PROPERTY,
    Report,
    ReportError,
    ReportOptions,
    UsageError,
    display_path,
    evidence_settings,
    load_run_report,
    report_options,
)

PER_TEST = "TEST.budget.per-test-time"
FAST_TIER = "TEST.budget.fast-tier-time"
PROBE_TIMEOUT_S = 10
INVALID_FIX = (
    "rerun the config's duration-report command on this commit and check that its report "
    "is complete; a missing, invalid or stale report always fails"
)
PER_TEST_FIX = (
    "speed the test up: remove real-clock waits, bound the actual waiting object, isolate "
    "contention and minimise steps; never relabel it to another tier"
)
FAST_TIER_FIX = (
    "remove redundant coverage, unnecessary work and wrong-tier execution; if still over, ask "
    "the owner once to approve a new wall limit for this machine from repeated measurements"
)


@dataclass(frozen=True)
class Machine:
    id: str
    platform: str
    runtime: str


@dataclass
class BudgetResult:
    findings: list[Finding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def cpu_model() -> str:
    system = platform.system()
    if system == "Darwin":
        try:
            result = run_text(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                timeout=PROBE_TIMEOUT_S,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            result = None
        if result is not None and result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    if system == "Linux":
        try:
            lines = Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        for key in ("model name", "Hardware", "Processor"):
            for line in lines:
                name, _, value = line.partition(":")
                if name.strip() == key and value.strip():
                    return value.strip()
    return platform.processor() or "unknown-cpu"


def machine_id(runtime: str) -> str:
    values = (
        platform.system(),
        platform.machine(),
        cpu_model(),
        " ".join(runtime.split()),
    )
    return "m-" + hashlib.sha256("\n".join(values).encode()).hexdigest()[:12]


def probe_machine(runtime: str) -> Machine:
    label = f"{platform.system()} {platform.machine()}".strip()
    return Machine(machine_id(runtime), label, " ".join(runtime.split()))


def current_revision(repo: Path) -> str | None:
    try:
        result = run_text(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            timeout=PROBE_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return (
        result.stdout.strip()
        if result.returncode == 0 and result.stdout.strip()
        else None
    )


def seconds(value: float) -> str:
    return f"{value:.3f}s"


def invalid(path: str, problem: str) -> Finding:
    return Finding(
        FAST_TIER, path, f"duration report is invalid: {problem}", INVALID_FIX
    )


def validity_findings(report: Report, expected_revision: str | None) -> list[Finding]:
    found = []
    if not report.cases:
        found.append(invalid(report.source, "it holds no test cases"))
    untimed = [
        case.id
        for case in report.cases
        if case.outcome not in {"skipped", "xfail"} and case.duration_s is None
    ]
    if untimed:
        sample = ", ".join(untimed[:3])
        found.append(
            invalid(
                report.source, f"{len(untimed)} executed cases lack a time ({sample})"
            )
        )
    if report.wall_s is None:
        found.append(
            invalid(report.source, f"no outer wall time ({WALL_PROPERTY} or root time)")
        )
    recorded = report.properties.get(REVISION_PROPERTY, "").strip()
    if not expected_revision:
        found.append(
            invalid(report.source, "the current revision cannot be determined")
        )
    elif not recorded:
        found.append(invalid(report.source, f"no {REVISION_PROPERTY} property"))
    elif recorded != expected_revision:
        found.append(
            invalid(
                report.source,
                f"stale: measured {recorded}, current is {expected_revision}",
            )
        )
    if not report.properties.get(RUNTIME_PROPERTY, "").strip():
        found.append(invalid(report.source, f"no {RUNTIME_PROPERTY} property"))
    return found


def approved_limits(config: dict[str, Any], machine: Machine) -> dict[str, Any] | None:
    budget = tests_settings(config, "fast_tier_budget")
    machines = budget.get("machines") if isinstance(budget, dict) else None
    for record in machines if isinstance(machines, list) else []:
        if not isinstance(record, dict) or record.get("id") != machine.id:
            continue
        limits_ok = all(
            isinstance(record.get(key), (int, float))
            and not isinstance(record.get(key), bool)
            for key in ("wall_limit_s", "per_test_limit_s")
        )
        if limits_ok and machine.id in approved_targets(config, "budget"):
            return record
    return None


def breach_findings(
    report: Report, wall_limit: float, per_test_limit: float
) -> list[Finding]:
    found = []
    if report.wall_s is not None and report.wall_s > wall_limit:
        found.append(
            Finding(
                FAST_TIER,
                report.source,
                f"fast tier took {seconds(report.wall_s)}, over the approved {seconds(wall_limit)}",
                FAST_TIER_FIX,
            )
        )
    for case in report.cases:
        if case.duration_s is not None and case.duration_s > per_test_limit:
            found.append(
                Finding(
                    PER_TEST,
                    case.file or report.source,
                    f"{case.id} took {seconds(case.duration_s)}, "
                    f"over the approved {seconds(per_test_limit)}",
                    PER_TEST_FIX,
                )
            )
    return found


def baseline_prompt(report: Report, machine: Machine) -> str:
    slowest = max((case.duration_s or 0.0 for case in report.cases), default=0.0)
    wall = seconds(report.wall_s) if report.wall_s is not None else "unavailable"
    return (
        f"machine {machine.id} ({machine.platform}, {machine.runtime}) has no approved budget; "
        "timing is report-only. Measure repeatedly, then ask the owner once: "
        f'"Approve a fast-tier wall limit of <value> and a per-test limit of <value> on '
        f'{machine.id}, based on these repeated measurements: <values>?" '
        f"This run: wall {wall}, slowest test {seconds(slowest)}. On approval the owner "
        "records the machine's id, platform, runtime and limits in "
        "tests.fast_tier_budget.machines of .agentrails/config.json, with a budget "
        "approval whose target is the machine id."
    )


def evaluate(
    report: Report,
    config: dict[str, Any],
    expected_revision: str | None,
) -> BudgetResult:
    result = BudgetResult(validity_findings(report, expected_revision))
    runtime = report.properties.get(RUNTIME_PROPERTY, "").strip()
    if result.findings or not runtime:
        return result
    machine = probe_machine(runtime)
    limits = approved_limits(config, machine)
    if limits is None:
        result.notes.append(baseline_prompt(report, machine))
        return result
    result.findings.extend(
        breach_findings(
            report, float(limits["wall_limit_s"]), float(limits["per_test_limit_s"])
        )
    )
    return result


def report_tier(report: Report, options: ReportOptions) -> str:
    return options.tier or report.properties.get(TIER_PROPERTY, "").strip() or "t1"


def run(repo: Path, config: dict[str, Any], argv: list[str]) -> list[Finding]:
    try:
        options = report_options(repo, argv)
        report = load_run_report(repo, config, options)
    except UsageError as error:
        return [
            Finding(FAST_TIER, "budget-check", f"usage: {error}", "fix the arguments")
        ]
    except ReportError as error:
        path = display_path(repo, options.junit, "budget-check")
        return [invalid(path, str(error))]
    fast_tier = evidence_settings(config).get("fast_tier", "t1")
    if report_tier(report, options) != fast_tier:
        return []
    revision = options.revision or current_revision(repo)
    result = evaluate(report, config, revision)
    for note in result.notes:
        print(f"{FAST_TIER}: {note}", file=sys.stderr)
    return result.findings
