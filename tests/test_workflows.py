"""Boundary tests for frozen proofs, isolated git trees and verifier ownership."""
import importlib.util
import json
from pathlib import Path
import re
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
            "pathCheck": ["true"]}))
        (self.repo / "value").write_text("old")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "base")
        self.parent = git(self.repo, "rev-parse", "HEAD")
        verifier = patch.object(workflow, "_run_verifier", return_value={
            "status": "pass", "blocking": [], "followUps": [], "criteria": [
                {"proofId": ident, "command": "python3 proof.py", "observed": "green", "verdict": "pass"}
                for ident in ("P1", "P2") ]})
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

    def test_gate_change_passes_and_is_reported(self):
        result = self.prove(self.change(gate_change=True))
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["deterministic"]["gateChanges"], [".omp/gate.json"])

    def test_hook_change_passes_and_is_reported(self):
        result = self.prove(self.change(files={".githooks/pre-commit": "#!/bin/sh\nexit 0\n"}))
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["deterministic"]["gateChanges"], [".githooks/pre-commit"])

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

    def draft(self, kind="defect", code=None, name="proof.py", ident="P1"):
        (self.repo / name).write_text(code if code is not None else (
            "from pathlib import Path\n"
            "assert Path('value').read_text() == 'fixed', 'expected fixed'\n"))
        return {"id": ident, "criterion": "value is fixed", "kind": kind,
                "cmd": ["python3", name], "files": [name],
                "expectRed": "expected fixed" if kind == "defect" else None}

    def freeze(self, proofs, **kwargs):
        return workflow.freeze(self.repo, self.tmp_path / "frozen.json", proofs, "frozen", **kwargs)

    def test_freeze_red_uncommitted_defect_and_atomic_fix(self):
        proof = self.draft()
        path, digest = self.freeze([proof])
        data = workflow.load_manifest(path, digest, 16)
        self.assertEqual(data["base"], self.parent)
        self.assertEqual(data["proofs"][0]["parent"], self.parent)
        self.assertIn("?? proof.py", git(self.repo, "status", "--porcelain"))
        (self.repo / "value").write_text("fixed")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "proof and fix")
        result = workflow.prove(self.repo, self.parent, "HEAD", path, digest, "fix")
        self.assertEqual(result["status"], "pass", result["blocking"])

    def test_freeze_unit_b_and_green_new_surface_return_to_plan(self):
        for kind in ("defect", "new-surface"):
            with self.subTest(kind=kind):
                with self.assertRaisesRegex(workflow.WorkflowError, "return to Plan"):
                    self.freeze([self.draft(kind, "assert True\n")])

    def test_freeze_noncompiling_new_surface_and_isolated_preserve(self):
        surface = self.draft("new-surface", "import missing_api\n")
        preserve = self.draft("preserve",
            "from pathlib import Path\nassert not Path('proof.py').exists()\n",
            "preserve.py", "P2")
        self.freeze([surface, preserve])

    def test_freeze_extra_dirty_file_and_command_dependency_rejected(self):
        proof = self.draft()
        (self.repo / "extra").write_text("dirty")
        with self.assertRaisesRegex(workflow.WorkflowError, "unfrozen change: extra"):
            self.freeze([proof])
        proof["cmd"].append("extra")
        with self.assertRaisesRegex(workflow.WorkflowError, "command file not frozen"):
            self.freeze([proof])

    def test_freeze_negative_control_rejects_generic_red(self):
        proof = self.draft()
        proof["cmd"] = ["python3", "-c", "raise SystemExit('FAIL')"]
        proof["expectRed"] = "FAIL"
        with self.assertRaisesRegex(workflow.WorkflowError, "negative control"):
            self.freeze([proof])

    def test_additive_refreeze_and_explicit_revision(self):
        proof = self.draft()
        previous = self.freeze([proof])
        second = self.draft("preserve", "assert True\n", "second.py", "P2")
        with patch.object(workflow, "_overlay_run", wraps=workflow._overlay_run) as run:
            previous = self.freeze([proof, second], previous=previous)
        self.assertEqual(run.call_count, 1)
        (self.repo / "proof.py").write_text((self.repo / "proof.py").read_text() + "# revision\n")
        with self.assertRaisesRegex(workflow.WorkflowError, "silent proof change"):
            self.freeze([proof, second], previous=previous)
        path, digest = self.freeze([proof, second], previous=previous, revise={"P1": "clarify proof"})
        self.assertEqual(workflow.load_manifest(path, digest, 16)["revisions"][0]["reason"], "clarify proof")
        (self.repo / "value").write_text("fixed")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "atomic fix")
        extra = self.draft("preserve", "assert True\n", "extra.py", "P3")
        with patch.object(workflow, "_overlay_run", wraps=workflow._overlay_run) as run:
            path, digest = self.freeze([proof, second, extra], previous=(path, digest))
        self.assertEqual(run.call_count, 1)
        self.assertEqual(workflow.load_manifest(path, digest, 16)["base"], self.parent)

    def test_freeze_alias_pinned_and_nonancestor_rejected(self):
        proof = self.draft()
        proof["parent"] = "HEAD~0"
        path, digest = self.freeze([proof])
        self.assertEqual(workflow.load_manifest(path, digest, 16)["proofs"][0]["parent"], self.parent)
        other = subprocess.check_output(["git", "-C", str(self.repo), "commit-tree",
            f"{self.parent}^{{tree}}", "-m", "unrelated"], text=True).strip()
        proof["parent"] = other
        with self.assertRaisesRegex(workflow.WorkflowError, "ancestor"):
            self.freeze([proof])

    def test_freeze_symlink_and_eol_rejected(self):
        proof = self.draft()
        (self.repo / "alias.py").symlink_to("proof.py")
        proof["files"] = ["alias.py"]
        with self.assertRaisesRegex(workflow.WorkflowError, "symlink"):
            self.freeze([proof])
        (self.repo / "alias.py").unlink()
        proof["files"] = ["proof.py"]
        (self.repo / ".gitattributes").write_text("*.py text eol=lf\n")
        git(self.repo, "add", ".gitattributes")
        git(self.repo, "commit", "-qm", "line ending policy")
        (self.repo / "proof.py").write_bytes(b"assert False, 'expected fixed'\r\n")
        with self.assertRaisesRegex(workflow.WorkflowError, "normalise line endings"):
            self.freeze([proof])

    def test_setup_failure_not_proof_evidence(self):
        with self.assertRaisesRegex(workflow.WorkflowError, "setup failed"):
            self.freeze([self.draft()], setup=["python3", "-c", "raise SystemExit('setup')"])

    def test_proof_committed_before_fix_blocked(self):
        self.draft()
        git(self.repo, "add", "proof.py")
        git(self.repo, "commit", "-qm", "premature proof")
        result = self.prove(self.change())
        self.assertTrue(any("proof committed before its fix" in error for error in result["blocking"]))

    def test_committed_head_proof_mutation_reports_only_hash_failure(self):
        proof = self.draft()
        path, digest = self.freeze([proof])
        (self.repo / "value").write_text("fixed")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "atomic proof and fix")
        (self.repo / "proof.py").write_text("assert True\n")
        git(self.repo, "add", "proof.py")
        git(self.repo, "commit", "-qm", "weaken proof")
        result = workflow.prove(self.repo, self.parent, "HEAD", path, digest, "fix")
        self.assertEqual(result["blocking"], ["P1: missing proof or hash mismatch: proof.py"])
        self.verifier.assert_not_called()

    def test_golden_file_mutation_blocked(self):
        result = self.prove(self.change(kind="preserve", code=
            "from pathlib import Path\nPath('value').write_text('golden')\n"))
        self.assertIn("proof run modified tracked files", result["blocking"])

    def test_expect_red_at_head_blocked(self):
        result = self.prove(self.change(code=
            "from pathlib import Path\n"
            "assert Path('value').read_text() == 'fixed', 'expected fixed'\n"
            "print('expected fixed')\n"))
        self.assertEqual(result["blocking"], ["P1: expectRed present at head"])

    def test_verifier_omitted_proof_blocks(self):
        self.verifier.return_value["criteria"] = []
        result = self.prove(self.change())
        self.assertIn("verifier omitted P1", result["blocking"])

    def test_stale_marker_removed_on_failed_rerun(self):
        args = self.change()
        self.assertEqual(self.prove(args)["status"], "pass")
        marker = self.repo / ".git/omp-prove" / f"{args[2]}.json"
        self.assertTrue(marker.exists())
        result = workflow.prove(*args, "0" * 64, "fix")
        self.assertEqual(result["status"], "fail")
        self.assertFalse(marker.exists())

    def test_blind_labels_missing_duplicate_and_batch_ids(self):
        files = []
        for n in range(2):
            path = self.tmp_path / f"batch-{n}.json"
            path.write_text(json.dumps([{"id": "same"}, {"id": "missing"}]))
            files.append(path)
        case = {"id": "same", "label": "yes", "evidence": "quote", "rationale": "reason"}
        results = [{"cases": [case]}, {"cases": [case]},
                   {"cases": [case, case]}, {"cases": [case]}]
        with patch.object(workflow, "agent", create=True), patch.object(
                workflow, "wait", return_value=results, create=True):
            result = workflow.blind_label(files, "rubric", ["yes", "no"])
        self.assertEqual(result["agreed"], {(0, "same"): "yes"})
        self.assertIn((0, "missing"), result["disputed"])
        self.assertIn((1, "missing"), result["disputed"])
        self.assertIn((1, "same"), result["disputed"])

    def test_prove_preserve_and_new_surface_isolated(self):
        args = self.change(kind="preserve", code="assert True\n", files={
            "new_api.py": "available = True\n",
            "test_new.py": "from new_api import available\nassert available\n",
            "test_preserve.py": "import unittest\nclass Existing(unittest.TestCase):\n"
                                "    def test_existing(self): self.assertTrue(True)\n"})
        data = json.loads(args[3].read_text())
        data["proofs"] = [
            {"id": "P1", "criterion": "existing behavior", "kind": "preserve",
             "cmd": ["python3", "-m", "unittest", "discover"], "files": ["test_preserve.py"],
             "sha256": {"test_preserve.py": workflow._sha(self.repo / "test_preserve.py")}},
            {"id": "P2", "criterion": "new API", "kind": "new-surface",
             "cmd": ["python3", "test_new.py"], "files": ["test_new.py"],
             "sha256": {"test_new.py": workflow._sha(self.repo / "test_new.py")}}]
        args[3].write_text(json.dumps(data))
        result = self.prove(args)
        self.assertEqual(result["status"], "pass", result["blocking"])

    def test_setup_runs_once_per_fresh_tree(self):
        proof = self.draft()
        setup = ["python3", "-c", "from pathlib import Path; Path('ready').write_text('ready')"]
        with patch.object(workflow, "run_check", wraps=workflow.run_check) as run:
            path, digest = self.freeze([proof], setup=setup)
        self.assertEqual(sum(call.args[0] == setup for call in run.call_args_list), 1)
        (self.repo / "value").write_text("fixed")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "atomic fix")
        with patch.object(workflow, "run_check", wraps=workflow.run_check) as run:
            result = workflow.prove(self.repo, self.parent, "HEAD", path, digest, "fix")
        self.assertEqual(result["status"], "pass", result["blocking"])
        self.assertEqual(sum(call.args[0] == setup for call in run.call_args_list), 2)

    def test_per_proof_parent_uses_distinct_commits_and_cleans_up(self):
        # The default base lacks 'dependency'; the review proof needs the pre-fix commit.
        (self.repo / "dependency").write_text("ready")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "dependency before fix")
        review_parent = git(self.repo, "rev-parse", "HEAD")
        args = self.change(code=(
            "from pathlib import Path\n"
            "assert Path('dependency').read_text() == 'ready'\n"
            "assert Path('value').read_text() == 'fixed', 'expected fixed'\n"))
        data = json.loads(args[3].read_text())
        data["proofs"][0]["parent"] = review_parent
        second = dict(data["proofs"][0], id="P2", parent=review_parent)
        data["proofs"].append(second)
        args[3].write_text(json.dumps(data))
        real_git = workflow._git
        with patch.object(workflow, "_git", wraps=real_git) as calls:
            self.assertEqual(self.prove(args)["status"], "pass")
        adds = [call for call in calls.call_args_list if call.args[1:3] == ("worktree", "add")]
        self.assertEqual(len(adds), 3)

    def test_invalid_per_proof_parent_names_proof_and_cleans_up(self):
        args = self.change()
        data = json.loads(args[3].read_text())
        data["proofs"][0]["parent"] = "not-a-commit"
        args[3].write_text(json.dumps(data))
        result = self.prove(args)
        self.assertEqual(result["status"], "fail")
        self.assertTrue(any("P1:" in error for error in result["blocking"]))

    def test_marker_written_in_common_dir_only_on_pass(self):
        args = self.change()
        marker = self.repo / ".git/omp-prove" / f"{args[2]}.json"
        self.verifier.return_value = {"status": "fail", "blocking": ["blocked"], "followUps": [], "criteria": []}
        self.assertEqual(self.prove(args)["status"], "fail")
        self.assertFalse(marker.exists())
        self.verifier.return_value = {"status": "pass", "blocking": [], "followUps": [], "criteria": [
            {"proofId": "P1", "command": "python3 proof.py", "observed": "green", "verdict": "pass"}]}
        self.assertEqual(self.prove(args)["status"], "pass")
        self.assertEqual(json.loads(marker.read_text()), {
            "schemaVersion": 1, "head": args[2], "kind": "prove", "status": "pass"})

    def test_docs_only_allowed_extensions_and_linked_worktree_marker(self):
        names = ["guide.md", "guide.txt", "a.png", "a.jpg", "a.jpeg", "a.svg", "a.gif"]
        for name in names:
            (self.repo / name).write_text("docs")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "docs")
        head = git(self.repo, "rev-parse", "HEAD")
        linked = self.tmp_path / "linked"
        git(self.repo, "worktree", "add", "--detach", str(linked), head)
        try:
            marker = workflow.exempt_docs_only(linked, self.parent, head)
            self.assertEqual(marker.resolve(), (self.repo / ".git/omp-prove" / f"{head}.json").resolve())
            self.assertEqual(json.loads(marker.read_text())["kind"], "docs-only")
        finally:
            git(self.repo, "worktree", "remove", str(linked))

    def test_docs_only_rejects_every_runtime_boundary_and_empty_diff(self):
        paths = ["code.py", "AGENTS.md", "CLAUDE.md", "SKILL.md", "docs/AGENTS.md"]
        paths += [f"docs/{part}/guide.md" for part in
                  ("skills", "agents", "rules", "prompts", ".agents", ".claude", ".omp")]
        for name in paths:
            with self.subTest(path=name):
                path = self.repo / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("runtime")
                git(self.repo, "add", ".")
                parent = git(self.repo, "rev-parse", "HEAD")
                git(self.repo, "commit", "-qm", "runtime")
                head = git(self.repo, "rev-parse", "HEAD")
                with self.assertRaisesRegex(workflow.WorkflowError, re.escape(name)):
                    workflow.exempt_docs_only(self.repo, parent, head)
                self.assertFalse((self.repo / ".git/omp-prove" / f"{head}.json").exists())
        with self.assertRaisesRegex(workflow.WorkflowError, "requires changed"):
            workflow.exempt_docs_only(self.repo, head, head)


