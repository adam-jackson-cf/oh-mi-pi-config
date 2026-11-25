from __future__ import annotations

import argparse
import shlex
from collections.abc import Callable
from pathlib import Path
from typing import Any

from guardrails.commands.adapters import contract
from guardrails.commands.adapters.pytest.dialect import DIALECT
from guardrails.commands.adapters.pytest.invocation import tier_arguments
from guardrails.foundation.findings import Finding
from guardrails.subcommand.dispatch import SUBCOMMANDS

NAME = DIALECT.name
SUFFIXES = (".py",)
MUTATION_TIER = "t3"
WRITING_ACTIONS = frozenset({"tiers", "junit-args", "mutation-report"})


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="guardrails adapter pytest", description=SUBCOMMANDS["adapter"].summary
    )
    actions = root.add_subparsers(dest="action")
    lint = actions.add_parser("lint")
    lint.add_argument("paths", nargs="*")
    tiers = actions.add_parser("tiers")
    tiers.add_argument("--out", required=True, type=Path)
    tiers.add_argument(
        "--tier", action="append", choices=sorted(contract.TIER_GUARDS), default=[]
    )
    junit_args = actions.add_parser("junit-args")
    junit_args.add_argument(
        "--tier", required=True, choices=sorted(contract.TIER_GUARDS)
    )
    junit_args.add_argument("--out", required=True)
    evidence = actions.add_parser("guard-evidence")
    evidence.add_argument("report", type=Path)
    evidence.add_argument("--tier", required=True, choices=sorted(contract.TIER_GUARDS))
    affected = actions.add_parser("affected")
    affected.add_argument("--base", required=True)
    affected.add_argument("--head", default="HEAD")
    actions.add_parser("mutation-tool")
    mutation_report = actions.add_parser("mutation-report")
    mutation_report.add_argument("--out", required=True, type=Path)
    conformance = actions.add_parser("conformance")
    conformance.add_argument("--case", action="append", default=[])
    return root


def t2_arguments(selector: object, paths: list[str]) -> list[str]:
    from guardrails.commands.adapters.pytest.tiers import selector_arguments

    chosen = (
        selector_arguments(selector) if isinstance(selector, str) and selector else []
    )
    return [*chosen, "--", *paths]


def affected_arguments(
    repo: Path, config: dict[str, Any], base: str, head: str
) -> list[Finding]:
    from guardrails.checker.affected import TierCommand, print_affected

    command = TierCommand(NAME, SUFFIXES, t2_arguments)
    return print_affected(repo, config, command, base, head)


def guard_evidence(report: Path, tier: str) -> list[Finding]:
    rerun = f"run tier {tier} with: {shlex.join(tier_arguments(tier, '<report>'))}"
    return contract.guard_evidence(report, tier, DIALECT, rerun)


def lint_action(
    repo: Path, config: dict[str, Any], options: argparse.Namespace
) -> list[Finding]:
    from guardrails.commands.adapters.pytest.lint import lint

    return lint(repo, config, options.paths)


def tiers_action(
    repo: Path, config: dict[str, Any], options: argparse.Namespace
) -> list[Finding]:
    from guardrails.commands.adapters.pytest.tiers import list_tiers

    return list_tiers(repo, config, options.tier, options.out)


def junit_action(
    repo: Path, config: dict[str, Any], options: argparse.Namespace
) -> list[Finding]:
    print(shlex.join(tier_arguments(options.tier, options.out)))
    return []


def evidence_action(
    repo: Path, config: dict[str, Any], options: argparse.Namespace
) -> list[Finding]:
    return guard_evidence(options.report, options.tier)


def affected_action(
    repo: Path, config: dict[str, Any], options: argparse.Namespace
) -> list[Finding]:
    return affected_arguments(repo, config, options.base, options.head)


def mutation_tool_action(
    repo: Path, config: dict[str, Any], options: argparse.Namespace
) -> list[Finding]:
    from guardrails.commands.adapters.pytest.mutation import TOOL

    print(f"{TOOL} (tier {MUTATION_TIER}, scoped to changed code; never run in CI)")
    return []


def mutation_report_action(
    repo: Path, config: dict[str, Any], options: argparse.Namespace
) -> list[Finding]:
    from guardrails.commands.adapters.pytest.mutation import write_report

    return write_report(repo, options.out)


def conformance_action(
    repo: Path, config: dict[str, Any], options: argparse.Namespace
) -> list[Finding]:
    from guardrails.commands.adapters.pytest.conformance import run_cases

    return run_cases(options.case)


Action = Callable[[Path, dict[str, Any], argparse.Namespace], list[Finding]]
ACTIONS: dict[str, Action] = {
    "lint": lint_action,
    "tiers": tiers_action,
    "junit-args": junit_action,
    "guard-evidence": evidence_action,
    "affected": affected_action,
    "mutation-tool": mutation_tool_action,
    "mutation-report": mutation_report_action,
}


def run(repo: Path, config: dict[str, Any], argv: list[str]) -> list[Finding]:
    options = parser().parse_args(argv or ["lint"])
    if options.action in WRITING_ACTIONS:
        problem = contract.output_finding(repo, options.out)
        if problem:
            return [problem]
    return ACTIONS.get(options.action, conformance_action)(repo, config, options)
