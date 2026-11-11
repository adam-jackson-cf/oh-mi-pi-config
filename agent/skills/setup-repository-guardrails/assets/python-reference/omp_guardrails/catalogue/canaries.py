"""Rule canaries: run one real check against planted fixture bytes in a disposable repository.

A fixture is a JSON file holding inputs, never expected answers:

```json
{
  "rule": "NAME.conventions",
  "expect": "fail",
  "check": {"subcommand": "naming", "argv": []},
  "profile": {"guardrails": {"naming": {}}},
  "base": {"src/app/ok.py": "..."},
  "files": {"src/app/BadName.py": "..."},
  "bytes": {"src/app/blob.bin": [0, 255]},
  "delete": ["src/app/ok.py"],
  "copy": ["pyproject.toml"]
}
```

The harness creates a git repository, commits ``base`` (if any), copies the ``copy`` files from
the real repository (configuration the check reads), writes ``files`` and ``bytes``, removes
``delete`` and stages the result, so diff-based checks see base versus head.

The check is one of:

- ``{"subcommand": "<omp-guardrails subcommand>", "argv": [...]}``: the subcommand's checker
  modules run in-process against the scratch repository. The profile is the repository's own,
  deep-merged with the fixture's ``profile`` (``"profile_base": "empty"`` starts from ``{}``).
  It is also written to the scratch ``.omp/test-profile.md``. Only findings for ``rule`` count.
- ``{"command": [argv ...], "timeout_s": N}``: a native tool (ruff, mypy, a linter) runs in the
  scratch repository; ``{python}`` in the argv is replaced with the running interpreter. A
  non-zero exit is a finding for ``rule``.

Modes:

- ``omp-guardrails canaries FIXTURE [...]`` reports each check's findings unchanged, so a
  compliant fixture exits 0 and a violating one exits 1 with the rule ID in the output. This is
  the ``canary.command`` form ``omp-guardrails rule-coverage`` executes for a repo checker.
- ``omp-guardrails canaries`` with no fixtures runs every fixture matched by
  ``guardrails.canaries.fixtures`` (repo globs) and compares each result with its ``expect``
  (``fail`` or ``pass``), reporting only mismatches.
"""

from __future__ import annotations

import copy
import importlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from omp_guardrails.catalogue.scratch import (
    ScratchError,
    init_repository,
    inside,
    output_tail,
    run_bounded,
    stage_all,
    write_files,
)
from omp_guardrails.config.profile import PROFILE_PATH, ProfileError
from omp_guardrails.findings import Finding
from omp_guardrails.structure.inventory import as_list, settings

HARNESS_RULE = "CANARY.fixture"
GIT_TIMEOUT_S = 60
DEFAULT_COMMAND_TIMEOUT_S = 120


class FixtureError(Exception):
    """A canary fixture is malformed or cannot be materialised."""


HARNESS_ERRORS = (
    FixtureError,
    ScratchError,
    ProfileError,
    OSError,
    ValueError,
    TypeError,
    subprocess.TimeoutExpired,
)


def deep_merge(base: dict, overrides: dict) -> dict:
    merged = copy.deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def load_fixture(path: Path) -> dict:
    try:
        fixture = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise FixtureError(f"cannot read fixture: {error}") from error
    if not isinstance(fixture, dict) or not isinstance(fixture.get("rule"), str):
        raise FixtureError("fixture must be an object naming its rule")
    check = fixture.get("check")
    if not isinstance(check, dict) or ("subcommand" in check) == ("command" in check):
        raise FixtureError("check must name exactly one of subcommand or command")
    if fixture.get("expect", "fail") not in {"fail", "pass"}:
        raise FixtureError("expect must be 'fail' or 'pass'")
    return fixture


