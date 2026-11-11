"""Consumer boundaries for frozen proofs, isolated executions and verifier ownership."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

MODULE = Path(__file__).resolve().parents[1] / "agent/skills/omp-dyna-workflows/templates/workflows.py"
spec = importlib.util.spec_from_file_location("workflows", MODULE)
workflow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workflow)
RAILS = Path(__file__).resolve().parents[1] / "agent/agent-rails.yml"
CONTRACT = workflow.dynaflows_contract()


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def verdict(ids=("P1",)):
    return {"status": "pass", "blocking": [], "followUps": [], "criteria": [
        {"proofId": ident, "command": "python proof.py", "observed": "green", "verdict": "pass"} for ident in ids]}


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
        self.write(".omp/gate.json", json.dumps({"schemaVersion": 2, "dynaflowsContract": CONTRACT, "checks": {"commit": ["false"], "ci": ["false"]}, "pathCheck": ["true"]}))
        self.write("value", "old")
        self.commit("base")
        self.parent = git(self.repo, "rev-parse", "HEAD")
        self.manifest_path = workflow._common(self.repo) / "omp-manifests" / "example.json"
        self.manifest_path.parent.mkdir(parents=True)
        self.verifier_patch = patch.object(workflow, "_run_verifier", return_value=verdict())
        self.verifier = self.verifier_patch.start()
        self.addCleanup(self.verifier_patch.stop)
        self.post_patch = patch.object(workflow, "_post_agent_verified")
        self.post = self.post_patch.start()
        self.addCleanup(self.post_patch.stop)
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

    def commit(self, message):
        git(self.repo, "add", ".")
        git(self.repo, "commit", "--allow-empty", "-qm", message)
        return git(self.repo, "rev-parse", "HEAD")

    def assert_cleaned(self):
        self.assertEqual(git(self.repo, "worktree", "list", "--porcelain").count("worktree "), 1)

    def proof(self, kind="defect", filename="proof.py", code=None, ident="P1"):
        self.write(filename, code if code is not None else "from pathlib import Path\nassert Path('value').read_text() == 'fixed', 'expected fixed'\n")
        return {"id": ident, "criterion": "value is fixed", "kind": kind, "cmd": [sys.executable, filename], "files": [filename],
                "expectRed": "expected fixed" if kind == "defect" else None}

    def ledger(self):
        common = workflow._common(self.repo)
        branch = git(self.repo, "symbolic-ref", "HEAD")
        root = str(self.repo.resolve())
        key = hashlib.sha256((root + "\0" + branch).encode()).hexdigest()
        path = common / "omp-authors" / f"{key}.json"
        workflow._atomic_json(path, {"schemaVersion": 1, "worktree": root, "branch": branch,
                                   "authors": [{"family": "openai", "model": "recorded-model", "agent": "writer"}]})
        return path

    def change(self, kind="defect", code=None, proofs=None):
        proofs = proofs or [self.proof(kind, code=code)]
        self.write("value", "fixed")
        self.head = self.commit("change")
        data = {"schemaVersion": 1, "slug": "example", "base": self.parent, "proofs": [
            dict(p, parent=p.get("parent", self.parent), sha256={name: workflow._sha(self.repo / name) for name in p["files"]}) for p in proofs]}
        self.save(data)
        self.ledger()
        self.verifier.return_value = verdict([p["id"] for p in proofs])
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

    def assert_block(self, result, fragment):
        self.assertEqual(result["status"], "fail", result)
        self.assertIn(fragment, "\n".join(result["blocking"]))
        self.assertFalse((workflow._common(self.repo) / "omp-prove" / f"{self.head}.json").exists())

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

    def test_changed_objective_after_failed_prove_requires_revision(self):
        self.change()
        self.verifier.return_value = dict(verdict(), status="fail", blocking=["value still wrong"])
        self.assert_block(self.prove(), "value still wrong")
        self.verifier.return_value = verdict()
        self.verifier.reset_mock()
        result = self.prove(objective="fix only the documented value")
        self.assert_block(result, f"objective changed after a failed prove of {self.head}; pass objective_revision=<reason>")
        self.verifier.assert_not_called()

    def test_objective_revision_is_recorded_and_briefed_after_failure(self):
        self.change()
        digest = workflow._sha(self.manifest_path)
        self.verifier.return_value = dict(verdict(), status="fail", blocking=["scope ambiguous"])
        self.assert_block(self.prove(), "scope ambiguous")
        self.verifier.return_value = verdict()
        reason = "owner clarified the required value"
        objective = "fix the documented value"
        result = self.prove(objective=objective, objective_revision=reason)
        self.assertEqual(result["status"], "pass", result)
        self.assertEqual(result["objectiveRevision"], {"previous": "fix value", "reason": reason})
        brief = self.verifier.call_args.args[0]
        self.assertIn("fix value", brief)
        self.assertIn(reason, brief)
        attempts = workflow._common(self.repo) / "omp-prove" / "attempts" / f"{self.head}.json"
        self.assertEqual(json.loads(attempts.read_text()), [
            {"objective": "fix value", "manifestSha256": digest, "status": "fail"},
            {"objective": objective, "manifestSha256": digest, "status": "pass"},
        ])

    def test_same_objective_after_failure_needs_no_revision(self):
        self.change()
        self.verifier.return_value = dict(verdict(), status="fail", blocking=["value still wrong"])
        self.assert_block(self.prove(), "value still wrong")
        self.verifier.return_value = verdict()
        result = self.prove()
        self.assertEqual(result["status"], "pass", result)
        self.assertNotIn("objectiveRevision", result)
        attempts = workflow._common(self.repo) / "omp-prove" / "attempts" / f"{self.head}.json"
        self.assertTrue(attempts.is_file(), "completed prove attempts must be recorded per head")
        self.assertEqual([item["status"] for item in json.loads(attempts.read_text())], ["fail", "pass"])

    def test_objective_revision_without_failed_differing_objective_is_rejected(self):
        self.change()
        for prior in ("none", "pass", "fail"):
            with self.subTest(prior=prior):
                if prior != "none":
                    self.verifier.return_value = verdict() if prior == "pass" else dict(
                        verdict(), status="fail", blocking=["value still wrong"])
                    objective = "previous successful objective" if prior == "pass" else "fix value"
                    self.assertEqual(self.prove(objective=objective)["status"], prior)
                self.verifier.reset_mock()
                result = self.prove(objective_revision="unsolicited scope change")
                self.assertEqual(result["status"], "fail", result)
                self.assertTrue(result["blocking"], result)
                self.assertIn("objective_revision", "\n".join(result["blocking"]))
                self.assertFalse((workflow._common(self.repo) / "omp-prove" / f"{self.head}.json").exists())
                self.verifier.assert_not_called()

    def test_objective_attempt_history_is_per_head(self):
        self.change()
        failed_head = self.head
        self.verifier.return_value = dict(verdict(), status="fail", blocking=["value still wrong"])
        self.assert_block(self.prove(), "value still wrong")
        self.head = self.commit("new implementation head")
        self.verifier.return_value = verdict()
        result = self.prove(objective="fix the clarified value")
        self.assertEqual(result["status"], "pass", result)
        self.assertNotIn("objectiveRevision", result)
        attempts = workflow._common(self.repo) / "omp-prove" / "attempts"
        self.assertTrue((attempts / f"{failed_head}.json").is_file(), "failed head attempt history must survive a new head")
        self.assertEqual(json.loads((attempts / f"{failed_head}.json").read_text()), [
            {"objective": "fix value", "manifestSha256": workflow._sha(self.manifest_path), "status": "fail"}])
        self.assertEqual(json.loads((attempts / f"{self.head}.json").read_text()), [
            {"objective": "fix the clarified value", "manifestSha256": workflow._sha(self.manifest_path), "status": "pass"}])

    def test_valid_kinds(self):
        for kind, code in (("defect", None), ("new-surface", None), ("preserve", "assert True\n")):
            with self.subTest(kind=kind):
                self.change(kind, code)
                self.assertEqual(self.prove()["status"], "pass")

    def test_baseline_failures(self):
        for kind, code, reason in (("defect", "assert True\n", "tautology"), ("new-surface", "assert True\n", "tautology"),
                                  ("defect", "from pathlib import Path\nassert Path('value').read_text() == 'fixed', 'other reason'", "wrong-reason red"),
                                  ("preserve", None, "preserve proof fails")):
            with self.subTest(kind=kind, reason=reason):
                self.change(kind, code)
                self.assert_block(self.prove(), reason)

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
        frozen_trees = counter.read_text().splitlines()
        self.assertEqual(len(frozen_trees), 1)  # Baseline and negative control share setup.
        data = self.change(proofs=proofs)
        data["setup"] = setup
        self.save(data)
        self.assertEqual(self.prove()["status"], "pass")
        trees = counter.read_text().splitlines()
        self.assertEqual(len(trees), 3)  # Two head runs and two parent runs use two trees.
        self.assertEqual(len(set(trees)), 3)

    def test_earlier_proofs_share_setup_and_remain_isolated(self):
        counter = self.tmp / "setup-count"
        setup = [sys.executable, "-c",
                 "from pathlib import Path\nimport subprocess\n"
                 "commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()\n"
                 f"with Path({str(counter)!r}).open('a') as out: out.write(commit + '\\n')\n"]
        code = ("from pathlib import Path\n"
                "assert not Path('residue').exists()\n"
                "Path('residue').write_text('run')\n")
        proofs = [self.proof("preserve", filename=f"proof{n}.py", ident=f"P{n}", code=code) for n in (1, 2)]
        earlier = self.commit("introduce both proofs")
        data = self.change(proofs=proofs)
        data["setup"] = setup
        self.save(data)
        result = self.prove()
        self.assertEqual(result["status"], "pass", result)
        self.assertCountEqual(counter.read_text().splitlines(), [self.parent, self.head, earlier])
        for proof in proofs:
            self.assertEqual(result["deterministic"]["proofs"][proof["id"]][-1]["location"],
                             f"earlier commit {earlier}")
        brief = self.verifier.call_args.args[0]
        labels = json.loads(re.search(r"Read-only scratch trees: (.*); your TMPDIR", brief).group(1))
        earlier_labels = [label for label in labels if label["sha"] == earlier]
        self.assertEqual({label["label"] for label in earlier_labels},
                         {"earlier commit", "earlier P1", "earlier P2"})
        self.assertEqual(len({label["path"] for label in earlier_labels}), 1)

    def test_earlier_commit_reuses_existing_parent_and_baseline_setup(self):
        counter = self.tmp / "setup-count"
        setup = [sys.executable, "-c",
                 "from pathlib import Path\nimport subprocess\n"
                 "commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()\n"
                 f"with Path({str(counter)!r}).open('a') as out: out.write(commit + '\\n')\n"]
        proofs = [self.proof("preserve", filename=f"proof{n}.py", ident=f"P{n}", code="assert True\n")
                  for n in (1, 2)]
        baseline = self.commit("introduce proofs")
        proofs.append(self.proof("preserve", filename="proof3.py", ident="P3", code="assert True\n"))
        proofs[-1]["parent"] = baseline
        proofs[0]["parent"] = git(self.repo, "rev-parse", f"{baseline}^")
        self.write("proof1.py", "assert 1 == 1\n")
        self.parent = self.commit("update first proof at PR parent")
        data = self.change(proofs=proofs)
        data["base"] = proofs[0]["parent"]
        data["setup"] = setup
        self.save(data)
        result = self.prove()
        self.assertEqual(result["status"], "pass", result)
        self.assertCountEqual(counter.read_text().splitlines(),
                              [proofs[0]["parent"], baseline, self.parent, self.head])
        locations = [run["location"] for run in result["deterministic"]["proofs"]["P1"]]
        self.assertIn(f"earlier commit {baseline}", locations)
        self.assertIn(f"earlier commit {self.parent}", locations)

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
        data = self.change("preserve", code)
        data["setup"] = [sys.executable, "-c",
                         "from pathlib import Path\n"
                         "for name in ('ready', 'ignored-ready'):\n"
                         "    Path(name).mkdir()\n"
                         "    Path(name, 'output').write_text('setup')\n"]
        self.save(data)
        result = self.prove()
        self.assertEqual(result["status"], "pass")
        self.assertEqual([run["output"].strip() for run in result["deterministic"]["proofs"]["P1"]],
                         ["False", "True", "False"])

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
        self.verifier.assert_not_called()

    def test_execution_cleanup_failure_prevents_pass(self):
        self.change("preserve", "assert True\n")
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
        self.verifier.assert_not_called()

    def test_setup_mutation_is_infrastructure(self):
        data = self.change()
        data["setup"] = [sys.executable, "-c", "from pathlib import Path; Path('value').write_text('bad')"]
        self.save(data)
        result = self.prove()
        self.assert_block(result, "setup changed tracked files")
        self.assertIsNone(result["deterministic"]["proofs"]["P1"][0]["ok"])
        self.verifier.assert_not_called()

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
        self.verifier.assert_not_called()

    def test_replacement_decode_and_resolved_cwd_scrubbing(self):
        self.change("preserve", "import os\nfrom pathlib import Path\nos.write(1,b'\\xff')\nprint(Path.cwd().resolve())\n")
        result = self.prove()
        self.assertEqual(result["status"], "pass")
        output = result["deterministic"]["proofs"]["P1"][0]["output"]
        self.assertIn("\ufffd", output)
        self.assertIn("<tree>", output)
        self.assertNotIn("omp-prove-", output)

    def test_overlay_does_not_leak_to_preserve(self):
        p1 = self.proof("new-surface", ident="P1")
        p2 = self.proof("preserve", "second.py", "from pathlib import Path\nassert not Path('proof.py').exists() or Path('value').read_text() == 'fixed'\n", "P2")
        self.change(proofs=[p1, p2])
        self.assertEqual(self.prove()["status"], "pass")

    def test_head_tracked_mutation_blocks(self):
        self.change("preserve", "from pathlib import Path\nPath('value').write_text('mutated')\n")
        self.assert_block(self.prove(), "modified tracked files")

    def test_hash_and_missing_file_blocks(self):
        data = self.change()
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
            workflow._write_pass_marker(self.repo, self.head, "prove")
            result = workflow.prove(self.repo, kwargs.get("parent", self.parent), self.head, self.manifest_path,
                                    kwargs.get("digest", workflow._sha(self.manifest_path)), "fix", target_ref=kwargs.get("target_ref", self.parent))
            self.assertEqual(result["status"], "fail")
            self.assertFalse((workflow._common(self.repo) / "omp-prove" / f"{self.head}.json").exists())

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

    def test_no_ledger_is_not_independence(self):
        self.change()
        self.ledger().unlink()
        self.assert_block(self.prove(), "authorship unknown")
        self.verifier.assert_not_called()

    def bot_github(self, *, author="app/dependabot", branch="dependabot/bun/js-minor-8e002485ce", verified=True, commit_author="dependabot[bot]", bad_commit=None):
        git(self.repo, "remote", "add", "origin", "git@github.com:example-owner/example-repo.git")
        def response(repo, *args):
            self.assertEqual(repo, self.repo.resolve())
            if args[:2] == ("pr", "list"):
                return [{"author": {"is_bot": True, "login": author}, "headRefName": branch, "headRefOid": self.head}]
            self.assertEqual(args[0], "api")
            self.assertTrue(args[1].startswith("repos/example-owner/example-repo/commits/"))
            trusted = bad_commit is not None and not args[1].endswith("/" + bad_commit)
            return {"author": {"login": "dependabot[bot]" if trusted else commit_author},
                    "commit": {"verification": {"verified": True if trusted else verified}}}
        return response

    def test_dependabot_missing_ledger_all_commits_external(self):
        self.change()
        self.ledger().unlink()
        first = self.head
        self.head = self.commit("second bot commit")
        with patch.object(workflow, "_github", side_effect=self.bot_github()) as github:
            result = self.prove()
        self.assertEqual(result["status"], "pass", result)
        self.assertEqual(result["authors"], [{"family": "external", "model": "dependabot[bot]", "agent": "dependabot[bot]"}])
        self.assertEqual({call.args[2].rsplit("/", 1)[-1] for call in github.call_args_list if call.args[1] == "api"}, {first, self.head})
        self.verifier.assert_called_once()

    def test_dependabot_missing_ledger_rejects_untrusted_metadata(self):
        self.change()
        self.ledger().unlink()
        first = self.head
        self.head = self.commit("second verified bot commit")
        cases = [({"verified": False}, "not verified"), ({"commit_author": "human"}, "author is not dependabot"),
                 ({"author": "human"}, "PR author is not Dependabot"), ({"branch": "agent/bump"}, "not dependabot/")]
        for values, reason in cases:
            with self.subTest(values=values):
                response = self.bot_github(**values, bad_commit=first)
                with patch.object(workflow, "_github", side_effect=response):
                    self.assert_block(self.prove(), reason)
                git(self.repo, "remote", "remove", "origin")
        self.verifier.assert_not_called()

    def test_dependabot_gh_failure_fails_closed(self):
        self.change()
        self.ledger().unlink()
        self.bot_github()
        with patch.object(workflow, "_github", side_effect=workflow.WorkflowError("gh failed: unavailable")):
            self.assert_block(self.prove(), "authorship unknown: gh failed: unavailable")
        self.verifier.assert_not_called()

    def test_existing_ledger_never_queries_dependabot(self):
        self.change()
        with patch.object(workflow, "_github", side_effect=AssertionError("must not query gh")):
            self.assertEqual(self.prove()["authors"][0]["family"], "openai")

    def test_context_authors_model_and_cleanup(self):
        self.change()
        context = workflow._common(self.repo) / "omp-prove/verifier-context.json"
        def inspect(brief, proof_ids, **kwargs):
            payload = json.loads(context.read_text())
            self.assertEqual(set(payload), {"schemaVersion", "worktree", "branch", "head", "parent"})
            self.assertEqual(payload["parent"], self.parent)
            self.assertEqual(payload["head"], self.head)
            self.assertEqual(payload["worktree"], str(self.repo.resolve()))
            self.assertEqual(kwargs, {"model": "provider/example-verifier"})
            self.assertEqual(proof_ids, ["P1"])
            for fragment in ("Source repo", "PR target/ref", self.parent + ".." + self.head, "baseline P1", "Objective: fix value", "recorded-model", "skill://git-tool", workflow.BLOCKING_RULE):
                self.assertIn(fragment, brief)
            return verdict()
        self.verifier.side_effect = inspect
        result = self.prove(verifier_model="provider/example-verifier")
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["authors"][0]["family"], "openai")
        self.assertFalse(context.exists())

    def test_verifier_brief_names_manifest_proof_ids(self):
        self.change(proofs=[self.proof(ident="P2", filename="second.py"), self.proof()])
        self.assertEqual(self.prove()["status"], "pass")
        brief, proof_ids = self.verifier.call_args.args
        self.assertEqual(proof_ids, ["P1", "P2"])
        self.assertIn("Each criteria entry's proofId must be one of: P1, P2; "
                      "put any other observation in blocking or followUps.", brief)

    def test_source_change_is_not_attributed_to_verifier(self):
        self.change()
        def external_change(brief, proof_ids):
            self.write("external", "changed")
            return verdict()
        self.verifier.side_effect = external_change
        self.assert_block(self.prove(), "source checkout changed during verification")

    def test_verifier_evidence_validation_and_json_safety(self):
        self.change()
        invalid = [None, "rail reason", LookupError("unavailable model"), {"status": "pass"}]
        for key in ("blocking", "followUps"):
            value = verdict()
            value[key] = [object()]
            invalid.append(value)
        for key, value in (("proofId", " "), ("command", 2), ("observed", ""), ("verdict", "PASS")):
            v = verdict()
            v["criteria"][0][key] = value
            invalid.append(v)
        v = verdict()
        v["extra"] = True
        invalid.append(v)
        for value in invalid:
            with self.subTest(value=repr(value)):
                self.verifier.return_value = value
                self.assert_block(self.prove(), "invalid or failed result")
        self.verifier.return_value = verdict(("unknown",))
        self.assert_block(self.prove(), "unknown proof ID")
        self.verifier.return_value = verdict(("P1", "unknown"))
        self.assert_block(self.prove(), "unknown proof ID")
        self.verifier.return_value = dict(verdict(), status="fail")
        self.assert_block(self.prove(), "without reason")
        self.verifier.return_value = dict(verdict(), blocking=["real reason"])
        self.assert_block(self.prove(), "real reason")

    def test_duplicate_known_criteria_and_followups(self):
        self.change()
        value = verdict()
        value["criteria"] *= 2
        value["followUps"] = ["parked"]
        self.verifier.return_value = value
        result = self.prove()
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["followUps"], ["parked"])
        value["criteria"][1] = dict(value["criteria"][1], verdict="fail")
        self.assert_block(self.prove(), "criterion failed")

    def test_long_output_has_live_full_evidence(self):
        self.change("preserve", "print('x'*6000)\n")
        def inspect(brief, proof_ids):
            self.assertIn("[TRUNCATED; full evidence:", brief)
            path = re.search(r"Full deterministic evidence: (.+)", brief).group(1)
            self.assertIn("x" * 6000, Path(path).read_text())
            return verdict()
        self.verifier.side_effect = inspect
        self.assertEqual(self.prove()["status"], "pass")

    def test_invalid_head_gate_precedes_tree_creation_and_setup(self):
        for text in ("null", "{", json.dumps({"schemaVersion": 3}), json.dumps({"schemaVersion": 2, "dynaflowsContract": CONTRACT, "pathCheck": ["true"]}), json.dumps({"schemaVersion": 2, "dynaflowsContract": CONTRACT, "checks": {"commit": ["true"]}})):
            self.write(".omp/gate.json", text)
            self.change()
            with patch.object(workflow, "_setup", side_effect=AssertionError("setup must not run")):
                self.assertEqual(self.prove()["status"], "fail")
        self.verifier.assert_not_called()

    def test_invalid_parent_gate_repair_and_literal_discovery(self):
        self.write(".omp/gate.json", "invalid json")
        self.write("runner", "ruff.toml pytest.ini ../outside /etc/hosts link\n")
        self.write("ruff.toml", "old")
        self.write("pytest.ini", "old")
        self.commit("broken parent")
        self.parent = git(self.repo, "rev-parse", "HEAD")
        self.write(".omp/gate.json", json.dumps({"schemaVersion": 2, "dynaflowsContract": CONTRACT, "checks": {"commit": ["runner", "apostrophe' with spaces"]}, "pathCheck": ["true"]}))
        self.write("ruff.toml", "changed")
        self.write("pytest.ini", "changed")
        self.change()
        result = self.prove()
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["deterministic"]["gateChanges"], [".omp/gate.json", "pytest.ini", "ruff.toml"])
        self.assertTrue(result["deterministic"]["gateDiagnostics"])

    def test_cleanup_failure_keeps_evidence_attempts_every_tree(self):
        self.change()
        original = workflow._git
        removed = []
        def fail_remove(repo, *args):
            if args[:2] == ("worktree", "remove"):
                removed.append(args[-1])
                if len(removed) == 1:
                    raise WorkflowErrorForTest("injected removal failure")
            return original(repo, *args)
        with patch.object(workflow, "_git", side_effect=fail_remove):
            result = self.prove_without_cleanup_assert()
        # TemporaryDirectory has removed test-owned scratch bytes; prune repairs its metadata.
        git(self.repo, "worktree", "prune")
        self.assertGreaterEqual(len(removed), 2)
        self.assertTrue(result["deterministic"]["proofs"])
        self.assert_block(result, "cleanup infrastructure")

    def prove_without_cleanup_assert(self):
        return workflow.prove(self.repo, self.parent, self.head, self.manifest_path, workflow._sha(self.manifest_path), "fix", target_ref=self.parent)

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

    def test_freeze_review_added_parent_and_metadata_validation(self):
        proof = self.proof()
        self.write("value", "fixed")
        self.commit("defective implementation")
        with self.assertRaisesRegex(workflow.WorkflowError, "defective implementation commit"):
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
        proofs = [self.proof("preserve", f"proof{i}.py", "assert True\n", f"P{i}") for i in range(17)]
        self.freeze(proofs, max_checks=17)
        with self.assertRaises(workflow.WorkflowError):
            self.freeze(proofs)
        self.change(proofs=proofs)
        self.assertEqual(self.prove(max_checks=17)["status"], "pass")
        self.assert_block(self.prove(), "proof count")
        self.assert_block(self.prove(max_checks=1), "proof count")
        for cap in (0, -1, True, 1.5, "2"):
            with self.assertRaises(workflow.WorkflowError):
                self.freeze(proofs, max_checks=cap)
            self.assert_block(self.prove(max_checks=cap), "positive integer")

    def test_earlier_commit_uses_own_proof_bytes_and_detects_mutation(self):
        proof = self.proof("preserve", code="from pathlib import Path\nPath('value').write_text('mutated')\n")
        self.commit("early mutating proof")
        self.change("preserve", "assert True\n")
        self.assert_block(self.prove(), "earlier commit")

    def test_docs_allowlist_and_whole_range(self):
        self.write("README.MD", "docs")
        head = self.commit("documentation")
        marker = workflow.exempt_docs_only(self.repo, self.parent, head, target_ref=self.parent)
        self.assertTrue(marker.exists())
        self.write("runtime.py", "x=1")
        runtime = self.commit("runtime")
        self.write("docs/guide.md", "docs")
        head = self.commit("docs last")
        for parent in (runtime, self.parent):
            with self.assertRaises(workflow.WorkflowError):
                workflow.exempt_docs_only(self.repo, parent, head, target_ref=self.parent)
            self.assertFalse((workflow._common(self.repo) / "omp-prove" / f"{head}.json").exists())

    def test_docs_paths_casefold_and_rename_sides(self):
        for name in ("requirements.txt", "app.png", "docs/Skills/example.md", "docs/CODEX.md", "docs/GEMINI.md", "docs/AGENTS.override.md", "docs/custominstructions.md", "docs/.github/guide.md"):
            self.write(name, "text")
            head = self.commit("ineligible")
            parent = git(self.repo, "rev-parse", "HEAD~1")
            with self.subTest(name=name), self.assertRaises(workflow.WorkflowError):
                workflow.exempt_docs_only(self.repo, parent, head, target_ref=parent)
        self.write("docs/picture.PNG", "image")
        parent = git(self.repo, "rev-parse", "HEAD")
        head = self.commit("image docs")
        self.assertTrue(workflow.exempt_docs_only(self.repo, parent, head, target_ref=parent).exists())
        (self.repo / "docs/picture.PNG").rename(self.repo / "application.png")
        head2 = self.commit("rename out")
        with self.assertRaises(workflow.WorkflowError):
            workflow.exempt_docs_only(self.repo, head, head2, target_ref=head)

    def test_draft_validation_precedes_hashing_and_commands(self):
        proof = self.proof()
        for invalid in ([dict(proof, files=[["not a path"]])], [dict(proof, cmd="shell")], [proof, dict(proof)]):
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
        self.verifier.assert_not_called()

    def test_removed_proof_requires_revision_and_retains_entire_old_digest(self):
        first = self.proof("preserve", code="assert True\n")
        second = self.proof("preserve", "second.py", "assert True\n", "P2")
        previous = self.freeze([first, second])
        old_second = json.loads(previous[0].read_text())["proofs"][1]
        self.commit("green preserved proofs")
        with self.assertRaisesRegex(workflow.WorkflowError, "silent proof change"):
            self.freeze([first], previous=previous)
        self.freeze([first], previous=previous, revise={"P2": "remove duplicate criterion"})
        revision = json.loads(self.manifest_path.read_text())["revisions"][-1]
        self.assertEqual(revision["oldSha256"], hashlib.sha256(json.dumps(old_second, sort_keys=True).encode()).hexdigest())

    def test_review_added_regression_uses_explicit_defective_parent(self):
        initial = self.proof("preserve", code="assert True\n")
        previous = self.freeze([initial])
        defective = self.commit("implementation missing review behavior")
        review = self.proof("defect", "review.py", "from pathlib import Path\nassert Path('value').read_text() == 'reviewed', 'review regression'\n", "P2")
        review["expectRed"] = "review regression"
        review["parent"] = defective
        self.freeze([initial, review], previous=previous)
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

    def test_parent_only_gate_reference_and_outside_files(self):
        self.write("runner", "pytest.ini ../outside /etc/hosts escaped\n")
        self.write("pytest.ini", "configuration")
        self.write(".omp/gate.json", json.dumps({"schemaVersion": 2, "dynaflowsContract": CONTRACT, "checks": {"commit": ["runner"]}, "pathCheck": ["true"]}))
        self.commit("parent runner")
        self.parent = git(self.repo, "rev-parse", "HEAD")
        self.write(".omp/gate.json", json.dumps({"schemaVersion": 2, "dynaflowsContract": CONTRACT, "checks": {"commit": ["true"]}, "pathCheck": ["true"]}))
        (self.repo / "pytest.ini").unlink()
        self.change()
        result = self.prove()
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["deterministic"]["gateChanges"], [".omp/gate.json", "pytest.ini"])
        external = self.tmp / "outside"
        external.write_text("never read")
        (self.repo / "escaped").symlink_to(external)
        named = workflow.gate_changes(["escaped", "../outside", "/etc/hosts"], self.repo,
                                      {"cmd": ["escaped", "../outside", "/etc/hosts"]})
        self.assertEqual(named, [])

    def test_early_red_proof_uses_own_bytes_not_head_overlay(self):
        self.proof("preserve", code="assert False, 'early red'\n")
        self.commit("early red proof")
        self.change("preserve", "assert True\n")
        result = self.prove()
        self.assert_block(result, "proof red at earlier commit")
        self.assertIn("early red", result["deterministic"]["proofs"]["P1"][-1]["output"])

    def test_verifier_spawn_error_and_context_cleanup_failure_block(self):
        self.change()
        self.verifier.side_effect = RuntimeError("matching family unavailable")
        self.assert_block(self.prove(), "matching family unavailable")
        context = workflow._common(self.repo) / "omp-prove/verifier-context.json"
        self.assertFalse(context.exists())
        self.verifier.side_effect = None
        self.verifier.return_value = verdict()
        original = Path.unlink
        def fail_context(path, *args, **kwargs):
            if path == context:
                raise OSError("context removal failed")
            return original(path, *args, **kwargs)
        with patch.object(Path, "unlink", fail_context):
            self.assert_block(self.prove(), "context removal failed")
        context.unlink()

    def test_source_comparison_failure_is_concrete(self):
        self.change()
        original = workflow._git
        seen = 0
        def fail_second_status(repo, *args):
            nonlocal seen
            if args == ("status", "--porcelain=v2"):
                seen += 1
                if seen == 2:
                    raise OSError("source comparison failed")
            return original(repo, *args)
        with patch.object(workflow, "_git", side_effect=fail_second_status):
            self.assert_block(self.prove(), "source comparison failed")

    def test_atomic_marker_replacement_in_common_git_directory(self):
        linked = self.tmp / "linked"
        git(self.repo, "worktree", "add", "-qb", "linked", str(linked))
        try:
            head = git(linked, "rev-parse", "HEAD")
            original = os.replace
            observed = []
            def inspect(source, destination):
                observed.append(json.loads(Path(source).read_text()))
                self.assertFalse(Path(destination).exists())
                return original(source, destination)
            with patch.object(workflow.os, "replace", side_effect=inspect):
                marker = workflow._write_pass_marker(linked, head, "prove")
            self.assertEqual(marker.parent, workflow._common(self.repo) / "omp-prove")
            self.assertEqual(observed, [{"schemaVersion": 1, "head": head, "kind": "prove", "status": "pass"}])
        finally:
            git(self.repo, "worktree", "remove", "--force", str(linked))

    def gate(self, **checks):
        gate = {"schemaVersion": 2, "dynaflowsContract": CONTRACT, "checks": {"commit": ["false"], "ci": ["false"], **checks}, "pathCheck": ["true"],
                "ci": {"requiredContext": "gate", "runner": "ci-gate"}}
        self.write(".omp/gate.json", json.dumps(gate))

    def rebase_parent(self, message="parent fixtures"):
        self.parent = self.commit(message)

    def test_retired_ci_host_blocks_freeze_and_prove(self):
        retired = "ciHost is retired; declare ci.requiredContext and ci.runner"
        self.write(".omp/gate.json", json.dumps({"schemaVersion": 2, "dynaflowsContract": CONTRACT, "checks": {"commit": ["false"]}, "pathCheck": ["true"], "ciHost": "github"}))
        self.rebase_parent("retired gate")
        with self.assertRaisesRegex(workflow.WorkflowError, re.escape(retired)):
            self.freeze([self.proof()])
        self.assertFalse(self.manifest_path.exists())
        self.change()
        self.assert_block(self.prove(), retired)
        self.verifier.assert_not_called()

    def test_stale_absent_or_newer_contract_blocks_freeze_and_prove(self):
        stale = workflow.CONTRACT_REMEDY
        self.assertIn("omp-guardrails init --extend", stale)
        self.assertNotIn("project-setup", stale)
        newer = "The repo expects a newer dynaflows contract; update omp."
        cases = ((None, f"Dynaflows contract vnone recorded; current is v5. {stale}"),
                 (4, f"Dynaflows contract v4 recorded; current is v5. {stale}"),
                 (6, f"Dynaflows contract v6 recorded; current is v5. {newer}"))
        with patch.object(workflow, "dynaflows_contract", return_value=5):
            for recorded, text in cases:
                with self.subTest(recorded=recorded):
                    self.manifest_path.unlink(missing_ok=True)
                    gate = {"schemaVersion": 2, "checks": {"commit": ["false"], "ci": ["false"]}, "pathCheck": ["true"]}
                    if recorded is not None:
                        gate["dynaflowsContract"] = recorded
                    self.write(".omp/gate.json", json.dumps(gate))
                    self.rebase_parent(f"contract {recorded}")
                    with self.assertRaises(workflow.WorkflowError) as raised:
                        self.freeze([self.proof()])
                    self.assertEqual(str(raised.exception), text)
                    self.assertFalse(self.manifest_path.exists())
                    self.change()
                    result = self.prove()
                    self.assert_block(result, text)
                    self.assertIn(text, result["blocking"])
            self.verifier.assert_not_called()

    def test_freeze_without_gate_reports_missing_contract(self):
        (self.repo / ".omp/gate.json").unlink()
        self.rebase_parent("no gate")
        with self.assertRaisesRegex(workflow.WorkflowError, re.escape(f"Dynaflows contract vnone recorded; current is v{CONTRACT}.")):
            self.freeze([self.proof()])

    def test_project_setup_freeze_tolerates_absent_or_lower_contract_only(self):
        newer = "Dynaflows contract v6 recorded; current is v5. The repo expects a newer dynaflows contract; update omp."
        with patch.object(workflow, "dynaflows_contract", return_value=5):
            for recorded in (None, "no gate", 4, 6):
                with self.subTest(recorded=recorded):
                    self.manifest_path.unlink(missing_ok=True)
                    gate_path = self.repo / ".omp/gate.json"
                    if recorded == "no gate":
                        gate_path.unlink(missing_ok=True)
                    else:
                        gate = {"schemaVersion": 2, "checks": {"commit": ["false"], "ci": ["false"]}, "pathCheck": ["true"]}
                        if recorded is not None:
                            gate["dynaflowsContract"] = recorded
                        self.write(".omp/gate.json", json.dumps(gate))
                    self.rebase_parent(f"setup contract {recorded}")
                    if recorded == 6:
                        with self.assertRaises(workflow.WorkflowError) as raised:
                            self.freeze([self.proof()], project_setup=True)
                        self.assertEqual(str(raised.exception), newer)
                        self.assertFalse(self.manifest_path.exists())
                    else:
                        self.freeze([self.proof()], project_setup=True)
                        self.assertTrue(self.manifest_path.exists())
                        if recorded == 4:
                            self.change()
                            self.assert_block(self.prove(), "Dynaflows contract v4 recorded; current is v5.")
            with self.assertRaisesRegex(workflow.WorkflowError, "project_setup must be a bool"):
                self.freeze([self.proof()], project_setup="yes")

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

    def profile(self, marker):
        front = json.dumps({"verification_surface": {"lever_command": "lever", "doctor_marker": marker}})
        self.write(".omp/test-profile.md", f"---\n{front}\n---\n\n# Repo test profile\n")

    def test_freeze_refuses_red_from_environment_doctor(self):
        self.profile("DOCTOR:")
        self.rebase_parent()
        doctor = self.proof(code="from pathlib import Path\nprint('DOCTOR: database not reachable')\n"
                                 "assert Path('value').read_text() == 'fixed', 'expected fixed'\n")
        with self.assertRaisesRegex(workflow.WorkflowError, "P1: red came from the environment doctor, not the behaviour"):
            self.freeze([doctor])
        self.assertFalse(self.manifest_path.exists())
        static = dict(self.proof(), expectRed="DOCTOR: database not reachable")
        with self.assertRaisesRegex(workflow.WorkflowError, "expectRed matches the doctor marker"):
            self.freeze([static])
        path, _ = self.freeze([self.proof()])
        self.assertTrue(path.exists())

    def test_freeze_skips_doctor_check_for_placeholder_or_unparsed_profile(self):
        doctor = "from pathlib import Path\nprint('<doctor failure marker>')\nassert Path('value').read_text() == 'fixed', 'expected fixed'\n"
        for text in (None, "# prose-only profile\n"):
            with self.subTest(text=text):
                if text is None:
                    self.profile("<doctor failure marker>")
                else:
                    self.write(".omp/test-profile.md", text)
                self.rebase_parent(f"profile {text}")
                path, _ = self.freeze([self.proof(code=doctor)], base=self.parent)
                self.assertTrue(path.exists())
                path.unlink()
        self.write(".omp/test-profile.md", "---\n{not json\n---\n")
        self.rebase_parent("broken profile")
        with self.assertRaisesRegex(workflow.WorkflowError, "front matter is not valid JSON"):
            self.freeze([self.proof()], base=self.parent)

    def journey(self, counter, flaky):
        condition = "n == 3" if flaky else "Path('value').read_text() == 'fixed'"
        code = ("import sys\nfrom pathlib import Path\ncount = Path(sys.argv[1])\n"
                "n = int(count.read_text()) + 1 if count.exists() else 1\ncount.write_text(str(n))\n"
                f"assert {condition}, 'journey not reached'\n")
        self.write("journey.py", code)
        return {"id": "J1", "criterion": "journey completes", "kind": "new-surface", "cmd": [sys.executable, "journey.py", str(counter)],
                "files": ["journey.py"], "expectRed": None, "journey": True}

    def test_freeze_runs_journey_once_at_baseline(self):
        counter = self.tmp / "journey-count"
        path, _ = self.freeze([self.journey(counter, flaky=False)])
        self.assertEqual(counter.read_text(), "1", "freeze runs only the baseline; stability is a prove() check at head")
        self.assertTrue(json.loads(path.read_text())["proofs"][0]["journey"])

    def test_prove_runs_journey_three_times_at_head(self):
        counter = self.tmp / "journey-count"
        self.change(proofs=[self.journey(counter, flaky=False)])
        result = self.prove()
        self.assertEqual(result["status"], "pass", result)
        locations = [(run["location"], run.get("run")) for run in result["deterministic"]["proofs"]["J1"]]
        self.assertEqual(locations, [("head", 1), ("head", 2), ("head", 3), ("parent", None)])
        self.assertEqual(counter.read_text(), "4", "three head journey runs plus one parent run")

    def test_prove_blocks_journey_that_fails_any_head_run(self):
        counter = self.tmp / "journey-count"
        proof = self.journey(counter, flaky=False)
        self.write("journey.py", "import sys\nfrom pathlib import Path\ncount = Path(sys.argv[1])\n"
                   "n = int(count.read_text()) + 1 if count.exists() else 1\ncount.write_text(str(n))\n"
                   "assert n != 2, 'journey flaked'\n")
        self.change(proofs=[proof])
        result = self.prove()
        self.assert_block(result, "J1: head run 2 failed")
        self.assertIn("J1: journey proof passed 2 of 3 clean head runs; journeys must pass every run", result["blocking"])
        self.verifier.assert_not_called()

    def test_non_journey_proofs_keep_two_head_runs(self):
        self.change()
        runs = [run.get("run") for run in self.prove()["deterministic"]["proofs"]["P1"] if run["location"] == "head"]
        self.assertEqual(runs, [1, 2])

    def test_prove_runs_profile_check_first_and_blocks(self):
        self.gate(profile=[sys.executable, "-c", "import sys; print('PROFILE.complete: coverage missing'); sys.exit(1)"])
        self.change()
        with patch.object(workflow, "_setup", side_effect=AssertionError("trees must not be built")):
            result = self.prove()
        self.assert_block(result, "checks.profile failed; complete .omp/test-profile.md before prove:\nPROFILE.complete: coverage missing")
        self.assertFalse(any(item.startswith("infrastructure") for item in result["blocking"]), result)
        self.verifier.assert_not_called()
        self.post.assert_not_called()

    def test_prove_passes_after_profile_check_passes(self):
        self.gate(profile=[sys.executable, "-c", "print('profile ok')"])
        self.change()
        self.assertEqual(self.prove()["status"], "pass")

    def test_prove_posts_agent_verified_status_after_marker(self):
        self.change()
        marker = workflow._common(self.repo) / "omp-prove" / f"{self.head}.json"
        self.post.side_effect = lambda repo, head: self.assertTrue(marker.is_file())
        result = self.prove()
        self.assertEqual(result["status"], "pass", result)
        self.post.assert_called_once_with(self.repo.resolve(), self.head)
        self.post.reset_mock()
        self.verifier.return_value = dict(verdict(), status="fail", blocking=["wrong"])
        self.assert_block(self.prove(), "wrong")
        self.post.assert_not_called()

    def test_prove_status_post_failure_fails_and_removes_marker(self):
        self.change()
        self.post.side_effect = workflow.WorkflowError("gh failed: HTTP 403")
        result = self.prove()
        self.assert_block(result, "infrastructure: posting agent-verified status: gh failed: HTTP 403")

    def test_post_agent_verified_targets_origin_statuses_for_head(self):
        self.post_patch.stop()
        self.addCleanup(self.post_patch.start)
        git(self.repo, "remote", "add", "origin", "https://github.com/example-owner/example-repo.git")
        with patch.object(workflow, "_github", return_value={"state": "success"}) as github:
            workflow._post_agent_verified(self.repo, "a" * 40)
        github.assert_called_once_with(self.repo, "api", "--method", "POST", f"repos/example-owner/example-repo/statuses/{'a' * 40}",
                                       "-f", "state=success", "-f", "context=agent-verified", "-f", "description=prove() pass")
        git(self.repo, "remote", "set-url", "origin", "https://example.test/owner/repo.git")
        with patch.object(workflow, "_github", side_effect=AssertionError("must not post")), \
                self.assertRaisesRegex(workflow.WorkflowError, "not a validated GitHub repository"):
            workflow._post_agent_verified(self.repo, "a" * 40)

    def test_drift_precheck_briefs_docs_and_comments_naming_removed_paths_and_symbols(self):
        self.write("lib/old_mod.py", "def helper_func():\n    pass\n\n\ndef _private_helper():\n    pass\n")
        self.write("lib/renamed_from.py", "VALUE = 1\n")
        self.write("web/mod.ts", "export function oldThing(): void {}\nexport const kept = 1;\n")
        self.write("docs/guide.md", "Use lib/old_mod.py and helper_func.\nSee lib/renamed_from.py.\nCall oldThing.\nIgnore _private_helper.\n")
        self.write("app.py", "# see helper_func for details\nhelper_func_count = 1\n")
        self.write("use.py", "result = helper_func()\n")
        self.write("docs/adr/0001-record.md", "We added helper_func.\n")
        self.rebase_parent()
        git(self.repo, "rm", "-q", "lib/old_mod.py")
        git(self.repo, "mv", "lib/renamed_from.py", "lib/renamed_to.py")
        self.write("web/mod.ts", "export const kept = 1;\n")
        self.change()
        result = self.prove()
        self.assertEqual(result["status"], "pass", result)
        drift = result["drift"]
        joined = "\n".join(drift)
        for fragment in ("docs/guide.md:1: names deleted or renamed path `lib/old_mod.py`", "docs/guide.md:1: names removed symbol `helper_func`",
                         "docs/guide.md:2: names deleted or renamed path `lib/renamed_from.py`", "docs/guide.md:3: names removed symbol `oldThing`",
                         "app.py:1: names removed symbol `helper_func`"):
            self.assertIn(fragment, joined)
        for absent in ("docs/adr/", "use.py", "_private_helper", "app.py:2", "`kept`", "web/mod.ts"):
            self.assertNotIn(absent, joined)
        brief = self.verifier.call_args.args[0]
        self.assertIn("Address every Deterministic drift item", brief)
        self.assertIn("docs/guide.md:3: names removed symbol `oldThing`", brief)
        self.assertNotIn("Audit undeclared harness/config tampering, wrong/stale docs", brief)

    def test_drift_precheck_reports_symbols_removed_from_export_lists(self):
        self.write("web/one.ts", "function oldThing() { return 1; }\nexport { oldThing };\n")
        self.write("web/many.ts", "const a = 1, b = 2;\nexport {\n  a as oldAlias,\n  type b,\n};\n")
        self.write("web/kept.ts", "const k = 1;\nexport { k as keptName };\n")
        self.write("guide.md", "Import oldThing.\nUse oldAlias and b.\nUse keptName.\n")
        self.rebase_parent()
        self.write("web/one.ts", "export const other = 1;\n")
        self.write("web/many.ts", "export const c = 1;\n")
        self.write("web/kept.ts", "const k = 2;\nexport { k as keptName };\n")
        self.change()
        joined = "\n".join(self.prove()["drift"])
        for fragment in ("guide.md:1: names removed symbol `oldThing`", "guide.md:2: names removed symbol `oldAlias`",
                         "guide.md:2: names removed symbol `b`"):
            self.assertIn(fragment, joined)
        self.assertNotIn("keptName", joined)

    def test_drift_precheck_reports_every_binding_of_a_removed_variable_declaration(self):
        self.write("web/vars.ts", "export const first = 1, removed = pair(1, 2), typed: Map<string, number> = new Map();\n")
        self.write("guide.md", "Use removed.\nUse typed.\nUse first.\n")
        self.rebase_parent()
        self.write("web/vars.ts", "export const first = 1;\n")
        self.change()
        joined = "\n".join(self.prove()["drift"])
        self.assertIn("guide.md:1: names removed symbol `removed`", joined)
        self.assertIn("guide.md:2: names removed symbol `typed`", joined)
        self.assertNotIn("`first`", joined)

    def test_drift_precheck_reports_docs_the_same_change_also_edits(self):
        self.write("old.py", "def removed_export():\n    pass\n")
        self.write("guide.md", "See old.py and removed_export.\n")
        self.rebase_parent()
        git(self.repo, "rm", "-q", "old.py")
        self.write("guide.md", "See old.py and removed_export.\nNew paragraph.\n")
        self.change()
        joined = "\n".join(self.prove()["drift"])
        self.assertIn("guide.md:1: names deleted or renamed path `old.py`", joined)
        self.assertIn("guide.md:1: names removed symbol `removed_export`", joined)

    def test_drift_precheck_reports_removals_another_file_redeclares(self):
        self.write("old.py", "def public_api():\n    pass\n")
        self.write("guide.md", "Import old.public_api.\n")
        self.rebase_parent()
        self.write("old.py", "VALUE = 1\n")
        self.write("unrelated.py", "def public_api():\n    pass\n")
        self.change()
        self.assertIn("guide.md:1: names removed symbol `public_api`", "\n".join(self.prove()["drift"]))

    def test_drift_precheck_keeps_symbols_the_same_file_redeclares(self):
        self.write("mod.py", "def public_api():\n    return 1\n")
        self.write("guide.md", "Call public_api.\n")
        self.rebase_parent()
        self.write("mod.py", "def public_api():\n    return 2\n\n\nVALUE = 1\n")
        self.change()
        self.assertEqual(self.prove()["drift"], [])

    def test_drift_precheck_reports_removed_go_and_rust_public_items(self):
        self.write("svc/api.go", "package svc\n\nfunc PublicAPI() {}\n\nfunc (s *Server) Handle() {}\n\nfunc private() {}\n")
        self.write("core/lib.rs", "pub fn rust_api() {}\npub struct Widget;\nfn hidden() {}\n")
        self.write("guide.md", "Call PublicAPI and Handle.\nUse rust_api with Widget.\nprivate hidden\n")
        self.rebase_parent()
        self.write("svc/api.go", "package svc\n")
        self.write("core/lib.rs", "pub fn other() {}\n")
        self.change()
        joined = "\n".join(self.prove()["drift"])
        for symbol in ("PublicAPI", "Handle", "rust_api", "Widget"):
            self.assertIn(f"names removed symbol `{symbol}`", joined)
        for absent in ("`private`", "`hidden`"):
            self.assertNotIn(absent, joined)

    def test_drift_precheck_is_empty_without_removals(self):
        self.change()
        self.assertEqual(self.prove()["drift"], [])

    def test_prove_accepts_head_whose_freeze_predates_contract_bump(self):
        gate = {"schemaVersion": 2, "checks": {"commit": ["false"], "ci": ["false"]}, "pathCheck": ["true"]}
        self.write(".omp/gate.json", json.dumps(dict(gate, dynaflowsContract=4)))
        self.rebase_parent("contract 4")
        with patch.object(workflow, "dynaflows_contract", return_value=4):
            path, _ = self.freeze([self.proof()])
        self.assertEqual(json.loads(path.read_text())["dynaflowsContract"], 4, "freeze records the contract it ran under")
        self.write("value", "fixed")
        self.head = self.commit("change under contract 4")
        self.ledger()
        with patch.object(workflow, "dynaflows_contract", return_value=5):
            self.assert_block(self.prove(), f"Dynaflows contract v4 recorded; current is v5. {workflow.CONTRACT_REMEDY}")
            self.write(".omp/gate.json", json.dumps(dict(gate, dynaflowsContract=5)))
            self.head = self.commit("chore: omp-guardrails init --extend")
            result = self.prove()
        self.assertEqual(result["status"], "pass", result)
        self.assertEqual(result["manifest"]["dynaflowsContract"], 4)

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
        self.verifier.assert_not_called()
        self.post.assert_not_called()
        self.ci.return_value = "success"
        self.assertEqual(self.prove()["status"], "pass")

    def test_prove_skips_ci_read_when_gate_declares_no_required_context(self):
        self.change()
        self.assertEqual(self.prove()["status"], "pass")
        self.ci.assert_not_called()

    def test_required_context_state_reads_statuses_and_check_runs_on_exact_head(self):
        self.ci_patch.stop()
        self.addCleanup(self.ci_patch.start)
        git(self.repo, "remote", "add", "origin", "https://github.com/example-owner/example-repo.git")
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

    def test_prove_writes_result_beside_pass_marker_and_posts_it_on_the_pr(self):
        self.change()
        result = self.prove()
        self.assertEqual(result["status"], "pass", result)
        path = workflow.result_path(self.repo, self.head)
        self.assertEqual(path.parent, workflow._common(self.repo) / "omp-prove")
        self.assertTrue((path.parent / f"{self.head}.json").is_file())
        self.assertEqual(json.loads(path.read_text()), result)
        self.assertEqual(result["prComment"], self.comment.return_value)
        self.assertEqual((result["head"], result["objective"], result["manifestSha256"]), (self.head, "fix value", workflow._sha(self.manifest_path)))
        repo, head, posted = self.comment.call_args.args
        self.assertEqual((repo, head, posted["status"]), (self.repo.resolve(), self.head, "pass"))
        self.verifier.return_value = dict(verdict(), status="fail", blocking=["wrong"])
        failed = self.prove()
        self.assert_block(failed, "wrong")
        self.assertEqual(json.loads(path.read_text())["status"], "fail", "a failed prove also writes and posts its result")
        self.assertEqual(self.comment.call_args.args[2]["status"], "fail")

    def test_unpublished_pass_fails_and_revokes_marker(self):
        self.change()
        self.comment.side_effect = workflow.WorkflowError("no unique open PR at requested head")
        result = self.prove()
        self.assert_block(result, "infrastructure: publishing prove result: no unique open PR at requested head")
        self.assertEqual(json.loads(workflow.result_path(self.repo, self.head).read_text())["status"], "fail")
        history = json.loads((workflow._common(self.repo) / "omp-prove" / "attempts" / f"{self.head}.json").read_text())
        self.assertEqual(history[-1]["status"], "fail")

    def test_result_comment_omits_local_paths_and_fits_github_limit(self):
        result = {"status": "pass", "head": "a" * 40, "worktree": str(self.repo), "blocking": [],
                  "deterministic": {"proofs": {"P1": [{"location": "head", "output": "x" * 5000 + "END"}]}}}
        body = workflow._result_comment(result)
        self.assertTrue(body.startswith(workflow.RESULT_MARKER))
        self.assertNotIn(str(self.repo), body)
        self.assertIn("[TRUNCATED to last 1000 chars]", body)
        self.assertIn("END", body)
        self.assertEqual(result["deterministic"]["proofs"]["P1"][0]["output"], "x" * 5000 + "END", "the returned result stays complete")
        result["deterministic"]["proofs"] = {f"P{n}": [{"output": "y" * 5000}] * 3 for n in range(30)}
        self.assertLessEqual(len(workflow._result_comment(result)), workflow.COMMENT_LIMIT)
        result["verifier"] = {"followUps": ["z" * workflow.COMMENT_LIMIT]}
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

    def test_verifier_receives_proof_records_as_settled_evidence(self):
        self.change(proofs=[self.proof(ident="P2", filename="second.py"), self.proof()])
        self.verifier.return_value = verdict(())
        result = self.prove()
        self.assertEqual(result["status"], "pass", "the verifier need not re-drive proofs prove() already ran")
        brief = self.verifier.call_args.args[0]
        self.assertIn("Deterministic proof records are settled evidence", brief)
        self.assertIn("Do not re-run proofs to confirm them.", brief)
        self.assertNotIn("Drive every criterion live", brief)


class WorkflowErrorForTest(RuntimeError):
    pass


class SchemaTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.path = Path(scratch.name) / "manifest.json"
        self.data = {"schemaVersion": 1, "slug": "valid", "base": "a" * 40, "proofs": [{"id": "P", "criterion": "works", "kind": "preserve",
                     "cmd": [sys.executable, "proof.py"], "files": ["proof.py"], "sha256": {"proof.py": "b" * 64}}]}

    def load(self, data):
        self.path.write_text(json.dumps(data))
        return workflow.load_manifest(self.path, workflow._sha(self.path))

    def test_manifest_invalid_shapes(self):
        mutations = [("slug", []), ("schemaVersion", True), ("base", None), ("setup", "shell"), ("revisions", {}),
                     ("dynaflowsContract", 0), ("dynaflowsContract", "5"), ("dynaflowsContract", True),
                     ("revisions", [{"id": [], "reason": "why", "oldSha256": "a" * 64}]), ("proofs", None)]
        for key, value in mutations:
            with self.subTest(key=key, value=value), self.assertRaises(workflow.WorkflowError):
                self.load(dict(self.data, **{key: value}))
        for key, value in (("files", [["unhashable"]]), ("files", ["a", "a"]), ("cmd", "shell"), ("kind", []), ("criterion", " "),
                           ("sha256", []), ("sha256", {"proof.py": 3}), ("expectRed", "unexpected"), ("parent", "HEAD")):
            data = copy.deepcopy(self.data)
            data["proofs"][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(workflow.WorkflowError):
                self.load(data)
        self.path.write_text("{")
        with self.assertRaises(workflow.WorkflowError):
            workflow.load_manifest(self.path, workflow._sha(self.path))

    def test_noncanonical_paths_and_tmp_independence(self):
        for name in ("./proof.py", "../proof.py", "a//b", "a/./b", "a/../b", "/proof.py", "a/", "C:proof", "a\\b", "a/.git/b", "a\0b", ""):
            data = copy.deepcopy(self.data)
            data["proofs"][0].update(files=[name], sha256={name: "b" * 64})
            with self.subTest(name=name), self.assertRaises(workflow.WorkflowError):
                self.load(data)
        with patch.object(workflow.tempfile, "gettempdir", side_effect=AssertionError("lexical validation touched host")):
            self.assertEqual(self.load(self.data), self.data)

    def test_gate_schema_optional_fields(self):
        valid = [{"schemaVersion": 2}, {"schemaVersion": 2, "pathCheckTimeoutMs": 1, "testGlobs": ["", "*.py"], "checks": {"ci": ["true"]}},
                 {"schemaVersion": 2, "ci": {"requiredContext": "gate", "runner": "ci-gate"},
                  "checks": {"format": ["ruff", "format"], "profile": ["omp-guardrails", "profile-check"]}}]
        invalid = [{"schemaVersion": True}, {"schemaVersion": 3}, *({"schemaVersion": 2, "pathCheckTimeoutMs": x} for x in (0, True, 1.5, None, 9007199254740992)),
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
        policy = workflow.gate_policy(valid[2])
        self.assertEqual((policy["format"], policy["profile"], policy["ci"]),
                         (["ruff", "format"], ["omp-guardrails", "profile-check"], {"requiredContext": "gate", "runner": "ci-gate"}))

    def test_gate_contract_must_be_positive_safe_integer(self):
        self.assertEqual(workflow.gate_policy({"schemaVersion": 2, "dynaflowsContract": 3})["dynaflowsContract"], 3)
        self.assertIsNone(workflow.gate_policy({"schemaVersion": 2})["dynaflowsContract"])
        for value in (0, -1, True, 2.0, "2", None, 9007199254740992):
            with self.subTest(value=value):
                self.assertEqual(workflow.gate_reason({"schemaVersion": 2, "dynaflowsContract": value}),
                                 "dynaflowsContract must be a positive safe integer")

    def test_contract_reads_the_single_top_level_rails_line(self):
        self.assertEqual(workflow.dynaflows_contract(), workflow.dynaflows_contract(RAILS))
        self.assertGreater(CONTRACT, 0)
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "agent-rails.yml"
            for text, expected in (("# note\ndynaflowsContract: 7  # current\nreferences: {}\n", 7),
                                   ("dynaflowsContract: 3\r\n", 3)):
                path.write_text(text, newline="")
                self.assertEqual(workflow.dynaflows_contract(path), expected)
            for text in ("references: {}\n", "dynaflowsContract: 0\n", "dynaflowsContract: two\n",
                         "  dynaflowsContract: 2\n", "dynaflowsContract: 2\ndynaflowsContract: 3\n"):
                path.write_text(text)
                with self.subTest(text=text), self.assertRaises(workflow.WorkflowError):
                    workflow.dynaflows_contract(path)
            with self.assertRaises(workflow.WorkflowError):
                workflow.dynaflows_contract(Path(scratch) / "missing.yml")
        loaded = {"__name__": "kernel"}
        exec(compile(MODULE.read_text(), "<cell>", "exec"), loaded)
        with self.assertRaisesRegex(loaded["WorkflowError"], "without __file__"):
            loaded["dynaflows_contract"]()
        # The eval kernel's %load sets __file__ in the shared namespace; a later %load replaces it.
        kernel = {"__name__": "kernel", "__file__": str(MODULE)}
        exec(compile(MODULE.read_text(), str(MODULE), "exec"), kernel)
        kernel["__file__"] = "/elsewhere/other.py"
        self.assertEqual(kernel["dynaflows_contract"](), CONTRACT)

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

    def test_workflow_code_holds_no_model_ids_or_hosts(self):
        text = MODULE.read_text()
        for pattern in (r"gpt-\d", r"claude-", r"sonnet", r"opus", r"openai-codex", r"\bnuc\b", r"omarchy", r"/Users/"):
            self.assertIsNone(re.search(pattern, text, re.IGNORECASE), pattern)

    def test_run_verifier_seam_selector_schema_and_override(self):
        with patch.object(workflow, "agent", create=True, return_value="handle") as spawn, patch.object(workflow, "wait", create=True, return_value=[verdict()]) as wait:
            workflow._run_verifier("brief", ["P2", "P1"])
            self.assertEqual(spawn.call_args.kwargs["agent"], "verifier")
            self.assertNotIn("model", spawn.call_args.kwargs)
            self.assertFalse(spawn.call_args.kwargs["schema"]["additionalProperties"])
            proof_id_schema = spawn.call_args.kwargs["schema"]["properties"]["criteria"]["items"]["properties"]["proofId"]
            self.assertEqual(proof_id_schema, {"enum": ["P1", "P2"]})
            self.assertEqual(wait.call_args.kwargs, {"raise_errors": False})
            workflow._run_verifier("brief", ["P1"], model="provider/example-verifier")
            self.assertEqual(spawn.call_args.kwargs["model"], "provider/example-verifier")

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
