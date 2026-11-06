"""Boundary tests for frozen proofs, isolated git trees and verifier ownership."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / "agent/skills/omp-dyna-workflows/templates/workflows.py"
spec = importlib.util.spec_from_file_location("workflows", MODULE)
workflow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workflow)


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="omp-proof-test-")
        self.addCleanup(scratch.cleanup)
        self.tmp_path = Path(scratch.name)
        self.repo = self.tmp_path / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "proof@example.test")
        git(self.repo, "config", "user.name", "Proof Test")
        (self.repo / ".omp").mkdir()
        # The commit check would fail if run: prove() must not execute repository checks.
        (self.repo / ".omp/gate.json").write_text(json.dumps({
            "schemaVersion": 2, "checks": {"commit": ["false"], "ci": ["false"]},
            "pathCheck": ["true"], "sensitive": ["auth/"]}))
        (self.repo / "value").write_text("old")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "base")
        self.parent = git(self.repo, "rev-parse", "HEAD")
        verifier = patch.object(workflow, "_run_verifier", return_value={
            "status": "pass", "blocking": [], "followUps": []})
        self.verifier = verifier.start()
        self.addCleanup(verifier.stop)

    def change(self, kind="defect", code=None, files=None, gate_change=False):
        repo = self.repo
        (repo / "value").write_text("fixed")
        (repo / "proof.py").write_text(code or (
            "from pathlib import Path\n"
            "assert Path('value').read_text() == 'fixed', 'expected fixed'\n"))
        for name, text in (files or {}).items():
            (repo / name).parent.mkdir(parents=True, exist_ok=True)
            (repo / name).write_text(text)
        if gate_change:
            path = repo / ".omp/gate.json"
            gate = json.loads(path.read_text())
            gate["checks"]["commit"] = ["printf", "gate"]
            path.write_text(json.dumps(gate))
        git(repo, "add", ".")
        git(repo, "commit", "-qm", "change")
        head = git(repo, "rev-parse", "HEAD")
        manifest = {"schemaVersion": 1, "slug": "example", "proofs": [{
            "id": "P1", "criterion": "value is fixed", "kind": kind,
            "cmd": [sys.executable, "proof.py"], "files": ["proof.py"],
            "expectRed": "expected fixed" if kind == "defect" else None,
            "sha256": {"proof.py": workflow._sha(repo / "proof.py")}}]}
        path = self.tmp_path / "manifest.json"
        path.write_text(json.dumps(manifest))
        return repo, self.parent, head, path

    def prove(self, change_args):
        repo, parent, head, manifest = change_args
        result = workflow.prove(repo, parent, head, manifest, workflow._sha(manifest), "fix value")
        # Scratch trees must be removed on success and refusal alike.
        self.assertEqual(git(repo, "worktree", "list", "--porcelain").count("worktree "), 1)
        return result

    def test_valid_defect_proof(self):
        self.assertEqual(self.prove(self.change(kind="defect"))["status"], "pass")

    def test_valid_preserve_proof(self):
        self.assertEqual(self.prove(self.change(kind="preserve", code="assert True\n"))["status"], "pass")

    def test_valid_new_surface_proof(self):
        self.assertEqual(self.prove(self.change(kind="new-surface"))["status"], "pass")

    def assert_invalid_evidence(self, code, reason):
        result = self.prove(self.change(code=code))
        self.assertEqual(result["status"], "fail")
        self.assertTrue(any(reason in item for item in result["blocking"]))

    def test_wrong_reason_red_rejected(self):
        self.assert_invalid_evidence(
            "from pathlib import Path\nassert Path('value').read_text() == 'fixed', 'different failure'\n",
            "wrong-reason red")

    def test_tautology_rejected(self):
        self.assert_invalid_evidence("assert True\n", "tautology")

    def test_flaky_proof_rejected(self):
        self.assert_invalid_evidence(
            "from pathlib import Path\np = Path('counter')\nn = int(p.read_text()) if p.exists() else 0\n"
            "p.write_text(str(n + 1))\nassert n == 0, 'flaky'\n", "head run 2 failed")

    def test_manifest_hash_mismatch_blocks(self):
        repo, parent, head, manifest = self.change()
        result = workflow.prove(repo, parent, head, manifest, "0" * 64, "fix")
        self.assertEqual(result["status"], "fail")
        self.assertTrue(any("hash mismatch" in item for item in result["blocking"]))

    def test_proof_hash_mismatch_blocks(self):
        args = self.change()
        manifest = args[3]
        data = json.loads(manifest.read_text())
        data["proofs"][0]["sha256"]["proof.py"] = "0" * 64
        manifest.write_text(json.dumps(data))
        result = self.prove(args)
        self.assertEqual(result["status"], "fail")
        self.assertTrue(any("hash mismatch" in item for item in result["blocking"]))

    def test_gate_change_requires_countersign(self):
        result = self.prove(self.change(gate_change=True))
        self.assertEqual(result["status"], "needs_owner")
        self.assertEqual(result["deterministic"]["needsCountersign"], [".omp/gate.json"])

    def assert_sensitive_change(self, name):
        result = self.prove(self.change(files={name: "# protected\n"}))
        self.assertEqual(result["status"], "needs_owner")
        self.assertEqual(result["deterministic"]["needsOwner"], [name])

    def test_sensitive_file_requires_owner(self):
        self.assert_sensitive_change("auth/login.py")

    def test_sensitive_descendant_requires_owner(self):
        self.assert_sensitive_change("auth/deep/token.py")

    def test_sensitive_nested_folder_requires_owner(self):
        self.assert_sensitive_change("src/auth/login.py")

    def test_verifier_mutation_detected(self):
        args = self.change()
        def mutate(brief):
            (args[0] / "unauthorized").write_text("mutation")
            return {"status": "pass", "blocking": [], "followUps": []}
        self.verifier.side_effect = mutate
        result = self.prove(args)
        self.assertEqual(result["status"], "fail")
        self.assertIn("verifier mutated the repo", result["blocking"])

    def test_unknown_proof_id_rejected(self):
        args = self.change()
        data = json.loads(args[3].read_text())
        data["proofs"][0].pop("id")
        args[3].write_text(json.dumps(data))
        result = self.prove(args)
        self.assertEqual(result["status"], "fail")
        self.assertIn("missing or duplicate proof id", result["blocking"])

    def test_sensitive_pattern_contract(self):
        self.assertEqual(workflow.sensitive_paths(
            ["auth/token", "src/auth/token", "authentication/x.ts", "src/authentication/x.ts",
             "else/auth", "x/secret.env", "x/public"], ["auth/", "secret"]),
            ["auth/token", "src/auth/token", "x/secret.env"])

    def test_verifier_exception_is_closed(self):
        self.verifier.side_effect = LookupError("verifier unavailable")
        result = self.prove(self.change())
        self.assertEqual(result["status"], "fail")
        self.assertTrue(result["blocking"])
        self.assertIn("verifier unavailable", result["blocking"])

    def test_deterministic_exception_is_closed(self):
        args = self.change()
        with patch.object(workflow, "deterministic_checks", side_effect=LookupError("proof runner failed")):
            result = self.prove(args)
        self.assertEqual(result["status"], "fail")
        self.assertIn("proof runner failed", result["blocking"])
        self.verifier.assert_not_called()

    def test_missing_gate_routes_to_guardrail_setup(self):
        (self.repo / ".omp/gate.json").unlink()
        result = self.prove(self.change())
        self.assertEqual(result["status"], "fail")
        self.assertIn(
            "missing checks.commit or pathCheck; use skill://setup-repository-guardrails",
            result["blocking"])
        self.verifier.assert_not_called()

    def test_missing_commit_check_routes_to_guardrail_setup(self):
        gate = {"schemaVersion": 2, "checks": {"ci": ["true"]}, "pathCheck": ["true"]}
        result = self.prove(self.change(files={".omp/gate.json": json.dumps(gate)}))
        self.assertEqual(result["status"], "fail")
        self.assertIn(
            "missing checks.commit or pathCheck; use skill://setup-repository-guardrails",
            result["blocking"])
        self.verifier.assert_not_called()

    def test_unsupported_gate_schema_fails(self):
        gate = {"schemaVersion": 3, "pathCheck": ["true"]}
        result = self.prove(self.change(files={".omp/gate.json": json.dumps(gate)}))
        self.assertEqual(result["status"], "fail")
        self.assertIn(".omp/gate.json has an unsupported schemaVersion or invalid fields", result["blocking"])
        self.verifier.assert_not_called()

    def test_non_object_gate_fails(self):
        result = self.prove(self.change(files={".omp/gate.json": "[]"}))
        self.assertEqual(result["status"], "fail")
        self.assertIn(".omp/gate.json must be an object", result["blocking"])
        self.verifier.assert_not_called()

    def test_verifier_off_schema_result_is_closed(self):
        self.verifier.return_value = {"status": "pass"}
        result = self.prove(self.change())
        self.assertEqual(result["status"], "fail")
        self.assertTrue(result["blocking"])


class GatePolicyTests(unittest.TestCase):
    def test_v2_reads_checks_object(self):
        policy = workflow.gate_policy({"schemaVersion": 2, "checks": {"commit": ["c"], "ci": ["i"]},
                                       "pathCheck": ["p"], "sensitive": ["auth/"]})
        self.assertEqual(policy, {"commit": ["c"], "ci": ["i"], "pathCheck": ["p"], "sensitive": ["auth/"]})

    def test_v1_prefers_commit_gate_then_fast_gate(self):
        both = {"schemaVersion": 1, "commitGate": ["c"], "fastGate": ["f"], "gate": ["g"]}
        self.assertEqual(workflow.gate_policy(both)["commit"], ["c"])
        self.assertEqual(workflow.gate_policy(both)["ci"], ["g"])
        self.assertEqual(workflow.gate_policy({"schemaVersion": 1, "fastGate": ["f"]})["commit"], ["f"])

    def test_unknown_schema_and_malformed_fields_are_invalid(self):
        valid = {"schemaVersion": 2, "checks": {"commit": ["c"], "ci": ["i"]}, "pathCheck": ["p"]}
        self.assertIsNotNone(workflow.gate_policy(valid))
        for invalid in ({"schemaVersion": 3}, {"schemaVersion": 2, "checks": []},
                        {**valid, "checks": {"commit": 42}}, {**valid, "checks": {"ci": "bun ci"}},
                        {**valid, "checks": {"commit": [""]}}, {**valid, "pathCheck": 42},
                        {**valid, "pathCheck": []}, {**valid, "sensitive": "auth/"},
                        {**valid, "ciHost": "invalid"}, {**valid, "agentMerge": "yes"},
                        {"schemaVersion": 1, "gate": "bash gate.sh"}):
            self.assertIsNone(workflow.gate_policy(invalid), invalid)

if __name__ == "__main__":
    unittest.main()
