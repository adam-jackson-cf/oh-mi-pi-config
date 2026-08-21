"""Statistical analysis and preregistered decision enforcement."""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import TypeAlias
from string import Template

JsonObject: TypeAlias = dict[str, object]
EvidenceKey: TypeAlias = tuple[str, str, int]
_DIMENSIONS = {"behavior_preserved", "change_locality", "separation_of_concerns", "unnecessary_abstraction"}
_TRIAL_FIELDS = {"scenario", "condition", "trial", "alias", "workspace", "context", "initial_prompt", "initial", "public", "hidden", "workspace_checksum_before", "workspace_checksum_after", "context_checksum", "changed_files", "production_nloc", "checkpoints", "passed"}
_CHECKPOINT_FIELDS = {"id", "workspace", "prompt", "omp", "public", "hidden", "workspace_checksum_before", "workspace_checksum_after", "changed_files", "production_nloc", "passed"}
_EXECUTION_FIELDS = {"argv", "exit_code", "stdout", "stderr", "timed_out"}
_PROBE_FIELDS = {"id", "command", "expected_outcome", "argv", "exit_code", "stdout", "stderr", "timed_out", "passed"}




def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if not isinstance(successes, int) or not isinstance(total, int) or total < 1 or successes < 0 or successes > total:
        raise ValueError("invalid Wilson interval counts")
    proportion = successes / total
    denominator = 1 + z**2 / total
    centre = (proportion + z**2 / (2 * total)) / denominator
    margin = z * math.sqrt((proportion * (1 - proportion) + z**2 / (4 * total)) / total) / denominator
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def fisher_exact_two_sided(a: int, b: int, c: int, d: int) -> float:
    if any(not isinstance(value, int) or value < 0 for value in (a, b, c, d)) or a + b + c + d == 0:
        raise ValueError("invalid Fisher exact table")
    first, second, total = a + b, c + d, a + c
    low, high = max(0, total - second), min(first, total)

    def probability(value: int) -> float:
        return math.comb(first, value) * math.comb(second, total - value) / math.comb(first + second, total)

    observed = probability(a)
    return min(1.0, sum(probability(value) for value in range(low, high + 1) if probability(value) <= observed + 1e-12))


def _object(value: object, name: str) -> JsonObject:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"invalid {name} evidence")
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _json(path: Path, name: str) -> JsonObject:
    try:
        value: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid {name} evidence") from error
    return _object(value, name)


def _list(value: object, name: str) -> list[object]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{name} is missing")
    return value

def _array(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{name} is invalid")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} is invalid")
    return value


def _positive_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} is invalid")
    return value

def _nonnegative_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} is invalid")
    return value

def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} is invalid")
    return float(value)

def _exact_object(value: object, fields: set[str], name: str) -> JsonObject:
    result = _object(value, name)
    if set(result) != fields:
        raise ValueError(f"{name} has missing or extra fields")
    return result


def _string_list(value: object, name: str, empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not empty and not value) or any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"{name} is invalid")
    return value


def _hash_map(value: object, name: str) -> dict[str, str]:
    record = _object(value, name)
    if any(not isinstance(item, str) or len(item) != 64 for item in record.values()):
        raise ValueError(f"{name} is invalid")
    return {key: item for key, item in record.items() if isinstance(item, str)}
