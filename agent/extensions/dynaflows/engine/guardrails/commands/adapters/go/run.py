from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from guardrails.commands.adapters.go.dialect import GUARDS_PROPERTY
from guardrails.commands.adapters.go.guard import (
    ALLOWED_VARIABLE,
    GUARDS_VARIABLE,
    JOURNAL_VARIABLE,
    PATH_SEPARATOR,
    PROTECTED_VARIABLE,
    write_overlay,
)
from guardrails.commands.adapters.go.packages import build_arguments, list_packages
from guardrails.commands.adapters.go.process import run_command
from guardrails.commands.adapters.go.report import (
    FAILING,
    convert,
    read_stream,
    write_junit,
)
from guardrails.commands.adapters.go.source import (
    NAME,
    SETTINGS,
    SourceError,
    command,
    package_patterns,
    selector_arguments,
)
from guardrails.context.config.adapter_declarations import adapter_tiers
from guardrails.foundation.child_env import clean_environment, repo_environment
from guardrails.foundation.inventory import as_list, settings

TIER_GUARDS = {
    "t1": ("fast-tier", "user-data"),
    "t2": ("user-data",),
    "t3": ("user-data",),
}
KEPT_GO_ENV = ("GOCACHE", "GOMODCACHE", "GOPATH", "GOENV")
XDG_FOLDERS = {
    "XDG_CONFIG_HOME": "config",
    "XDG_DATA_HOME": "data",
    "XDG_CACHE_HOME": "cache",
    "XDG_STATE_HOME": "state",
}


def run_timeout(profile: dict[str, Any]) -> float:
    timeout = settings(profile, SETTINGS).get("run_timeout_s", 1800)
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise SourceError(f"{SETTINGS}.run_timeout_s must be a positive finite number")
    return float(timeout)


def go_environment(
    go: list[str], repo: Path, environment: Mapping[str, str]
) -> dict[str, str]:
    result = run_command(
        [*go, "env", "-json", "GOROOT", "GOVERSION", *KEPT_GO_ENV],
        repo,
        environment,
        60,
    )
    if result.returncode:
        raise SourceError(
            f"go env exited {result.returncode}: {result.stderr.strip()[-400:]}"
        )
    values = json.loads(result.stdout)
    return {key: str(value) for key, value in values.items()}


def path_variants(*paths: Path) -> str:
    variants: list[str] = []
    for path in paths:
        for item in (os.path.abspath(path), os.path.realpath(path)):
            if item not in variants:
                variants.append(item)
    return PATH_SEPARATOR.join(variants)


def revision(repo: Path) -> str | None:
    result = run_command(["git", "rev-parse", "HEAD"], repo, clean_environment(), 60)
    return result.stdout.strip() or None if result.returncode == 0 else None


def guarded_environment(
    base: Mapping[str, str],
    scratch: Path,
    repo: Path,
    go_values: dict[str, str],
    tier: str,
) -> dict[str, str]:
    environment = dict(base)
    real_home = Path(base.get("HOME") or Path.home())
    temporary = Path(base.get("TMPDIR") or tempfile.gettempdir())
    home = scratch / "home"
    home.mkdir()
    environment.update(HOME=str(home), USERPROFILE=str(home))
    for name, folder in XDG_FOLDERS.items():
        target = home / folder
        target.mkdir()
        environment[name] = str(target)
    environment.update({
        key: go_values[key] for key in KEPT_GO_ENV if go_values.get(key)
    })
    environment[GUARDS_VARIABLE] = ",".join(TIER_GUARDS[tier])
    environment[PROTECTED_VARIABLE] = path_variants(real_home)
    environment[ALLOWED_VARIABLE] = path_variants(home, temporary, repo)
    environment[JOURNAL_VARIABLE] = os.path.realpath(scratch / "journal.txt")
    return environment


def tier_selection(profile: dict[str, Any], tier: str) -> tuple[list[str], list[str]]:
    spec = adapter_tiers(profile, NAME).get(tier)
    if not isinstance(spec, dict) or not isinstance(spec.get("selector"), str):
        raise SourceError(f"the go adapter needs a go test selector for tier {tier}")
    return selector_arguments(spec["selector"]), package_patterns(
        as_list(spec.get("location"))
    )


@dataclass(frozen=True)
class Invocation:
    repo: Path
    tier: str
    environment: dict[str, str]
    go_values: dict[str, str]


def guarded_test(
    invocation: Invocation, command_line: list[str], timeout: float
) -> tuple[subprocess.CompletedProcess[str], float, list[str]]:
    go_values = invocation.go_values
    with tempfile.TemporaryDirectory(prefix="omp-go-run-") as folder:
        scratch = Path(folder).resolve()
        overlay = write_overlay(Path(go_values["GOROOT"]), scratch / "overlay")
        guarded = guarded_environment(
            invocation.environment, scratch, invocation.repo, go_values, invocation.tier
        )
        toolchain = str(Path(go_values["GOROOT"]) / "bin" / "go")
        started = time.monotonic()
        result = run_command(
            [toolchain, "test", "-json", f"-overlay={overlay}", *command_line],
            invocation.repo,
            guarded,
            timeout,
        )
        wall = time.monotonic() - started
        journal = scratch / "journal.txt"
        violations = (
            journal.read_text(encoding="utf-8").splitlines()
            if journal.is_file()
            else []
        )
    return result, wall, violations


def run_properties(
    repo: Path, tier: str, go_values: dict[str, str], code: int, wall: float
) -> dict[str, str]:
    properties = {
        "omp.tier": tier,
        "omp.runtime": f"go {go_values['GOVERSION'].removeprefix('go')}",
        "omp.wall_s": f"{wall:.3f}",
        "omp.exit_code": str(code),
        GUARDS_PROPERTY: ",".join(TIER_GUARDS[tier]),
    }
    head = revision(repo)
    if head:
        properties["omp.revision"] = head
    return properties


def run_tier(
    repo: Path,
    profile: dict[str, Any],
    tier: str,
    out: Path,
    base: Mapping[str, str] | None = None,
) -> int:
    arguments, patterns = tier_selection(profile, tier)
    go = command(profile)
    environment = repo_environment(base)
    go_values = go_environment(go, repo, environment)
    packages = list_packages(
        go, repo, patterns, build_arguments(arguments), environment
    )
    invocation = Invocation(repo, tier, environment, go_values)
    result, wall, violations = guarded_test(
        invocation, [*arguments, *patterns], run_timeout(profile)
    )
    cases = convert(read_stream(result.stdout), packages, violations)
    write_junit(
        out, cases, run_properties(repo, tier, go_values, result.returncode, wall), wall
    )
    failing = [case for case in cases if case.outcome in FAILING]
    for case in failing:
        print(f"{case.case_id}: {case.message[:300]}", file=sys.stderr)
    if result.stderr.strip():
        print(result.stderr.strip()[-2000:], file=sys.stderr)
    return result.returncode or (1 if failing else 0)


def kept_go_environment(profile: dict[str, Any]) -> dict[str, str]:
    values = go_environment(command(profile), Path.cwd(), repo_environment())
    return {key: values[key] for key in (*KEPT_GO_ENV, "GOROOT") if values.get(key)}
