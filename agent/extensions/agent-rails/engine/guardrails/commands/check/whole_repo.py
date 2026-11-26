from __future__ import annotations

from pathlib import Path
from typing import Any

from guardrails.commands.check.integrity import commit_findings
from guardrails.context.changes import DiffError, range_finding, staged_range
from guardrails.context.config.reader import checker_settings, config_section
from guardrails.context.config.section_schema import ConfigValueError
from guardrails.context.config.validator import CHECK_STEPS
from guardrails.foundation.findings import Finding, config_label
from guardrails.foundation.inventory import InventoryError, as_list, repo_paths
from guardrails.subcommand.dispatch import (
    config_finding,
    module_names,
    outside_exclusions,
    run_modules,
)

CHECKER = "check"
RULE = "CONFIG.complete"
OPT_IN_STEPS = ("python-subprocess-timeouts",)
DEFAULT_STEPS = tuple(step for step in CHECK_STEPS if step not in OPT_IN_STEPS)
STEPS = CHECK_STEPS
STAGED_DIFFS = {
    "guardrails.commands.diff.coverage_ratchet": "TEST.coverage-ratchet",
    "guardrails.commands.diff.golden_record": "TEST.golden-change-record",
    "guardrails.commands.diff.identity_artefacts": "TEST.identity-artefacts",
    "guardrails.commands.diff.growth": "TEST.growth.justified",
}
STEP_SETTINGS = {
    "python": (
        "python-quality",
        "python-fanout",
        "python-scalability",
        "python-subprocess-timeouts",
    ),
}


def settings_finding(message: str, fix: str) -> Finding:
    return Finding(RULE, config_label("checkers"), message, fix)


def selected_steps(declared: object) -> tuple[list[str], list[Finding]]:
    if declared is None:
        return list(DEFAULT_STEPS), []
    steps = as_list(declared)
    unknown = [step for step in steps if step not in STEPS]
    if not steps or unknown:
        message = (
            f"checkers.check.run names unknown steps {unknown}"
            if unknown
            else "checkers.check.run is empty"
        )
        return [], [settings_finding(message, f"choose steps from {list(STEPS)}")]
    return list(dict.fromkeys(steps)), []


def owned_steps(config: dict[str, Any]) -> tuple[dict[str, list[str]], list[Finding]]:
    declared = checker_settings(config, CHECKER).get("repo")
    if declared is None:
        return {}, []
    ownable = [*STEPS, "naming", "structure"]
    if not isinstance(declared, dict) or not declared:
        return {}, [
            settings_finding(
                "checkers.check.repo must map steps to repo checker files",
                'write {"<step>": ["<repo-relative checker path>", ...]}',
            )
        ]
    findings = [
        settings_finding(
            f"checkers.check.repo names {step!r}, which the repo cannot own",
            f"choose steps from {ownable}",
        )
        for step in declared
        if step not in ownable
    ]
    findings += [
        settings_finding(
            f"checkers.check.repo.{step} must list the repo checker files",
            "give a non-empty list of repo-relative checker paths",
        )
        for step, checkers in declared.items()
        if not isinstance(checkers, list)
        or not checkers
        or not all(isinstance(checker, str) and checker.strip() for checker in checkers)
    ]
    return (dict(declared), []) if not findings else ({}, findings)


def dead_settings(
    config: dict[str, Any], configured: list[str], owned: dict[str, list[str]]
) -> list[Finding]:
    findings = []
    for section, readers in STEP_SETTINGS.items():
        if section not in config_section(config, "checkers"):
            continue
        wanted = [step for step in readers if step in configured or step in owned]
        if wanted and all(step in owned for step in wanted):
            findings.append(
                settings_finding(
                    f"checkers.{section} configures only repo-owned steps {wanted}",
                    f"remove checkers.{section}; the repo's checkers carry those rules",
                )
            )
    return findings


def configured_steps(config: dict[str, Any]) -> tuple[list[str], list[Finding]]:
    declared = checker_settings(config, CHECKER).get("run")
    steps, findings = selected_steps(declared)
    owned, problems = owned_steps(config)
    findings += problems
    clash = sorted(set(as_list(declared)) & set(owned))
    if clash:
        findings.append(
            settings_finding(
                f"checkers.check.run lists repo-owned steps {clash}",
                "list a step in run or in repo, not both",
            )
        )
    findings += dead_settings(config, steps, owned)
    if findings:
        return [], findings
    return [step for step in steps if step not in owned], []


def missing_checkers(repo: Path, config: dict[str, Any]) -> list[Finding]:
    owned, _ = owned_steps(config)
    if not owned:
        return []
    try:
        listed = set(repo_paths(repo))
    except InventoryError as error:
        return [
            Finding(
                RULE, "guardrails check", str(error), "run check inside a git checkout"
            )
        ]
    return [
        settings_finding(
            f"checkers.check.repo.{step} names {checker}, which is not in the repository",
            "name the checker file the repo runs through commands.commit",
        )
        for step, checkers in owned.items()
        for checker in checkers
        if checker not in listed
    ]


def staged_diff_findings(repo: Path, config: dict[str, Any]) -> list[Finding]:
    try:
        diff = staged_range(repo)
    except DiffError as error:
        return [range_finding(rule, error) for rule in STAGED_DIFFS.values()]
    return run_modules(
        tuple(STAGED_DIFFS), repo, config, ["--base", diff.base, "--head", diff.head]
    )


def run_step(repo: Path, config: dict[str, Any], step: str) -> list[Finding]:
    name, *argv = step.split()
    if name == "python-quality":
        argv.extend(["--event", "staged"])
    try:
        modules = module_names(name, config)
    except ConfigValueError as error:
        return [config_finding(error)]
    return run_modules(modules, repo, config, argv)


def run(repo: Path, config: dict[str, Any], argv: list[str]) -> list[Finding]:
    if argv:
        return [
            Finding(
                RULE,
                "guardrails check",
                f"check takes no paths or options, got {argv}",
                "run it over the whole repository; scope exclusions are config settings",
            )
        ]
    steps, findings = configured_steps(config)
    integrity_findings = commit_findings(repo, config) + staged_diff_findings(
        repo, config
    )
    if not findings:
        findings = missing_checkers(repo, config)
    for step in [] if findings else steps:
        findings.extend(run_step(repo, config, step))
    return integrity_findings + outside_exclusions(findings, config)
