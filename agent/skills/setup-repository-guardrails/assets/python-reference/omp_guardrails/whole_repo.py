"""``omp-guardrails check``: the harness checks the agent rails run at commit, over the whole repo.

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
- ``exclude``: repository path patterns (as in ``structure.inventory``) for generated, vendored
  or fixture trees; findings at matching paths are dropped. Findings about the record itself
  (``record:``) are never dropped.
"""

from __future__ import annotations

from pathlib import Path

from omp_guardrails.config.profile import PROFILE_PATH
from omp_guardrails.findings import Finding
from omp_guardrails.structure.inventory import as_list, matches_any, settings

CHECKER = "check"
RULE = "PROFILE.complete"
RECORD_PREFIX = "record:"
DEFAULT_STEPS = (
    "profile-check",
    "structure",
    "naming",
    "python-quality",
    "python-fanout",
    "python-scalability",
    "surface-lint",
    "adapter lint",
)
# Every static whole-repo step a record may select.
STEPS = (*DEFAULT_STEPS, "path-check", "python-test-hygiene")


def configured_steps(profile: dict) -> tuple[list[str], list[Finding]]:
    declared = settings(profile, CHECKER).get("run")
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
        return [], [Finding(RULE, PROFILE_PATH, message, f"choose steps from {list(STEPS)}")]
    return list(dict.fromkeys(steps)), []


def run_step(repo: Path, profile: dict, step: str) -> list[Finding]:
    from omp_guardrails import cli

    name, *argv = step.split()
    try:
        modules = cli.module_names(name, profile)
    except cli.ProfileError as error:
        return [cli.profile_finding(error)]
    return cli.run_modules(modules, repo, profile, argv)


def excluded(finding: Finding, patterns: list[str]) -> bool:
    if finding.path.startswith(RECORD_PREFIX):
        return False
    return matches_any(finding.path, patterns)


def run(repo: Path, profile: dict, argv: list[str]) -> list[Finding]:
    if argv:
        return [
            Finding(
                RULE,
                "omp-guardrails check",
                f"check takes no paths or options, got {argv}",
                "run it over the whole repository; scope exclusions are record settings",
            )
        ]
    steps, findings = configured_steps(profile)
    for step in steps:
        findings.extend(run_step(repo, profile, step))
    patterns = as_list(settings(profile, CHECKER).get("exclude"))
    return [finding for finding in findings if not excluded(finding, patterns)]
