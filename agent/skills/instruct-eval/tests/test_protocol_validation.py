import json
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from validation import amend, checksums, freeze_protocol, manifest, validate_frozen, validate_run
from analysis import analyze
from runner import run_trials
from scoring import blind_score
from experiment import freeze, init

KINDS = ("pristine", "complete-reference", "plausible-failure")
TYPES = ("shortcut", "change", "overcorrection")
DIMENSIONS = {"behavior_preserved": "Behavior remains correct.", "change_locality": "Change stays local.", "separation_of_concerns": "Responsibilities remain separated.", "unnecessary_abstraction": "No needless abstraction."}


def scenario(root, scenario_type):
    fixture = root / scenario_type; fixture.mkdir(exist_ok=True); (fixture / "app.txt").write_text("original\n", encoding="utf-8")
    probes = root / "probes" / scenario_type
    for kind in KINDS: (probes / kind).mkdir(parents=True, exist_ok=True)
    return {"id": scenario_type, "type": scenario_type, "fixture": str(fixture), "failure_mechanism": f"{scenario_type} mechanism", "public_behavior": "Public behavior remains correct.", "hidden_invariants": ["Hidden behavior remains correct."], "disallowed_shortcuts": ["Do not hard-code the result."], "initial_request": "Implement behavior.", "checkpoints": [{"id": "follow-up", "request": "Make a follow-up."}], "public_tests": [{"id": "public", "command": [sys.executable, "-c", "pass"], "expected_outcome": "pass"}], "hidden_tests": [{"id": kind, "kind": kind, "fixture": str(probes / kind), "command": [sys.executable, "-c", "pass"], "expected_outcome": "pass", "oracle_expected_outcome": "pass"} for kind in KINDS], "rubric": {"preferred_architecture": "local_state", "archetypes": {"local_state": "State is local."}, "dimensions": DIMENSIONS}}


def request(root):
    return {"schema_version": 1, "run_id": "run-1", "created_at": "2026-01-01T00:00:00Z", "instruction": "Keep state local.", "provider": "openai", "model": "gpt-5", "thinking": "medium", "trials_per_condition": 2, "practical_effect_threshold": .25, "significance_level": .05, "protocol_path": "protocol.json", "repository": str(root), "python_version": "3.12", "omp_version": "1.0"}


def spec(root):
    return {"schema_version": 1, "instruction": "Keep state local.", "provider": "openai", "model": "gpt-5", "thinking": "medium", "trials_per_condition": 2, "practical_effect_threshold": .25, "significance_level": .05, "timeout_seconds": 10, "permissions": {"approval_mode": "write", "tools": ["read", "edit"]}, "design_review": {"desired_behavior": "Implement complete behavior.", "competing_behavior": "Take a narrow shortcut.", "candidate_injection_point": "Initial instruction slot only.", "falsifiable_hypothesis": "Candidate improves preferred outcomes without regression.", "architecture_leak_review": {"approved": True, "rationale": "Prompts describe behavior without architecture labels."}}, "scenarios": [scenario(root, kind) for kind in TYPES], "decision_rules": {"behavior_preservation": {"minimum_candidate_rate": 1.0, "maximum_regression": 0.0}, "reproducibility": {"minimum_trials": 2}, "checkpoint_effect": {"required": True, "minimum_delta": 0.0}, "control": {"required": True, "minimum_preferred_rate_delta": 0.0}}}

def completed_oracle_review(protocol):
    outcomes = []
    for item in protocol["scenarios"]:
        for probe in item["hidden_tests"]:
            outcomes.append({"scenario": item["id"], "kind": probe["kind"], "id": probe["id"], "command": probe["command"], "expected_outcome": probe["oracle_expected_outcome"], "argv": probe["command"], "exit_code": 0, "stdout": "", "stderr": "", "timed_out": False, "passed": True})
    return {"run_id": protocol["run_id"], "passed": True, "outcomes": outcomes}