def _initial_prompt(protocol: JsonObject, scenario: JsonObject, condition: str) -> str:
    assets = _object(protocol.get("assets"), "frozen protocol assets")
    template_path = Path(_string(assets.get("initial_template"), "initial prompt template"))
    try:
        template = Template(template_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError("initial prompt template is unavailable") from error
    names = {
        match.group("named") or match.group("braced")
        for match in template.pattern.finditer(template.template)
        if match.group("named") or match.group("braced")
    }
    if names != {"initial_request", "instruction_block"}:
        raise ValueError("initial prompt template has invalid placeholders")
    conditions = _object(protocol.get("conditions"), "frozen protocol conditions")
    condition_record = _object(conditions.get(condition), "frozen trial condition")
    instruction = condition_record.get("instruction")
    if not isinstance(instruction, str):
        raise ValueError("frozen trial instruction is invalid")
    try:
        return template.substitute(
            initial_request=_string(scenario.get("initial_request"), "frozen initial request"),
            instruction_block=instruction,
        )
    except (KeyError, ValueError) as error:
        raise ValueError("initial prompt template rendering failed") from error




def _execution(value: object, name: str) -> JsonObject:
    execution = _exact_object(value, _EXECUTION_FIELDS, name)
    _string_list(execution.get("argv"), f"{name} argv")
    if execution.get("exit_code") is not None and (isinstance(execution.get("exit_code"), bool) or not isinstance(execution.get("exit_code"), int)):
        raise ValueError(f"{name} exit code is invalid")
    if not isinstance(execution.get("stdout"), str) or not isinstance(execution.get("stderr"), str) or type(execution.get("timed_out")) is not bool:
        raise ValueError(f"{name} streams or timeout state are invalid")
    return execution


def _probe_bundle(value: object, frozen: list[object], name: str) -> JsonObject:
    bundle = _exact_object(value, {"passed", "outcomes"}, name)
    if type(bundle.get("passed")) is not bool:
        raise ValueError(f"{name} pass state is invalid")
    outcomes = _list(bundle.get("outcomes"), f"{name} outcomes")
    if len(outcomes) != len(frozen):
        raise ValueError(f"{name} does not cover the frozen probes")
    passed = True
    for outcome_value, frozen_value in zip(outcomes, frozen):
        outcome = _exact_object(outcome_value, _PROBE_FIELDS, f"{name} outcome")
        probe = _object(frozen_value, f"{name} frozen probe")
        if outcome.get("id") != probe.get("id") or outcome.get("command") != probe.get("command") or outcome.get("argv") != probe.get("command") or outcome.get("expected_outcome") != probe.get("expected_outcome"):
            raise ValueError(f"{name} outcome disagrees with the frozen probe")
        execution = _execution({key: outcome[key] for key in _EXECUTION_FIELDS}, f"{name} probe execution")
        expected_pass = execution["timed_out"] is False and isinstance(execution["exit_code"], int) and ((execution["exit_code"] == 0) == (probe.get("expected_outcome") == "pass"))
        if outcome.get("passed") != expected_pass:
            raise ValueError(f"{name} outcome pass state is inconsistent")
        passed = passed and expected_pass
    if bundle["passed"] != passed:
        raise ValueError(f"{name} aggregate pass state is inconsistent")
    return bundle


def _validate_trial_record(trial: JsonObject, scenario: JsonObject, expected_initial_prompt: str) -> None:
    if set(trial) != _TRIAL_FIELDS:
        raise ValueError("trial record has missing or extra fields")
    _string(trial.get("alias"), "trial alias")
    _string(trial.get("workspace"), "trial workspace")
    _string(trial.get("context"), "trial context")
    if _string(trial.get("initial_prompt"), "initial prompt") != expected_initial_prompt:
        raise ValueError("initial prompt disagrees with the frozen treatment")
    initial = _execution(trial.get("initial"), "initial execution")
    public_tests = _list(scenario.get("public_tests"), "frozen public tests")
    hidden_tests = _list(scenario.get("hidden_tests"), "frozen hidden tests")
    public = _probe_bundle(trial.get("public"), public_tests, "public oracle")
    hidden = _probe_bundle(trial.get("hidden"), hidden_tests, "hidden oracle")
    before = _hash_map(trial.get("workspace_checksum_before"), "workspace checksum before")
    after = _hash_map(trial.get("workspace_checksum_after"), "workspace checksum after")
    context = _hash_map(trial.get("context_checksum"), "context checksum")
    if context != after:
        raise ValueError("checkpoint context does not match the completed initial workspace")
    changed = _string_list(trial.get("changed_files"), "changed files", empty=True)
    if changed != sorted(key for key in set(before) | set(after) if before.get(key) != after.get(key)):
        raise ValueError("changed-file evidence disagrees with workspace checksums")
    if type(trial.get("passed")) is not bool or trial["passed"] != (initial.get("exit_code") == 0 and public["passed"] is True and hidden["passed"] is True):
        raise ValueError("trial pass state is inconsistent")
    checkpoints = _list(trial.get("checkpoints"), "trial checkpoints")
    expected = {_string(_object(value, "frozen checkpoint").get("id"), "frozen checkpoint id"): _object(value, "frozen checkpoint") for value in _list(scenario.get("checkpoints"), "frozen checkpoints")}
    if len(checkpoints) != len(expected):
        raise ValueError("checkpoint evidence does not cover the frozen plan")
    for value in checkpoints:
        checkpoint = _exact_object(value, _CHECKPOINT_FIELDS, "checkpoint")
        checkpoint_id = _string(checkpoint.get("id"), "checkpoint id")
        frozen_checkpoint = expected.get(checkpoint_id)
        if frozen_checkpoint is None or checkpoint.get("prompt") != frozen_checkpoint.get("request"):
            raise ValueError("checkpoint prompt disagrees with the frozen request")
        _string(checkpoint.get("workspace"), "checkpoint workspace")
        omp = _execution(checkpoint.get("omp"), "checkpoint execution")
        checkpoint_public = _probe_bundle(checkpoint.get("public"), public_tests, "checkpoint public oracle")
        checkpoint_hidden = _probe_bundle(checkpoint.get("hidden"), hidden_tests, "checkpoint hidden oracle")
        checkpoint_before = _hash_map(checkpoint.get("workspace_checksum_before"), "checkpoint checksum before")
        checkpoint_after = _hash_map(checkpoint.get("workspace_checksum_after"), "checkpoint checksum after")
        if checkpoint_before != context:
            raise ValueError("checkpoint did not start from the frozen initial context")
        checkpoint_changed = _string_list(checkpoint.get("changed_files"), "checkpoint changed files", empty=True)
        if checkpoint_changed != sorted(key for key in set(checkpoint_before) | set(checkpoint_after) if checkpoint_before.get(key) != checkpoint_after.get(key)):
            raise ValueError("checkpoint changed-file evidence disagrees with checksums")
        checkpoint_nloc = checkpoint.get("production_nloc")
        if isinstance(checkpoint_nloc, bool) or not isinstance(checkpoint_nloc, int) or checkpoint_nloc < 0:
            raise ValueError("checkpoint production NLOC is invalid")
        if type(checkpoint.get("passed")) is not bool or checkpoint["passed"] != (omp.get("exit_code") == 0 and checkpoint_public["passed"] is True and checkpoint_hidden["passed"] is True):
            raise ValueError("checkpoint pass state is inconsistent")




def _passed(record: JsonObject, turn: str) -> bool:
    execution = _object(record.get(turn), "execution record")
    public = _object(record.get("public"), "public oracle")
    hidden = _object(record.get("hidden"), "hidden oracle")
    return execution.get("exit_code") == 0 and public.get("passed") is True and hidden.get("passed") is True


def _behavior(trial: JsonObject) -> bool:
    return _passed(trial, "initial")


def _checkpoint_rate(trial: JsonObject, expected_ids: tuple[str, ...]) -> float:
    checkpoints = _list(trial.get("checkpoints"), "trial checkpoint evidence")
    by_id: dict[str, JsonObject] = {}
    for value in checkpoints:
        checkpoint = _object(value, "checkpoint")
        checkpoint_id = _string(checkpoint.get("id"), "checkpoint id")
        if checkpoint_id in by_id:
            raise ValueError("duplicate checkpoint evidence")
        by_id[checkpoint_id] = checkpoint
    if tuple(sorted(by_id)) != tuple(sorted(expected_ids)):
        raise ValueError("checkpoint evidence does not match frozen protocol")
    return sum(float(_passed(by_id[checkpoint_id], "omp")) for checkpoint_id in expected_ids) / len(expected_ids)


def _rate(values: list[bool]) -> JsonObject:
    if not values:
        raise ValueError("empty evidence group")
    successes = sum(values)
    return {"successes": successes, "total": len(values), "rate": successes / len(values), "wilson_95": list(wilson_interval(successes, len(values)))}


def _conditions(protocol: JsonObject) -> tuple[str, str]:
    conditions = _object(protocol.get("conditions"), "frozen protocol conditions")
    if set(conditions) != {"baseline", "candidate"}:
        raise ValueError("frozen protocol has invalid conditions")
    return "baseline", "candidate"


def _plan(protocol: JsonObject, baseline: str, candidate: str) -> tuple[list[EvidenceKey], dict[str, tuple[str, ...]]]:
    count = _positive_int(protocol.get("trials_per_condition"), "trials per condition")
    scenarios = _list(protocol.get("scenarios"), "frozen protocol scenarios")
    scenario_ids: set[str] = set()
    checkpoint_ids: dict[str, tuple[str, ...]] = {}
    plan: list[EvidenceKey] = []
    for value in scenarios:
        scenario = _object(value, "scenario")
        scenario_id = _string(scenario.get("id"), "scenario id")
        if scenario_id in scenario_ids:
            raise ValueError("duplicate frozen scenario")
        scenario_ids.add(scenario_id)
        checkpoints = _list(scenario.get("checkpoints"), "frozen scenario checkpoints")
        ids: list[str] = []
        for checkpoint_value in checkpoints:
            checkpoint = _object(checkpoint_value, "frozen checkpoint")
            checkpoint_id = _string(checkpoint.get("id"), "frozen checkpoint id")
            if checkpoint_id in ids:
                raise ValueError("duplicate frozen checkpoint")
            ids.append(checkpoint_id)
        checkpoint_ids[scenario_id] = tuple(ids)
        for condition in (baseline, candidate):
            plan.extend((scenario_id, condition, number) for number in range(1, count + 1))
    return plan, checkpoint_ids


def _key(record: JsonObject, name: str) -> EvidenceKey:
    return (
        _string(record.get("scenario"), f"{name} scenario"),
        _string(record.get("condition"), f"{name} condition"),
        _positive_int(record.get("trial"), f"{name} trial"),
    )


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise ValueError("invalid evidence hash input") from error


def analyze(run_dir: Path, protocol: JsonObject, write: bool = True) -> JsonObject:
    """Summarize exact frozen evidence and authorize only on unanimous rule success."""
    trial_path, score_path, alias_path = run_dir / "trials.json", run_dir / "scores.json", run_dir / "alias-map.json"
    trial_root = _json(trial_path, "trial")
    if set(trial_root) != {"run_id", "trials"} or trial_root.get("run_id") != protocol.get("run_id"):
        raise ValueError("trial evidence does not identify the frozen run")
    trials = _list(trial_root.get("trials"), "required trial evidence")
    score_items = _list(_json(score_path, "score").get("scores"), "required score evidence")
    aliases = _list(_json(alias_path, "alias map").get("aliases"), "required alias evidence")
    baseline, candidate = _conditions(protocol)
    plan, checkpoint_ids = _plan(protocol, baseline, candidate)
    expected = set(plan)
    scenario_rubrics: dict[str, JsonObject] = {}
    for scenario_value in _list(protocol.get("scenarios"), "frozen protocol scenarios"):
        scenario = _object(scenario_value, "scenario")
        scenario_id = _string(scenario.get("id"), "scenario id")
        rubric = _object(scenario.get("rubric"), "scenario rubric")
        scenario_rubrics[scenario_id] = rubric
    scenario_by_id = {_string(_object(value, "scenario").get("id"), "scenario id"): _object(value, "scenario") for value in _list(protocol.get("scenarios"), "frozen protocol scenarios")}

    trial_by_key: dict[EvidenceKey, JsonObject] = {}
    for value in trials:
        trial = _object(value, "trial record")
        key = _key(trial, "trial")
        scenario = scenario_by_id.get(key[0])
        if scenario is None:
            raise ValueError("trial scenario is not frozen")
        _validate_trial_record(trial, scenario, _initial_prompt(protocol, scenario, key[1]))
        if key in trial_by_key:
            raise ValueError("duplicate trial evidence")
        trial_by_key[key] = trial
    if set(trial_by_key) != expected:
        raise ValueError("trial evidence does not match frozen plan")

    alias_by_name: dict[str, JsonObject] = {}
    alias_by_key: dict[EvidenceKey, JsonObject] = {}
    for value in aliases:
        alias = _object(value, "alias record")
        name = _string(alias.get("alias"), "alias")
        key = _key(alias, "alias")
        if name in alias_by_name or key in alias_by_key:
            raise ValueError("duplicate alias evidence")
        if key not in expected:
            raise ValueError("alias evidence does not match frozen plan")
        trial = trial_by_key[key]
        if _string(alias.get("workspace"), "alias workspace") != _string(trial.get("context"), "trial context"):
            raise ValueError("alias workspace does not match immutable trial context")
        alias_by_name[name], alias_by_key[key] = alias, alias
    if set(alias_by_key) != expected:
        raise ValueError("alias evidence does not match frozen plan")

    score_by_key: dict[EvidenceKey, JsonObject] = {}
    for value in score_items:
        score = _object(value, "score record")
        alias = alias_by_name.get(_string(score.get("alias"), "score alias"))
        if alias is None:
            raise ValueError("score cannot be joined to blinded evidence")
        key = _key(alias, "alias")
        if key in score_by_key:
            raise ValueError("duplicate score evidence")
        if set(score) != {"alias", "scenario", "preferred", "schema_version", "archetype", "dimensions", "evidence", "rationale"}:
            raise ValueError("score evidence does not have the exact persisted fields")
        if _string(score.get("scenario"), "score scenario") != key[0] or score.get("schema_version") != 1:
            raise ValueError("score evidence is inconsistent with alias evidence")
        rubric = scenario_rubrics[key[0]]
        rubric_archetypes = _object(rubric.get("archetypes"), "scenario archetypes")
        archetype = _string(score.get("archetype"), "score archetype")
        if archetype not in rubric_archetypes:
            raise ValueError("score evidence names an undeclared archetype")
        preferred_architecture = _string(rubric.get("preferred_architecture"), "preferred architecture")
        if type(score.get("preferred")) is not bool or score["preferred"] != (archetype == preferred_architecture):
            raise ValueError("score preference does not match the frozen rubric")
        dimensions = _object(score.get("dimensions"), "score dimensions")
        if set(dimensions) != _DIMENSIONS or any(type(item) is not bool for item in dimensions.values()):
            raise ValueError("score dimensions are invalid")
        evidence = _list(score.get("evidence"), "score evidence")
        if any(not isinstance(item, str) or not item.strip() for item in evidence):
            raise ValueError("score evidence citations are invalid")
        _string(score.get("rationale"), "score rationale")
        score_by_key[key] = score
    if set(score_by_key) != expected:
        raise ValueError("score evidence does not match frozen plan")

    grouped: dict[str, dict[str, JsonObject]] = {}
    aggregate_behavior: dict[str, list[bool]] = {baseline: [], candidate: []}
    aggregate_checkpoints: dict[str, list[float]] = {baseline: [], candidate: []}
    preferred: dict[str, list[bool]] = {baseline: [], candidate: []}
    archetypes: dict[str, Counter[str]] = {baseline: Counter(), candidate: Counter()}
    changed_files: dict[str, Counter[str]] = {baseline: Counter(), candidate: Counter()}
    production_nloc: dict[str, list[int]] = {baseline: [], candidate: []}

    for scenario, condition, number in plan:
        trial = trial_by_key[(scenario, condition, number)]
        changed = _array(trial.get("changed_files"), "changed-file evidence")
        if any(not isinstance(item, str) for item in changed):
            raise ValueError("trial has invalid changed-file evidence")
        nloc = trial.get("production_nloc")
        if isinstance(nloc, bool) or not isinstance(nloc, int) or nloc < 0:
            raise ValueError("trial has invalid production-NLOC evidence")
        passed = _behavior(trial)
        checkpoint = _checkpoint_rate(trial, checkpoint_ids[scenario])
        score = score_by_key[(scenario, condition, number)]
        preferred_value = score.get("preferred")
        if type(preferred_value) is not bool:
            raise ValueError("score preferred evidence is invalid")
        archetype = _string(score.get("archetype"), "score archetype")
        aggregate_behavior[condition].append(passed)
        aggregate_checkpoints[condition].append(checkpoint)
        preferred[condition].append(preferred_value)
        archetypes[condition][archetype] += 1
        changed_files[condition].update(item for item in changed if isinstance(item, str))
        production_nloc[condition].append(nloc)
        scenario_group = grouped.setdefault(scenario, {baseline: {"behavior": [], "checkpoint": [], "architecture": [], "archetypes": Counter[str](), "changed_files": Counter[str](), "production_nloc": []}, candidate: {"behavior": [], "checkpoint": [], "architecture": [], "archetypes": Counter[str](), "changed_files": Counter[str](), "production_nloc": []}})
        entry = scenario_group[condition]
        behavior = entry["behavior"]
        checkpoints = entry["checkpoint"]
        architecture = entry["architecture"]
        entry_archetypes = entry["archetypes"]
        entry_changed = entry["changed_files"]
        entry_nloc = entry["production_nloc"]
        if not isinstance(behavior, list) or not isinstance(checkpoints, list) or not isinstance(architecture, list) or not isinstance(entry_archetypes, Counter) or not isinstance(entry_changed, Counter) or not isinstance(entry_nloc, list):
            raise ValueError("internal evidence grouping is invalid")
        behavior.append(passed); checkpoints.append(checkpoint); architecture.append(preferred_value)
        entry_archetypes[archetype] += 1; entry_changed.update(item for item in changed if isinstance(item, str)); entry_nloc.append(nloc)

    def summarize(values: dict[str, JsonObject]) -> JsonObject:
        result: JsonObject = {}
        for condition in (baseline, candidate):
            value = values[condition]
            behavior = value["behavior"]
            architecture = value["architecture"]
            checkpoints = value["checkpoint"]
            archetype_counts = value["archetypes"]
            file_counts = value["changed_files"]
            nloc_values = value["production_nloc"]
            if not isinstance(behavior, list) or not all(type(item) is bool for item in behavior) or not isinstance(architecture, list) or not all(type(item) is bool for item in architecture) or not isinstance(checkpoints, list) or not all(isinstance(item, float) for item in checkpoints) or not isinstance(archetype_counts, Counter) or not isinstance(file_counts, Counter) or not isinstance(nloc_values, list) or not all(isinstance(item, int) for item in nloc_values):
                raise ValueError("internal evidence summary is invalid")
            result[condition] = {"behavior": _rate(behavior), "architecture": _rate(architecture), "checkpoint_rate": sum(checkpoints) / len(checkpoints), "archetypes": dict(sorted(archetype_counts.items())), "changed_files": dict(sorted(file_counts.items())), "production_nloc": {"values": nloc_values, "total": sum(nloc_values), "mean": sum(nloc_values) / len(nloc_values)}}
        return result

    per_scenario = {scenario: summarize(grouped[scenario]) for scenario in checkpoint_ids}
    aggregate = {condition: {"behavior": _rate(aggregate_behavior[condition]), "architecture": _rate(preferred[condition]), "checkpoint_rate": sum(aggregate_checkpoints[condition]) / len(aggregate_checkpoints[condition]), "archetypes": dict(sorted(archetypes[condition].items())), "changed_files": dict(sorted(changed_files[condition].items())), "production_nloc": {"values": production_nloc[condition], "total": sum(production_nloc[condition]), "mean": sum(production_nloc[condition]) / len(production_nloc[condition])}} for condition in (baseline, candidate)}
    behavior = _object(aggregate[candidate], "candidate aggregate")["behavior"]
    baseline_behavior = _object(aggregate[baseline], "baseline aggregate")["behavior"]
    architecture = _object(aggregate[candidate], "candidate aggregate")["architecture"]
    baseline_architecture = _object(aggregate[baseline], "baseline aggregate")["architecture"]
    behavior_rate = _number(_object(behavior, "behavior rate").get("rate"), "behavior rate")
    baseline_rate = _number(_object(baseline_behavior, "behavior rate").get("rate"), "baseline behavior rate")
    architecture_rate = _number(_object(architecture, "architecture rate").get("rate"), "architecture rate")
    baseline_architecture_rate = _number(_object(baseline_architecture, "architecture rate").get("rate"), "baseline architecture rate")
    candidate_checkpoint = _number(_object(aggregate[candidate], "candidate aggregate").get("checkpoint_rate"), "candidate checkpoint rate")
    baseline_checkpoint = _number(_object(aggregate[baseline], "baseline aggregate").get("checkpoint_rate"), "baseline checkpoint rate")
    behavior_delta, architecture_delta, checkpoint_delta = behavior_rate - baseline_rate, architecture_rate - baseline_architecture_rate, candidate_checkpoint - baseline_checkpoint
    candidate_counts, baseline_counts = _object(behavior, "behavior rate"), _object(baseline_behavior, "behavior rate")
    fisher = fisher_exact_two_sided(_nonnegative_int(baseline_counts.get("successes"), "baseline successes"), _positive_int(baseline_counts.get("total"), "baseline total") - _nonnegative_int(baseline_counts.get("successes"), "baseline successes"), _nonnegative_int(candidate_counts.get("successes"), "candidate successes"), _positive_int(candidate_counts.get("total"), "candidate total") - _nonnegative_int(candidate_counts.get("successes"), "candidate successes"))
    rules = _object(protocol.get("decision_rules"), "frozen decision rules")
    preservation, reproducibility, checkpoint_rule, control = (_object(rules.get(name), name) for name in ("behavior_preservation", "reproducibility", "checkpoint_effect", "control"))
    behavior_ok = behavior_rate >= _number(preservation.get("minimum_candidate_rate"), "minimum candidate rate") and behavior_delta >= -_number(preservation.get("maximum_regression"), "maximum regression")
    reproducibility_ok = min(len(aggregate_behavior[baseline]), len(aggregate_behavior[candidate])) >= _positive_int(reproducibility.get("minimum_trials"), "minimum trials")
    checkpoint_ok = checkpoint_rule.get("required") is False or checkpoint_delta >= _number(checkpoint_rule.get("minimum_delta"), "minimum checkpoint delta")
    control_ok = control.get("required") is False or architecture_delta >= _number(control.get("minimum_preferred_rate_delta"), "minimum preferred rate delta")
    practical_ok = behavior_delta >= _number(protocol.get("practical_effect_threshold"), "practical effect threshold")
    significance_ok = fisher <= _number(protocol.get("significance_level"), "significance level")
    if not all(type(value) is bool for value in (behavior_ok, reproducibility_ok, checkpoint_ok, control_ok, practical_ok, significance_ok)):
        raise ValueError("frozen decision rules are invalid")
    frozen_rules = {"behavior_preservation": behavior_ok, "reproducibility": reproducibility_ok, "checkpoint_effect": checkpoint_ok, "control": control_ok, "practical_effect": practical_ok, "significance": significance_ok}
    comparison: JsonObject = {"behavior_rate_delta": behavior_delta, "architecture_preferred_rate_delta": architecture_delta, "checkpoint_rate_delta": checkpoint_delta, "fisher_exact_two_sided": fisher, "rules": frozen_rules}
    result: JsonObject = {"decision": "authorize" if all(frozen_rules.values()) else "do_not_authorize", "authorized": all(frozen_rules.values()), "aggregate": aggregate, "per_scenario": per_scenario, "comparative_review": comparison, "evidence_hashes": {"trials.json": _sha256(trial_path), "alias-map.json": _sha256(alias_path), "scores.json": _sha256(score_path)}}
    if write:
        (run_dir / "comparative-review.json").write_text(json.dumps(comparison, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (run_dir / "analysis.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result
