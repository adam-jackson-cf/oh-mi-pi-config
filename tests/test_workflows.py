"""Consumer boundaries for frozen proofs (red at base, green at head), isolated executions and the prove() result."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / "agent/skills/omp-dyna-workflows/templates/workflows.py"
spec = importlib.util.spec_from_file_location("workflows", MODULE)
workflow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workflow)
SWITCH = {"dynaflows": {"enabled": True}}
ORIGIN = "https://github.com/Example-Owner/Example-Repo.git"
RED_AT_BASE = "from pathlib import Path\nassert Path('value').read_text() == 'fixed', 'expected fixed'\n"
MAP_PATH = "docs/features"
# Proof file names the tests freeze; the base feature map links them all, so proofs are mapped.
MAPPED = ["proof.py", "-proof", "second.py", "later.py", "journey.py", *(f"proof{n}.py" for n in range(17))]


def map_doc(*targets, title="Value"):
    """A feature-map document whose 'Driving it with' section links `targets` (paths relative to docs/features/)."""
    links = "\n".join(f"- [{target}]({target})" for target in targets) or "- none yet"
    return (f"# {title}\n\n## Sub-features\n\n- value\n\n## How to get to it (user POV)\n\n- read value\n\n"
            f"## Driving it with lever\n\n{links}\n\n## Gotchas\n\n- none\n")


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        environment = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        env = patch.dict(os.environ, environment, clear=True)
        env.start()
        self.addCleanup(env.stop)
        scratch = tempfile.TemporaryDirectory(prefix="omp-proof-test-", dir=Path.home())
        self.addCleanup(scratch.cleanup)
        self.tmp = Path(scratch.name)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "proof@example.test")
        git(self.repo, "config", "user.name", "Proof Test")
        git(self.repo, "remote", "add", "origin", ORIGIN)
        # Records live under the harness, never in the repo: point the resolver at a temp harness root.
        self.config = self.tmp / "config"
        self.record = self.config / "repos/example-owner/example-repo"
        root_patch = patch.object(workflow, "_records_root", return_value=self.config)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        self.write_gate(json.dumps({"schemaVersion": 2, "checks": {"commit": ["false"], "ci": ["false"]}, "pathCheck": ["true"]}))
        self.profile("<doctor failure marker>")
        self.write(f"{MAP_PATH}/value.md", map_doc(*(f"../../{name}" for name in MAPPED)))
        self.write("value", "old")
        self.commit("base")
        self.parent = git(self.repo, "rev-parse", "HEAD")
        self.manifest_path = workflow._common(self.repo) / "omp-manifests" / "example.json"
        self.manifest_path.parent.mkdir(parents=True)
        self.ci_patch = patch.object(workflow, "_required_context_state", return_value="success")
        self.ci = self.ci_patch.start()
        self.addCleanup(self.ci_patch.stop)
        self.comment_patch = patch.object(workflow, "_post_result_comment", return_value="https://github.com/example-owner/example-repo/pull/1#issuecomment-1")
        self.comment = self.comment_patch.start()
        self.addCleanup(self.comment_patch.stop)
        self.addCleanup(self.assert_cleaned)

    def write(self, name, text):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def write_gate(self, text):
        """Write the record's gate.json; a JSON object without a dynaflows switch gets it turned on."""
        try:
            gate = json.loads(text)
        except ValueError:
            gate = None
        if isinstance(gate, dict) and "dynaflows" not in gate:
            text = json.dumps({**gate, **SWITCH})
        self.record.mkdir(parents=True, exist_ok=True)
        (self.record / "gate.json").write_text(text)

    def write_profile(self, text):
        self.record.mkdir(parents=True, exist_ok=True)
        (self.record / "profile.md").write_text(text)

    def commit(self, message):
        git(self.repo, "add", ".")
        git(self.repo, "commit", "--allow-empty", "-qm", message)
        return git(self.repo, "rev-parse", "HEAD")

    def assert_cleaned(self):
        self.assertEqual(git(self.repo, "worktree", "list", "--porcelain").count("worktree "), 1)

    def proof(self, filename="proof.py", code=None, ident="P1"):
        """A proof that is red at base (value is 'old') unless `code` says otherwise."""
        self.write(filename, code if code is not None else RED_AT_BASE)
        return {"id": ident, "criterion": "value is fixed", "cmd": [sys.executable, filename], "files": [filename]}

    def change(self, code=None, proofs=None):
        proofs = [self.proof(code=code)] if proofs is None else proofs
        self.write("value", "fixed")
        self.head = self.commit("change")
        data = {"schemaVersion": 1, "slug": "example", "base": self.parent, "proofs": [
            dict(p, parent=p.get("parent", self.parent), sha256={name: workflow._sha(self.repo / name) for name in p["files"]}) for p in proofs]}
        self.save(data)
        return data

    def save(self, data):
        self.manifest_path.write_text(json.dumps(data))

    def prove(self, objective="fix value", **kwargs):
        args = dict(target_ref=self.parent, **kwargs)
        result = workflow.prove(self.repo, self.parent, self.head, self.manifest_path, workflow._sha(self.manifest_path), objective, **args)
        self.assert_cleaned()
        json.dumps(result)
        return result

    def freeze(self, proofs, **kwargs):
        try:
            return workflow.freeze(self.repo, self.manifest_path, proofs, "example", **kwargs)
        finally:
            self.assert_cleaned()

    def stored_status(self, head=None):
        path = workflow.result_path(self.repo, head or self.head)
        return json.loads(path.read_text())["status"] if path.exists() else None

    def assert_block(self, result, fragment):
        self.assertEqual(result["status"], "fail", result)
        self.assertIn(fragment, "\n".join(result["blocking"]))
        self.assertNotEqual(self.stored_status(), "pass", "a failed prove never leaves a pass result for the head")

    def test_freeze_requires_durable_manifest_but_reads_temporary_previous(self):
        proof = self.proof()
        durable = self.manifest_path
        for root in dict.fromkeys((Path(tempfile.gettempdir()).resolve(), Path("/tmp").resolve())):
            with self.subTest(root=str(root)), tempfile.TemporaryDirectory(dir=root) as scratch:
                self.manifest_path = Path(scratch) / "manifest.json"
                with self.assertRaisesRegex(workflow.WorkflowError, re.escape("<git-common-dir>/omp-manifests/<slug>.json")):
                    self.freeze([proof])
                self.assertFalse(self.manifest_path.exists())
        self.manifest_path = durable
        path, digest = self.freeze([proof])
        self.assertEqual(path, durable.resolve())
        self.assertEqual(workflow._sha(path), digest)
        with tempfile.TemporaryDirectory() as scratch:
            previous = Path(scratch) / "previous.json"
            previous.write_bytes(path.read_bytes())
            self.assertEqual(self.freeze([proof], previous=(previous, digest)), (path, digest))

    def test_one_rule_red_at_base_then_green_at_head(self):
        proof = self.proof()
        path, digest = self.freeze([proof])
        self.assertNotIn("kind", json.loads(path.read_text())["proofs"][0])
        self.write("value", "fixed")
        self.head = self.commit("implementation")
        result = workflow.prove(self.repo, self.parent, self.head, path, digest, "fix value", target_ref=self.parent)
        self.assertEqual(result["status"], "pass", result)
        self.assertEqual([run["location"] for run in result["deterministic"]["proofs"]["P1"]], ["head", "head"],
                         "prove() runs proofs at head only; red at base was shown at freeze")
        self.assertEqual(self.stored_status(), "pass")

    def test_freeze_refuses_any_proof_green_at_base(self):
        for code in ("assert True\n", "print('no assertion')\n"):
            with self.subTest(code=code), self.assertRaisesRegex(workflow.WorkflowError, "P1: passes at baseline; a proof must fail at base"):
                self.freeze([self.proof(code=code)])
            self.assertFalse(self.manifest_path.exists())

    def test_any_failing_run_at_base_is_red(self):
        for code in (RED_AT_BASE, "import sys\nsys.exit(3)\n", "raise SystemExit('some other reason')\n"):
            with self.subTest(code=code):
                self.manifest_path.unlink(missing_ok=True)
                self.freeze([self.proof(code=code)])
                self.assertTrue(self.manifest_path.exists())

    def test_prove_blocks_proof_red_at_head(self):
        data = self.change()
        self.write("value", "regressed")
        self.head = self.commit("regression")
        self.save(data)
        self.assert_block(self.prove(), "P1: head run 1 failed")

    def test_zero_proofs_freeze_and_prove_check_ci_green_only(self):
        self.write("docs.md", "docs\n")
        self.commit("docs")
        path, digest = self.freeze([], base=self.parent)
        self.assertEqual(json.loads(path.read_text())["proofs"], [])
        self.head = git(self.repo, "rev-parse", "HEAD")
        self.write_gate(json.dumps({"schemaVersion": 2, "pathCheck": ["true"], "ci": {"requiredContext": "gate", "runner": "ci-gate"}}))
        with patch.object(workflow, "_setup", side_effect=AssertionError("zero proofs build no trees")):
            result = workflow.prove(self.repo, self.parent, self.head, path, digest, "docs", target_ref=self.parent)
            self.assertEqual(result["status"], "pass", result)
            self.ci.assert_called_once_with(self.repo.resolve(), self.head, "gate")
            self.assertEqual(self.stored_status(), "pass")
            self.ci.return_value = "pending"
            result = workflow.prove(self.repo, self.parent, self.head, path, digest, "docs", target_ref=self.parent)
        self.assert_block(result, 'required CI context "gate" is pending')
        self.assertEqual(self.stored_status(), "fail")

    def test_setup_runs_once_per_tree_in_freeze_and_prove(self):
        self.write(".gitignore", "setup/\n")
        self.commit("ignore setup")
        self.parent = git(self.repo, "rev-parse", "HEAD")
        counter = self.tmp / "setup-count"
        setup = [sys.executable, "-c",
                 "from pathlib import Path\n"
                 f"with Path({str(counter)!r}).open('a') as out: out.write(str(Path.cwd()) + '\\n')\n"
                 "Path('setup').mkdir(exist_ok=True)\n"
                 "p = Path('setup/count')\n"
                 "p.write_text(p.read_text() + 'x' if p.exists() else 'x')\n"]
        code = ("from pathlib import Path\n"
                "assert not Path('residue').exists()\n"
                "Path('residue').write_text('run')\n"
                "assert Path('setup/count').read_text() == 'x'\n"
                "assert Path('value').read_text() == 'fixed', 'expected fixed'\n")
        proofs = [self.proof(filename=f"proof{n}.py", ident=f"P{n}", code=code) for n in (1, 2, 3)]
        self.freeze(proofs, setup=setup)
        self.assertEqual(len(counter.read_text().splitlines()), 1)  # One baseline tree for all proofs.
        data = self.change(proofs=proofs)
        data["setup"] = setup
        self.save(data)
        self.assertEqual(self.prove()["status"], "pass")
        trees = counter.read_text().splitlines()
        self.assertEqual(len(trees), 2)  # Every head run shares the one head tree's setup.
        self.assertEqual(len(set(trees)), 2)

    def test_setup_outputs_survive_and_proof_artifacts_are_removed(self):
        self.write(".gitignore", "ignored-ready/\nignored-run*\n")
        self.commit("ignore artifacts")
        self.parent = git(self.repo, "rev-parse", "HEAD")
        code = (
            "from pathlib import Path\n"
            "for name in ('ready/output', 'ignored-ready/output'):\n"
            "    assert Path(name).read_text() == 'setup'\n"
            "for name in ('artifact', 'nested', 'ignored-run', 'ignored-run-dir'):\n"
            "    assert not Path(name).exists(), name\n"
            "Path('artifact').write_text('run')\n"
            "Path('nested/new/directory').mkdir(parents=True)\n"
            "Path('nested/new/directory/output').write_text('run')\n"
            "Path('ignored-run').write_text('run')\n"
            "Path('ignored-run-dir/nested').mkdir(parents=True)\n"
            "Path('ignored-run-dir/nested/output').write_text('run')\n"
            "print(Path('ignored-ready/cache').exists())\n"
            "Path('ignored-ready/cache').write_text('intentionally retained')\n"
        )
        data = self.change(code)
        data["setup"] = [sys.executable, "-c",
                         "from pathlib import Path\n"
                         "for name in ('ready', 'ignored-ready'):\n"
                         "    Path(name).mkdir()\n"
                         "    Path(name, 'output').write_text('setup')\n"]
        self.save(data)
        result = self.prove()
        self.assertEqual(result["status"], "pass")
        self.assertEqual([run["output"].strip() for run in result["deterministic"]["proofs"]["P1"]], ["False", "True"])

    def test_reset_restores_tracked_files_and_removes_overlays(self):
        tree = self.tmp / "scratch"
        git(self.repo, "worktree", "add", "--detach", str(tree), self.parent)
        try:
            baselines = {}
            proof = {"id": "P1", "cmd": [sys.executable, "-c",
                     "from pathlib import Path; assert Path('overlay').read_text() == 'proof'; "
                     "assert Path('value').read_text() == 'overlaid'"]}
            record = workflow._execute(tree, proof, baselines,
                                       overlay={"overlay": b"proof", "value": b"overlaid"})
            self.assertTrue(record["ok"], record)
            self.assertEqual((tree / "value").read_text(), "old")
            self.assertFalse((tree / "overlay").exists())
            (tree / "value").write_text("residue")
            (tree / "artifact").write_text("residue")
            outside = self.tmp / "outside"
            outside.mkdir()
            (outside / "keep").write_text("safe")
            (tree / "linked").symlink_to(outside, target_is_directory=True)
            (tree / "new-dir").mkdir()
            (tree / "new-dir/link").symlink_to(outside, target_is_directory=True)
            (tree / "odd\nname").write_text("residue")
            proof["cmd"] = [sys.executable, "-c",
                            "from pathlib import Path; assert Path('value').read_text() == 'old'; "
                            "assert not Path('artifact').exists(); assert not Path('overlay').exists()"]
            self.assertTrue(workflow._execute(tree, proof, baselines)["ok"])
            self.assertEqual((outside / "keep").read_text(), "safe")
            self.assertFalse((tree / "linked").is_symlink())
            self.assertFalse((tree / "new-dir").exists())
            self.assertFalse((tree / "odd\nname").exists())
        finally:
            git(self.repo, "worktree", "remove", "--force", str(tree))

    def test_baseline_recording_failure_is_attributed_without_repeating_setup(self):
        data = self.change()
        counter = self.tmp / "setup-count"
        data["setup"] = [sys.executable, "-c",
                         f"from pathlib import Path; p = Path({str(counter)!r}); "
                         "p.write_text(p.read_text() + 'x' if p.exists() else 'x')"]
        self.save(data)
        with patch.object(workflow, "_entries", side_effect=OSError("baseline listing failed")):
            result = self.prove()
        self.assert_block(result, "P1: head run 1: infrastructure: baseline listing failed")
        self.assertEqual(counter.read_text(), "x")

    def test_execution_cleanup_failure_prevents_pass(self):
        self.change()
        entries = workflow._entries
        calls = 0
        def fail_cleanup(tree):
            nonlocal calls
            calls += 1
            if calls == 3:  # Setup baseline, before execution, finally.
                raise OSError("artifact cleanup failed")
            return entries(tree)
        with patch.object(workflow, "_entries", side_effect=fail_cleanup):
            result = self.prove()
        self.assert_block(result, "cleanup infrastructure: artifact cleanup failed")
        self.assertIsNone(result["deterministic"]["proofs"]["P1"][0]["ok"])

    def test_setup_mutation_is_infrastructure(self):
        data = self.change()
        data["setup"] = [sys.executable, "-c", "from pathlib import Path; Path('value').write_text('bad')"]
        self.save(data)
        result = self.prove()
        self.assert_block(result, "setup changed tracked files")
        self.assertIsNone(result["deterministic"]["proofs"]["P1"][0]["ok"])

    def test_launch_and_timeout_are_not_red_evidence(self):
        data = self.change()
        for cmd, timeout, fragment in ((["/missing-proof-executable"], 1, "launch error"),
                                        ([sys.executable, "-c", "import time; time.sleep(2)"], 0.01, "timeout")):
            with self.subTest(cmd=cmd):
                data["proofs"][0]["cmd"] = cmd
                self.save(data)
                with patch.object(workflow, "CHECK_TIMEOUT", timeout):
                    result = self.prove()
                self.assert_block(result, fragment)
                record = result["deterministic"]["proofs"]["P1"][0]
                self.assertIsNone(record["ok"])
                self.assertIn("P1: head run 1", record["error"])
        proof = self.proof(code="import time; time.sleep(2)\n")
        with patch.object(workflow, "CHECK_TIMEOUT", 0.01), self.assertRaisesRegex(workflow.WorkflowError, "infrastructure: timeout"):
            self.freeze([proof])

    def test_replacement_decode_and_resolved_cwd_scrubbing(self):
        self.change("import os\nfrom pathlib import Path\nos.write(1,b'\\xff')\nprint(Path.cwd().resolve())\n")
        result = self.prove()
        self.assertEqual(result["status"], "pass")
        output = result["deterministic"]["proofs"]["P1"][0]["output"]
        self.assertIn("\ufffd", output)
        self.assertIn("<tree>", output)
        self.assertNotIn("omp-prove-", output)

    def test_head_tracked_mutation_blocks(self):
        self.change("from pathlib import Path\nPath('value').write_text('mutated')\n")
        self.assert_block(self.prove(), "modified tracked files")

    def test_hash_and_missing_file_blocks(self):
        self.change()
        for change in ("mutate", "delete"):
            with self.subTest(change=change):
                if change == "mutate":
                    self.write("proof.py", "assert True\n")
                else:
                    (self.repo / "proof.py").unlink()
                self.head = self.commit(change)
                self.assert_block(self.prove(), "hash mismatch")

    def test_missing_and_duplicate_ids(self):
        data = self.change()
        for proofs in ([{k: v for k, v in data["proofs"][0].items() if k != "id"}], data["proofs"] * 2):
            self.save(dict(data, proofs=proofs))
            self.assert_block(self.prove(), "missing or duplicate proof id")

    def test_head_first_revocation_on_invalid_inputs(self):
        self.change()
        for kwargs in ({"parent": "missing"}, {"target_ref": "missing"}, {"digest": "0" * 64}):
            workflow._atomic_json(workflow.result_path(self.repo, self.head), {"head": self.head, "status": "pass"})
            result = workflow.prove(self.repo, kwargs.get("parent", self.parent), self.head, self.manifest_path,
                                    kwargs.get("digest", workflow._sha(self.manifest_path)), "fix", target_ref=kwargs.get("target_ref", self.parent))
            self.assertEqual(result["status"], "fail")
            self.assertEqual(self.stored_status(), "fail")

    def test_strict_range_and_proof_ancestry(self):
        data = self.change()
        for parent, target in ((self.head, self.parent), (self.parent, self.head)):
            result = workflow.prove(self.repo, parent, self.head, self.manifest_path, workflow._sha(self.manifest_path), "fix", target_ref=target)
            self.assertEqual(result["status"], "fail")
        data["proofs"][0]["parent"] = self.head
        self.save(data)
        self.assert_block(self.prove(), "proof parent")
        unrelated = git(self.repo, "commit-tree", f"{self.head}^{{tree}}", "-m", "unrelated")
        data["proofs"][0]["parent"] = unrelated
        self.save(data)
        self.assert_block(self.prove(), "proof parent")
        self.save(dict(data, base=self.head))
        self.assert_block(self.prove(), "base must be")

    def test_invalid_head_gate_precedes_tree_creation_and_setup(self):
        for text in ("null", "{", json.dumps({"schemaVersion": 3}), json.dumps({"schemaVersion": 2, "checks": {"commit": ["true"]}}),
                     json.dumps({"schemaVersion": 2, "pathCheck": ["true"], "dynaflows": {"enabled": "yes"}})):
            self.write_gate(text)
            self.change()
            with patch.object(workflow, "_setup", side_effect=AssertionError("setup must not run")):
                self.assertEqual(self.prove()["status"], "fail")

    def test_cleanup_failure_blocks_and_keeps_evidence(self):
        self.change()
        original = workflow._git
        removed = []
        def fail_remove(repo, *args):
            if args[:2] == ("worktree", "remove"):
                removed.append(args[-1])
                raise WorkflowErrorForTest("injected removal failure")
            return original(repo, *args)
        with patch.object(workflow, "_git", side_effect=fail_remove):
            result = workflow.prove(self.repo, self.parent, self.head, self.manifest_path, workflow._sha(self.manifest_path), "fix", target_ref=self.parent)
        # TemporaryDirectory has removed test-owned scratch bytes; prune repairs its metadata.
        git(self.repo, "worktree", "prune")
        self.assertEqual(len(removed), 1)
        self.assertTrue(result["deterministic"]["proofs"])
        self.assert_block(result, "cleanup infrastructure")

    def test_freeze_retention_lineage_and_revisions(self):
        proof = self.proof()
        previous = self.freeze([proof], setup=[sys.executable, "-c", "pass"])
        old = json.loads(previous[0].read_text())
        with patch.object(workflow, "_execute", side_effect=AssertionError("retention reran")):
            retained = self.freeze([proof], previous=previous)
        self.assertEqual(json.loads(retained[0].read_text()), old)
        self.write("value", "fixed")
        self.commit("implementation")
        for kwargs in ({"base": "HEAD"}, {"setup": ["true"]}):
            with self.assertRaisesRegex(workflow.WorkflowError, "immutable"):
                self.freeze([proof], previous=retained, **kwargs)
        modified = dict(proof, criterion="specific value")
        with self.assertRaisesRegex(workflow.WorkflowError, "silent proof change"):
            self.freeze([modified], previous=retained)
        updated = self.freeze([modified], previous=retained, revise={"P1": "clarify criterion"})
        revision = json.loads(updated[0].read_text())["revisions"][-1]
        expected = hashlib.sha256(json.dumps(old["proofs"][0], sort_keys=True).encode()).hexdigest()
        self.assertEqual(revision["oldSha256"], expected)

    def test_freeze_later_parent_and_metadata_validation(self):
        proof = self.proof()
        self.write("value", "fixed")
        self.commit("defective implementation")
        with self.assertRaisesRegex(workflow.WorkflowError, "defective implementation commit as parent"):
            self.freeze([proof])
        for revise in ({"P1": " "}, {"unknown": "reason"}, []):
            with self.assertRaises(workflow.WorkflowError):
                self.freeze([proof], revise=revise)

    def test_freeze_paths_command_files_and_option_guards(self):
        proof = self.proof(filename="-proof")
        proof["cmd"] = [sys.executable, "./-proof"]
        self.freeze([proof])
        self.write("runner", "print('runner')")
        self.commit("runner")
        proof = self.proof()
        for token in ("runner", "./runner", "--config=runner", str(self.repo / "runner"), "--config=" + str(self.repo / "runner")):
            with self.subTest(token=token):
                candidate = dict(proof, cmd=[sys.executable, token])
                with self.assertRaises(workflow.WorkflowError):
                    self.freeze([candidate])

    def test_freeze_ignored_symlink_and_check_ignore_error(self):
        proof = self.proof()
        self.write(".gitignore", "proof.py\n")
        self.commit("ignore")
        with self.assertRaisesRegex(workflow.WorkflowError, "ignored"):
            self.freeze([proof])
        self.write(".gitignore", "")
        self.commit("unignore")
        (self.repo / "proof.py").unlink(missing_ok=True)
        (self.repo / "proof.py").symlink_to(self.repo / "value")
        with self.assertRaisesRegex(workflow.WorkflowError, "symlink"):
            self.freeze([proof])
        (self.repo / "proof.py").unlink()
        proof = self.proof()
        original = subprocess.run
        def fail_ignore(cmd, **kwargs):
            if "check-ignore" in cmd:
                return subprocess.CompletedProcess(cmd, 128, b"", b"ignore failed")
            return original(cmd, **kwargs)
        with patch.object(workflow.subprocess, "run", side_effect=fail_ignore):
            with self.assertRaisesRegex(workflow.WorkflowError, "check-ignore infrastructure"):
                self.freeze([proof])

    def test_caps_are_proof_specific(self):
        proofs = [self.proof(f"proof{i}.py", ident=f"P{i}") for i in range(17)]
        self.freeze(proofs, max_checks=17)
        with self.assertRaises(workflow.WorkflowError):
            self.freeze(proofs)
        self.change(proofs=proofs)
        self.assertEqual(self.prove(max_checks=17)["status"], "pass")
        self.assert_block(self.prove(), "within max_checks")
        self.assert_block(self.prove(max_checks=1), "within max_checks")
        for cap in (0, -1, True, 1.5, "2"):
            with self.assertRaises(workflow.WorkflowError):
                self.freeze(proofs, max_checks=cap)
            self.assert_block(self.prove(max_checks=cap), "positive integer")

    def test_draft_validation_precedes_hashing_and_commands(self):
        proof = self.proof()
        for invalid in ([dict(proof, files=[["not a path"]])], [dict(proof, cmd="shell")], [proof, dict(proof)],
                        [dict(proof, kind="defect")], [dict(proof, expectRed="expected fixed")]):
            with patch.object(workflow, "_git", side_effect=AssertionError("Git ran before draft validation")):
                with self.assertRaises(workflow.WorkflowError):
                    self.freeze(invalid)

    def test_frozen_command_tokens_cannot_bypass_source_isolation(self):
        data = self.change()
        for token in (str(self.repo / "proof.py"), "--config=" + str(self.repo / "proof.py")):
            data["proofs"][0]["cmd"] = [sys.executable, token]
            self.save(data)
            self.assert_block(self.prove(), "absolute command reference")
        self.write("runner", "assert True")
        self.head = self.commit("runner")
        data["proofs"][0]["cmd"] = [sys.executable, "--config=runner"]
        self.save(data)
        self.assert_block(self.prove(), "unchanged runners")

    def test_removed_proof_requires_revision_and_retains_entire_old_digest(self):
        first = self.proof()
        second = self.proof("second.py", ident="P2")
        previous = self.freeze([first, second])
        old_second = json.loads(previous[0].read_text())["proofs"][1]
        self.commit("frozen proofs")
        with self.assertRaisesRegex(workflow.WorkflowError, "silent proof change"):
            self.freeze([first], previous=previous)
        self.freeze([first], previous=previous, revise={"P2": "remove duplicate criterion"})
        revision = json.loads(self.manifest_path.read_text())["revisions"][-1]
        self.assertEqual(revision["oldSha256"], hashlib.sha256(json.dumps(old_second, sort_keys=True).encode()).hexdigest())

    def test_later_proof_uses_explicit_parent_where_it_is_red(self):
        initial = self.proof()
        previous = self.freeze([initial])
        defective = self.commit("implementation missing a later-found behaviour")
        later = self.proof("later.py", "from pathlib import Path\nassert Path('value').read_text() == 'reviewed', 'later regression'\n", "P2")
        later["parent"] = defective
        self.freeze([initial, later], previous=previous)
        frozen = json.loads(self.manifest_path.read_text())
        self.assertEqual(frozen["proofs"][1]["parent"], defective)
        self.assertEqual(frozen["base"], self.parent)

    def test_freeze_parent_before_base_and_slug_change(self):
        proof = self.proof()
        previous = self.freeze([proof])
        with self.assertRaisesRegex(workflow.WorkflowError, "immutable"):
            workflow.freeze(self.repo, self.manifest_path, [proof], "different", previous=previous)
        self.commit("later base")
        with self.assertRaisesRegex(workflow.WorkflowError, "at or after base"):
            self.freeze([dict(proof, parent=self.parent)], base="HEAD")

    def test_result_file_is_the_one_merge_signal_in_the_common_git_directory(self):
        self.change()
        linked = self.tmp / "linked"
        git(self.repo, "worktree", "add", "-qb", "linked", str(linked), self.head)
        try:
            original = os.replace
            observed = []
            def inspect(source, destination):
                observed.append((json.loads(Path(source).read_text()), Path(destination)))
                return original(source, destination)
            with patch.object(workflow.os, "replace", side_effect=inspect):
                result = workflow.prove(linked, self.parent, self.head, self.manifest_path, workflow._sha(self.manifest_path), "fix value", target_ref=self.parent)
            self.assertEqual(result["status"], "pass", result)
            path = workflow.result_path(linked, self.head)
            self.assertEqual(path, workflow._common(self.repo) / "omp-prove" / f"{self.head}.result.json")
            self.assertEqual({destination for _, destination in observed}, {path}, "the result file is the only file prove() writes")
            stored = json.loads(path.read_text())
            self.assertEqual((stored["head"], stored["status"]), (self.head, "pass"))
            self.assertEqual(sorted(p.name for p in path.parent.iterdir()), [path.name], "no separate pass marker")
        finally:
            git(self.repo, "worktree", "remove", "--force", str(linked))

    def gate(self, **checks):
        gate = {"schemaVersion": 2, "checks": {"commit": ["false"], "ci": ["false"], **checks}, "pathCheck": ["true"],
                "ci": {"requiredContext": "gate", "runner": "ci-gate"}}
        self.write_gate(json.dumps(gate))

    def rebase_parent(self, message="parent fixtures"):
        self.parent = self.commit(message)

    def test_prove_without_a_record_is_the_guardrail_prerequisite(self):
        shutil.rmtree(self.record)
        self.change()
        with patch.object(workflow, "_setup", side_effect=AssertionError("trees must not be built")):
            result = self.prove()
        self.assert_block(result, workflow.GUARDRAIL_PREREQUISITE)
        self.assert_block(result, f"dynaflows off for example-owner/example-repo: no per-repo record at {self.record / 'gate.json'}")

    def test_prove_requires_path_check_but_not_commit_check(self):
        self.write_gate(json.dumps({"schemaVersion": 2, "checks": {"commit": ["true"]}}))
        self.change()
        self.assert_block(self.prove(), workflow.GUARDRAIL_PREREQUISITE)
        self.write_gate(json.dumps({"schemaVersion": 2, "pathCheck": ["true"]}))
        self.assertEqual(self.prove()["status"], "pass")

    def test_prove_without_origin_finds_no_record(self):
        git(self.repo, "remote", "remove", "origin")
        self.change()
        self.assert_block(self.prove(), "dynaflows off for none: no origin remote")

    def test_repo_tracked_omp_files_are_never_read(self):
        # A leftover repo .omp/ directory must not change policy: only the record counts.
        self.write(".omp/gate.json", json.dumps({"schemaVersion": 2, "ciHost": "github"}))
        self.write(".omp/test-profile.md", "---\n{not json\n---\n")
        self.rebase_parent("leftover repo omp files")
        self.freeze([self.proof()])
        self.change()
        self.assertEqual(self.prove()["status"], "pass")

    def test_linked_worktree_resolves_to_the_main_checkout_record(self):
        linked = self.tmp / "linked"
        git(self.repo, "worktree", "add", "-qb", "linked", str(linked))
        try:
            record, gate = workflow.read_record(linked)
            self.assertEqual(record, self.record)
            self.assertEqual(gate["pathCheck"], ["true"])
        finally:
            git(self.repo, "worktree", "remove", "--force", str(linked))

    def test_unreadable_or_rejected_record_raises(self):
        for text, fragment in (("{", "record "), (json.dumps({"schemaVersion": 2, "dynaflows": {"enabled": "yes"}}), 'dynaflows must be {"enabled": true|false}')):
            with self.subTest(text=text):
                (self.record / "gate.json").write_text(text)
                with self.assertRaisesRegex(workflow.WorkflowError, re.escape(fragment)):
                    workflow.read_record(self.repo)

    def test_record_with_legacy_contract_fields_still_loads_freezes_and_proves(self):
        for legacy in ({"dynaflowsContract": 1}, {"dynaflowsContract": 99}, {"dynaflowsContract": "old"},
                       {"pathCheckTimeoutMs": 5000, "dynaflowsContract": 3}):
            with self.subTest(legacy=legacy):
                self.manifest_path.unlink(missing_ok=True)
                self.write_gate(json.dumps({"schemaVersion": 2, "checks": {"commit": ["false"]}, "pathCheck": ["true"], **legacy}))
                record, gate = workflow.read_record(self.repo)
                self.assertEqual(record, self.record)
                self.assertEqual({k: gate[k] for k in legacy}, legacy)
                self.write("value", "old")
                self.parent = self.commit(f"legacy record {legacy}")
                self.freeze([self.proof()])
                self.assertNotIn("dynaflowsContract", json.loads(self.manifest_path.read_text()))
                self.change()
                self.assertEqual(self.prove()["status"], "pass")

    def test_freeze_without_record_refuses_unless_project_setup(self):
        (self.record / "gate.json").unlink()
        no_record = f"dynaflows off for example-owner/example-repo: no per-repo record at {self.record / 'gate.json'}"
        with self.assertRaises(workflow.WorkflowError) as raised:
            self.freeze([self.proof()])
        self.assertEqual(str(raised.exception), no_record)
        self.assertFalse(self.manifest_path.exists())
        with self.assertRaisesRegex(workflow.WorkflowError, re.escape(workflow.FEATURE_MAP_SETUP)):
            self.freeze([self.proof()], project_setup=True)
        self.assertFalse(self.manifest_path.exists(), "without a record there is no feature map to link proofs from")
        (self.repo / "proof.py").unlink()
        self.freeze([], project_setup=True)
        self.assertTrue(self.manifest_path.exists())
        with self.assertRaisesRegex(workflow.WorkflowError, "project_setup must be a bool"):
            self.freeze([self.proof()], project_setup="yes")

    def test_run_check_strips_harness_variables_from_repo_commands(self):
        probe = [sys.executable, "-c", "import os; print(repr(os.environ.get('AGENT')), os.environ.get('KEEP'))"]
        with patch.dict(os.environ, {"AGENT": "1", "KEEP": "kept"}):
            ok, out = workflow.run_check(probe, self.repo)
            self.assertEqual(os.environ["AGENT"], "1", "the harness's own environment is unchanged")
        self.assertTrue(ok)
        self.assertEqual(out.strip(), "None kept")

    def test_retired_ci_host_blocks_freeze_and_prove(self):
        retired = "ciHost is retired; declare ci.requiredContext and ci.runner"
        self.write_gate(json.dumps({"schemaVersion": 2, "checks": {"commit": ["false"]}, "pathCheck": ["true"], "ciHost": "github"}))
        self.rebase_parent("retired gate")
        with self.assertRaisesRegex(workflow.WorkflowError, re.escape(retired)):
            self.freeze([self.proof()])
        self.assertFalse(self.manifest_path.exists())
        self.change()
        self.assert_block(self.prove(), retired)

    def test_freeze_formats_before_hashing_and_reports_changed_files(self):
        self.write("fmt.py", "from pathlib import Path\nfor name in ('proof.py', 'other.txt'):\n"
                   "    p = Path(name)\n    if p.exists() and not p.read_text().endswith('# formatted\\n'):\n"
                   "        p.write_text(p.read_text() + '# formatted\\n')\n")
        self.write("other.txt", "untouched\n")
        self.gate(format=[sys.executable, "fmt.py"])
        self.rebase_parent()
        proof = self.proof()
        result = self.freeze([proof])
        path, digest = result
        self.assertEqual(result.formatted, ["other.txt", "proof.py"])
        self.assertTrue((self.repo / "proof.py").read_text().endswith("# formatted\n"))
        manifest = json.loads(path.read_text())
        self.assertEqual(manifest["proofs"][0]["sha256"]["proof.py"], workflow._sha(self.repo / "proof.py"))
        self.assertEqual(workflow._sha(path), digest)
        with self.assertRaisesRegex(workflow.WorkflowError, "unfrozen change: other.txt"):
            self.freeze([proof])
        git(self.repo, "commit", "-qm", "commit formatter output", "--", "other.txt")
        self.assertEqual(self.freeze([proof]).formatted, [])

    def test_freeze_format_failure_refuses(self):
        self.gate(format=[sys.executable, "-c", "import sys; print('cannot format'); sys.exit(3)"])
        self.rebase_parent()
        with self.assertRaisesRegex(workflow.WorkflowError, "checks.format failed:\n(.|\n)*cannot format"):
            self.freeze([self.proof()])
        self.assertFalse(self.manifest_path.exists())

    def test_freeze_without_format_returns_no_formatted_paths(self):
        result = self.freeze([self.proof()])
        self.assertEqual(result.formatted, [])
        self.assertEqual(len(result), 2)

    def profile(self, marker, **surface):
        values = {"lever_command": "lever", "doctor_marker": marker, "feature_map_path": f"{MAP_PATH}/", **surface}
        front = json.dumps({"verification_surface": {k: v for k, v in values.items() if v is not None}})
        self.write_profile(f"---\n{front}\n---\n\n# Repo test profile\n")

    def test_freeze_refuses_red_from_environment_doctor(self):
        self.profile("DOCTOR:")
        self.rebase_parent()
        doctor = self.proof(code="from pathlib import Path\nprint('DOCTOR: database not reachable')\n" + RED_AT_BASE)
        with self.assertRaisesRegex(workflow.WorkflowError, "P1: red came from the environment doctor, not the behaviour"):
            self.freeze([doctor])
        self.assertFalse(self.manifest_path.exists())
        path, _ = self.freeze([self.proof()])
        self.assertTrue(path.exists())

    def test_freeze_skips_doctor_check_for_placeholder_or_unparsed_profile(self):
        doctor = "from pathlib import Path\nprint('<doctor failure marker>')\n" + RED_AT_BASE
        for marker in ("<doctor failure marker>", None):
            with self.subTest(marker=marker):
                self.profile(marker)
                self.rebase_parent(f"profile {marker}")
                path, _ = self.freeze([self.proof(code=doctor)], base=self.parent)
                self.assertTrue(path.exists())
                path.unlink()
        self.write_profile("---\n{not json\n---\n")
        self.rebase_parent("broken profile")
        with self.assertRaisesRegex(workflow.WorkflowError, "front matter is not valid JSON"):
            self.freeze([self.proof()], base=self.parent)

    def test_freeze_refuses_a_proof_no_feature_map_document_links(self):
        self.write(f"{MAP_PATH}/value.md", map_doc("../../journey.py"))
        self.rebase_parent("map links only the journey")
        unmapped = self.proof("unmapped.py")
        with self.assertRaisesRegex(workflow.WorkflowError, re.escape(
                f"proof files not linked from any feature-map document under {MAP_PATH}: P1: unmapped.py")):
            self.freeze([unmapped])
        self.assertFalse(self.manifest_path.exists())
        # Naming a file in prose is not a link; only a resolving link from a map document maps a proof.
        self.write(f"{MAP_PATH}/value.md", map_doc("../../journey.py") + "\nSee unmapped.py.\n")
        self.rebase_parent("prose mention")
        with self.assertRaisesRegex(workflow.WorkflowError, "not linked from any feature-map document"):
            self.freeze([unmapped])

    def test_freeze_accepts_a_proof_linked_from_an_uncommitted_map_entry(self):
        self.write(f"{MAP_PATH}/value.md", map_doc())
        self.rebase_parent("empty map")
        proof = self.proof("tests/value_test.py")
        self.write(f"{MAP_PATH}/value.md", map_doc("../../tests/value_test.py"))
        path, _ = self.freeze([proof])
        self.assertEqual(json.loads(path.read_text())["proofs"][0]["files"], ["tests/value_test.py"],
                         "the map entry is documentation, not a frozen proof file")

    def test_freeze_without_a_feature_map_refuses_proofs_but_not_zero_proofs(self):
        for surface in ({"feature_map_path": None}, {"feature_map_path": "<feature map path>"}):
            with self.subTest(surface=surface):
                self.profile(None, **surface)
                with self.assertRaisesRegex(workflow.WorkflowError, re.escape(workflow.FEATURE_MAP_SETUP)):
                    self.freeze([self.proof()])
                self.assertFalse(self.manifest_path.exists())
        self.write_profile("---\n" + json.dumps({"verification_surface": {"none": "library", "approval": "owner"}}) + "\n---\n")
        with self.assertRaisesRegex(workflow.WorkflowError, "project-setup"):
            self.freeze([self.proof()])
        self.profile(None, feature_map_path="docs/missing")
        with self.assertRaisesRegex(workflow.WorkflowError, "feature map docs/missing .* does not exist"):
            self.freeze([self.proof()])
        (self.repo / "proof.py").unlink()
        path, _ = self.freeze([])
        self.assertEqual(json.loads(path.read_text())["proofs"], [])

    def test_prove_blocks_a_changed_map_document_that_links_no_frozen_proof(self):
        self.write(f"{MAP_PATH}/other.md", map_doc("../../other_test.py", title="Other"))
        self.write("other_test.py", "assert True\n")
        data = self.change()
        self.assert_block(self.prove(), f"feature-map documents changed in {self.parent}..{self.head} link no frozen proof: "
                                        f"{MAP_PATH}/other.md")
        self.write(f"{MAP_PATH}/other.md", map_doc("../../other_test.py", "../../proof.py", title="Other"))
        self.head = self.commit("link the frozen proof")
        self.save(data)
        self.assertEqual(self.prove()["status"], "pass")

    def test_prove_passes_when_every_changed_map_document_links_a_proof(self):
        self.write(f"{MAP_PATH}/value.md", map_doc("../../proof.py", "../../journey.py"))
        self.write(f"{MAP_PATH}/README.md", "# Features\n\n- [Value](value.md)\n")
        self.profile(None)
        front = json.loads((self.record / "profile.md").read_text().split("---")[1])
        front["guardrails"] = {"feature_map": {"index_files": ["README.md"]}}
        self.write_profile(f"---\n{json.dumps(front)}\n---\n")
        self.change()
        result = self.prove()
        self.assertEqual(result["status"], "pass", result)
        (self.record / "profile.md").write_text("---\n" + json.dumps({"verification_surface": front["verification_surface"]}) + "\n---\n")
        self.assert_block(self.prove(), f"link no frozen proof: {MAP_PATH}/README.md")

    def test_zero_proof_pr_without_a_map_change_passes(self):
        self.write("docs.md", "docs\n")
        self.commit("docs")
        path, digest = self.freeze([], base=self.parent)
        self.head = git(self.repo, "rev-parse", "HEAD")
        result = workflow.prove(self.repo, self.parent, self.head, path, digest, "docs", target_ref=self.parent)
        self.assertEqual(result["status"], "pass", result)
        self.write(f"{MAP_PATH}/value.md", map_doc("../../proof.py") + "\nMore behaviour.\n")
        self.head = self.commit("behaviour change without proof")
        result = workflow.prove(self.repo, self.parent, self.head, path, digest, "docs", target_ref=self.parent)
        self.assert_block(result, f"link no frozen proof: {MAP_PATH}/value.md")

    def journey(self, counter):
        code = ("import sys\nfrom pathlib import Path\ncount = Path(sys.argv[1])\n"
                "n = int(count.read_text()) + 1 if count.exists() else 1\ncount.write_text(str(n))\n"
                "assert Path('value').read_text() == 'fixed', 'journey not reached'\n")
        self.write("journey.py", code)
        return {"id": "J1", "criterion": "journey completes", "cmd": [sys.executable, "journey.py", str(counter)],
                "files": ["journey.py"], "journey": True}

    def test_freeze_runs_journey_once_at_baseline(self):
        counter = self.tmp / "journey-count"
        path, _ = self.freeze([self.journey(counter)])
        self.assertEqual(counter.read_text(), "1", "freeze runs only the baseline; stability is a prove() check at head")
        self.assertTrue(json.loads(path.read_text())["proofs"][0]["journey"])

    def test_prove_runs_journey_three_times_at_head(self):
        counter = self.tmp / "journey-count"
        self.change(proofs=[self.journey(counter)])
        result = self.prove()
        self.assertEqual(result["status"], "pass", result)
        locations = [(run["location"], run.get("run")) for run in result["deterministic"]["proofs"]["J1"]]
        self.assertEqual(locations, [("head", 1), ("head", 2), ("head", 3)])
        self.assertEqual(counter.read_text(), "3")

    def test_prove_blocks_journey_that_fails_any_head_run(self):
        counter = self.tmp / "journey-count"
        proof = self.journey(counter)
        self.write("journey.py", "import sys\nfrom pathlib import Path\ncount = Path(sys.argv[1])\n"
                   "n = int(count.read_text()) + 1 if count.exists() else 1\ncount.write_text(str(n))\n"
                   "assert n != 2, 'journey flaked'\n")
        self.change(proofs=[proof])
        result = self.prove()
        self.assert_block(result, "J1: head run 2 failed")
        self.assertIn("J1: journey proof passed 2 of 3 clean head runs; journeys must pass every run", result["blocking"])

    def test_non_journey_proofs_keep_two_head_runs(self):
        self.change()
        runs = [run.get("run") for run in self.prove()["deterministic"]["proofs"]["P1"] if run["location"] == "head"]
        self.assertEqual(runs, [1, 2])

    def test_prove_never_runs_the_profile_check(self):
        self.gate(profile=[sys.executable, "-c", "import sys; sys.exit(1)"])
        self.change()
        self.assertEqual(self.prove()["status"], "pass", "CI already ran the repo's gates")

    def test_prove_refuses_before_any_spend_when_required_context_is_not_success(self):
        self.gate()
        self.change()
        for state in ("pending", "failure", "missing", "in_progress/"):
            with self.subTest(state=state):
                self.ci.reset_mock()
                self.ci.return_value = state
                with patch.object(workflow, "_setup", side_effect=AssertionError("trees must not be built")):
                    result = self.prove()
                self.assert_block(result, f'required CI context "gate" is {state} for {self.head}; prove() runs only after CI is green on this exact head')
                self.assertFalse(any(item.startswith("infrastructure") for item in result["blocking"]), result)
                self.ci.assert_called_once_with(self.repo.resolve(), self.head, "gate")
        self.ci.return_value = "success"
        self.assertEqual(self.prove()["status"], "pass")

    def test_prove_skips_ci_read_when_gate_declares_no_required_context(self):
        self.change()
        self.assertEqual(self.prove()["status"], "pass")
        self.ci.assert_not_called()

    def test_required_context_state_reads_statuses_and_check_runs_on_exact_head(self):
        self.ci_patch.stop()
        self.addCleanup(self.ci_patch.start)
        git(self.repo, "remote", "set-url", "origin", "https://github.com/example-owner/example-repo.git")
        head = "a" * 40
        def respond(statuses, runs):
            def github(repo, *args):
                self.assertEqual(args[0], "api")
                if "/status?" in args[1]:
                    self.assertEqual(args[1], f"repos/example-owner/example-repo/commits/{head}/status?per_page=100")
                    return {"statuses": statuses}
                self.assertEqual(args[1], f"repos/example-owner/example-repo/commits/{head}/check-runs?check_name=ci%2Fgate&per_page=100")
                return {"check_runs": runs}
            return github
        cases = [([{"context": "ci/gate", "state": "success"}], [], "success"),
                 ([], [{"name": "ci/gate", "status": "completed", "conclusion": "success"}], "success"),
                 ([{"context": "ci/gate", "state": "pending"}], [], "pending"),
                 ([{"context": "other", "state": "success"}], [{"name": "other", "status": "completed", "conclusion": "success"}], "missing"),
                 ([], [{"name": "ci/gate", "status": "in_progress", "conclusion": None}], "in_progress/"),
                 ([], [{"name": "ci/gate", "status": "completed", "conclusion": "failure"}], "completed/failure")]
        for statuses, runs, expected in cases:
            with self.subTest(expected=expected, statuses=statuses, runs=runs), patch.object(workflow, "_github", side_effect=respond(statuses, runs)):
                self.assertEqual(workflow._required_context_state(self.repo, head, "ci/gate"), expected)

    def test_prove_writes_result_and_posts_it_on_the_pr(self):
        self.change()
        result = self.prove()
        self.assertEqual(result["status"], "pass", result)
        path = workflow.result_path(self.repo, self.head)
        self.assertEqual(path.parent, workflow._common(self.repo) / "omp-prove")
        self.assertEqual(json.loads(path.read_text()), result)
        self.assertEqual(result["prComment"], self.comment.return_value)
        self.assertEqual((result["head"], result["objective"], result["manifestSha256"]), (self.head, "fix value", workflow._sha(self.manifest_path)))
        repo, head, posted = self.comment.call_args.args
        self.assertEqual((repo, head, posted["status"]), (self.repo.resolve(), self.head, "pass"))
        self.write("value", "regressed")
        self.head = self.commit("regression")
        failed = self.prove()
        self.assert_block(failed, "head run 1 failed")
        self.assertEqual(self.stored_status(), "fail", "a failed prove also writes and posts its result")
        self.assertEqual(self.comment.call_args.args[2]["status"], "fail")

    def test_unpublished_pass_fails(self):
        self.change()
        self.comment.side_effect = workflow.WorkflowError("no unique open PR at requested head")
        result = self.prove()
        self.assert_block(result, "infrastructure: publishing prove result: no unique open PR at requested head")
        self.assertEqual(self.stored_status(), "fail")

    def test_unwritable_result_never_leaves_a_stale_pass(self):
        self.change()
        self.assertEqual(self.prove()["status"], "pass")
        writes = []
        original = workflow._atomic_json
        def fail_second(path, value):
            writes.append(path)
            if len(writes) == 2:
                raise OSError("disk full")
            return original(path, value)
        with patch.object(workflow, "_atomic_json", side_effect=fail_second):
            result = self.prove()
        self.assert_block(result, "infrastructure: publishing prove result: disk full")
        self.assertIsNone(self.stored_status())

    def test_result_comment_fits_github_limit(self):
        result = {"status": "pass", "head": "a" * 40, "blocking": [],
                  "deterministic": {"proofs": {"P1": [{"location": "head", "output": "x" * 5000 + "END"}]}}}
        body = workflow._result_comment(result)
        self.assertTrue(body.startswith(workflow.RESULT_MARKER))
        self.assertIn("[TRUNCATED to last 1000 chars]", body)
        self.assertIn("END", body)
        self.assertEqual(result["deterministic"]["proofs"]["P1"][0]["output"], "x" * 5000 + "END", "the returned result stays complete")
        result["deterministic"]["proofs"] = {f"P{n}": [{"output": "y" * 5000}] * 3 for n in range(30)}
        self.assertLessEqual(len(workflow._result_comment(result)), workflow.COMMENT_LIMIT)
        result["blocking"] = ["z" * workflow.COMMENT_LIMIT]
        self.assertIn("too large for a PR comment", workflow._result_comment(result))

    def test_post_result_comment_targets_the_one_open_pr_at_head(self):
        self.comment_patch.stop()
        self.addCleanup(self.comment_patch.start)
        head = "a" * 40
        bodies = []
        def gh(repo, *args):
            bodies.append(Path(args[-1]).read_text())
            return "https://github.com/example-owner/example-repo/pull/7#issuecomment-9\n"
        with patch.object(workflow, "_github", return_value=[{"number": 7, "headRefOid": head}, {"number": 8, "headRefOid": "b" * 40}]) as github, \
                patch.object(workflow, "_gh", side_effect=gh) as text:
            url = workflow._post_result_comment(self.repo, head, {"status": "pass", "head": head})
        self.assertEqual(url, "https://github.com/example-owner/example-repo/pull/7#issuecomment-9")
        github.assert_called_once_with(self.repo, "pr", "list", "--search", head, "--state", "open", "--json", "headRefOid,number")
        self.assertEqual(text.call_args.args[1:4], ("pr", "comment", "7"))
        self.assertIn('"status": "pass"', bodies[0])
        with patch.object(workflow, "_github", return_value=[]), self.assertRaisesRegex(workflow.WorkflowError, "no unique open PR"):
            workflow._post_result_comment(self.repo, head, {"status": "pass", "head": head})