def materialise(repo: Path, scratch: Path, fixture: dict, profile: dict) -> None:
    init_repository(scratch, timeout=GIT_TIMEOUT_S)
    base = fixture.get("base")
    if isinstance(base, dict) and base:
        write_files(scratch, base)
        stage_all(scratch, timeout=GIT_TIMEOUT_S)
        commit = ["git", "-C", str(scratch), "commit", "-q", "--no-verify", "-m", "base"]
        result = run_bounded(commit, scratch, timeout=GIT_TIMEOUT_S)
        if result.returncode:
            raise FixtureError(f"cannot commit the fixture base: {output_tail(result)}")
    for relative in as_list(fixture.get("copy")):
        if not (repo / relative).is_file():
            raise FixtureError(f"copy names a missing repository file: {relative}")
        target = inside(scratch, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo / relative, target)
    write_files(scratch, fixture.get("files") or {})
    for relative, octets in (fixture.get("bytes") or {}).items():
        target = inside(scratch, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(bytes(octets))
    for relative in as_list(fixture.get("delete")):
        inside(scratch, relative).unlink(missing_ok=True)
    front_matter = json.dumps(profile, indent=2, sort_keys=True)
    write_files(scratch, {PROFILE_PATH: f"---\n{front_matter}\n---\n"})
    stage_all(scratch, timeout=GIT_TIMEOUT_S)


def fixture_profile(profile: dict, fixture: dict) -> dict:
    base = {} if fixture.get("profile_base") == "empty" else profile
    overrides = fixture.get("profile")
    return deep_merge(base, overrides if isinstance(overrides, dict) else {})


def subcommand_findings(scratch: Path, profile: dict, rule: str, check: dict) -> list[Finding]:
    from omp_guardrails import cli

    name = check["subcommand"]
    if name not in cli.SUBCOMMANDS or name == "canaries":
        raise FixtureError(f"unknown or recursive subcommand {name!r}")
    findings: list[Finding] = []
    for module_name in cli.module_names(name, profile):
        module = importlib.import_module(module_name)
        findings.extend(module.run(scratch, profile, as_list(check.get("argv"))))
    return [finding for finding in findings if finding.rule_id == rule]


def command_findings(scratch: Path, rule: str, check: dict, label: str) -> list[Finding]:
    argv = [part.replace("{python}", sys.executable) for part in as_list(check["command"])]
    if not argv:
        raise FixtureError("command must be a non-empty argv")
    timeout = check.get("timeout_s", DEFAULT_COMMAND_TIMEOUT_S)
    result = run_bounded(argv, scratch, timeout=timeout)
    if result.returncode == 0:
        return []
    return [
        Finding(
            rule,
            label,
            f"{argv[0]} exited {result.returncode}: {output_tail(result)}",
            "the planted violation was rejected by the native tool",
        )
    ]


def run_fixture(repo: Path, profile: dict, path: Path, label: str) -> tuple[dict, list[Finding]]:
    fixture = load_fixture(path)
    merged = fixture_profile(profile, fixture)
    with tempfile.TemporaryDirectory(prefix="omp-guardrails-canary-") as directory:
        scratch = Path(directory).resolve()
        materialise(repo, scratch, fixture, merged)
        check = fixture["check"]
        if "subcommand" in check:
            return fixture, subcommand_findings(scratch, merged, fixture["rule"], check)
        return fixture, command_findings(scratch, fixture["rule"], check, label)


def harness_finding(label: str, error: Exception) -> Finding:
    return Finding(HARNESS_RULE, label, str(error), "repair the canary fixture")


def fixture_path(repo: Path, raw: str) -> tuple[Path, str]:
    path = Path(raw)
    resolved = path if path.is_absolute() else repo / path
    try:
        label = resolved.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        label = resolved.name
    return resolved, label


def explicit_run(repo: Path, profile: dict, argv: list[str]) -> list[Finding]:
    findings = []
    for raw in argv:
        path, label = fixture_path(repo, raw)
        try:
            findings.extend(run_fixture(repo, profile, path, label)[1])
        except HARNESS_ERRORS as error:
            findings.append(harness_finding(label, error))
    return findings


def configured_fixtures(repo: Path, profile: dict) -> list[str]:
    patterns = as_list(settings(profile, "canaries").get("fixtures"))
    found = {
        path.relative_to(repo).as_posix() for pattern in patterns for path in repo.glob(pattern)
    }
    return sorted(found)


def expectation_run(repo: Path, profile: dict) -> list[Finding]:
    paths = configured_fixtures(repo, profile)
    if not paths:
        return [
            Finding(
                HARNESS_RULE,
                PROFILE_PATH,
                "no canary fixtures matched guardrails.canaries.fixtures",
                "declare fixture globs or pass fixture paths",
            )
        ]
    findings = []
    for label in paths:
        try:
            fixture, result = run_fixture(repo, profile, repo / label, label)
        except HARNESS_ERRORS as error:
            findings.append(harness_finding(label, error))
            continue
        expect_fail = fixture.get("expect", "fail") == "fail"
        if expect_fail and not result:
            findings.append(
                Finding(
                    fixture["rule"],
                    label,
                    "violating canary passed",
                    "the check no longer rejects the planted violation; restore it",
                )
            )
        elif not expect_fail and result:
            findings.append(
                Finding(
                    fixture["rule"],
                    label,
                    f"compliant canary failed: {result[0].message}",
                    "the check rejects compliant input; narrow it",
                )
            )
    return findings


def run(repo: Path, profile: dict, argv: list[str]) -> list[Finding]:
    if argv:
        return explicit_run(repo, profile, argv)
    return expectation_run(repo, profile)
