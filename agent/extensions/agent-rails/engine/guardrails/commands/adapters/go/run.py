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

from guardrails.commands.adapters.contract import TIER_GUARDS
from guardrails.commands.adapters.go.packages import build_arguments, list_packages
from guardrails.commands.adapters.go.report import (
    FAILING,
    convert,
    read_stream,
    write_junit,
)
from guardrails.commands.adapters.go.syscall_overlay import (
    ALLOWED_VARIABLE,
    GUARDS_VARIABLE,
    JOURNAL_VARIABLE,
    PATH_SEPARATOR,
    PROTECTED_VARIABLE,
    write_overlay,
)
from guardrails.commands.adapters.go.toolchain import (
    NAME,
    SETTINGS,
    ToolchainError,
    command,
    options,
    package_patterns,
    selector_arguments,
)
from guardrails.context.config.adapter_declarations import adapter_tiers
from guardrails.foundation.child_env import clean_environment, repo_environment
from guardrails.foundation.inventory import as_list
from guardrails.host.process import run_text
from guardrails.test_report.junit import (
    EXIT_CODE_PROPERTY,
    GUARDS_PROPERTY,
    REVISION_PROPERTY,
    RUNTIME_PROPERTY,
    TIER_PROPERTY,
    WALL_PROPERTY,
)

KEPT_GO_ENV = ("GOCACHE", "GOMODCACHE", "GOPATH", "GOENV")
XDG_FOLDERS = {
    "XDG_CONFIG_HOME": "config",
    "XDG_DATA_HOME": "data",
    "XDG_CACHE_HOME": "cache",
    "XDG_STATE_HOME": "state",
}


def run_timeout(config: dict[str, Any]) -> float:
    timeout = options(config).get("run_timeout_s", 1800)
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise ToolchainError(
            f"{SETTINGS}.run_timeout_s must be a positive finite number"
        )
    return float(timeout)


def go_environment(
    go: list[str], repo: Path, environment: Mapping[str, str]
) -> dict[str, str]:
    result = run_text(
        [*go, "env", "-json", "GOROOT", "GOVERSION", *KEPT_GO_ENV],
        cwd=repo,
        env=environment,
        timeout=60,
        check=False,
    )
    if result.returncode:
        raise ToolchainError(
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
    result = run_text(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        env=clean_environment(),
        timeout=60,
        check=False,
    )
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


def tier_selection(config: dict[str, Any], tier: str) -> tuple[list[str], list[str]]:
    spec = adapter_tiers(config, NAME).get(tier)
    if not isinstance(spec, dict) or not isinstance(spec.get("selector"), str):
        raise ToolchainError(f"the go adapter needs a go test selector for tier {tier}")
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
    with tempfile.TemporaryDirectory(prefix="guardrails-go-run-") as folder:
        scratch = Path(folder).resolve()
        overlay = write_overlay(Path(go_values["GOROOT"]), scratch / "overlay")
        guarded = guarded_environment(
            invocation.environment, scratch, invocation.repo, go_values, invocation.tier
        )
        toolchain = str(Path(go_values["GOROOT"]) / "bin" / "go")
        started = time.monotonic()
        result = run_text(
            [toolchain, "test", "-json", f"-overlay={overlay}", *command_line],
            cwd=invocation.repo,
            env=guarded,
            timeout=timeout,
            check=False,
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
        TIER_PROPERTY: tier,
        RUNTIME_PROPERTY: f"go {go_values['GOVERSION'].removeprefix('go')}",
        WALL_PROPERTY: f"{wall:.3f}",
        EXIT_CODE_PROPERTY: str(code),
        GUARDS_PROPERTY: ",".join(TIER_GUARDS[tier]),
    }
    head = revision(repo)
    if head:
        properties[REVISION_PROPERTY] = head
    return properties


def run_tier(
    repo: Path,
    config: dict[str, Any],
    tier: str,
    out: Path,
    base: Mapping[str, str] | None = None,
) -> int:
    arguments, patterns = tier_selection(config, tier)
    go = command(config)
    environment = repo_environment(base)
    go_values = go_environment(go, repo, environment)
    packages = list_packages(
        go, repo, patterns, build_arguments(arguments), environment
    )
    invocation = Invocation(repo, tier, environment, go_values)
    result, wall, violations = guarded_test(
        invocation, [*arguments, *patterns], run_timeout(config)
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


def kept_go_environment(config: dict[str, Any]) -> dict[str, str]:
    values = go_environment(command(config), Path.cwd(), repo_environment())
    return {key: values[key] for key in (*KEPT_GO_ENV, "GOROOT") if values.get(key)}
