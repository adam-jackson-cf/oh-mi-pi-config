import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from scoring import _tree_checksums, blind_score

DIMENSIONS = {"behavior_preserved": True, "change_locality": True, "separation_of_concerns": True, "unnecessary_abstraction": True}


class BlindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.run_dir = Path(self.temp.name)
        self.source = self.run_dir / "source"; self.source.mkdir(); (self.source / "implementation.py").write_text("original", encoding="utf-8")
        checksum = _tree_checksums(self.source)
        trial = {"scenario": "change", "trial": 1, "workspace": str(self.source), "context": str(self.source), "context_checksum": checksum}
        (self.run_dir / "trials.json").write_text(json.dumps({"trials": [{**trial, "condition": "baseline"}, {**trial, "condition": "candidate"}]}), encoding="utf-8")
        self.protocol = {"provider": "openai", "model": "gpt-5", "thinking": "medium", "execution": {"timeout_seconds": 5}, "assets": {"initial_template": str(ROOT / "assets/templates/initial-request.txt"), "checkpoint_template": str(ROOT / "assets/templates/checkpoint-request.txt"), "scorer_template": str(ROOT / "assets/templates/architecture-score.txt"), "rubric_template": str(ROOT / "assets/templates/architecture-rubric.json"), "score_schema": str(ROOT / "assets/schemas/architecture-score.schema.json")}, "scenarios": [{"id": "change", "rubric": {"preferred_architecture": "local_state", "archetypes": {"local_state": "Local state."}, "dimensions": {key: key for key in DIMENSIONS}}}]}
        self.raw = {"schema_version": 1, "archetype": "local_state", "dimensions": DIMENSIONS, "evidence": ["implementation.py: local state"], "rationale": "State ownership is local."}

    def tearDown(self): self.temp.cleanup()

    def test_randomizes_aliases_and_writes_alias_map_only_after_all_trials(self):
        prompts = []
        def execute(workspace, prompt, protocol):
            prompts.append(prompt); self.assertNotIn("baseline", prompt.lower()); self.assertNotIn("candidate", prompt.lower())
            return {"argv": [], "stdout": json.dumps(self.raw), "stderr": "", "exit_code": 0}
        with patch("scoring.execute_omp", side_effect=execute), patch("scoring.secrets.SystemRandom") as random_class:
            random_class.return_value.shuffle.side_effect = lambda values: values.reverse()
            result = blind_score(self.run_dir, self.protocol)
        alias_map = json.loads((self.run_dir / "alias-map.json").read_text(encoding="utf-8"))
        self.assertTrue(random_class.return_value.shuffle.called); self.assertEqual(set(alias_map), {"aliases"}); self.assertEqual(len(alias_map["aliases"]), 2)
        self.assertEqual({item["alias"] for item in result["scores"]}, {"implementation-001", "implementation-002"}); self.assertEqual(len(prompts), 2)
        with patch("scoring.execute_omp", return_value={"argv": [], "stdout": "not json", "stderr": "", "exit_code": 0}):
            with self.assertRaises(ValueError): blind_score(Path(tempfile.mkdtemp()), self.protocol)

    def test_score_shape_is_exact_and_preferred_is_runtime_derived(self):
        for raw in (dict(self.raw, preferred=True), dict(self.raw, schema_version=2), dict(self.raw, archetype="undeclared"), dict(self.raw, dimensions={"behavior_preserved": True}), dict(self.raw, evidence=[]), dict(self.raw, rationale="")):
            shutil.rmtree(self.run_dir / ".blind", ignore_errors=True)
            with self.subTest(raw=raw), patch("scoring.execute_omp", return_value={"argv": [], "stdout": json.dumps(raw), "stderr": "", "exit_code": 0}):
                with self.assertRaises(ValueError): blind_score(self.run_dir, self.protocol)

    def test_scorer_workspace_mutation_is_rejected_and_no_alias_map_is_published(self):
        def mutate(workspace, prompt, protocol):
            (Path(workspace) / "implementation.py").write_text("mutated", encoding="utf-8")
            return {"argv": [], "stdout": json.dumps(self.raw), "stderr": "", "exit_code": 0}
        with patch("scoring.execute_omp", side_effect=mutate):
            with self.assertRaises(ValueError): blind_score(self.run_dir, self.protocol)
        self.assertFalse((self.run_dir / "alias-map.json").exists()); self.assertEqual((self.source / "implementation.py").read_text(encoding="utf-8"), "original")
        shutil.rmtree(self.run_dir / "blind-workspaces", ignore_errors=True)
        (self.source / "escape").symlink_to(self.run_dir / "outside")
        with self.assertRaisesRegex(ValueError, "symlink"):
            blind_score(self.run_dir, self.protocol)


if __name__ == "__main__": unittest.main()
