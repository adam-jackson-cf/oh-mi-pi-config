from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from guardrails.commands.adapters.pytest.dialect import NAME
from guardrails.context.config.adapter_declarations import adapter_tiers
from guardrails.context.config.profile import PROFILE_PATH
from guardrails.foundation.child_env import repo_environment
from guardrails.foundation.findings import Finding
from guardrails.foundation.inventory import as_list, settings
from guardrails.host.process import run_text

ENGINE_ROOT = Path(__file__).resolve().parents[4]
PLUGIN = "guardrails.commands.adapters.pytest.plugin"
SETTINGS = "adapter_pytest"
NO_TESTS_COLLECTED = 5
PROPAGATION = "TEST.failure-propagation"


def selector_arguments(selector: str) -> list[str]:
    text = selector.strip()
    if text.startswith("-"):
        return shlex.split(text)
    return ["-m", text]


def collect_command(locations: list[str], selector: str, out_file: Path) -> list[str]:
    return [
        sys.executable,
        "-m",
        NAME,
        "--collect-only",
        "-q",
        "-p",
        "no:cacheprovider",
        "-p",
        PLUGIN,
        f"--omp-collect-out={out_file}",
        *selector_arguments(selector),
        "--",
        *locations,
    ]


def package_environment() -> dict[str, str]:
    environment = repo_environment()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    root = str(ENGINE_ROOT)
    current = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = os.pathsep.join([root, current] if current else [root])
    return environment


def collect_tier(
    repo: Path, profile: dict[str, Any], tier: str, out_dir: Path
) -> list[Finding]:
    spec = adapter_tiers(profile, NAME).get(tier)
    if not isinstance(spec, dict) or not isinstance(spec.get("selector"), str):
        return [
            Finding(
                PROPAGATION,
                PROFILE_PATH,
                f"the pytest adapter has no selector for tier {tier}",
                "record it under adapters[].tiers",
            )
        ]
    locations = as_list(spec.get("location"))
    timeout = settings(profile, SETTINGS).get("collect_timeout_s")
    with tempfile.TemporaryDirectory(prefix="omp-collect-") as scratch:
        listing = Path(scratch) / "ids.json"
        command = collect_command(locations, spec["selector"], listing)
        try:
            result = run_text(
                command,
                cwd=repo,
                env=package_environment(),
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return [timeout_finding(tier, timeout)]
        collected = json.loads(listing.read_text()) if listing.exists() else []
    ids = [test_id(repo, file, node) for file, node in collected]
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = [f"# tier {tier} selector {spec['selector']!r}", *ids]
    (out_dir / f"{tier}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return collection_findings(tier, result)


def test_id(repo: Path, file: str, node_id: str) -> str:
    try:
        path = Path(file).relative_to(repo.resolve()).as_posix()
    except ValueError:
        path = node_id.split("::", 1)[0]
    _, separator, rest = node_id.partition("::")
    return f"{path}{separator}{rest}"


def collection_findings(
    tier: str, result: subprocess.CompletedProcess[str]
) -> list[Finding]:
    if result.returncode == 0:
        return []
    if result.returncode == NO_TESTS_COLLECTED:
        message = f"tier {tier} selector collected no tests (pytest exit 5)"
        fix = "fix the tier's selector or location; an empty collection never passes"
    else:
        tail = " ".join((result.stdout + result.stderr).strip().splitlines()[-3:])
        message = (
            f"tier {tier} collection failed (pytest exit {result.returncode}): {tail}"
        )
        fix = "make the tier collect without errors"
    return [Finding(PROPAGATION, f"tiers.{tier}", message, fix)]


def timeout_finding(tier: str, timeout: object) -> Finding:
    return Finding(
        PROPAGATION,
        f"tiers.{tier}",
        f"tier {tier} collection exceeded collect_timeout_s={timeout}",
        "make collection finish within the recorded limit",
    )


def list_tiers(
    repo: Path, profile: dict[str, Any], tiers: list[str], out_dir: Path
) -> list[Finding]:
    findings: list[Finding] = []
    for tier in tiers or sorted(adapter_tiers(profile, NAME)):
        findings.extend(collect_tier(repo, profile, tier, out_dir))
    return findings
