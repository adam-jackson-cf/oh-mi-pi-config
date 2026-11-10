"""Collect-only test-ID listing per tier from the profile's tier selectors (adapter item 2).

Each ``tiers.<tier>`` entry gives ``location`` (one or more paths handed to pytest) and
``selector``. A selector starting with ``-`` is a list of pytest arguments split by shell rules
(for example ``-m "fast and not slow"``); any other selector is a marker expression passed as
``-m <selector>``. The listing writes ``<out>/<tier>.txt``: one test ID per line in the shared
form ``<repository-relative file>::<name>`` (pytest's node ID with its file part made
repository-relative), which ``omp-guardrails collection-check --tier <tier>=<out>/<tier>.txt``
reads for TEST.tier-selectors.

pytest's exit code 5 (no tests collected) and any other collection failure are
TEST.failure-propagation findings here as well: an empty or broken collection never passes.

Settings (``guardrails.adapter_pytest`` in the profile): ``collect_timeout_s``, an optional
owner-chosen time limit in seconds for each collection run.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

import omp_guardrails
from omp_guardrails.findings import Finding
from omp_guardrails.structure.inventory import as_list, settings

PLUGIN = "omp_guardrails.adapters.pytest.plugin"
SETTINGS = "adapter_pytest"
NO_TESTS_COLLECTED = 5
PROPAGATION = "TEST.failure-propagation"
PROFILE_PATH = ".omp/test-profile.md"


def selector_arguments(selector: str) -> list[str]:
    text = selector.strip()
    if text.startswith("-"):
        return shlex.split(text)
    return ["-m", text]


def collect_command(locations: list[str], selector: str, out_file: Path) -> list[str]:
    return [
        sys.executable,
        "-m",
        "pytest",
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
    """The child pytest must import this package even when it runs from a source checkout."""
    environment = dict(os.environ)
    root = str(Path(omp_guardrails.__file__).resolve().parent.parent)
    current = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = os.pathsep.join([root, current] if current else [root])
    return environment


def collect_tier(repo: Path, profile: dict, tier: str, out_dir: Path) -> list[Finding]:
    spec = profile.get("tiers", {}).get(tier)
    if not isinstance(spec, dict) or not isinstance(spec.get("selector"), str):
        return [Finding(PROPAGATION, PROFILE_PATH, f"tiers.{tier} has no selector", "record it")]
    locations = as_list(spec.get("location"))
    timeout = settings(profile, SETTINGS).get("collect_timeout_s")
    with tempfile.TemporaryDirectory(prefix="omp-collect-") as scratch:
        listing = Path(scratch) / "ids.json"
        command = collect_command(locations, spec["selector"], listing)
        try:
            result = subprocess.run(
                command,
                cwd=repo,
                env=package_environment(),
                capture_output=True,
                text=True,
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
    """``<repository-relative file>::<rest of the node ID>``."""
    try:
        path = Path(file).relative_to(repo.resolve()).as_posix()
    except ValueError:
        path = node_id.split("::", 1)[0]
    _, separator, rest = node_id.partition("::")
    return f"{path}{separator}{rest}"


def collection_findings(tier: str, result: subprocess.CompletedProcess) -> list[Finding]:
    if result.returncode == 0:
        return []
    if result.returncode == NO_TESTS_COLLECTED:
        message = f"tier {tier} selector collected no tests (pytest exit 5)"
        fix = "fix the tier's selector or location; an empty collection never passes"
    else:
        tail = " ".join((result.stdout + result.stderr).strip().splitlines()[-3:])
        message = f"tier {tier} collection failed (pytest exit {result.returncode}): {tail}"
        fix = "make the tier collect without errors"
    return [Finding(PROPAGATION, f"tiers.{tier}", message, fix)]


def timeout_finding(tier: str, timeout: object) -> Finding:
    return Finding(
        PROPAGATION,
        f"tiers.{tier}",
        f"tier {tier} collection exceeded collect_timeout_s={timeout}",
        "make collection finish within the recorded limit",
    )


def list_tiers(repo: Path, profile: dict, tiers: list[str], out_dir: Path) -> list[Finding]:
    """List the named tiers, or every tier the profile declares."""
    findings: list[Finding] = []
    declared = profile.get("tiers")
    for tier in tiers or sorted(declared if isinstance(declared, dict) else {}):
        findings.extend(collect_tier(repo, profile, tier, out_dir))
    return findings
