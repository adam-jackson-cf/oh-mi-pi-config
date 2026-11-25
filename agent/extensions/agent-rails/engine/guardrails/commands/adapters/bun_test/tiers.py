from __future__ import annotations

import shlex
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from typing import Any

from guardrails.commands.adapters.bun_test.dialect import (
    DIALECT,
    native_cases,
    write_entry,
)
from guardrails.commands.adapters.contract import PROPAGATION, collect_timeout
from guardrails.context.config.adapter_declarations import adapter_tiers
from guardrails.context.config.reader import checker_settings
from guardrails.foundation.child_env import repo_environment
from guardrails.foundation.findings import Finding
from guardrails.foundation.inventory import as_list
from guardrails.host.process import run_text

NAME = "bun_test"
SETTINGS = "adapter_bun_test"


def selector_arguments(selector: str) -> list[str]:
    return shlex.split(selector)


def command(config: dict[str, Any]) -> list[str]:
    configured = checker_settings(config, SETTINGS).get("command")
    if configured is not None:
        if (
            not isinstance(configured, list)
            or not configured
            or not all(isinstance(part, str) for part in configured)
        ):
            raise ValueError(
                f"checkers.{SETTINGS}.command must be a nonempty argv list"
            )
        return configured
    bun = shutil.which("bun")
    if bun is None:
        raise ValueError(
            f"bun is not on PATH; activate the repository's Bun or set "
            f"checkers.{SETTINGS}.command in .agentrails/config.json"
        )
    return [bun]


def selected_ids(report: Path) -> list[str]:
    root = ElementTree.parse(report).getroot()
    ids = []
    for case in native_cases(root):
        outcome, reason = DIALECT.outcome(case.element)
        if outcome in {"failed", "error"}:
            raise ValueError(f"{case.id} failed while listing: {reason}")
        if outcome == "passed":
            ids.append(case.id)
    if len(ids) != len(set(ids)):
        raise ValueError(
            "bun test collected duplicate IDs; give every test a unique title"
        )
    return sorted(ids)


def collect_tier(
    repo: Path, config: dict[str, Any], tier: str, out: Path
) -> list[Finding]:
    spec = adapter_tiers(config, NAME).get(tier)
    try:
        if not isinstance(spec, dict) or not isinstance(spec.get("selector"), str):
            raise ValueError(
                f"the bun_test adapter needs a bun test selector for tier {tier}"
            )
        timeout = collect_timeout(config, SETTINGS)
        with tempfile.TemporaryDirectory(prefix="guardrails-bun-collect-") as scratch:
            report = Path(scratch) / "ids.xml"
            entry = Path(scratch) / "collect.preload.mjs"
            journal = Path(scratch) / "collect.journal.jsonl"
            write_entry(
                entry, {"mode": "collect", "tier": tier, "journal": str(journal)}
            )
            argv = [
                *command(config),
                "test",
                "--preload",
                str(entry),
                "--reporter=junit",
                f"--reporter-outfile={report}",
                *selector_arguments(spec["selector"]),
                *as_list(spec.get("location")),
            ]
            result = run_text(
                argv, cwd=repo, env=repo_environment(), timeout=timeout, check=False
            )
            if result.returncode or not report.is_file():
                tail = " ".join((result.stdout + result.stderr).split())[-600:]
                raise ValueError(
                    f"bun test collection exited {result.returncode}: {tail}"
                )
            ids = selected_ids(report)
        if not ids:
            raise ValueError(
                "selector collected no tests; empty collection never passes"
            )
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{tier}.txt").write_text(
            "\n".join([f"# tier {tier} selector {spec['selector']!r}", *ids]) + "\n",
            encoding="utf-8",
        )
    except (
        OSError,
        ValueError,
        TypeError,
        ElementTree.ParseError,
        subprocess.TimeoutExpired,
    ) as error:
        return [
            Finding(
                PROPAGATION,
                f"tiers.{tier}",
                str(error),
                "fix the tier's selector or location in tests.adapters[].tiers, or the "
                "Bun installation, and collect again",
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