class GatePolicyTests(unittest.TestCase):
    def test_v2_reads_checks_object(self):
        policy = workflow.gate_policy({"schemaVersion": 2, "checks": {"commit": ["c"], "ci": ["i"]},
                                       "pathCheck": ["p"]})
        self.assertEqual(policy, {"commit": ["c"], "ci": ["i"], "pathCheck": ["p"]})

    def test_unknown_schema_and_malformed_fields_are_invalid(self):
        valid = {"schemaVersion": 2, "checks": {"commit": ["c"], "ci": ["i"]}, "pathCheck": ["p"]}
        self.assertIsNotNone(workflow.gate_policy(valid))
        for invalid in ({"schemaVersion": 3}, {"schemaVersion": 2, "checks": []},
                        {**valid, "checks": {"commit": 42}}, {**valid, "checks": {"ci": "bun ci"}},
                        {**valid, "checks": {"commit": [""]}}, {**valid, "pathCheck": 42},
                        {**valid, "pathCheck": []},
                        {**valid, "ciHost": "invalid"}, {**valid, "agentMerge": "yes"},
                        {"schemaVersion": 1, "gate": ["g"], "commitGate": ["c"], "pathCheck": ["p"]}):
            self.assertIsNone(workflow.gate_policy(invalid), invalid)

if __name__ == "__main__":
    unittest.main()
