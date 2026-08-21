"""Freeze, verify, and archive instruction-evaluation evidence."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import re
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import analysis
import runner

_SPEC = {"schema_version", "instruction", "provider", "model", "thinking", "trials_per_condition", "practical_effect_threshold", "significance_level", "timeout_seconds", "permissions", "design_review", "scenarios", "decision_rules"}
_REQUEST = {"schema_version", "run_id", "created_at", "instruction", "provider", "model", "thinking", "trials_per_condition", "practical_effect_threshold", "significance_level", "protocol_path", "repository", "python_version", "omp_version"}
_PROTOCOL = _SPEC | {"run_id", "conditions", "execution", "assets"}
_KINDS = {"pristine", "complete-reference", "plausible-failure"}
_TYPES = {"shortcut", "change", "overcorrection"}
_DIMENSIONS = {"behavior_preserved", "change_locality", "separation_of_concerns", "unnecessary_abstraction"}
_SAFE_TOOLS = {"read", "edit", "write", "glob", "grep", "lsp"}


def _hash(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()
def _json(path: Path, value: Any = None) -> Any:
    if value is None: return json.loads(path.read_text(encoding="utf-8"))
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"); return value

def _exact(value: Any, keys: set[str], name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys: raise ValueError(f"{name} has missing or extra fields")
    return value

def _nonempty(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value: raise ValueError(f"{name} must be a nonempty string")
    return value
def _strings(value: Any, name: str) -> list[str]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"{name} must be a nonempty string list")
    return value



def _command(value: Any, name: str) -> None:
    if not isinstance(value, list) or not value or any(not isinstance(x, str) or not x for x in value): raise ValueError(f"{name} command must be a nonempty argv array")

def _permissions(value: Any) -> None:
    permissions = _exact(value, {"approval_mode", "tools"}, "permissions")
    if permissions["approval_mode"] not in {"always-ask", "write", "yolo"}: raise ValueError("invalid approval mode")
    tools = permissions["tools"]
    if not isinstance(tools, list) or not tools or len(tools) != len(set(tools)) or any(not isinstance(tool, str) or re.fullmatch(r"[A-Za-z0-9_-]+", tool) is None for tool in tools):
        raise ValueError("permissions tools must be unique OMP tool names")
    if not set(tools) <= _SAFE_TOOLS:
        raise ValueError("permissions must exclude process-capable and out-of-workspace tools")

def validate_treatment_isolation(baseline: str, candidate: str, treatment: str) -> None:
    if baseline != "": raise ValueError("baseline instruction must be empty")
    if candidate != treatment or not treatment: raise ValueError("candidate instruction must exactly equal the treatment")

def _number(value: Any, name: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be a number in [{minimum}, {maximum}]")
    return float(value)

def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value

def _validate_spec(spec: dict[str, Any], repository: Path) -> None:
    _exact(spec, _SPEC, "spec")
    if spec["schema_version"] != 1: raise ValueError("unsupported schema version")
    _permissions(spec["permissions"])
    design = _exact(spec["design_review"], {"desired_behavior", "competing_behavior", "candidate_injection_point", "falsifiable_hypothesis", "architecture_leak_review"}, "design review")
    for key in ("desired_behavior", "competing_behavior", "candidate_injection_point", "falsifiable_hypothesis"):
        _nonempty(design[key], key)
    leak_review = _exact(design["architecture_leak_review"], {"approved", "rationale"}, "architecture leak review")
    if leak_review["approved"] is not True:
        raise ValueError("architecture leak review must approve architecture-neutral prompts")
    _nonempty(leak_review["rationale"], "architecture leak review rationale")
    for key in ("instruction", "provider", "model", "thinking"): _nonempty(spec[key], key)
    _positive_int(spec["trials_per_condition"], "trials_per_condition")
    _positive_int(spec["timeout_seconds"], "timeout_seconds")
    _number(spec["practical_effect_threshold"], "practical_effect_threshold", 0, 1)
    _number(spec["significance_level"], "significance_level", 0, 1)
    if spec["significance_level"] == 0: raise ValueError("significance_level must be greater than 0")
    if not isinstance(spec["scenarios"], list) or not spec["scenarios"]: raise ValueError("scenarios must be nonempty")
    types: set[str] = set()
    scenario_ids: set[str] = set()
    failure_mechanisms: set[str] = set()
    for scenario in spec["scenarios"]:
        required = {"id", "type", "fixture", "failure_mechanism", "public_behavior", "hidden_invariants", "disallowed_shortcuts", "initial_request", "checkpoints", "public_tests", "hidden_tests", "rubric"}
        _exact(scenario, required, "scenario")
        scenario_id = _nonempty(scenario["id"], "scenario id")
        if scenario_id in scenario_ids:
            raise ValueError("scenario ids must be unique")
        scenario_ids.add(scenario_id)
        failure_mechanism = _nonempty(scenario["failure_mechanism"], "failure mechanism")
        if failure_mechanism in failure_mechanisms:
            raise ValueError("scenario failure mechanisms must be distinct")
        failure_mechanisms.add(failure_mechanism)
        _nonempty(scenario["public_behavior"], "public behavior")
        _strings(scenario["hidden_invariants"], "hidden invariants")
        _strings(scenario["disallowed_shortcuts"], "disallowed shortcuts")
        _nonempty(scenario["initial_request"], "initial_request")
        if scenario["type"] not in _TYPES: raise ValueError("invalid scenario type")
        types.add(scenario["type"])
        fixture = Path(_nonempty(scenario["fixture"], "fixture"))
        fixture_path = fixture if fixture.is_absolute() else repository / fixture
        if not fixture_path.is_dir(): raise ValueError("scenario fixture is not a directory")
        runner._reject_symlinks(fixture_path)
        if not isinstance(scenario["checkpoints"], list) or not scenario["checkpoints"]: raise ValueError("checkpoints must be nonempty")
        checkpoint_ids: set[str] = set()
        for checkpoint in scenario["checkpoints"]:
            _exact(checkpoint, {"id", "request"}, "checkpoint")
            checkpoint_id = _nonempty(checkpoint["id"], "checkpoint id")
            if checkpoint_id in checkpoint_ids: raise ValueError("checkpoint ids must be unique within a scenario")
            checkpoint_ids.add(checkpoint_id)
            _nonempty(checkpoint["request"], "checkpoint request")
        test_ids: set[str] = set()
        for tests, hidden in ((scenario["public_tests"], False), (scenario["hidden_tests"], True)):
            if not isinstance(tests, list) or not tests: raise ValueError("test list must be nonempty")
            for test in tests:
                required_test = {"id", "command", "expected_outcome"} | ({"kind", "fixture", "oracle_expected_outcome"} if hidden else set())
                _exact(test, required_test, "test")
                test_id = _nonempty(test["id"], "test id")
                if test_id in test_ids: raise ValueError("test ids must be unique within a scenario")
                test_ids.add(test_id)
                _command(test["command"], "test")
                if test["expected_outcome"] not in {"pass", "fail"}: raise ValueError("invalid expected outcome")
                if hidden:
                    if test["kind"] not in _KINDS: raise ValueError("invalid hidden test kind")
                    if test["oracle_expected_outcome"] not in {"pass", "fail"}: raise ValueError("invalid oracle expected outcome")
                    probe_fixture = Path(_nonempty(test["fixture"], "hidden test fixture"))
                    probe_path = probe_fixture if probe_fixture.is_absolute() else repository / probe_fixture
                    if not probe_path.is_dir(): raise ValueError("hidden test fixture is not a directory")
                    runner._reject_symlinks(probe_path)
                    resolved_fixture, resolved_probe = fixture_path.resolve(), probe_path.resolve()
                    if resolved_fixture == resolved_probe or resolved_fixture.is_relative_to(resolved_probe) or resolved_probe.is_relative_to(resolved_fixture):
                        raise ValueError("hidden test fixture must be outside the scenario fixture")
        if {x["kind"] for x in scenario["hidden_tests"]} != _KINDS: raise ValueError("each scenario needs every hidden kind")
        rubric = _exact(scenario["rubric"], {"preferred_architecture", "archetypes", "dimensions"}, "rubric"); _nonempty(rubric["preferred_architecture"], "preferred_architecture")
        if not isinstance(rubric["archetypes"], dict) or not rubric["archetypes"] or any(not isinstance(v, str) or not v for v in rubric["archetypes"].values()): raise ValueError("rubric archetypes are invalid")
        if rubric["preferred_architecture"] not in rubric["archetypes"]: raise ValueError("preferred_architecture must name a declared archetype")
        if set(rubric["dimensions"]) != _DIMENSIONS or any(not isinstance(v, str) or not v for v in rubric["dimensions"].values()): raise ValueError("rubric dimensions are invalid")
    if types != _TYPES: raise ValueError("all required scenario types are mandatory")
    rules = _exact(spec["decision_rules"], {"behavior_preservation", "reproducibility", "checkpoint_effect", "control"}, "decision_rules")
    _exact(rules["behavior_preservation"], {"minimum_candidate_rate", "maximum_regression"}, "behavior_preservation")
    _number(rules["behavior_preservation"]["minimum_candidate_rate"], "minimum_candidate_rate", 0, 1)
    _number(rules["behavior_preservation"]["maximum_regression"], "maximum_regression", 0, 1)
    _exact(rules["reproducibility"], {"minimum_trials"}, "reproducibility")
    _positive_int(rules["reproducibility"]["minimum_trials"], "minimum_trials")
    _exact(rules["checkpoint_effect"], {"required", "minimum_delta"}, "checkpoint_effect")
    if not isinstance(rules["checkpoint_effect"]["required"], bool): raise ValueError("checkpoint_effect.required must be boolean")
    _number(rules["checkpoint_effect"]["minimum_delta"], "checkpoint_effect.minimum_delta", -1, 1)
    _exact(rules["control"], {"required", "minimum_preferred_rate_delta"}, "control")
    if not isinstance(rules["control"]["required"], bool): raise ValueError("control.required must be boolean")
    _number(rules["control"]["minimum_preferred_rate_delta"], "control.minimum_preferred_rate_delta", -1, 1)

def freeze_protocol(request: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    _exact(request, _REQUEST, "request")
    repository = Path(_nonempty(request["repository"], "repository")).resolve()
    if not repository.is_dir(): raise ValueError("repository is not a directory")
    _validate_spec(spec, repository)
    for key in ("instruction", "provider", "model", "thinking", "trials_per_condition", "practical_effect_threshold", "significance_level"):
        if request[key] != spec[key]: raise ValueError(f"request and spec disagree on {key}")
    assets = spec.get("assets")
    if assets is not None: raise ValueError("assets belong to the frozen protocol source configuration")
    root = Path(__file__).resolve().parents[1] / "assets"
    paths = {"initial_template": root / "templates" / "initial-request.txt", "checkpoint_template": root / "templates" / "checkpoint-request.txt", "scorer_template": root / "templates" / "architecture-score.txt", "rubric_template": root / "templates" / "architecture-rubric.json", "score_schema": root / "schemas" / "architecture-score.schema.json"}
    if any(not path.is_file() for path in paths.values()): raise ValueError("shipped frozen assets are missing")
    scenarios = [{**scenario, "fixture": str((Path(scenario["fixture"]) if Path(scenario["fixture"]).is_absolute() else repository / scenario["fixture"]).resolve()), "hidden_tests": [{**test, "fixture": str((Path(test["fixture"]) if Path(test["fixture"]).is_absolute() else repository / test["fixture"]).resolve())} for test in scenario["hidden_tests"]]} for scenario in spec["scenarios"]]
    protocol = {**spec, "scenarios": scenarios, "run_id": request["run_id"], "conditions": {"baseline": {"instruction": ""}, "candidate": {"instruction": spec["instruction"]}}, "execution": {"timeout_seconds": spec["timeout_seconds"]}, "assets": {key: str(path.resolve()) for key, path in paths.items()}}
    validate_treatment_isolation(protocol["conditions"]["baseline"]["instruction"], protocol["conditions"]["candidate"]["instruction"], spec["instruction"])
    return protocol

def manifest(protocol: dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": 1, "run_id": protocol["run_id"], "assets": sorted(protocol["assets"]), "fixtures": [{"scenario": scenario["id"], "root": scenario["fixture"]} for scenario in protocol["scenarios"]], "artifacts": ["request.json", "protocol.json", "manifest.json", "oracle-review.json", "checksums.json"]}
def validate_request(run_dir: Path, protocol: dict[str, Any]) -> None:
    request = _exact(_json(run_dir / "request.json"), _REQUEST, "request")
    for key in ("run_id", "instruction", "provider", "model", "thinking", "trials_per_condition", "practical_effect_threshold", "significance_level"):
        if request[key] != protocol[key]:
            raise ValueError(f"frozen request and protocol disagree on {key}")



def checksums(run_dir: Path, protocol: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in protocol["assets"].items():
        asset = Path(value)
        if asset.is_symlink(): raise ValueError("frozen asset must not be a symlink")
        result[f"asset:{key}"] = _hash(asset)
    for scenario in protocol["scenarios"]:
        fixture = Path(scenario["fixture"])
        runner._reject_symlinks(fixture)
        for path in sorted(fixture.rglob("*")):
            if path.is_file(): result[f"fixture:{scenario['id']}:{path.relative_to(fixture)}"] = _hash(path)
        for probe in scenario["hidden_tests"]:
            probe_fixture = Path(probe["fixture"])
            runner._reject_symlinks(probe_fixture)
            for path in sorted(probe_fixture.rglob("*")):
                if path.is_file(): result[f"probe_fixture:{scenario['id']}:{probe['id']}:{path.relative_to(probe_fixture)}"] = _hash(path)
    result["protocol.json"] = _hash(run_dir / "protocol.json")
    result["manifest.json"] = _hash(run_dir / "manifest.json")
    result["oracle-review.json"] = _hash(run_dir / "oracle-review.json")
    result["request.json"] = _hash(run_dir / "request.json")
    return result

def validate_oracle_review(run_dir: Path, protocol: dict[str, Any]) -> None:
    review = _exact(_json(run_dir / "oracle-review.json"), {"run_id", "passed", "outcomes"}, "oracle review")
    if review["run_id"] != protocol["run_id"] or review["passed"] is not True:
        raise ValueError("oracle review does not authorize the frozen run")
    outcomes = review["outcomes"]
    if not isinstance(outcomes, list):
        raise ValueError("oracle review outcomes are invalid")
    expected = {(scenario["id"], probe["id"]): probe for scenario in protocol["scenarios"] for probe in scenario["hidden_tests"]}
    if len(outcomes) != len(expected):
        raise ValueError("oracle review does not cover every frozen probe")
    seen: set[tuple[str, str]] = set()
    fields = {"scenario", "kind", "id", "command", "expected_outcome", "argv", "exit_code", "stdout", "stderr", "timed_out", "passed"}
    for value in outcomes:
        outcome = _exact(value, fields, "oracle outcome")
        key = (_nonempty(outcome["scenario"], "oracle scenario"), _nonempty(outcome["id"], "oracle probe id"))
        probe = expected.get(key)
        if probe is None or key in seen:
            raise ValueError("oracle review contains unknown or duplicate probes")
        seen.add(key)
        if outcome["kind"] != probe["kind"] or outcome["command"] != probe["command"] or outcome["argv"] != probe["command"] or outcome["expected_outcome"] != probe["oracle_expected_outcome"]:
            raise ValueError("oracle outcome disagrees with the frozen probe")
        semantic_pass = outcome["timed_out"] is False and isinstance(outcome["exit_code"], int) and ((outcome["exit_code"] == 0) == (probe["oracle_expected_outcome"] == "pass"))
        if outcome["passed"] is not True or not semantic_pass or not isinstance(outcome["stdout"], str) or not isinstance(outcome["stderr"], str):
            raise ValueError("oracle outcome did not pass")



def validate_frozen(run_dir: Path, protocol: dict[str, Any]) -> None:
    _exact(protocol, _PROTOCOL, "protocol")
    _permissions(protocol["permissions"])
    validate_request(run_dir, protocol)
    validate_oracle_review(run_dir, protocol)
    if _json(run_dir / "manifest.json") != manifest(protocol): raise ValueError("frozen manifest does not match the protocol")
    frozen = _json(run_dir / "checksums.json")
    if not isinstance(frozen, dict) or frozen != checksums(run_dir, protocol): raise ValueError("frozen protocol or assets changed; amend and refreeze")

def oracle_review(protocol: dict[str, Any]) -> dict[str, Any]:
    outcomes: list[dict[str, Any]] = []
    timeout = protocol["execution"]["timeout_seconds"]
    with TemporaryDirectory(prefix="instruct-eval-oracles-") as directory:
        oracle_root = Path(directory)
        for scenario in protocol["scenarios"]:
            for probe in scenario["hidden_tests"]:
                workspace = oracle_root / scenario["id"] / probe["id"]
                runner._copy(Path(probe["fixture"]), workspace)
                oracle_probe = {**probe, "expected_outcome": probe["oracle_expected_outcome"]}
                outcome = runner._tests(workspace, [oracle_probe], timeout)["outcomes"][0]
                outcomes.append({"scenario": scenario["id"], "kind": probe["kind"], **outcome})
    review = {"run_id": protocol["run_id"], "passed": all(item["passed"] for item in outcomes), "outcomes": outcomes}
    if not review["passed"]: raise ValueError("oracle review failed")
    return review


def amend(protocol: dict[str, Any], amendment: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    validate_frozen(run_dir, protocol)
    _exact(amendment, {"reason", "changes", "new_run_id"}, "amendment")
    reason = _nonempty(amendment["reason"], "amendment reason")
    if not isinstance(amendment["changes"], dict): raise ValueError("amendment changes must be an object")
    new_run_id = _nonempty(amendment["new_run_id"], "amendment new_run_id")
    if new_run_id in {".", ".."} or Path(new_run_id).name != new_run_id:
        raise ValueError("successor run id must name a sibling")
    successor = run_dir.parent / new_run_id
    if successor.resolve().parent != run_dir.parent.resolve():
        raise ValueError("successor run id must remain beneath the evidence root")
    if successor.exists() or new_run_id == protocol["run_id"]:
        raise ValueError("successor run must be distinct and new")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-%f")
    archive = run_dir / "invalidated" / stamp; archive.mkdir(parents=True)
    moved: dict[str, dict[str, str]] = {}
    for path in (run_dir / name for name in ("trials.json", "alias-map.json", "scores.json", "analysis.json", "comparative-review.json", "verification.json")):
        if path.exists():
            target = archive / path.name; shutil.move(str(path), str(target)); moved[path.name] = {"sha256": _hash(target), "archive_path": str(target.relative_to(run_dir))}; os.chmod(target, 0o444)
    record = {"at": datetime.now(timezone.utc).isoformat(), "reason": reason, "changes": amendment["changes"], "new_run_id": new_run_id, "archive": str(archive.relative_to(run_dir)), "invalidated": moved}
    history_path = run_dir / "amendments.json"; history = _json(history_path) if history_path.exists() else {"amendments": []}
    if not isinstance(history, dict) or set(history) != {"amendments"} or not isinstance(history["amendments"], list): raise ValueError("invalid amendment history")
    history["amendments"].append(record); _json(history_path, history)
    request = _json(run_dir / "request.json")
    successor_request = {**request, "run_id": new_run_id, "created_at": datetime.now(timezone.utc).isoformat()}
    successor.mkdir()
    _json(successor / "request.json", successor_request)
    _json(successor / "amendment.json", {"parent_run_id": protocol["run_id"], **record})
    return record

def validate_run(run_dir: Path, protocol: dict[str, Any]) -> dict[str, Any]:
    validate_frozen(run_dir, protocol)
    for name in ("trials.json", "scores.json", "analysis.json", "comparative-review.json"):
        if not (run_dir / name).is_file(): raise ValueError(f"required evidence missing: {name}")
    saved_analysis = _json(run_dir / "analysis.json")
    recomputed = analysis.analyze(run_dir, protocol, write=False)
    if saved_analysis != recomputed: raise ValueError("saved analysis does not match recomputed evidence")
    review = _json(run_dir / "comparative-review.json")
    if review != recomputed.get("comparative_review"): raise ValueError("saved comparative review does not match recomputed evidence")
    if not isinstance(recomputed.get("authorized"), bool): raise ValueError("analysis authorization decision is invalid")
    result = {"valid": True, "run_id": protocol["run_id"], "authorized": recomputed["authorized"], "decision": "authorize" if recomputed["authorized"] else "do_not_authorize", "checked": ["request.json", "protocol.json", "manifest.json", "oracle-review.json", "checksums.json", "trials.json", "alias-map.json", "scores.json", "analysis.json", "comparative-review.json"]}
    _json(run_dir / "verification.json", result); return result
