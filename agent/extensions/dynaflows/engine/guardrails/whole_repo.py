"""``guardrails check``: the harness checks the agent rails run at commit, over the whole repo.

The rails first run the repo's own commit command (the record's ``checks.commit``), then this
command from the harness with ``--repo <worktree> --record <record dir>``. Every step inspects
the whole checkout: no baselines and no diff scoping, so drift from contributors who do not use
the harness blocks too. Any finding blocks the commit until it is fixed. The command reads
repository files and writes nothing anywhere.

Steps are static: they read tracked files and need neither the repository's environment nor its
test run. The default steps (``DEFAULT_STEPS``) are ``profile-check``, ``structure``, ``naming``,
``python-quality``, ``python-fanout``, ``python-scalability``, ``surface-lint`` and the profile
adapter's ``lint``. Diff, run-report, collection, mutation and lever checks need evidence from a
test run or a commit range, so they are not steps.

Settings (``guardrails.check`` in the record's ``profile.md``):

- ``run``: the steps to run, replacing the default, chosen from ``STEPS`` (a step is a
  subcommand name, or ``"adapter lint"``). Every step that is configured runs in full.
- ``repo``: the steps the repository's own checkers already enforce, each mapped to the list of
  checker files that between them enforce every rule the step reports, for example
  ``{"python-quality": ["scripts/checks/check-python-quality.py"]}``. The repo's checkers run at
  commit through ``checks.commit``, so ``check`` leaves the step out. A step the repo owns cannot
  also be in ``run``, every checker file must be in the repository's file list, and settings
  sections read only by owned steps (``STEP_SETTINGS``) are rejected as dead.
  ``profile-check`` checks the record itself and ``path-check`` is always harness-owned;
  neither can be owned by the repository.
- ``exclude``: repository path patterns (as in ``structure.inventory``) for generated, vendored
  or fixture trees; findings at matching paths are dropped. Findings about the record itself
  (``record:``) are never dropped.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from guardrails.config.profile import PROFILE_PATH, ProfileError
from guardrails.findings import Finding
from guardrails.structure.inventory import (
    InventoryError,
    as_list,
    matches_any,
    repo_paths,
    settings,
)

CHECKER = "check"
RULE = "PROFILE.complete"
RECORD_PREFIX = "record:"
DEFAULT_STEPS = (
    "profile-check",
    "python-quality",
    "python-fanout",
    "python-scalability",
    "surface-lint",
    "adapter lint",
)
# Every static whole-repo step a record may select.
STEPS = (*DEFAULT_STEPS, "python-test-hygiene")
UNOWNABLE = ("profile-check",)
# Settings sections that only check steps read; other sections also serve diff checks or prove().
STEP_SETTINGS = {
    "python": ("python-quality", "python-fanout", "python-scalability", "python-test-hygiene"),
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
    """``guardrails.check.repo``: step -> the repo checker files that enforce it."""
    declared = settings(profile, CHECKER).get("repo")
    if declared is None:
        return {}, []
    ownable = [step for step in (*STEPS, "naming", "structure") if step not in UNOWNABLE]
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


def dead_settings(profile: dict[str, Any], configured: list[str], owned: dict[str, list[str]]) -> list[Finding]:
    """Settings sections whose configured steps are all owned by the repo."""
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
    """The steps ``check`` runs: ``run`` (or the default) without the repo-owned steps."""
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
        return [Finding(RULE, "guardrails check", str(error), "run check inside a git checkout")]
    return [
        record_finding(
            f"guardrails.check.repo.{step} names {checker}, which is not in the repository",
            "name the checker file the repo runs through checks.commit",
        )
        for step, checkers in owned.items()
        for checker in checkers
        if checker not in listed
    ]


def run_step(repo: Path, profile: dict[str, Any], step: str) -> list[Finding]:
    from guardrails import cli

    name, *argv = step.split()
    if name == "python-quality":
        argv.extend(["--event", "staged"])
    try:
        modules = cli.module_names(name, profile)
    except ProfileError as error:
        return [cli.profile_finding(error)]
    return cli.run_modules(modules, repo, profile, argv)


def excluded(finding: Finding, patterns: list[str]) -> bool:
    if finding.path.startswith(RECORD_PREFIX):
        return False
    return matches_any(finding.path, patterns)


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
    from guardrails.diffs.integrity import commit_findings

    integrity_findings = commit_findings(repo, profile)
    if not findings:
        findings = missing_checkers(repo, profile)
    for step in [] if findings else steps:
        findings.extend(run_step(repo, profile, step))
    patterns = as_list(settings(profile, CHECKER).get("exclude"))
    return integrity_findings + [finding for finding in findings if not excluded(finding, patterns)]
