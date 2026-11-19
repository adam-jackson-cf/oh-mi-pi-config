"""Native Vitest collect-only IDs using record selectors, never a test run."""

from __future__ import annotations

import json
import math
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from guardrails.child_env import repo_environment
from guardrails.findings import Finding
from guardrails.structure.inventory import as_list, settings

RULE = "TEST.failure-propagation"


def selector_arguments(selector: str) -> list[str]:
    """Selectors are native Vitest CLI arguments, not prose or shell commands."""
    return shlex.split(selector)


def command(repo: Path, profile: dict[str, Any]) -> list[str]:
    configured = settings(profile, "adapter_vitest").get("command")
    if configured is not None:
        if (
            not isinstance(configured, list)
            or not configured
            or not all(isinstance(part, str) for part in configured)
        ):
            raise ValueError(
                "guardrails.adapter_vitest.command must be a nonempty argv list"
            )
        return configured
    executable = repo / "node_modules/vitest/vitest.mjs"
    if not executable.is_file():
        raise ValueError(
            "install the repository's locked Vitest dependency or record adapter_vitest.command"
        )
    return ["node", str(executable)]


def collected_ids(repo: Path, entries: object) -> list[str]:
    if not isinstance(entries, list):
        raise ValueError("Vitest list JSON must be an array")
    ids = []
    for item in entries:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("file"), str)
            or not isinstance(item.get("name"), str)
        ):
            raise ValueError("Vitest list entry requires file and name strings")
        relative = (
            (repo / item["file"]).resolve().relative_to(repo.resolve()).as_posix()
        )
        ids.append(f"{relative}::{item['name']}")
    if len(ids) != len(set(ids)):
        raise ValueError(
            "Vitest collected duplicate IDs across projects; select one project per tier"
        )
    return sorted(ids)


def collect_tier(
    repo: Path, profile: dict[str, Any], tier: str, out: Path
) -> list[Finding]:
    spec = profile.get("tiers", {}).get(tier)
    try:
        if not isinstance(spec, dict) or not isinstance(spec.get("selector"), str):
            raise ValueError(f"tiers.{tier} requires a native Vitest selector")
        timeout = settings(profile, "adapter_vitest").get("collect_timeout_s", 120)
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError(
                "adapter_vitest.collect_timeout_s must be a positive finite number"
            )
        with tempfile.TemporaryDirectory(prefix="omp-vitest-collect-") as scratch:
            listing = Path(scratch) / "ids.json"
            argv = [
                *command(repo, profile),
                "list",
                *selector_arguments(spec["selector"]),
                "--json",
                str(listing),
                "--staticParse=false",
                "--cache=false",
                "--fsModuleCache=false",
                "--configLoader=runner",
                *as_list(spec.get("location")),
            ]
            result = subprocess.run(
                argv,
                cwd=repo,
                env=repo_environment(),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            if result.returncode:
                tail = " ".join((result.stdout + result.stderr).splitlines()[-5:])
                raise ValueError(
                    f"Vitest collection exited {result.returncode}: {tail}"
                )
            ids = collected_ids(repo, json.loads(listing.read_text(encoding="utf-8")))
        if not ids:
            raise ValueError(
                "selector collected no tests; empty collection never passes"
            )
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{tier}.txt").write_text(
            "\n".join([f"# tier {tier} selector {spec['selector']!r}", *ids]) + "\n",
            encoding="utf-8",
        )
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired) as error:
        return [
            Finding(
                RULE,
                f"tiers.{tier}",
                str(error),
                "fix the recorded selector, location or Vitest installation and collect again",
            )
        ]
    return []


def list_tiers(
    repo: Path, profile: dict[str, Any], tiers: list[str], out: Path
) -> list[Finding]:
    findings = []
    for tier in tiers or sorted(profile.get("tiers", {})):
        findings.extend(collect_tier(repo, profile, tier, out))
    return findings
