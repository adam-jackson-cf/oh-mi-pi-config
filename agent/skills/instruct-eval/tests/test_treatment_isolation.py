import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from runner import execute_omp, run_trials
from validation import validate_treatment_isolation


class TreatmentIsolationTests(unittest.TestCase):
    def test_execute_omp_uses_supported_frozen_argv(self):
        protocol = {"provider": "openai", "model": "gpt-5", "thinking": "medium", "permissions": {"approval_mode": "write", "tools": ["read", "edit"]}, "execution": {"timeout_seconds": 9}}
        with tempfile.TemporaryDirectory() as directory, patch("runner.subprocess.run") as run:
            run.return_value.returncode = 0; run.return_value.stdout = "ok"; run.return_value.stderr = ""
            execute_omp(Path(directory), "Do work.", protocol)
        self.assertEqual(run.call_args.args[0], ["omp", "--model", "openai/gpt-5", "--thinking", "medium", "--approval-mode", "write", "--tools", "read,edit", "--no-session", "-p", "Do work."]); self.assertFalse(run.call_args.kwargs.get("shell", False))

    def test_empty_baseline_and_candidate_only_initial_treatment(self):
        validate_treatment_isolation("", "Keep state local.", "Keep state local.")
        for invalid in (("Initial", "Candidate", "I"), ("", "Candidate extra", "Candidate")):
            with self.assertRaises(ValueError): validate_treatment_isolation(*invalid)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); fixture = root / "fixture"; fixture.mkdir(); (fixture / "app.py").write_text("x = 1\n# comment\n", encoding="utf-8"); (fixture / "tests").mkdir(); (fixture / "tests/test_app.py").write_text("assert True\n", encoding="utf-8")
            probe = root / "probe"; probe.mkdir(); (probe / "hidden_oracle.py").write_text("hidden\n", encoding="utf-8")
            hidden_command = [sys.executable, "-c", "import os; from pathlib import Path; assert Path(os.environ['OMP_EVALUATED_WORKSPACE']).is_dir(); assert not (Path.cwd() / 'app.py').exists()"]
            protocol = {"run_id": "run", "instruction": "Keep state local.", "provider": "openai", "model": "gpt-5", "thinking": "medium", "permissions": {"approval_mode": "write", "tools": ["read", "edit"]}, "trials_per_condition": 1, "execution": {"timeout_seconds": 5}, "assets": {"initial_template": str(ROOT / "assets/templates/initial-request.txt"), "checkpoint_template": str(ROOT / "assets/templates/checkpoint-request.txt")}, "conditions": {"baseline": {"instruction": ""}, "candidate": {"instruction": "Keep state local."}}, "scenarios": [{"id": "change", "fixture": str(fixture), "initial_request": "Initial request", "checkpoints": [{"id": "follow-up", "request": "Apply follow-up."}], "public_tests": [], "hidden_tests": [{"id": "hidden", "kind": "pristine", "fixture": str(probe), "command": hidden_command, "expected_outcome": "pass"}]}]}
            calls = []
            def execute(workspace, prompt, frozen): calls.append(prompt); return {"argv": [], "stdout": "", "stderr": "", "exit_code": 0, "timed_out": False}
            with patch("runner.execute_omp", side_effect=execute): evidence = run_trials(root / "run", protocol)
            self.assertFalse((root / "run" / "alias-map.json").exists())
            prompts = {trial["condition"]: trial["initial_prompt"] for trial in evidence["trials"]}
            self.assertEqual(prompts["candidate"].replace("Keep state local.", ""), prompts["baseline"])
            self.assertTrue(all(not (Path(trial["workspace"]) / "hidden_oracle.py").exists() for trial in evidence["trials"]))
            self.assertTrue(all(trial["hidden"]["passed"] for trial in evidence["trials"]))
            linked = root / "linked-fixture"; linked.mkdir(); (linked / "escape").symlink_to(probe / "hidden_oracle.py")
            hidden_base = root / "hidden-base"; hidden_base.mkdir(); (hidden_base / "hidden_tests").mkdir()
            protocol["scenarios"][0]["fixture"] = str(hidden_base)
            with self.assertRaisesRegex(ValueError, "hidden oracle"): run_trials(root / "hidden-run", protocol)
            root_named_oracle = root / "oracle"; root_named_oracle.mkdir()
            protocol["scenarios"][0]["fixture"] = str(root_named_oracle)
            with self.assertRaisesRegex(ValueError, "hidden oracle"): run_trials(root / "root-name-run", protocol)
            protocol["scenarios"][0]["fixture"] = str(linked)
            with self.assertRaisesRegex(ValueError, "symlink"): run_trials(root / "symlink-run", protocol)
            protocol["scenarios"][0]["fixture"] = str(fixture)
            def checkpoint_link(workspace, prompt, frozen):
                if prompt == "Apply follow-up.":
                    (Path(workspace) / "escape").symlink_to(probe / "hidden_oracle.py")
                return {"argv": [], "stdout": "", "stderr": "", "exit_code": 0, "timed_out": False}
            with patch("runner.execute_omp", side_effect=checkpoint_link), self.assertRaisesRegex(ValueError, "symlink"):
                run_trials(root / "checkpoint-symlink-run", protocol)
        self.assertEqual(calls.count("Apply follow-up."), 2)
        for item in evidence["trials"]:
            self.assertIn("omp", item["checkpoints"][0]); self.assertNotEqual(item["workspace"], item["checkpoints"][0]["workspace"]); self.assertEqual(item["production_nloc"], 1)

    def test_probe_commands_are_argv_not_shell_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); fixture = root / "fixture"; fixture.mkdir(); protocol = {"run_id": "run", "instruction": "I", "provider": "openai", "model": "gpt-5", "thinking": "medium", "permissions": {"approval_mode": "write", "tools": ["read", "edit"]}, "trials_per_condition": 1, "execution": {"timeout_seconds": 5}, "assets": {"initial_template": str(ROOT / "assets/templates/initial-request.txt"), "checkpoint_template": str(ROOT / "assets/templates/checkpoint-request.txt")}, "conditions": {"baseline": {"instruction": ""}, "candidate": {"instruction": "I"}}, "scenarios": [{"id": "change", "fixture": str(fixture), "initial_request": "Initial", "checkpoints": [{"id": "c", "request": "C"}], "public_tests": [{"id": "p", "command": [sys.executable, "-c", "pass"], "expected_outcome": "pass"}], "hidden_tests": []}]}
            with patch("runner.execute_omp", return_value={"argv": [], "stdout": "", "stderr": "", "exit_code": 0, "timed_out": False}), patch("runner.subprocess.run") as run:
                run.return_value.returncode = 0; run.return_value.stdout = ""; run.return_value.stderr = ""; run_trials(root / "run", protocol)
        self.assertIsInstance(run.call_args.args[0], list); self.assertFalse(run.call_args.kwargs.get("shell", False))


if __name__ == "__main__": unittest.main()
