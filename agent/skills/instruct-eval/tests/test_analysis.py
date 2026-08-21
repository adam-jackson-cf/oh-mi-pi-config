import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from string import Template

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from analysis import analyze, fisher_exact_two_sided, wilson_interval
INITIAL_TEMPLATE = ROOT / "assets" / "templates" / "initial-request.txt"


def initial_prompt(condition):
    instruction = "" if condition == "baseline" else "Keep state local."
    return Template(INITIAL_TEMPLATE.read_text(encoding="utf-8")).substitute(
        initial_request="Implement behavior.",
        instruction_block=instruction,
    )




def execution(passed):
    return {"argv": ["omp"], "exit_code": 0 if passed else 1, "stdout": "", "stderr": "", "timed_out": False}


def oracle(probe_id, passed):
    outcome = {"id": probe_id, "command": ["probe"], "expected_outcome": "pass", "argv": ["probe"], "exit_code": 0 if passed else 1, "stdout": "", "stderr": "", "timed_out": False, "passed": passed}
    return {"passed": passed, "outcomes": [outcome]}


def trial(condition, number, passed, checkpoint=True, checkpoint_id="follow-up"):
    digest = {"app.py": "0" * 64}
    checkpoint_record = {"id": checkpoint_id, "workspace": f"checkpoint-{condition}-{number}", "prompt": "Continue.", "omp": execution(checkpoint), "public": oracle("public", checkpoint), "hidden": oracle("hidden", checkpoint), "workspace_checksum_before": digest, "workspace_checksum_after": digest, "changed_files": [], "production_nloc": 1, "passed": checkpoint}
    return {"scenario": "change", "condition": condition, "trial": number, "alias": f"trial-{condition}-{number}", "workspace": f"workspace-{condition}-{number}", "context": f"context-{condition}-{number}", "initial_prompt": initial_prompt(condition), "initial": execution(passed), "public": oracle("public", passed), "hidden": oracle("hidden", passed), "workspace_checksum_before": digest, "workspace_checksum_after": digest, "context_checksum": digest, "checkpoints": [checkpoint_record], "changed_files": [], "production_nloc": 1, "passed": passed}


def score(alias, preferred):
    return {"alias": alias, "scenario": "change", "preferred": preferred, "schema_version": 1, "archetype": "local_state" if preferred else "global_state", "dimensions": {"behavior_preserved": preferred, "change_locality": preferred, "separation_of_concerns": preferred, "unnecessary_abstraction": preferred}, "evidence": ["implementation.py: state is local"], "rationale": "Observed implementation."}


def protocol(trials_per_condition=6, minimum_trials=6):
    public_tests = [{"id": "public", "command": ["probe"], "expected_outcome": "pass"}]
    hidden_tests = [{"id": "hidden", "kind": "pristine", "fixture": "probe", "command": ["probe"], "expected_outcome": "pass"}]
    return {"run_id": "run-1", "assets": {"initial_template": str(INITIAL_TEMPLATE)}, "conditions": {"baseline": {"instruction": ""}, "candidate": {"instruction": "Keep state local."}}, "scenarios": [{"id": "change", "initial_request": "Implement behavior.", "checkpoints": [{"id": "follow-up", "request": "Continue."}], "public_tests": public_tests, "hidden_tests": hidden_tests, "rubric": {"preferred_architecture": "local_state", "archetypes": {"local_state": "Local state.", "global_state": "Global state."}, "dimensions": {"behavior_preserved": "Behavior.", "change_locality": "Locality.", "separation_of_concerns": "Separation.", "unnecessary_abstraction": "Abstraction."}}}], "trials_per_condition": trials_per_condition, "practical_effect_threshold": .5, "significance_level": .05, "decision_rules": {"behavior_preservation": {"minimum_candidate_rate": 1.0, "maximum_regression": 0.0}, "reproducibility": {"minimum_trials": minimum_trials}, "checkpoint_effect": {"required": True, "minimum_delta": 0.0}, "control": {"required": True, "minimum_preferred_rate_delta": 0.0}}}


