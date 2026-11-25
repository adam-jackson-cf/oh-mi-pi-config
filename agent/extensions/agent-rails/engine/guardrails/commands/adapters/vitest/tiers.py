from __future__ import annotations

import json
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from guardrails.commands.adapters.contract import PROPAGATION, collect_timeout
from guardrails.context.config.adapter_declarations import adapter_tiers
from guardrails.foundation.child_env import repo_environment
from guardrails.foundation.findings import Finding
from guardrails.foundation.inventory import as_list, settings
from guardrails.host.process import run_text

NAME = "vitest"
SETTINGS = "adapter_vitest"


def selector_arguments(selector: str) -> list[str]:
    return shlex.split(selector)


def command(repo: Path, config: dict[str, Any]) -> list[str]:
    configured = settings(config, SETTINGS).get("command")
    if configured is not None:
        if (
            not isinstance(configured, list)
            or not configured
            or not all(isinstance(part, str) for part in configured)
        ):
            raise ValueError(
                "checkers.adapter_vitest.command must be a nonempty argv list"
            )
        return configured
    executable = repo / "node_modules/vitest/vitest.mjs"
    if not executable.is_file():
        raise ValueError(
            "install the repository's locked Vitest dependency or set "
            "checkers.adapter_vitest.command in .agentrails/config.json"
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
    repo: Path, config: dict[str, Any], tier: str, out: Path
) -> list[Finding]:
    spec = adapter_tiers(config, NAME).get(tier)
    try:
        if not isinstance(spec, dict) or not isinstance(spec.get("selector"), str):
            raise ValueError(
                f"the vitest adapter needs a native Vitest selector for tier {tier}"
            )
        timeout = collect_timeout(config, SETTINGS)
        with tempfile.TemporaryDirectory(
            prefix="guardrails-vitest-collect-"
        ) as scratch:
            listing = Path(scratch) / "ids.json"
            argv = [
                *command(repo, config),
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
            result = run_text(
                argv, cwd=repo, env=repo_environment(), timeout=timeout, check=False
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
                PROPAGATION,
                f"tiers.{tier}",
                str(error),
                "fix the tier's selector or location in tests.adapters[].tiers, or the "
                "Vitest installation, and collect again",
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
