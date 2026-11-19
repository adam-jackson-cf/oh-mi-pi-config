from __future__ import annotations

import argparse
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from guardrails.commands.adapters.go.run import TIER_GUARDS
from guardrails.foundation.findings import Finding

NAME = "go"
PROPAGATION = "TEST.failure-propagation"
OUTPUT_RULE = "GUARDRAILS.output-outside-repo"
WRITING_ACTIONS = frozenset({"tiers", "run", "mutation-report"})


def output_finding(repo: Path, out: object) -> Finding | None:
    target = Path(str(out)).expanduser()
    target = (target if target.is_absolute() else repo / target).resolve()
    if target.is_relative_to(repo.resolve()):
        return Finding(
            OUTPUT_RULE,
            str(out),
            "adapter evidence must be outside the repository",
            "choose an external temporary directory",
        )
    return None


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="guardrails adapter go")
    actions = root.add_subparsers(dest="action")
    lint = actions.add_parser("lint")
    lint.add_argument("paths", nargs="*")
    tiers = actions.add_parser("tiers")
    tiers.add_argument("--out", type=Path, required=True)
    tiers.add_argument(
        "--tier", choices=sorted(TIER_GUARDS), action="append", default=[]
    )
    run = actions.add_parser("run")
    run.add_argument("--tier", choices=sorted(TIER_GUARDS), required=True)
    run.add_argument("--out", type=Path, required=True)
    evidence = actions.add_parser("guard-evidence")
    evidence.add_argument("report", type=Path)
    evidence.add_argument("--tier", choices=sorted(TIER_GUARDS), required=True)
    affected = actions.add_parser("affected")
    affected.add_argument("--base", required=True)
    affected.add_argument("--head", default="HEAD")
    actions.add_parser("mutation-tool")
    mutation = actions.add_parser("mutation-report")
    mutation.add_argument("--input", type=Path, required=True)
    mutation.add_argument("--out", type=Path, required=True)
    conformance = actions.add_parser("conformance")
    conformance.add_argument("--case", action="append", default=[])
    return root


def lint_action(
    repo: Path, profile: dict[str, Any], options: argparse.Namespace
) -> list[Finding]:
    from guardrails.commands.adapters.go.lint import lint

    return lint(repo, profile, options.paths)


def tiers_action(
    repo: Path, profile: dict[str, Any], options: argparse.Namespace
) -> list[Finding]:
    from guardrails.commands.adapters.go.tiers import list_tiers

    return list_tiers(repo, profile, options.tier, options.out)


def run_action(
    repo: Path, profile: dict[str, Any], options: argparse.Namespace
) -> list[Finding]:
    from guardrails.commands.adapters.go.evidence import report_finding
    from guardrails.commands.adapters.go.run import run_tier

    try:
        code = run_tier(repo, profile, options.tier, options.out)
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        return [report_finding(options.out, error)]
    if code:
        return [
            Finding(
                PROPAGATION,
                options.out.as_posix(),
                f"go test exited {code}; the run report names the failing tests",
                "fix the failing tests, then rerun the tier",
            )
        ]
    return []


def evidence_action(
    repo: Path, profile: dict[str, Any], options: argparse.Namespace
) -> list[Finding]:
    from guardrails.commands.adapters.go.evidence import guard_evidence

    return guard_evidence(options.report, options.tier)


def affected_action(
    repo: Path, profile: dict[str, Any], options: argparse.Namespace
) -> list[Finding]:
    from guardrails.commands.adapters.go.affected import affected_arguments

    return affected_arguments(repo, profile, options.base, options.head)


def mutation_tool_action(
    repo: Path, profile: dict[str, Any], options: argparse.Namespace
) -> list[Finding]:
    from guardrails.commands.adapters.go.mutation import TOOL, VERSION

    print(f"{TOOL}@{VERSION} (gremlins unleash --output <json>; tier t3)")
    return []


def mutation_report_action(
    repo: Path, profile: dict[str, Any], options: argparse.Namespace
) -> list[Finding]:
    from guardrails.commands.adapters.go.mutation import write_report

    return write_report(repo, options.input, options.out)


def conformance_action(
    repo: Path, profile: dict[str, Any], options: argparse.Namespace
) -> list[Finding]:
    from guardrails.commands.adapters.go.conformance import run_cases

    return run_cases(options.case, profile=profile)


Action = Callable[[Path, dict[str, Any], argparse.Namespace], list[Finding]]
ACTIONS: dict[str, Action] = {
    "lint": lint_action,
    "tiers": tiers_action,
    "run": run_action,
    "guard-evidence": evidence_action,
    "affected": affected_action,
    "mutation-tool": mutation_tool_action,
    "mutation-report": mutation_report_action,
}


def run(repo: Path, profile: dict[str, Any], argv: list[str]) -> list[Finding]:
    options = parser().parse_args(argv or ["lint"])
    if options.action in WRITING_ACTIONS:
        problem = output_finding(repo, options.out)
        if problem:
            return [problem]
    return ACTIONS.get(options.action, conformance_action)(repo, profile, options)