class AnalysisTests(unittest.TestCase):
    def write_evidence(self, root, trials, preferred):
        aliases = [{"alias": f"implementation-{n:03d}", "scenario": item["scenario"], "condition": item["condition"], "trial": item["trial"], "workspace": item["context"]} for n, item in enumerate(trials, 1)]
        scores = [score(alias["alias"], value) for alias, value in zip(aliases, preferred)]
        (root / "trials.json").write_text(json.dumps({"run_id": "run-1", "trials": trials}), encoding="utf-8")
        (root / "scores.json").write_text(json.dumps({"scores": scores}), encoding="utf-8")
        (root / "alias-map.json").write_text(json.dumps({"aliases": aliases}), encoding="utf-8")
        return aliases, scores

    def trials(self, baseline_passed=False, candidate_passed=True, baseline_checkpoint=False, candidate_checkpoint=True):
        return [trial("baseline", n, baseline_passed, baseline_checkpoint) for n in range(1, 7)] + [trial("candidate", n, candidate_passed, candidate_checkpoint) for n in range(1, 7)]

    def test_wilson_rejects_zero_observations_and_fisher_is_symmetric(self):
        with self.assertRaises(ValueError): wilson_interval(0, 0)
        low, high = wilson_interval(6, 6)
        self.assertLess(low, 1.0); self.assertAlmostEqual(high, 1.0)
        self.assertLess(fisher_exact_two_sided(6, 0, 0, 6), .05)
        self.assertEqual(fisher_exact_two_sided(6, 0, 0, 6), fisher_exact_two_sided(0, 6, 6, 0))

    def test_authorizes_only_when_every_preregistered_rule_passes_and_hashes_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); trials = self.trials(); self.write_evidence(root, trials, [False] * 6 + [True] * 6)
            result = analyze(root, protocol())
            self.assertTrue((root / "analysis.json").is_file()); self.assertTrue((root / "comparative-review.json").is_file())
            self.assertEqual(result["evidence_hashes"], {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in ("trials.json", "alias-map.json", "scores.json")})
        self.assertEqual(result["decision"], "authorize"); self.assertTrue(result["authorized"]); self.assertTrue(all(result["comparative_review"]["rules"].values()))

    def test_write_false_is_deterministic_and_does_not_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); self.write_evidence(root, self.trials(), [False] * 6 + [True] * 6)
            preview = analyze(root, protocol(), write=False)
            self.assertFalse((root / "analysis.json").exists()); self.assertFalse((root / "comparative-review.json").exists())
            published = analyze(root, protocol())
            self.assertEqual(preview, published)
            self.assertEqual(json.loads((root / "analysis.json").read_text(encoding="utf-8")), published)
            self.assertEqual(json.loads((root / "comparative-review.json").read_text(encoding="utf-8")), published["comparative_review"])

    def test_each_failed_rule_records_do_not_authorize(self):
        cases = {"behavior_preservation": (self.trials(True, False, True, True), [False] * 12, protocol()), "checkpoint_effect": (self.trials(False, True, True, False), [False] * 6 + [True] * 6, protocol()), "control": (self.trials(False, True, False, True), [True] * 6 + [False] * 6, protocol()), "reproducibility": ([trial("baseline", 1, False), trial("candidate", 1, True)], [False, True], protocol(1, 6))}
        for name, (trials, preferred, frozen_protocol) in cases.items():
            with self.subTest(rule=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); self.write_evidence(root, trials, preferred); result = analyze(root, frozen_protocol)
                self.assertEqual(result["decision"], "do_not_authorize"); self.assertFalse(result["authorized"]); self.assertFalse(result["comparative_review"]["rules"][name])

    def test_rejects_every_evidence_graph_mismatch(self):
        cases = {
            "missing trial": lambda trials, aliases, scores: trials.pop(),
            "extra trial": lambda trials, aliases, scores: trials.append(trial("change", 1, True)),
            "duplicate trial": lambda trials, aliases, scores: trials.append(dict(trials[0])),
            "missing alias": lambda trials, aliases, scores: aliases.pop(),
            "extra alias": lambda trials, aliases, scores: aliases.append({"alias": "extra", "scenario": "change", "condition": "baseline", "trial": 99, "workspace": "extra"}),
            "duplicate alias key": lambda trials, aliases, scores: aliases.append(dict(aliases[0], alias="another")),
            "alias workspace": lambda trials, aliases, scores: aliases.__setitem__(0, dict(aliases[0], workspace="wrong")),
            "missing score": lambda trials, aliases, scores: scores.pop(),
            "extra score": lambda trials, aliases, scores: scores.append(score(aliases[0]["alias"], True)),
            "unknown score alias": lambda trials, aliases, scores: scores.__setitem__(0, dict(scores[0], alias="unknown")),
            "score scenario": lambda trials, aliases, scores: scores.__setitem__(0, dict(scores[0], scenario="wrong")),
            "score schema": lambda trials, aliases, scores: scores.__setitem__(0, dict(scores[0], schema_version=2)),
            "score archetype": lambda trials, aliases, scores: scores.__setitem__(0, dict(scores[0], archetype="undeclared")),
            "score preference": lambda trials, aliases, scores: scores.__setitem__(0, dict(scores[0], preferred=not scores[0]["preferred"])),
            "score dimensions": lambda trials, aliases, scores: scores.__setitem__(0, dict(scores[0], dimensions={"behavior_preserved": True})),
            "score evidence": lambda trials, aliases, scores: scores.__setitem__(0, dict(scores[0], evidence=[])),
            "score rationale": lambda trials, aliases, scores: scores.__setitem__(0, dict(scores[0], rationale="")),
            "score extra field": lambda trials, aliases, scores: scores.__setitem__(0, dict(scores[0], injected=True)),
            "checkpoint identity": lambda trials, aliases, scores: trials[0]["checkpoints"].__setitem__(0, dict(trials[0]["checkpoints"][0], id="wrong")),
            "trial execution field": lambda trials, aliases, scores: trials[0]["initial"].pop("stdout"),
            "trial isolation field": lambda trials, aliases, scores: trials[0].pop("context_checksum"),
            "context edge": lambda trials, aliases, scores: trials[0].__setitem__("context_checksum", {"app.py": "1" * 64}),
            "checkpoint edge": lambda trials, aliases, scores: trials[0]["checkpoints"][0].__setitem__("workspace_checksum_before", {"app.py": "1" * 64}),
            "initial treatment": lambda trials, aliases, scores: trials[0].__setitem__("initial_prompt", "unfrozen prompt"),
        }
        for name, mutate in cases.items():
            with self.subTest(mismatch=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); trials = self.trials(); aliases, scores = self.write_evidence(root, trials, [False] * 6 + [True] * 6)
                mutate(trials, aliases, scores)
                (root / "trials.json").write_text(json.dumps({"run_id": "run-1", "trials": trials}), encoding="utf-8")
                (root / "scores.json").write_text(json.dumps({"scores": scores}), encoding="utf-8")
                (root / "alias-map.json").write_text(json.dumps({"aliases": aliases}), encoding="utf-8")
                with self.assertRaises(ValueError): analyze(root, protocol(), write=False)

    def test_rejects_trial_evidence_from_another_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_evidence(root, self.trials(), [False] * 6 + [True] * 6)
            evidence = json.loads((root / "trials.json").read_text(encoding="utf-8"))
            evidence["run_id"] = "another-run"
            (root / "trials.json").write_text(json.dumps(evidence), encoding="utf-8")
            with self.assertRaises(ValueError):
                analyze(root, protocol(), write=False)


if __name__ == "__main__": unittest.main()
