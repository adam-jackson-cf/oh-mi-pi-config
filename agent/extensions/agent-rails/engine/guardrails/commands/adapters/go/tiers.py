from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

from guardrails.commands.adapters.contract import PROPAGATION, collect_timeout
from guardrails.commands.adapters.go.packages import build_arguments, list_packages
from guardrails.commands.adapters.go.syntax import TEST_NAME, TEST_SUFFIX
from guardrails.commands.adapters.go.toolchain import (
    NAME,
    SETTINGS,
    ToolchainError,
    command,
    package_patterns,
    selector_arguments,
)
from guardrails.context.config.adapter_declarations import adapter_tiers
from guardrails.foundation.child_env import repo_environment
from guardrails.foundation.findings import Finding
from guardrails.foundation.inventory import as_list
from guardrails.host.process import run_text
from guardrails.toolkit.affected import TierCommand, print_affected


def flag_value(arguments: list[str], flag: str) -> str | None:
    found = None
    for index, item in enumerate(arguments):
        if item == flag and index + 1 < len(arguments):
            found = arguments[index + 1]
        elif item.startswith(f"{flag}="):
            found = item.split("=", 1)[1]
    return found


def top_level(pattern: str | None) -> str:
    return (pattern or ".").split("/", 1)[0] or "."


def listed_names(stdout: str) -> dict[str, list[str]]:
    names: dict[str, list[str]] = {}
    for raw in stdout.splitlines():
        try:
            event = json.loads(raw)
        except ValueError:
            continue
        output = str(event.get("Output", "")).strip()
        if event.get("Action") == "output" and TEST_NAME.match(output):
            names.setdefault(str(event.get("Package", "")), []).append(output)
    return names


def tier_ids(repo: Path, config: dict[str, Any], tier: str) -> list[str]:
    spec = adapter_tiers(config, NAME).get(tier)
    if not isinstance(spec, dict) or not isinstance(spec.get("selector"), str):
        raise ToolchainError(f"the go adapter needs a go test selector for tier {tier}")
    arguments = selector_arguments(spec["selector"])
    patterns = package_patterns(as_list(spec.get("location")))
    go = command(config)
    build = build_arguments(arguments)
    environment = repo_environment()
    run = top_level(flag_value(arguments, "-run"))
    skip = flag_value(arguments, "-skip")
    result = run_text(
        [*go, "test", "-json", "-list", run, *build, *patterns],
        cwd=repo,
        env=environment,
        timeout=collect_timeout(config, SETTINGS),
        check=False,
    )
    if result.returncode:
        tail = " ".join((result.stdout + result.stderr).split())[-600:]
        raise ToolchainError(f"go test -list exited {result.returncode}: {tail}")
    packages = list_packages(go, repo, patterns, build, environment)
    ids = []
    for package, names in listed_names(result.stdout).items():
        known = packages.get(package)
        for name in names:
            if skip and re.search(top_level(skip), name):
                continue
            if known is None or name not in known.tests:
                raise ToolchainError(
                    f"{package}: cannot find the file that defines {name}"
                )
            ids.append(f"{known.tests[name]}::{name}")
    if len(ids) != len(set(ids)):
        raise ToolchainError("go test listed duplicate test IDs")
    return sorted(ids)


def collect_tier(
    repo: Path, config: dict[str, Any], tier: str, out: Path
) -> list[Finding]:
    try:
        ids = tier_ids(repo, config, tier)
        if not ids:
            raise ToolchainError(
                "selector collected no tests; empty collection never passes"
            )
        selector = adapter_tiers(config, NAME)[tier]["selector"]
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{tier}.txt").write_text(
            "\n".join([f"# tier {tier} selector {selector!r}", *ids]) + "\n",
            encoding="utf-8",
        )
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        return [
            Finding(
                PROPAGATION,
                f"tiers.{tier}",
                str(error),
                "fix the tier's selector or location in tests.adapters[].tiers, or the "
                "Go installation, and collect again",
            )
        ]
    return []


def list_tiers(
    repo: Path, config: dict[str, Any], tiers: list[str], out: Path
) -> list[Finding]:
    findings = []
    for tier in tiers or sorted(adapter_tiers(config, NAME)):
        findings.extend(collect_tier(repo, config, tier, out))
    return findings


def affected_arguments(
    repo: Path, config: dict[str, Any], base: str, head: str
) -> list[Finding]:
    def t2_arguments(selector: object, paths: list[str]) -> list[str]:
        folders = sorted({PurePosixPath(path).parent.as_posix() for path in paths})
        return [*selector_arguments(str(selector)), *package_patterns(folders)]

    command = TierCommand(NAME, (TEST_SUFFIX,), t2_arguments)
    return print_affected(repo, config, command, base, head)
