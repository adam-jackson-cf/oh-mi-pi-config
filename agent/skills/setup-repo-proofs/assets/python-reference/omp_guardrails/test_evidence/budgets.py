"""TEST.budget.per-test-time and TEST.budget.fast-tier-time over a normalised run report.

Policy (test-guidance ``suite-budget.md``):

- Limits are per machine and owner-approved: the profile's ``fast_tier_budget.machines``
  records ``id``, ``platform``, ``runtime``, ``wall_limit_s``, ``per_test_limit_s`` and
  ``approval`` for each measuring machine.
- Budgets run only on the local commit gate (``omp-guardrails budget-check``). CI is an
  offline process: it never measures or reports test timing and never runs this checker; the
  CI gate runs ``report-check`` (TEST.failure-propagation) only.
- A known, approved machine blocks on any breach.
- An unknown or unapproved machine is report-only, and the checker prints the owner question
  that records its baseline.
- A missing, invalid or stale report is fatal, including on a report-only machine.

Validity and freshness rules (what Command notes should cite):

1. The report parses as JUnit XML and holds at least one test case.
2. Every executed (not skipped) case has a finite non-negative ``time``, and the run has an
   outer wall time (``omp.wall_s`` property or the root ``time``).
3. The ``omp.revision`` property equals ``--revision`` or, when absent, ``git rev-parse HEAD``
   of the repository: a report from another commit is stale.
4. The ``omp.runtime`` property names the test runtime, so the machine can be identified.

Machine id probe (deterministic, local, no host names): ``m-`` followed by the first 12 hex
digits of SHA-256 over four newline-joined values: the operating system
(``platform.system()``), the architecture (``platform.machine()``), the CPU model (macOS
``sysctl -n machdep.cpu.brand_string``; Linux the first ``model name``, ``Hardware`` or
``Processor`` line of ``/proc/cpuinfo``; otherwise ``platform.processor()``) and the report's
``omp.runtime`` value with whitespace collapsed. A runtime upgrade therefore yields a new
machine id, which runs report-only until the owner approves its baseline.

Budgets apply to the fast tier only: the report's tier (``--tier``, else ``omp.tier``, else
``t1``) must equal profile setting ``guardrails.test_evidence.fast_tier`` (default ``t1``).
"""

from __future__ import annotations

import hashlib
import platform
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from omp_guardrails.findings import Finding
from omp_guardrails.test_evidence.junit import (
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
    "rerun the profile's duration-report command on this commit and check that its report "
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
            result = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True,
                text=True,
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


def machine_id(
    runtime: str,
    *,
    system: str | None = None,
    architecture: str | None = None,
    cpu: str | None = None,
) -> str:
    """The deterministic machine id documented in the module docstring."""
    values = (
        system if system is not None else platform.system(),
        architecture if architecture is not None else platform.machine(),
        cpu if cpu is not None else cpu_model(),
        " ".join(runtime.split()),
    )
    return "m-" + hashlib.sha256("\n".join(values).encode()).hexdigest()[:12]


def probe_machine(runtime: str) -> Machine:
    label = f"{platform.system()} {platform.machine()}".strip()
    return Machine(machine_id(runtime), label, " ".join(runtime.split()))


def current_revision(repo: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None


def seconds(value: float) -> str:
    return f"{value:.3f}s"


def invalid(path: str, problem: str) -> Finding:
    return Finding(FAST_TIER, path, f"duration report is invalid: {problem}", INVALID_FIX)


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
        found.append(invalid(report.source, f"{len(untimed)} executed cases lack a time ({sample})"))
    if report.wall_s is None:
        found.append(invalid(report.source, "no outer wall time (omp.wall_s or root time)"))
    recorded = report.properties.get("omp.revision", "").strip()
    if not expected_revision:
        found.append(invalid(report.source, "the current revision cannot be determined"))
    elif not recorded:
        found.append(invalid(report.source, "no omp.revision property"))
    elif recorded != expected_revision:
        found.append(
            invalid(report.source, f"stale: measured {recorded}, current is {expected_revision}")
        )
    if not report.properties.get("omp.runtime", "").strip():
        found.append(invalid(report.source, "no omp.runtime property"))
    return found


def approved_limits(profile: dict, machine: Machine) -> dict | None:
    budget = profile.get("fast_tier_budget")
    machines = budget.get("machines") if isinstance(budget, dict) else None
    for record in machines if isinstance(machines, list) else []:
        if not isinstance(record, dict) or record.get("id") != machine.id:
            continue
        limits_ok = all(
            isinstance(record.get(key), (int, float)) and not isinstance(record.get(key), bool)
            for key in ("wall_limit_s", "per_test_limit_s")
        )
        approval = record.get("approval")
        if limits_ok and isinstance(approval, str) and approval.strip():
            return record
    return None


def breach_findings(report: Report, wall_limit: float, per_test_limit: float) -> list[Finding]:
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
        f"\"Approve a fast-tier wall limit of <value> and a per-test limit of <value> on "
        f"{machine.id}, based on these repeated measurements: <values>?\" "
        f"This run: wall {wall}, slowest test {seconds(slowest)}. On approval record the "
        "machine's id, platform, runtime, limits and approval in fast_tier_budget.machines."
    )


def evaluate(
    report: Report,
    profile: dict,
    expected_revision: str | None,
    machine: Machine | None = None,
) -> BudgetResult:
    """Validity is always fatal; timing blocks only on an approved machine."""
    result = BudgetResult(validity_findings(report, expected_revision))
    runtime = report.properties.get("omp.runtime", "").strip()
    if result.findings or not runtime:
        return result
    machine = machine or probe_machine(runtime)
    limits = approved_limits(profile, machine)
    if limits is None:
        result.notes.append(baseline_prompt(report, machine))
        return result
    result.findings.extend(
        breach_findings(report, float(limits["wall_limit_s"]), float(limits["per_test_limit_s"]))
    )
    return result


def report_tier(report: Report, options: ReportOptions) -> str:
    return options.tier or report.properties.get("omp.tier", "").strip() or "t1"


def run(repo: Path, profile: dict, argv: list[str]) -> list[Finding]:
    """``omp-guardrails budget-check --junit PATH [--tier T] [--revision SHA]``."""
    try:
        options = report_options(repo, argv)
        report = load_run_report(repo, profile, options)
    except UsageError as error:
        return [Finding(FAST_TIER, "budget-check", f"usage: {error}", "fix the arguments")]
    except ReportError as error:
        path = display_path(repo, options.junit, "budget-check")
        return [invalid(path, str(error))]
    fast_tier = evidence_settings(profile).get("fast_tier", "t1")
    if report_tier(report, options) != fast_tier:
        return []
    revision = options.revision or current_revision(repo)
    result = evaluate(report, profile, revision)
    for note in result.notes:
        print(f"{FAST_TIER}: {note}", file=sys.stderr)
    return result.findings