class WorkflowErrorForTest(RuntimeError):
    pass


class SchemaTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.path = Path(scratch.name) / "manifest.json"
        self.data = {"schemaVersion": 1, "slug": "valid", "base": "a" * 40, "proofs": [{"id": "P", "criterion": "works",
                     "cmd": [sys.executable, "proof.py"], "files": ["proof.py"], "sha256": {"proof.py": "b" * 64}}]}

    def load(self, data):
        self.path.write_text(json.dumps(data))
        return workflow.load_manifest(self.path, workflow._sha(self.path))

    def test_manifest_invalid_shapes(self):
        mutations = [("slug", []), ("schemaVersion", True), ("base", None), ("setup", "shell"), ("revisions", {}),
                     ("revisions", [{"id": [], "reason": "why", "oldSha256": "a" * 64}]), ("proofs", None), ("proofs", {})]
        for key, value in mutations:
            with self.subTest(key=key, value=value), self.assertRaises(workflow.WorkflowError):
                self.load(dict(self.data, **{key: value}))
        for key, value in (("files", [["unhashable"]]), ("files", ["a", "a"]), ("cmd", "shell"), ("criterion", " "),
                           ("sha256", []), ("sha256", {"proof.py": 3}), ("parent", "HEAD")):
            data = copy.deepcopy(self.data)
            data["proofs"][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(workflow.WorkflowError):
                self.load(data)
        self.path.write_text("{")
        with self.assertRaises(workflow.WorkflowError):
            workflow.load_manifest(self.path, workflow._sha(self.path))

    def test_zero_proofs_are_valid(self):
        data = dict(self.data, proofs=[])
        self.assertEqual(self.load(data), data)

    def test_retired_proof_kinds_and_expect_red_are_refused(self):
        for key, value in (("kind", "defect"), ("kind", "preserve"), ("kind", "new-surface"), ("expectRed", None), ("expectRed", "x")):
            data = copy.deepcopy(self.data)
            data["proofs"][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaisesRegex(workflow.WorkflowError, f"P: {key} retired"):
                self.load(data)

    def test_legacy_manifest_contract_field_is_ignored(self):
        data = dict(self.data, dynaflowsContract=2)
        self.assertEqual(self.load(data), data)

    def test_noncanonical_paths_and_tmp_independence(self):
        for name in ("./proof.py", "../proof.py", "a//b", "a/./b", "a/../b", "/proof.py", "a/", "C:proof", "a\\b", "a/.git/b", "a\0b", ""):
            data = copy.deepcopy(self.data)
            data["proofs"][0].update(files=[name], sha256={name: "b" * 64})
            with self.subTest(name=name), self.assertRaises(workflow.WorkflowError):
                self.load(data)
        with patch.object(workflow.tempfile, "gettempdir", side_effect=AssertionError("lexical validation touched host")):
            self.assertEqual(self.load(self.data), self.data)

    def test_gate_schema_optional_fields(self):
        valid = [{"schemaVersion": 2, **SWITCH}, {"schemaVersion": 2, "testGlobs": ["", "*.py"], "checks": {"ci": ["true"]}, **SWITCH},
                 {"schemaVersion": 2, "ci": {"requiredContext": "gate", "runner": "ci-gate"},
                  "checks": {"format": ["ruff", "format"], "profile": ["omp-guardrails", "profile-check"]}, "dynaflows": {"enabled": False}}]
        invalid = [{"schemaVersion": True}, {"schemaVersion": 3},
                   {"schemaVersion": 2, "checks": {"ci": None}}, {"schemaVersion": 2, "pathCheck": None}, {"schemaVersion": 2, "testGlobs": [2]},
                   {"schemaVersion": 2, "agentMerge": False}, {"schemaVersion": 2, "checks": {"format": "ruff format"}},
                   {"schemaVersion": 2, "checks": {"profile": []}}, {"schemaVersion": 2, "ci": None}, {"schemaVersion": 2, "ci": "gate"},
                   {"schemaVersion": 2, "ci": {"requiredContext": "gate"}}, {"schemaVersion": 2, "ci": {"requiredContext": " ", "runner": "ci-gate"}},
                   {"schemaVersion": 2, "ci": {"requiredContext": "gate", "runner": 1}}]
        for data in valid:
            self.assertIsNotNone(workflow.gate_policy(data))
        for data in invalid:
            with self.subTest(data=data):
                self.assertIsNone(workflow.gate_policy(data))
                self.assertIsInstance(workflow.gate_reason(data), str)
        self.assertIsNone(workflow.gate_reason(valid[2]))
        self.assertEqual(workflow.gate_reason({"schemaVersion": 2, "ci": {"requiredContext": "gate"}}),
                         "ci must declare nonblank requiredContext and runner")
        for switch in (None, {}, {"enabled": 1}, {"enabled": "true"}, True):
            with self.subTest(dynaflows=switch):
                gate = {"schemaVersion": 2} if switch is None else {"schemaVersion": 2, "dynaflows": switch}
                self.assertEqual(workflow.gate_reason(gate), 'dynaflows must be {"enabled": true|false}')
        policy = workflow.gate_policy(valid[2])
        self.assertEqual(policy, {"pathCheck": None, "format": ["ruff", "format"], "ci": {"requiredContext": "gate", "runner": "ci-gate"}})

    def test_legacy_record_fields_are_accepted_and_ignored(self):
        for legacy in ({"dynaflowsContract": 3}, {"dynaflowsContract": 0}, {"dynaflowsContract": "2"}, {"dynaflowsContract": None},
                       {"pathCheckTimeoutMs": 0}, {"pathCheckTimeoutMs": 1.5}, {"pathCheckTimeoutMs": 9007199254740992}):
            with self.subTest(legacy=legacy):
                gate = {"schemaVersion": 2, "pathCheck": ["true"], **SWITCH, **legacy}
                self.assertIsNone(workflow.gate_reason(gate))
                self.assertEqual(workflow.gate_policy(gate), {"pathCheck": ["true"], "format": None, "ci": None})

    def test_retired_ci_host_is_rejected_with_migration_message(self):
        for value in ("github", "self-hosted", None):
            gate = {"schemaVersion": 2, "ciHost": value, "ci": {"requiredContext": "gate", "runner": "ci-gate"}}
            with self.subTest(value=value):
                self.assertIsNone(workflow.gate_policy(gate))
                self.assertEqual(workflow.gate_reason(gate), "ciHost is retired; declare ci.requiredContext and ci.runner")

    def test_journey_flag_must_be_boolean(self):
        for value in (True, False):
            data = copy.deepcopy(self.data)
            data["proofs"][0]["journey"] = value
            self.assertEqual(self.load(data), data)
        data = copy.deepcopy(self.data)
        data["proofs"][0]["journey"] = "yes"
        with self.assertRaisesRegex(workflow.WorkflowError, "journey must be a boolean"):
            self.load(data)

    def test_harness_root_survives_a_later_kernel_load(self):
        loaded = {"__name__": "kernel"}
        exec(compile(MODULE.read_text(), "<cell>", "exec"), loaded)
        with self.assertRaisesRegex(loaded["WorkflowError"], "without __file__"):
            loaded["_harness_root"]()
        # The eval kernel's %load sets __file__ in the shared namespace; a later %load replaces it.
        kernel = {"__name__": "kernel", "__file__": str(MODULE)}
        exec(compile(MODULE.read_text(), str(MODULE), "exec"), kernel)
        kernel["__file__"] = "/elsewhere/other.py"
        self.assertEqual(kernel["_harness_root"](), MODULE.resolve().parents[4])

    def test_workflow_code_holds_no_model_ids_or_hosts(self):
        text = MODULE.read_text()
        for pattern in (r"gpt-\d", r"claude-", r"sonnet", r"opus", r"openai-codex", r"\bnuc\b", r"omarchy", r"/Users/"):
            self.assertIsNone(re.search(pattern, text, re.IGNORECASE), pattern)

    def test_load_safe_no_bang_lines(self):
        self.assertIsNone(re.search(r"^\s*!", MODULE.read_text(), re.MULTILINE))


class LabelTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.path = Path(scratch.name) / "batch.json"
        self.path.write_text(json.dumps({"cases": [{"id": "a"}, {"id": "b"}]}))
        self.spawn = patch.object(workflow, "agent", create=True, return_value="job")
        self.agent = self.spawn.start()
        self.addCleanup(self.spawn.stop)
        self.wait_patch = patch.object(workflow, "wait", create=True)
        self.wait = self.wait_patch.start()
        self.addCleanup(self.wait_patch.stop)

    def answer(self, ident, label="yes"):
        return {"id": ident, "label": label, "evidence": "quote", "rationale": "reason"}

    def label(self, results):
        self.wait.return_value = results
        result = workflow.blind_label([self.path], "rubric", ["yes", "no"])
        json.dumps(result)
        self.assertEqual(sorted(r["id"] for r in result["agreed"] + result["disputed"]), ["a", "b"])
        return result

    def test_readonly_selectors_and_json_lists(self):
        answer = {"cases": [self.answer("a"), self.answer("b")]}
        result = self.label([answer, answer])
        self.assertEqual(len(result["agreed"]), 2)
        self.assertEqual(result["failed"], [])
        self.assertEqual({c.kwargs["agent"] for c in self.agent.call_args_list}, {"scout", "reviewer"})
        self.assertIn("Read only those two files", self.agent.call_args.args[0])

    def test_disputes_missing_duplicates_and_unknown(self):
        first = {"cases": [self.answer("a"), self.answer("b")]}
        for second in ({"cases": [self.answer("a", "no")]}, {"cases": [self.answer("a"), self.answer("a"), self.answer("b")]},
                       {"cases": [self.answer("unknown")]}, None, RuntimeError("failed"), {"cases": [self.answer("a", "invalid")]}):
            result = self.label([first, second])
            self.assertTrue(result["disputed"])
            if not isinstance(second, dict) or second.get("cases", [{}])[0].get("id") == "unknown":
                self.assertTrue(result["failed"])

    def test_inputs_validated_before_spawn(self):
        for cases in (None, [{"id": ""}], [{"id": "a"}, {"id": "a"}], [{"id": 1}]):
            self.path.write_text(json.dumps(cases))
            with self.assertRaises(workflow.WorkflowError):
                workflow.blind_label([self.path], "rubric", ["yes"])
        for selectors in (("scout", "task"), ("scout", "scout"), ("verifier", "reviewer")):
            with self.assertRaises(workflow.WorkflowError):
                workflow.blind_label([], "rubric", ["yes"], selectors)
        with self.assertRaises(workflow.WorkflowError):
            workflow.blind_label([self.path] * 9, "rubric", ["yes"])
        self.agent.assert_not_called()


if __name__ == "__main__":
    unittest.main()
