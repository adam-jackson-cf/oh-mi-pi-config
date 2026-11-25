from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from guardrails.context.config import adapter_declarations
from guardrails.context.config.section_schema import ConfigValueError
from guardrails.foundation.findings import CONFIG_LABEL, Finding, config_label
from guardrails.foundation.inventory import (
    as_list,
    config_section,
    matches_any,
    settings,
)


@dataclass(frozen=True)
class Subcommand:
    modules: tuple[str, ...]
    summary: str
    needs_config: bool = True
    prints_data: bool = False
    honours_exclusions: bool = False


SUBCOMMANDS: dict[str, Subcommand] = {
    "check": Subcommand(
        ("guardrails.commands.check.whole_repo",),
        "the harness commit checks: whole-repo steps, then CODE.* and the TEST diff rules "
        "over the staged changes (any finding blocks)",
    ),
    "init": Subcommand(
        ("guardrails.commands.init.command",),
        "draft the setup-owned config sections (commands, checkers, structure, "
        "houseStyle, tests) into --out FILE outside the repo",
        needs_config=False,
    ),
    "path-check": Subcommand(
        ("guardrails.commands.structure_naming.path_check",),
        "GATE.path-check over changed or all paths",
    ),
    "structure": Subcommand(
        ("guardrails.commands.structure_naming.layout",),
        "STRUCT.* repository layout allowlists",
    ),
    "rename-residue": Subcommand(
        ("guardrails.commands.structure_naming.rename_residue",),
        "RENAME.residue over --base SHA --head SHA at PR time",
    ),
    "prefix-folder": Subcommand(
        ("guardrails.commands.structure_naming.prefix_folder",),
        "STRUCT.prefix-folder over --base SHA --head SHA at PR time",
    ),
    "responsibility-overlap": Subcommand(
        ("guardrails.commands.structure_naming.responsibility_overlap",),
        "STRUCT.overlapping-responsibilities over --base SHA --head SHA at PR time",
    ),
    "name-pairs": Subcommand(
        ("guardrails.commands.structure_naming.name_pair.candidates",),
        "structure-naming name-pair candidates JSON for --base SHA --head SHA",
        prints_data=True,
    ),
    "kind-candidates": Subcommand(
        ("guardrails.commands.structure_naming.kind_candidates",),
        "structure-naming kind-folder file candidates JSON for --base SHA --head SHA",
        prints_data=True,
    ),
    "consumer-candidates": Subcommand(
        ("guardrails.commands.structure_naming.consumer_candidates",),
        "structure-naming consumer-file candidates JSON for --base SHA --head SHA",
        prints_data=True,
    ),
    "naming": Subcommand(
        ("guardrails.commands.structure_naming.naming",),
        "NAME.conventions and NAME.test-files",
    ),
    "python-quality": Subcommand(
        ("guardrails.commands.python_policy.quality",),
        "PYQ structure and class-shape rules",
        honours_exclusions=True,
    ),
    "python-fanout": Subcommand(
        ("guardrails.commands.python_policy.fanout",),
        "FANOUT.* import fan-out limits",
        honours_exclusions=True,
    ),
    "python-scalability": Subcommand(
        ("guardrails.commands.python_policy.scalability",),
        "PYS scalability rules",
        honours_exclusions=True,
    ),
    "python-test-hygiene": Subcommand(
        ("guardrails.commands.python_policy.subprocess_timeouts",),
        "TEST.subprocess-timeouts: bounded process calls in Python tests",
        honours_exclusions=True,
    ),
    "toolchain-parity": Subcommand(
        ("guardrails.commands.catalogue.toolchain_parity",), "PARITY.* toolchain parity"
    ),
    "report-check": Subcommand(
        ("guardrails.commands.test_evidence.propagation",),
        "TEST.failure-propagation over the normalised run report (commit and CI gates)",
    ),
    "budget-check": Subcommand(
        ("guardrails.commands.test_evidence.budgets",),
        "TEST.budget.* over the fast-tier duration report (local commit gate only)",
    ),
    "collection-check": Subcommand(
        ("guardrails.toolkit.test_report.collection",),
        "TEST.tier-selectors over tier collections",
    ),
    "diff-check": Subcommand(
        (
            "guardrails.commands.diffs.coverage_ratchet",
            "guardrails.commands.diffs.golden_record",
            "guardrails.commands.diffs.identity_artefacts",
            "guardrails.commands.diffs.timeouts",
            "guardrails.commands.diffs.growth",
            "guardrails.commands.check.integrity",
        ),
        "TEST.coverage-ratchet, TEST.golden-change-record, TEST.identity-artefacts, "
        "TEST.no-evasion timeout increases, TEST.growth.justified, TEST.duplicate-path and CODE.* integrity",
    ),
    "affected": Subcommand(
        ("guardrails.commands.affected",),
        "affected T2 test files for a diff range, from the config's ownership areas",
        prints_data=True,
    ),
    "mutation-check": Subcommand(
        ("guardrails.commands.test_evidence.mutation",),
        "TEST.mutation-sensitivity over a normalised mutation report and the diff (T3)",
    ),
    "guard-run": Subcommand(
        ("guardrails.commands.environment.guarded_run",),
        "TEST.user-data-guard and TEST.fast-tier-isolation around a test command",
    ),
    "surface-lint": Subcommand(
        (
            "guardrails.commands.surface.feature_map",
            "guardrails.commands.surface.journeys",
        ),
        "SURFACE.feature-map and SURFACE.journeys",
        honours_exclusions=True,
    ),
    "lever-selftest": Subcommand(
        ("guardrails.commands.surface.lever_selftest",), "SURFACE.lever-isolation"
    ),
    "adapter": Subcommand(
        (),
        "run one of the config's test-framework adapters: adapter <name> <action>",
        prints_data=True,
    ),
    "config-check": Subcommand(
        ("guardrails.commands.config.command",),
        "validate the repo's .agentrails/config.json, a config file or one section; "
        "one JSON line (config-check --help)",
        needs_config=False,
        prints_data=True,
    ),
    "shadow-review": Subcommand(
        ("guardrails.commands.shadow_review.command",),
        "export, import and report review labels for logged judgment decisions; "
        "write Jev Inspector sessions (shadow-review --help)",
        needs_config=False,
        prints_data=True,
    ),
}


