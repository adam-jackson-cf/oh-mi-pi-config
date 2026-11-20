from __future__ import annotations

from pathlib import Path
from typing import Any

from guardrails.commands.check.integrity import commit_findings
from guardrails.context.changes import DiffError, range_finding, staged_range
from guardrails.context.config.profile import PROFILE_PATH, ProfileError
from guardrails.foundation.findings import Finding
from guardrails.foundation.inventory import (
    InventoryError,
    as_list,
    repo_paths,
    settings,
)
from guardrails.toolkit.dispatch import (
    module_names,
    outside_exclusions,
    profile_finding,
    run_modules,
)

CHECKER = "check"
RULE = "PROFILE.complete"
DEFAULT_STEPS = (
    "profile-check",
    "python-quality",
    "python-fanout",
    "python-scalability",
    "surface-lint",
    "adapter lint",
)
STEPS = (*DEFAULT_STEPS, "python-test-hygiene")
UNOWNABLE = ("profile-check",)
STAGED_DIFFS = {
    "guardrails.commands.diffs.coverage_ratchet": "TEST.coverage-ratchet",
    "guardrails.commands.diffs.golden_record": "TEST.golden-change-record",
    "guardrails.commands.diffs.identity_artefacts": "TEST.identity-artefacts",
    "guardrails.commands.diffs.growth": "TEST.growth.justified",
}
STEP_SETTINGS = {
    "python": (
        "python-quality",
        "python-fanout",
        "python-scalability",
        "python-test-hygiene",
    ),
}


def record_finding(message: str, fix: str) -> Finding:
    return Finding(RULE, PROFILE_PATH, message, fix)


def selected_steps(declared: object) -> tuple[list[str], list[Finding]]:
    if declared is None:
        return list(DEFAULT_STEPS), []
    steps = as_list(declared)
    unknown = [step for step in steps if step not in STEPS]
    if not steps or unknown:
        message = (
            f"guardrails.check.run names unknown steps {unknown}"
            if unknown
            else "guardrails.check.run is empty"
        )
        return [], [record_finding(message, f"choose steps from {list(STEPS)}")]
    return list(dict.fromkeys(steps)), []


def owned_steps(profile: dict[str, Any]) -> tuple[dict[str, list[str]], list[Finding]]:
    declared = settings(profile, CHECKER).get("repo")
    if declared is None:
        return {}, []
    ownable = [
        step for step in (*STEPS, "naming", "structure") if step not in UNOWNABLE
    ]
    if not isinstance(declared, dict) or not declared:
        return {}, [
            record_finding(
                "guardrails.check.repo must map steps to repo checker files",
                'write {"<step>": ["<repo-relative checker path>", ...]}',
            )
        ]
    findings = [
        record_finding(
            f"guardrails.check.repo names {step!r}, which the repo cannot own",
            f"choose steps from {ownable}",
        )
        for step in declared
        if step not in ownable
    ]
    findings += [
        record_finding(
            f"guardrails.check.repo.{step} must list the repo checker files",
            "give a non-empty list of repo-relative checker paths",
        )
        for step, checkers in declared.items()
        if not isinstance(checkers, list)
        or not checkers
        or not all(isinstance(checker, str) and checker.strip() for checker in checkers)
    ]
    return (dict(declared), []) if not findings else ({}, findings)


def dead_settings(
    profile: dict[str, Any], configured: list[str], owned: dict[str, list[str]]
) -> list[Finding]:
    findings = []
    for section, readers in STEP_SETTINGS.items():
        if section not in (profile.get("guardrails") or {}):
            continue
        wanted = [step for step in readers if step in configured or step in owned]
        if wanted and all(step in owned for step in wanted):
            findings.append(
                record_finding(
                    f"guardrails.{section} configures only repo-owned steps {wanted}",
                    f"remove guardrails.{section}; the repo's checkers carry those rules",
                )
            )
    return findings


def configured_steps(profile: dict[str, Any]) -> tuple[list[str], list[Finding]]:
    declared = settings(profile, CHECKER).get("run")
    steps, findings = selected_steps(declared)
    owned, problems = owned_steps(profile)
    findings += problems
    clash = sorted(set(as_list(declared)) & set(owned))
    if clash:
        findings.append(
            record_finding(
                f"guardrails.check.run lists repo-owned steps {clash}",
                "list a step in run or in repo, not both",
            )
        )
    findings += dead_settings(profile, steps, owned)
    if findings:
        return [], findings
    return [step for step in steps if step not in owned], []


def missing_checkers(repo: Path, profile: dict[str, Any]) -> list[Finding]:
    owned, _ = owned_steps(profile)
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
        record_finding(
            f"guardrails.check.repo.{step} names {checker}, which is not in the repository",
            "name the checker file the repo runs through checks.commit",
        )
        for step, checkers in owned.items()
        for checker in checkers
        if checker not in listed
    ]


def staged_diff_findings(repo: Path, profile: dict[str, Any]) -> list[Finding]:
    try:
        diff = staged_range(repo)
    except DiffError as error:
        return [range_finding(rule, error) for rule in STAGED_DIFFS.values()]
    return run_modules(
        tuple(STAGED_DIFFS), repo, profile, ["--base", diff.base, "--head", diff.head]
    )


def run_step(repo: Path, profile: dict[str, Any], step: str) -> list[Finding]:
    name, *argv = step.split()
    if name == "python-quality":
        argv.extend(["--event", "staged"])
    try:
        modules = module_names(name, profile)
    except ProfileError as error:
        return [profile_finding(error)]
    return run_modules(modules, repo, profile, argv)


def run(repo: Path, profile: dict[str, Any], argv: list[str]) -> list[Finding]:
    if argv:
        return [
            Finding(
                RULE,
                "guardrails check",
                f"check takes no paths or options, got {argv}",
                "run it over the whole repository; scope exclusions are record settings",
            )
        ]
    steps, findings = configured_steps(profile)
    integrity_findings = commit_findings(repo, profile) + staged_diff_findings(
        repo, profile
    )
    if not findings:
        findings = missing_checkers(repo, profile)
    for step in [] if findings else steps:
        findings.extend(run_step(repo, profile, step))
    return integrity_findings + outside_exclusions(findings, profile)
