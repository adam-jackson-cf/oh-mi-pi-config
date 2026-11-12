"""Discover a repository's own controls so init can draft its record for owner confirmation.

Init reads, and never writes, the repository:

- make targets in ``Makefile``, ``GNUmakefile`` or ``makefile`` at the root;
- hook managers: ``.pre-commit-config.yaml`` (with its hook ids), ``.husky/pre-commit`` and
  ``lefthook.yml``;
- CI jobs in ``.github/workflows/*.yml`` and ``*.yaml``: each job's id and display name (the
  name GitHub reports as its check run);
- the default branch's required status checks, through ``gh`` (rulesets and classic branch
  protection). ``--offline`` skips ``gh``; any ``gh`` failure is reported, never guessed.

The YAML files are scanned line by line with the standard library, so only the plain block
forms GitHub and pre-commit document are recognised; the report shows what was found, and the
owner confirms the draft.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from omp_guardrails.child_env import repo_environment

MAKEFILES = ("GNUmakefile", "makefile", "Makefile")
MAKE_TARGET = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)\s*:(?!=)")
HOOK_ID = re.compile(r"^\s*-\s*id:\s*['\"]?([^'\"\s#]+)")
JOB_KEY = re.compile(r"^(\s+)([A-Za-z0-9_-]+):\s*(?:#.*)?$")
JOB_NAME = re.compile(r"^\s+name:\s*['\"]?(.+?)['\"]?\s*(?:#.*)?$")
GH_TIMEOUT_S = 30


@dataclass(frozen=True)
class CiJob:
    workflow: str
    job_id: str
    name: str


@dataclass
class Discovery:
    make_targets: list[str] = field(default_factory=list)
    hook_managers: list[str] = field(default_factory=list)
    pre_commit_hooks: list[str] = field(default_factory=list)
    ci_jobs: list[CiJob] = field(default_factory=list)
    required_checks: list[str] | None = None
    notes: list[str] = field(default_factory=list)

    def report(self) -> list[str]:
        jobs = ", ".join(f"{job.name} ({job.workflow})" for job in self.ci_jobs) or "none"
        required = (
            "unknown" if self.required_checks is None else ", ".join(self.required_checks) or "none"
        )
        lines = [
            f"discovered: make targets: {', '.join(self.make_targets) or 'none'}",
            f"discovered: hook managers: {', '.join(self.hook_managers) or 'none'}",
            f"discovered: CI jobs: {jobs}",
            f"discovered: required checks on the default branch: {required}",
        ]
        if self.pre_commit_hooks:
            lines.insert(2, f"discovered: pre-commit hooks: {', '.join(self.pre_commit_hooks)}")
        return lines + [f"discovered: {note}" for note in self.notes]


def read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def make_targets(repo: Path) -> list[str]:
    for name in MAKEFILES:
        text = read(repo / name)
        if text is not None:
            found = (MAKE_TARGET.match(line) for line in text.splitlines())
            names = {match.group(1) for match in found if match}
            return sorted(name for name in names if not name.startswith("."))
    return []


def hook_managers(repo: Path) -> tuple[list[str], list[str]]:
    managers, hooks = [], []
    config = read(repo / ".pre-commit-config.yaml")
    if config is not None:
        managers.append("pre-commit")
        found = (HOOK_ID.match(line) for line in config.splitlines())
        hooks = [match.group(1) for match in found if match]
    if (repo / ".husky/pre-commit").is_file():
        managers.append("husky")
    if (repo / "lefthook.yml").is_file() or (repo / "lefthook.yaml").is_file():
        managers.append("lefthook")
    return managers, hooks


def workflow_jobs(workflow: str, text: str) -> list[CiJob]:
    """Jobs under the top-level ``jobs:`` key, with the ``name:`` each one declares."""
    jobs: list[CiJob] = []
    in_jobs = False
    indent: int | None = None
    child: int | None = None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line[0].isspace():
            in_jobs = line.rstrip().startswith("jobs:")
            indent = child = None
            continue
        if not in_jobs:
            continue
        width = len(line) - len(line.lstrip())
        key = JOB_KEY.match(line)
        if key and (indent is None or width == indent):
            indent, child = width, None
            jobs.append(CiJob(workflow, key.group(2), key.group(2)))
            continue
        if not jobs or indent is None or width <= indent:
            continue
        child = width if child is None else child
        name = JOB_NAME.match(line)
        if name and width == child:
            jobs[-1] = CiJob(workflow, jobs[-1].job_id, name.group(1))
    return jobs


def ci_jobs(repo: Path) -> list[CiJob]:
    folder = repo / ".github/workflows"
    if not folder.is_dir():
        return []
    files = sorted([*folder.glob("*.yml"), *folder.glob("*.yaml")])
    return [
        job
        for path in files
        if (text := read(path)) is not None
        for job in workflow_jobs(path.relative_to(repo).as_posix(), text)
    ]


class GhError(Exception):
    """``gh`` could not answer."""


def gh_json(repo: Path, args: list[str], *, missing_ok: bool = False) -> object:
    gh = shutil.which("gh")
    if gh is None:
        raise GhError("gh is not on PATH")
    try:
        result = subprocess.run(
            [gh, *args],
            cwd=repo,
            env=repo_environment(),
            capture_output=True,
            text=True,
            timeout=GH_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GhError(f"gh {args[0]} failed: {error}") from error
    if result.returncode:
        if missing_ok and "404" in result.stderr + result.stdout:
            return None
        detail = (result.stderr or result.stdout).strip().splitlines()[-1:] or ["no output"]
        raise GhError(f"gh {' '.join(args[:2])} exited {result.returncode}: {detail[0]}")
    try:
        return json.loads(result.stdout)
    except ValueError as error:
        raise GhError(f"gh {' '.join(args[:2])} printed no JSON") from error


def required_checks(repo: Path) -> list[str]:
    """Contexts the default branch requires, from rulesets and classic branch protection."""
    view = gh_json(repo, ["repo", "view", "--json", "nameWithOwner,defaultBranchRef"])
    if not isinstance(view, dict):
        raise GhError("gh repo view returned no repository")
    slug = view.get("nameWithOwner")
    branch = (view.get("defaultBranchRef") or {}).get("name")
    if not isinstance(slug, str) or not isinstance(branch, str):
        raise GhError("gh repo view returned no default branch")
    found: list[str] = []
    rules = gh_json(repo, ["api", f"repos/{slug}/rules/branches/{branch}"], missing_ok=True)
    for rule in rules if isinstance(rules, list) else []:
        if isinstance(rule, dict) and rule.get("type") == "required_status_checks":
            checks = (rule.get("parameters") or {}).get("required_status_checks") or []
            found.extend(item["context"] for item in checks if isinstance(item, dict))
    protection = gh_json(
        repo,
        ["api", f"repos/{slug}/branches/{branch}/protection/required_status_checks"],
        missing_ok=True,
    )
    if isinstance(protection, dict):
        found.extend(str(item) for item in protection.get("contexts") or [])
        found.extend(
            item["context"] for item in protection.get("checks") or [] if isinstance(item, dict)
        )
    return sorted(set(found))


def discover(repo: Path, *, offline: bool) -> Discovery:
    managers, hooks = hook_managers(repo)
    found = Discovery(make_targets(repo), managers, hooks, ci_jobs(repo))
    if offline:
        found.notes.append("required checks not read (--offline)")
        return found
    try:
        found.required_checks = required_checks(repo)
    except GhError as error:
        found.notes.append(f"required checks unknown: {error}")
    return found