EXCLUDED_ADAPTER_ACTIONS = frozenset({"lint"})


def outside_exclusions(
    findings: list[Finding], config: dict[str, Any]
) -> list[Finding]:
    patterns = as_list(settings(config, "check").get("exclude"))
    return [
        finding
        for finding in findings
        if finding.path.startswith(CONFIG_LABEL)
        or not matches_any(finding.path, patterns)
    ]


def honours_exclusions(name: str, argv: list[str]) -> bool:
    if name == "adapter":
        return (argv[0] if argv else "lint") in EXCLUDED_ADAPTER_ACTIONS
    return SUBCOMMANDS[name].honours_exclusions


def module_names(name: str, config: dict[str, Any]) -> tuple[str, ...]:
    if name != "adapter":
        return SUBCOMMANDS[name].modules
    return tuple(
        adapter_declarations.module_name(item)
        for item in adapter_declarations.names(config)
    )


def adapter_module(config: dict[str, Any], argv: list[str]) -> tuple[str, list[str]]:
    declared = adapter_declarations.names(config)
    if not argv or argv[0] not in declared:
        given = repr(argv[0]) if argv else "no adapter"
        raise ConfigValueError(
            f"adapter takes one of the config's tests.adapters {declared} first, got {given}"
        )
    return adapter_declarations.module_name(argv[0]), argv[1:]


def checker_module(module_name: str) -> ModuleType:
    return importlib.import_module(module_name)


def switched_off(config: dict[str, Any]) -> frozenset[str]:
    """Code-check rules the repo's gates.rules turns off; a rule not listed is on."""
    rules = config_section(config_section(config, "gates"), "rules")
    return frozenset(rule for rule, enabled in rules.items() if enabled is False)


def run_modules(
    modules: tuple[str, ...], repo: Path, config: dict[str, Any], argv: list[str]
) -> list[Finding]:
    findings: list[Finding] = []
    for module_name in modules:
        module = checker_module(module_name)
        findings.extend(module.run(repo, config, argv))
    off = switched_off(config)
    return [finding for finding in findings if finding.rule_id not in off]


def config_finding(error: ConfigValueError) -> Finding:
    return Finding(
        "CONFIG.complete",
        config_label("tests"),
        str(error),
        "record tests.adapters in .agentrails/config.json (guardrails init drafts the "
        "tests section)",
    )
