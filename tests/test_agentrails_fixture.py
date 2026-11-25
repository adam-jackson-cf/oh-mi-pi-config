"""Shared .agentrails/config.json builders for the engine tests, and their own check.

Every config starts from the S1a fixture tests/fixtures/agentrails/valid/minimal.json, so a
config a test writes is one the engine's validator accepts.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from guardrails.context.config import validator
from guardrails.host.process import run_text

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/agentrails"
MINIMAL = FIXTURES / "valid/minimal.json"
SLUG = "example-owner/ledger"
APPROVED = {"approvedBy": "example-owner", "approvedAt": "2026-11-24T09:00:00Z"}
# Sections whose keys a test overrides one by one; the others are replaced whole.
MERGED = ("tests", "structure")


def fixture(name: str = "minimal") -> dict[str, Any]:
    """One valid S1a fixture config."""
    loaded: dict[str, Any] = json.loads(
        (FIXTURES / "valid" / f"{name}.json").read_text(encoding="utf-8")
    )
    return loaded


def merge(base: object, override: object) -> object:
    """Objects merge key by key, recursively; an exemption ({none: true}) or any other
    override replaces the base value."""
    if (
        not isinstance(base, dict)
        or not isinstance(override, dict)
        or "none" in override
    ):
        return copy.deepcopy(override)
    merged = dict(base)
    for key, value in override.items():
        merged[key] = merge(base[key], value) if key in base else copy.deepcopy(value)
    return merged


def config(**sections: Any) -> dict[str, Any]:
    """The minimal fixture with `sections` applied: tests and structure merge, the rest replace."""
    built = fixture()
    for name, value in sections.items():
        built[name] = (
            merge(built[name], value) if name in MERGED else copy.deepcopy(value)
        )
    return built


def exception(
    rule: str, path: str, reason: str, check: str | None = None
) -> dict[str, Any]:
    entry = {"rule": rule, "path": path, "reason": reason, **APPROVED}
    if check is not None:
        entry["check"] = check
    return entry


def approval(kind: str, target: str, reason: str = "owner approved") -> dict[str, Any]:
    return {"kind": kind, "target": target, "reason": reason, **APPROVED}


def git(repo: Path, *args: str) -> str:
    result = run_text(["git", "-C", str(repo), *args], check=False, timeout=60)
    return result.stdout.strip() if result.returncode == 0 else ""


def write_config(repo: Path, values: dict[str, Any]) -> Path:
    """Write <repo main checkout>/.agentrails/config.json.

    The origin remote is set to the config's repo.slug when the repo has none; with an origin,
    repo.slug follows it. The folder is excluded through the repo's info/exclude.
    """
    values = copy.deepcopy(values)
    origin = git(repo, "remote", "get-url", "origin")
    if origin:
        slug = (
            origin
            .removesuffix(".git")
            .replace("git@github.com:", "")
            .split("github.com/")[-1]
        )
        values["repo"]["slug"] = slug.lower()
    else:
        slug = values["repo"]["slug"]
        git(repo, "remote", "add", "origin", f"https://github.com/{slug}.git")
    common = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    exclude = common / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    if "/.agentrails/" not in existing.splitlines():
        exclude.write_text(existing + "/.agentrails/\n", encoding="utf-8")
    path = common.parent / ".agentrails" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")
    return path


@pytest.mark.fast
def test_builders_make_configs_the_validator_accepts() -> None:
    assert validator.validate(config(), origin=SLUG) == []
    exempted = config(
        tests={"tiers": {"t3": {"none": True}}},
        approvals=[*fixture()["approvals"], approval("exemption", "tiers.t3", "none")],
        exceptions=[exception("NAME.conventions", "src/**", "generated names")],
    )
    exempted["tests"]["adapters"][0]["tiers"].pop("t3")
    assert validator.validate(exempted, origin=SLUG) == []