class ProtocolValidationTests(unittest.TestCase):
    def test_two_argument_freeze_contains_assets_instruction_and_complete_protocol(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); frozen = freeze_protocol(request(root), spec(root))
        self.assertEqual(frozen["instruction"], "Keep state local."); self.assertEqual(frozen["conditions"], {"baseline": {"instruction": ""}, "candidate": {"instruction": "Keep state local."}})
        self.assertEqual(set(frozen["assets"]), {"initial_template", "checkpoint_template", "scorer_template", "rubric_template", "score_schema"}); self.assertEqual({item["type"] for item in frozen["scenarios"]}, set(TYPES))

    def test_freeze_rejects_incomplete_scenario_and_extra_spec_shape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for mutate in (lambda value: value.__setitem__("unexpected", True), lambda value: value.__setitem__("significance_level", 0), lambda value: value["scenarios"][0]["rubric"].__setitem__("preferred_architecture", "undeclared"), lambda value: value["scenarios"][0]["checkpoints"].clear(), lambda value: value["scenarios"][0]["hidden_tests"].pop(), lambda value: value["permissions"].__setitem__("tools", ["read,edit"]), lambda value: value["design_review"]["architecture_leak_review"].__setitem__("approved", False), lambda value: value["scenarios"][1].__setitem__("id", value["scenarios"][0]["id"]), lambda value: value["scenarios"][1].__setitem__("failure_mechanism", value["scenarios"][0]["failure_mechanism"]), lambda value: value["scenarios"][0]["checkpoints"].append(dict(value["scenarios"][0]["checkpoints"][0])), lambda value: value["scenarios"][0]["hidden_tests"][1].__setitem__("id", value["scenarios"][0]["hidden_tests"][0]["id"])):
                candidate = spec(root); mutate(candidate)
                with self.subTest(mutate=mutate), self.assertRaises(ValueError): freeze_protocol(request(root), candidate)

    def test_init_requires_git_repository_root_before_evidence_storage(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            repository = parent / "repository"; repository.mkdir()
            subprocess.run(["git", "init", str(repository)], check=True, capture_output=True, text=True)
            todo = repository / ".todo"; todo.mkdir()
            nested = repository / "nested"; nested.mkdir()
            non_repository = parent / "non-repository"; non_repository.mkdir()
            def args(root, run_id, thinking=None, extra=False):
                path = parent / f"{run_id}.json"
                payload = {"instruction": "Keep state local.", "provider": "openai", "model": "gpt-5", "repository": str(root), "trials_per_condition": 2, "practical_effect_threshold": .25, "significance_level": .05, "run_id": run_id}
                if thinking is not None: payload["thinking"] = thinking
                if extra: payload["unexpected"] = True
                path.write_text(json.dumps(payload), encoding="utf-8")
                return SimpleNamespace(payload=str(path))
            with patch("experiment._preflight", return_value="omp 1.0") as preflight:
                for invalid_root in (todo, nested, non_repository):
                    with self.subTest(root=invalid_root), self.assertRaises(ValueError):
                        init(args(invalid_root, f"invalid-{invalid_root.name}"))
                    self.assertFalse((invalid_root / ".experiments" / "instruct-eval").exists())
                preflight.assert_not_called()
                outside_storage = parent / "outside-storage"; outside_storage.mkdir()
                (repository / ".experiments").symlink_to(outside_storage, target_is_directory=True)
                with self.assertRaisesRegex(ValueError, "symlink"):
                    init(args(repository, "redirected-storage"))
                self.assertFalse((outside_storage / "instruct-eval").exists())
                (repository / ".experiments").unlink()
                with self.assertRaises(ValueError): init(args(repository, "invalid-payload", extra=True))
                request_record = init(args(repository, "valid"))
                override_record = init(args(repository, "override", thinking="high"))
            self.assertEqual(request_record["repository"], str(repository.resolve()))
            self.assertEqual(request_record["thinking"], "medium"); self.assertEqual(override_record["thinking"], "high")
            self.assertTrue((repository / ".experiments" / "instruct-eval" / "valid" / "request.json").is_file())

    def test_checksums_fail_closed_and_amendment_archives_hashes_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); protocol = freeze_protocol(request(root), spec(root)); (root / "request.json").write_text(json.dumps(request(root), sort_keys=True), encoding="utf-8"); (root / "oracle-review.json").write_text(json.dumps({"run_id": "run-1", "passed": True, "outcomes": []}, sort_keys=True), encoding="utf-8"); (root / "protocol.json").write_text(json.dumps(protocol, sort_keys=True), encoding="utf-8"); (root / "manifest.json").write_text(json.dumps(manifest(protocol), sort_keys=True), encoding="utf-8"); (root / "checksums.json").write_text(json.dumps(checksums(root, protocol), sort_keys=True), encoding="utf-8")
            with self.assertRaises(ValueError): validate_frozen(root, protocol)
            (root / "oracle-review.json").write_text(json.dumps(completed_oracle_review(protocol), sort_keys=True), encoding="utf-8"); (root / "checksums.json").write_text(json.dumps(checksums(root, protocol), sort_keys=True), encoding="utf-8")
            validate_frozen(root, protocol)
            original_request = (root / "request.json").read_text(encoding="utf-8"); tampered = request(root); tampered["instruction"] = "Changed."; (root / "request.json").write_text(json.dumps(tampered), encoding="utf-8")
            with self.assertRaises(ValueError): validate_frozen(root, protocol)
            (root / "request.json").write_text(original_request, encoding="utf-8"); (root / "manifest.json").unlink()
            with self.assertRaises((FileNotFoundError, ValueError)): validate_frozen(root, protocol)
            (root / "manifest.json").write_text(json.dumps(manifest(protocol), sort_keys=True), encoding="utf-8"); fixture_path = Path(protocol["scenarios"][0]["fixture"]) / "app.txt"; fixture_path.write_text("changed\n", encoding="utf-8")
            with self.assertRaises(ValueError): validate_frozen(root, protocol)
            fixture_path.write_text("original\n", encoding="utf-8")
            validate_frozen(root, protocol)
            for invalid_id in (".", "..", "../escape"):
                with self.subTest(new_run_id=invalid_id), self.assertRaises(ValueError):
                    amend(protocol, {"reason": "oracle defect", "changes": {"probe": "corrected"}, "new_run_id": invalid_id}, root)
            for name in ("trials.json", "alias-map.json", "scores.json", "analysis.json", "comparative-review.json", "verification.json"): (root / name).write_text(name, encoding="utf-8")
            old_created_at = request(root)["created_at"]
            new_run_id = f"run-2-{root.name}"
            record = amend(protocol, {"reason": "oracle defect", "changes": {"probe": "corrected"}, "new_run_id": new_run_id}, root)
            self.assertTrue((root / record["archive"]).is_dir()); self.assertEqual(set(record["invalidated"]), {"trials.json", "alias-map.json", "scores.json", "analysis.json", "comparative-review.json", "verification.json"})
            self.assertTrue(all("sha256" in value and "archive_path" in value for value in record["invalidated"].values()))
            successor_request = json.loads((root.parent / new_run_id / "request.json").read_text(encoding="utf-8"))
            self.assertNotEqual(successor_request["created_at"], old_created_at); self.assertTrue((root / "checksums.json").is_file())

    def test_freeze_runs_isolated_oracles_and_rejects_same_run_refreeze(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); subprocess.run(["git", "init", str(root)], check=True, capture_output=True, text=True); run_dir = root / ".experiments" / "instruct-eval" / "run-1"; run_dir.mkdir(parents=True)
            candidate = spec(root); (run_dir / "request.json").write_text(json.dumps(request(root)), encoding="utf-8")
            spec_path = root / "spec.json"; spec_path.write_text(json.dumps(candidate), encoding="utf-8")
            args = SimpleNamespace(root=str(root), run_id="run-1", spec=str(spec_path))
            frozen = freeze(args)
            review = json.loads((run_dir / "oracle-review.json").read_text(encoding="utf-8"))
            self.assertTrue(review["passed"]); self.assertEqual(len(review["outcomes"]), 9)
            self.assertTrue((run_dir / "checksums.json").is_file())
            with self.assertRaises(ValueError): freeze(args)
            candidate["scenarios"][0]["hidden_tests"][0]["command"] = [sys.executable, "-c", "raise SystemExit(1)"]
            (root / ".experiments" / "instruct-eval" / "run-2").mkdir()
            failed_request = {**request(root), "run_id": "run-2"}
            (root / ".experiments" / "instruct-eval" / "run-2" / "request.json").write_text(json.dumps(failed_request), encoding="utf-8")
            spec_path.write_text(json.dumps(candidate), encoding="utf-8")
            with self.assertRaises(ValueError): freeze(SimpleNamespace(root=str(root), run_id="run-2", spec=str(spec_path)))
            self.assertFalse((root / ".experiments" / "instruct-eval" / "run-2" / "protocol.json").exists())

    def test_validation_records_do_not_authorize_after_complete_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); protocol = freeze_protocol(request(root), spec(root)); (root / "request.json").write_text(json.dumps(request(root)), encoding="utf-8"); (root / "oracle-review.json").write_text(json.dumps({"run_id": "run-1", "passed": True, "outcomes": []}), encoding="utf-8"); (root / "protocol.json").write_text(json.dumps(protocol), encoding="utf-8"); (root / "manifest.json").write_text(json.dumps(manifest(protocol)), encoding="utf-8"); (root / "checksums.json").write_text(json.dumps(checksums(root, protocol)), encoding="utf-8")
            for name, value in (("trials.json", {"trials": []}), ("scores.json", {"scores": []}), ("comparative-review.json", {}), ("analysis.json", {"decision": "do_not_authorize", "authorized": False})):
                (root / name).write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(ValueError): validate_run(root, protocol)

    def test_controlled_full_lifecycle_recomputes_and_validates_all_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); subprocess.run(["git", "init", str(root)], check=True, capture_output=True, text=True)
            run_dir = root / ".experiments" / "instruct-eval" / "run-1"
            run_dir.mkdir(parents=True)
            candidate = spec(root)
            spec_path = root / "spec.json"
            spec_path.write_text(json.dumps(candidate), encoding="utf-8")
            (run_dir / "request.json").write_text(json.dumps(request(root)), encoding="utf-8")
            protocol = freeze(SimpleNamespace(root=str(root), run_id="run-1", spec=str(spec_path)))
            execution = {"argv": ["omp"], "stdout": "", "stderr": "", "exit_code": 0, "timed_out": False}
            with patch("runner.execute_omp", return_value=execution):
                run_trials(run_dir, protocol)
            raw_score = {"schema_version": 1, "archetype": "local_state", "dimensions": {key: True for key in DIMENSIONS}, "evidence": ["app.txt"], "rationale": "State remains local."}
            with patch("scoring.execute_omp", return_value={**execution, "stdout": json.dumps(raw_score)}):
                blind_score(run_dir, protocol)
            published = analyze(run_dir, protocol)
            verification = validate_run(run_dir, protocol)
            self.assertEqual(verification["authorized"], published["authorized"])
            self.assertEqual(verification["decision"], published["decision"])
            self.assertEqual(set(verification["checked"]), {"request.json", "protocol.json", "manifest.json", "oracle-review.json", "checksums.json", "trials.json", "alias-map.json", "scores.json", "analysis.json", "comparative-review.json"})
            (run_dir / "analysis.json").write_text(json.dumps({**published, "authorized": not published["authorized"]}), encoding="utf-8")
            with self.assertRaises(ValueError):
                validate_run(run_dir, protocol)


if __name__ == "__main__": unittest.main()
