"""Collect-only ``bun test`` IDs per tier from the record's selectors (adapter item 2).

Bun has no list mode, so the listing runs ``bun test`` with the adapter's preload in collect mode:
every test body and hook is a no-op, so only module top level runs. The selector's tests pass
without running; Bun's native JUnit report then names them, and the IDs are built from its suite
nesting exactly as the run report builds them (``<file>::<suite> > ... > <title>``). Tests a
registration skips (``skip``, ``todo``, a false ``if``) are not listed: they never run.

Selectors are native ``bun test`` arguments (for example ``--test-name-pattern fast``); locations
are ``bun test`` path filters. The runner is ``bun`` on ``PATH``, the repository's own toolchain,
unless ``guardrails.adapter_bun_test.command`` records an argv list. ``collect_timeout_s`` bounds
the listing (120 seconds unless recorded otherwise). A load error, an empty or a duplicated
collection is a TEST.failure-propagation finding. Give each adapter its own ``--out`` directory.
"""

from __future__ import annotations

import math
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
from guardrails.context.config.adapter_declarations import adapter_tiers
from guardrails.foundation.child_env import repo_environment
from guardrails.foundation.findings import Finding
from guardrails.foundation.inventory import as_list, settings
from guardrails.host.process import run_text

NAME = "bun_test"
SETTINGS = "adapter_bun_test"
RULE = "TEST.failure-propagation"


def selector_arguments(selector: str) -> list[str]:
    """Selectors are native ``bun test`` arguments, not prose or shell commands."""
    return shlex.split(selector)


def command(profile: dict[str, Any]) -> list[str]:
    configured = settings(profile, SETTINGS).get("command")
    if configured is not None:
        if (
            not isinstance(configured, list)
            or not configured
            or not all(isinstance(part, str) for part in configured)
        ):
            raise ValueError(
                f"guardrails.{SETTINGS}.command must be a nonempty argv list"
            )
        return configured
    bun = shutil.which("bun")
    if bun is None:
        raise ValueError(
            f"bun is not on PATH; activate the repository's Bun or record {SETTINGS}.command"
        )
    return [bun]


def collect_timeout(profile: dict[str, Any]) -> float:
    timeout = settings(profile, SETTINGS).get("collect_timeout_s", 120)
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise ValueError(
            f"{SETTINGS}.collect_timeout_s must be a positive finite number"
        )
    return float(timeout)


def selected_ids(report: Path) -> list[str]:
    """The IDs whose no-op body passed; any failing case means the listing ran test code."""
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
    repo: Path, profile: dict[str, Any], tier: str, out: Path
) -> list[Finding]:
    spec = adapter_tiers(profile, NAME).get(tier)
    try:
        if not isinstance(spec, dict) or not isinstance(spec.get("selector"), str):
            raise ValueError(
                f"the bun_test adapter needs a bun test selector for tier {tier}"
            )
        timeout = collect_timeout(profile)
        with tempfile.TemporaryDirectory(prefix="omp-bun-collect-") as scratch:
            report = Path(scratch) / "ids.xml"
            entry = Path(scratch) / "collect.preload.mjs"
            journal = Path(scratch) / "collect.journal.jsonl"
            write_entry(
                entry, {"mode": "collect", "tier": tier, "journal": str(journal)}
            )
            argv = [
                *command(profile),
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
                RULE,
                f"tiers.{tier}",
                str(error),
                "fix the recorded selector, location or Bun installation and collect again",
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
