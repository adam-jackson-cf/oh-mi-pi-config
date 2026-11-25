from __future__ import annotations

import argparse
import shlex
from collections.abc import Callable
from pathlib import Path
from typing import Any

from guardrails.commands.adapters.contract import TIER_GUARDS, output_finding
from guardrails.foundation.findings import Finding
from guardrails.subcommand.dispatch import SUBCOMMANDS

NAME = "bun_test"
WRITING_ACTIONS = frozenset({"tiers", "junit-args", "mutation-report"})
STRYKER_RUNNER = "command"
STRYKER_COMMAND = "bun test"


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="guardrails adapter bun_test", description=SUBCOMMANDS["adapter"].summary
    )
    actions = root.add_subparsers(dest="action")
    lint = actions.add_parser("lint")
    lint.add_argument("paths", nargs="*")
    tiers = actions.add_parser("tiers")
    tiers.add_argument("--out", type=Path, required=True)
    tiers.add_argument(
        "--tier", choices=sorted(TIER_GUARDS), action="append", default=[]
    )
    junit = actions.add_parser("junit-args")
    junit.add_argument("--out", required=True)
    junit.add_argument("--tier", choices=sorted(TIER_GUARDS), required=True)
    evidence = actions.add_parser("guard-evidence")
    evidence.add_argument("report", type=Path)
    evidence.add_argument("--tier", choices=sorted(TIER_GUARDS), required=True)
    affected = actions.add_parser("affected")
    affected.add_argument("--base", required=True)
    affected.add_argument("--head", default="HEAD")
    actions.add_parser("mutation-tool")
    mutation = actions.add_parser("mutation-report")
    mutation.add_argument("--input", type=Path)
    mutation.add_argument("--out", type=Path, required=True)
    conformance = actions.add_parser("conformance")
    conformance.add_argument("--case", action="append", default=[])
    return root


def lint_action(
    repo: Path, config: dict[str, Any], options: argparse.Namespace
) -> list[Finding]:
    from guardrails.commands.adapters.bun_test.lint import lint

    return lint(repo, config, options.paths)


def tiers_action(
    repo: Path, config: dict[str, Any], options: argparse.Namespace
) -> list[Finding]:
    from guardrails.commands.adapters.bun_test.tiers import list_tiers

    return list_tiers(repo, config, options.tier, options.out)


def junit_action(
    repo: Path, config: dict[str, Any], options: argparse.Namespace
) -> list[Finding]:
    from guardrails.commands.adapters.bun_test.dialect import junit_arguments

    print(shlex.join(junit_arguments(options.tier, options.out)))
    return []


def evidence_action(
    repo: Path, config: dict[str, Any], options: argparse.Namespace
) -> list[Finding]:
    from guardrails.commands.adapters.bun_test.dialect import guard_evidence

    return guard_evidence(options.report, options.tier)


def affected_action(
    repo: Path, config: dict[str, Any], options: argparse.Namespace
) -> list[Finding]:
    from guardrails.checker.affected import TierCommand, print_affected
    from guardrails.commands.adapters.bun_test.tiers import selector_arguments
    from guardrails.commands.adapters.javascript import cases

    def t2_arguments(selector: object, paths: list[str]) -> list[str]:
        return [*selector_arguments(str(selector)), *paths]

    command = TierCommand(NAME, cases.SUFFIXES, t2_arguments)
    return print_affected(repo, config, command, options.base, options.head)


def mutation_tool_action(
    repo: Path, config: dict[str, Any], options: argparse.Namespace
) -> list[Finding]:
    from guardrails.commands.adapters.javascript import stryker

    print(
        f"{stryker.TOOL}@{stryker.VERSION} (testRunner {STRYKER_RUNNER!r}, "
        f"commandRunner {STRYKER_COMMAND!r}, coverageAnalysis 'off'; tier t3; JSON reporter)"
    )
    return []


def mutation_report_action(
    repo: Path, config: dict[str, Any], options: argparse.Namespace
) -> list[Finding]:
    from guardrails.commands.adapters.javascript import stryker
    from guardrails.context.config.reader import checker_settings

    source = options.input or Path(
        checker_settings(config, "adapter_bun_test").get(
            "mutation_report", "reports/mutation/mutation.json"
        )
    )
    return stryker.write_report(repo, repo / source, options.out)


def conformance_action(
    repo: Path, config: dict[str, Any], options: argparse.Namespace
) -> list[Finding]:
    from guardrails.commands.adapters.bun_test.conformance import run_cases

    return run_cases(options.case, config=config)


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
        problem = output_finding(repo, options.out)
        if problem:
            return [problem]
    return ACTIONS.get(options.action, conformance_action)(repo, config, options)
