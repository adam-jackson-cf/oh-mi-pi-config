from __future__ import annotations

import json
import math
import re
import subprocess
from pathlib import Path
from typing import Any

from guardrails.commands.adapters.go.packages import build_arguments, list_packages
from guardrails.commands.adapters.go.process import run_command
from guardrails.commands.adapters.go.source import (
    NAME,
    SETTINGS,
    SourceError,
    command,
    package_patterns,
    selector_arguments,
)
from guardrails.context.config.adapter_declarations import adapter_tiers
from guardrails.foundation.child_env import repo_environment
from guardrails.foundation.findings import Finding
from guardrails.foundation.inventory import as_list, settings

RULE = "TEST.failure-propagation"
LISTED = re.compile(r"^(Test|Fuzz|Example)(?![a-z])\w*$")


def collect_timeout(profile: dict[str, Any]) -> float:
    timeout = settings(profile, SETTINGS).get("collect_timeout_s", 120)
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise SourceError(
            f"{SETTINGS}.collect_timeout_s must be a positive finite number"
        )
    return float(timeout)


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
        if event.get("Action") == "output" and LISTED.match(output):
            names.setdefault(str(event.get("Package", "")), []).append(output)
    return names


def tier_ids(repo: Path, profile: dict[str, Any], tier: str) -> list[str]:
    spec = adapter_tiers(profile, NAME).get(tier)
    if not isinstance(spec, dict) or not isinstance(spec.get("selector"), str):
        raise SourceError(f"the go adapter needs a go test selector for tier {tier}")
    arguments = selector_arguments(spec["selector"])
    patterns = package_patterns(as_list(spec.get("location")))
    go = command(profile)
    build = build_arguments(arguments)
    environment = repo_environment()
    run = top_level(flag_value(arguments, "-run"))
    skip = flag_value(arguments, "-skip")
    result = run_command(
        [*go, "test", "-json", "-list", run, *build, *patterns],
        repo,
        environment,
        collect_timeout(profile),
    )
    if result.returncode:
        tail = " ".join((result.stdout + result.stderr).split())[-600:]
        raise SourceError(f"go test -list exited {result.returncode}: {tail}")
    packages = list_packages(go, repo, patterns, build, environment)
    ids = []
    for package, names in listed_names(result.stdout).items():
        known = packages.get(package)
        for name in names:
            if skip and re.search(top_level(skip), name):
                continue
            if known is None or name not in known.tests:
                raise SourceError(
                    f"{package}: cannot find the file that defines {name}"
                )
            ids.append(f"{known.tests[name]}::{name}")
    if len(ids) != len(set(ids)):
        raise SourceError("go test listed duplicate test IDs")
    return sorted(ids)


def collect_tier(
    repo: Path, profile: dict[str, Any], tier: str, out: Path
) -> list[Finding]:
    try:
        ids = tier_ids(repo, profile, tier)
        if not ids:
            raise SourceError(
                "selector collected no tests; empty collection never passes"
            )
        selector = adapter_tiers(profile, NAME)[tier]["selector"]
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{tier}.txt").write_text(
            "\n".join([f"# tier {tier} selector {selector!r}", *ids]) + "\n",
            encoding="utf-8",
        )
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        return [
            Finding(
                RULE,
                f"tiers.{tier}",
                str(error),
                "fix the recorded selector, location or Go installation and collect again",
            )
        ]
    return []


def list_tiers(
    repo: Path, profile: dict[str, Any], tiers: list[str], out: Path
) -> list[Finding]:
    findings = []
    for tier in tiers or sorted(adapter_tiers(profile, NAME)):
        findings.extend(collect_tier(repo, profile, tier, out))
    return findings
