"""Blind architecture scoring for instruction evaluations."""
from __future__ import annotations

import hashlib
import json
import secrets
import shutil
from pathlib import Path
from string import Template
from typing import Any

from runner import _reject_symlinks, execute_omp

_DIMENSIONS = {"behavior_preserved", "change_locality", "separation_of_concerns", "unnecessary_abstraction"}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid JSON evidence: {path}") from error
    if not isinstance(value, dict):
        raise ValueError(f"JSON evidence must be an object: {path}")
    return value


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        if path.is_symlink():
            digest.update(b"L\\0" + relative + b"\\0" + str(path.readlink()).encode("utf-8"))
        elif path.is_file():
            digest.update(b"F\\0" + relative + b"\\0")
            digest.update(path.read_bytes())
    return digest.hexdigest()

def _tree_checksums(root: Path) -> dict[str, str]:
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(root.rglob("*")) if path.is_file()}


def _asset_path(run_dir: Path, protocol: dict[str, Any], name: str) -> Path:
    assets = protocol.get("assets")
    if not isinstance(assets, dict) or not isinstance(assets.get(name), str):
        raise ValueError(f"frozen protocol lacks assets.{name}")
    path = Path(assets[name])
    if not path.is_absolute():
        path = run_dir / path
    if not path.is_file():
        raise ValueError(f"frozen asset missing: {path}")
    return path


def _render(template_path: Path, values: dict[str, str]) -> str:
    try:
        rendered = Template(template_path.read_text(encoding="utf-8")).substitute(values)
    except (OSError, KeyError, ValueError) as error:
        raise ValueError(f"cannot render frozen scorer template: {template_path}") from error
    if "$" in rendered:
        raise ValueError("scorer template retained an unresolved placeholder")
    return rendered


def _valid_score(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"schema_version", "archetype", "dimensions", "evidence", "rationale"}:
        raise ValueError("scorer output does not have the exact raw-score fields")
    if value["schema_version"] != 1 or not isinstance(value["archetype"], str) or not value["archetype"].strip():
        raise ValueError("scorer output has an invalid schema version or archetype")
    dimensions = value["dimensions"]
    if not isinstance(dimensions, dict) or set(dimensions) != _DIMENSIONS or any(type(item) is not bool for item in dimensions.values()):
        raise ValueError("scorer output has invalid dimensions")
    evidence = value["evidence"]
    if not isinstance(evidence, list) or not evidence or any(not isinstance(item, str) or not item.strip() for item in evidence):
        raise ValueError("scorer output has invalid evidence")
    if not isinstance(value["rationale"], str) or not value["rationale"].strip():
        raise ValueError("scorer output has an invalid rationale")
    return value


def _scenario(protocol: dict[str, Any], scenario_id: str) -> dict[str, Any]:
    scenarios = protocol.get("scenarios")
    if not isinstance(scenarios, list):
        raise ValueError("frozen protocol lacks scenarios")
    for scenario in scenarios:
        if isinstance(scenario, dict) and scenario.get("id") == scenario_id:
            return scenario
    raise ValueError(f"unknown scenario in trial evidence: {scenario_id}")


def blind_score(run_dir: Path, protocol: dict[str, Any]) -> dict[str, Any]:
    """Score completed work in randomized aliases without revealing treatment."""
    trials_value = _read_json(run_dir / "trials.json").get("trials")
    if not isinstance(trials_value, list) or not trials_value:
        raise ValueError("trial evidence is missing")
    template = _asset_path(run_dir, protocol, "scorer_template")
    _asset_path(run_dir, protocol, "rubric_template")
    _read_json(_asset_path(run_dir, protocol, "score_schema"))
    timeout = protocol.get("execution", {}).get("timeout_seconds")
    if not isinstance(timeout, int) or timeout < 1:
        raise ValueError("frozen protocol has no valid timeout")
    if any(not isinstance(trial, dict) for trial in trials_value):
        raise ValueError("trial evidence contains an invalid record")
    completed = list(trials_value)
    secrets.SystemRandom().shuffle(completed)
    blind_root = run_dir / "blind-workspaces"
    blind_root.mkdir(parents=True, exist_ok=True)
    aliases: list[dict[str, Any]] = []
    scores: list[dict[str, Any]] = []
    for ordinal, trial in enumerate(completed, 1):
        scenario_id, workspace = trial.get("scenario"), trial.get("context")
        if not isinstance(scenario_id, str) or not isinstance(workspace, str):
            raise ValueError("completed trial lacks scenario or immutable context evidence")
        source = Path(workspace)
        if not source.is_dir():
            raise ValueError(f"trial context missing: {source}")
        _reject_symlinks(source)
        context_checksum = trial.get("context_checksum")
        if not isinstance(context_checksum, dict) or context_checksum != _tree_checksums(source):
            raise ValueError("trial context disagrees with its immutable checksum evidence")
        scenario = _scenario(protocol, scenario_id)
        rubric = scenario.get("rubric")
        if not isinstance(rubric, dict):
            raise ValueError(f"scenario {scenario_id} lacks a frozen rubric")
        alias = f"implementation-{ordinal:03d}"
        blind_workspace = blind_root / alias
        if blind_workspace.exists():
            raise ValueError(f"blind workspace already exists: {blind_workspace}")
        shutil.copytree(source, blind_workspace)
        rubric_path = blind_workspace / ".instruct-eval-rubric.json"
        rubric_path.write_text(json.dumps(rubric, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        before = _tree_digest(blind_workspace)
        prompt = _render(template, {"workspace": str(blind_workspace), "rubric_path": str(rubric_path), "alias": alias})
        result = execute_omp(blind_workspace, prompt, protocol)
        after = _tree_digest(blind_workspace)
        if after != before:
            raise ValueError(f"blind scorer mutated workspace for {alias}")
        if not isinstance(result, dict) or result.get("exit_code") != 0:
            raise ValueError(f"blind scorer failed for {alias}")
        try:
            raw = _valid_score(json.loads(result.get("stdout", "")))
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError(f"blind scorer returned invalid JSON for {alias}") from error
        if raw["archetype"] not in rubric["archetypes"]:
            raise ValueError(f"blind scorer returned an undeclared archetype for {alias}")
        preferred = raw["archetype"] == rubric["preferred_architecture"]
        scores.append({"alias": alias, "scenario": scenario_id, "preferred": preferred, **raw})
        aliases.append({"alias": alias, "scenario": scenario_id, "trial": trial.get("trial"), "condition": trial.get("condition"), "workspace": str(source)})
    alias_map = {"aliases": aliases}
    evidence = {"scores": scores}
    (run_dir / "alias-map.json").write_text(json.dumps(alias_map, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (run_dir / "scores.json").write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return evidence
