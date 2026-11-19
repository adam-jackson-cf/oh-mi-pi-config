"""Consumer boundaries for frozen proofs (red at base, green at head), isolated executions and the prove() result."""

import copy
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any, Never
from unittest.mock import patch

import pytest

MODULE = (
    Path(__file__).resolve().parents[1]
    / "agent/extensions/dynaflows/engine/workflows.py"
)
spec = importlib.util.spec_from_file_location("workflows", MODULE)
assert spec is not None
assert spec.loader is not None
workflow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workflow)
SWITCH = {"dynaflows": {"enabled": True}}
ORIGIN = "https://github.com/Example-Owner/Example-Repo.git"
RED_AT_BASE = "from pathlib import Path\nassert Path('value').read_text() == 'fixed', 'expected fixed'\n"
MAP_PATH = "docs/features"
# Proof file names the tests freeze; the base feature map links them all, so proofs are mapped.
MAPPED = [
    "proof.py",
    "-proof",
    "second.py",
    "later.py",
    "journey.py",
    *(f"proof{n}.py" for n in range(17)),
]


def map_doc(*targets: str, title: str = "Value") -> str:
    """A feature-map document whose 'Driving it with' section links `targets` (paths relative to docs/features/)."""
    links = "\n".join(f"- [{target}]({target})" for target in targets) or "- none yet"
    return (
        f"# {title}\n\n## Sub-features\n\n- value\n\n## How to get to it (user POV)\n\n- read value\n\n"
        f"## Driving it with lever\n\n{links}\n\n## Gotchas\n\n- none\n"
    )


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), *args], text=True, timeout=60
    ).strip()


class RepositoryCase(unittest.TestCase):
    def setUp(self) -> None:
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
        self.write_gate(
            json.dumps({
                "schemaVersion": 2,
                "checks": {"commit": ["false"], "ci": ["false"]},
            })
        )
        self.profile("<doctor failure marker>")
        self.write(
            f"{MAP_PATH}/value.md", map_doc(*(f"../../{name}" for name in MAPPED))
        )
        self.write("value", "old")
        self.commit("base")
        self.parent = git(self.repo, "rev-parse", "HEAD")
        self.manifest_path = (
            workflow._common(self.repo) / "omp-manifests" / "example.json"
        )
        self.manifest_path.parent.mkdir(parents=True)
        self.ci_patch = patch.object(
            workflow, "_required_context_state", return_value="success"
        )
        self.ci = self.ci_patch.start()
        self.addCleanup(self.ci_patch.stop)
        self.comment_patch = patch.object(
            workflow,
            "_post_result_comment",
            return_value="https://github.com/example-owner/example-repo/pull/1#issuecomment-1",
        )
        self.comment = self.comment_patch.start()
        self.addCleanup(self.comment_patch.stop)
        self.addCleanup(self.assert_cleaned)

    def write(self, name: str, text: str) -> Path:
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def write_gate(self, text: str) -> None:
        """Write the record's gate.json; a JSON object without a dynaflows switch gets it turned on,
        and one without ownerOnlyPaths gets none."""
        try:
            gate = json.loads(text)
        except ValueError:
            gate = None
        if isinstance(gate, dict):
            text = json.dumps({**SWITCH, "ownerOnlyPaths": [], **gate})
        self.record.mkdir(parents=True, exist_ok=True)
        (self.record / "gate.json").write_text(text)

    def write_profile(self, text: str) -> None:
        self.record.mkdir(parents=True, exist_ok=True)
        (self.record / "profile.md").write_text(text)

    def commit(self, message: str) -> str:
        git(self.repo, "add", ".")
        git(self.repo, "commit", "--allow-empty", "-qm", message)
        return git(self.repo, "rev-parse", "HEAD")

    def assert_cleaned(self) -> None:
        assert git(self.repo, "worktree", "list", "--porcelain").count("worktree ") == 1

    def proof(
        self, filename: str = "proof.py", code: str | None = None, ident: str = "P1"
    ) -> dict[str, Any]:
        """A proof that is red at base (value is 'old') unless `code` says otherwise."""
        self.write(filename, code if code is not None else RED_AT_BASE)
        return {
            "id": ident,
            "criterion": "value is fixed",
            "cmd": [sys.executable, filename],
            "files": [filename],
        }

    def change(
        self, code: str | None = None, proofs: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        proofs = [self.proof(code=code)] if proofs is None else proofs
        self.write("value", "fixed")
        self.head = self.commit("change")
        data = {
            "schemaVersion": 1,
            "slug": "example",
            "base": self.parent,
            "proofs": [
                dict(
                    p,
                    parent=p.get("parent", self.parent),
                    sha256={
                        name: workflow._sha(self.repo / name) for name in p["files"]
                    },
                )
                for p in proofs
            ],
        }
        self.save(data)
        return data

    def save(self, data: dict[str, Any]) -> None:
        self.manifest_path.write_text(json.dumps(data))

    def prove(self, objective: str = "fix value", **kwargs: Any) -> dict[str, Any]:
        args = dict(target_ref=self.parent, **kwargs)
        result: dict[str, Any] = workflow.prove(
            self.repo,
            self.parent,
            self.head,
            (self.manifest_path, workflow._sha(self.manifest_path)),
            objective,
            **args,
        )
        self.assert_cleaned()
        json.dumps(result)
        return result

    def freeze(self, proofs: list[dict[str, Any]], **kwargs: Any) -> Any:
        try:
            return workflow.freeze(
                self.repo, self.manifest_path, proofs, "example", **kwargs
            )
        finally:
            self.assert_cleaned()

    def stored_status(self, head: str | None = None) -> Any:
        path = workflow.result_path(self.repo, head or self.head)
        return json.loads(path.read_text())["status"] if path.exists() else None

    def assert_block(self, result: dict[str, Any], fragment: str) -> None:
        assert result["status"] == "fail", result
        assert fragment in "\n".join(result["blocking"])
        assert self.stored_status() != "pass", (
            "a failed prove never leaves a pass result for the head"
        )

    def gate(self, **checks: list[str]) -> None:
        gate = {
            "schemaVersion": 2,
            "checks": {"commit": ["false"], "ci": ["false"], **checks},
            "ci": {"requiredContext": "gate", "runner": "ci-gate"},
        }
        self.write_gate(json.dumps(gate))

    def rebase_parent(self, message: str = "parent fixtures") -> None:
        self.parent = self.commit(message)

    def profile(self, marker: str | None, **surface: str | None) -> None:
        values = {
            "lever_command": "lever",
            "doctor_marker": marker,
            "feature_map_path": f"{MAP_PATH}/",
            **surface,
        }
        front = json.dumps({
            "verification_surface": {k: v for k, v in values.items() if v is not None},
            "structure": {"owners": {"patterns": {"**": "proof engine"}}},
            "guardrails": {
                "structure": {
                    "root_directories": ["docs", "oracle", "verifier"],
                    "root_files": [
                        *MAPPED,
                        "value",
                        "docs.md",
                        "json.py",
                        ".gitignore",
                        "runner",
                        "fmt.py",
                        "other.txt",
                        "other_test.py",
                    ],
                }
            },
        })
        self.write_profile(f"---\n{front}\n---\n\n# Repo test profile\n")


class ProofRunTests(RepositoryCase):
    """Red at base, green at head: proof runs, setup, cleanup and input validation."""

    def test_freeze_requires_durable_manifest_but_reads_temporary_previous(
        self,
    ) -> None:
        proof = self.proof()
        durable = self.manifest_path
        for root in dict.fromkeys((
            Path(tempfile.gettempdir()).resolve(),
            Path("/tmp").resolve(),
        )):
            with (
                self.subTest(root=str(root)),
                tempfile.TemporaryDirectory(dir=root) as scratch,
            ):
                self.manifest_path = Path(scratch) / "manifest.json"
                with pytest.raises(
                    workflow.WorkflowError,
                    match=re.escape("<git-common-dir>/omp-manifests/<slug>.json"),
                ):
                    self.freeze([proof])
                assert not self.manifest_path.exists()
        self.manifest_path = durable
        path, digest = self.freeze([proof])
        assert path == durable.resolve()
        assert workflow._sha(path) == digest
        with tempfile.TemporaryDirectory() as scratch:
            previous = Path(scratch) / "previous.json"
            previous.write_bytes(path.read_bytes())
            assert self.freeze([proof], previous=(previous, digest)) == (path, digest)

    def test_one_rule_red_at_base_then_green_at_head(self) -> None:
        proof = self.proof()
        path, digest = self.freeze([proof])
        assert "kind" not in json.loads(path.read_text())["proofs"][0]
        self.write("value", "fixed")
        self.head = self.commit("implementation")
        result = workflow.prove(
            self.repo,
            self.parent,
            self.head,
            (path, digest),
            "fix value",
            target_ref=self.parent,
        )
        assert result["status"] == "pass", result
        assert [run["location"] for run in result["deterministic"]["proofs"]["P1"]] == [
            "head",
            "head",
        ], "prove() runs proofs at head only; red at base was shown at freeze"
        assert self.stored_status() == "pass"

    def test_freeze_refuses_any_proof_green_at_base(self) -> None:
        for code in ("assert True\n", "print('no assertion')\n"):
            with (
                self.subTest(code=code),
                pytest.raises(
                    workflow.WorkflowError,
                    match="P1: passes at baseline; a proof must fail at base",
                ),
            ):
                self.freeze([self.proof(code=code)])
            assert not self.manifest_path.exists()

    def test_any_failing_run_at_base_is_red(self) -> None:
        for code in (
            RED_AT_BASE,
            "import sys\nsys.exit(3)\n",
            "raise SystemExit('some other reason')\n",
        ):
            with self.subTest(code=code):
                self.manifest_path.unlink(missing_ok=True)
                self.freeze([self.proof(code=code)])
                assert self.manifest_path.exists()

    def test_prove_blocks_proof_red_at_head(self) -> None:
        data = self.change()
        self.write("value", "regressed")
        self.head = self.commit("regression")
        self.save(data)
        self.assert_block(self.prove(), "P1: head run 1 failed")

    def test_zero_proofs_freeze_and_prove_check_ci_green_only(self) -> None:
        self.write("docs.md", "docs\n")
        self.commit("docs")
        path, digest = self.freeze([], base=self.parent)
        assert json.loads(path.read_text())["proofs"] == []
        self.head = git(self.repo, "rev-parse", "HEAD")
        self.write_gate(
            json.dumps({
                "schemaVersion": 2,
                "checks": {"commit": ["true"]},
                "ci": {"requiredContext": "gate", "runner": "ci-gate"},
            })
        )
        with patch.object(
            workflow, "_setup", side_effect=AssertionError("zero proofs build no trees")
        ):
            result = workflow.prove(
                self.repo,
                self.parent,
                self.head,
                (path, digest),
                "docs",
                target_ref=self.parent,
            )
            assert result["status"] == "pass", result
            self.ci.assert_called_once_with(self.repo.resolve(), self.head, "gate")
            assert self.stored_status() == "pass"
            self.ci.return_value = "pending"
            result = workflow.prove(
                self.repo,
                self.parent,
                self.head,
                (path, digest),
                "docs",
                target_ref=self.parent,
            )
        self.assert_block(result, 'required CI context "gate" is pending')
        assert self.stored_status() == "fail"

    def test_setup_runs_once_per_tree_in_freeze_and_prove(self) -> None:
        self.write(".gitignore", "setup/\n")
        self.commit("ignore setup")
        self.parent = git(self.repo, "rev-parse", "HEAD")
        counter = self.tmp / "setup-count"
        setup = [
            sys.executable,
            "-c",
            "from pathlib import Path\n"
            f"with Path({str(counter)!r}).open('a') as out: out.write(str(Path.cwd()) + '\\n')\n"
            "Path('setup').mkdir(exist_ok=True)\n"
            "p = Path('setup/count')\n"
            "p.write_text(p.read_text() + 'x' if p.exists() else 'x')\n",
        ]
        code = (
            "from pathlib import Path\n"
            "assert not Path('residue').exists()\n"
            "Path('residue').write_text('run')\n"
            "assert Path('setup/count').read_text() == 'x'\n"
            "assert Path('value').read_text() == 'fixed', 'expected fixed'\n"
        )
        proofs = [
            self.proof(filename=f"proof{n}.py", ident=f"P{n}", code=code)
            for n in (1, 2, 3)
        ]
        self.freeze(proofs, setup=setup)
        assert (
            len(counter.read_text().splitlines()) == 1
        )  # One baseline tree for all proofs.
        data = self.change(proofs=proofs)
        data["setup"] = setup
        self.save(data)
        assert self.prove()["status"] == "pass"
        trees = counter.read_text().splitlines()
        assert len(trees) == 2  # Every head run shares the one head tree's setup.
        assert len(set(trees)) == 2

    def test_setup_outputs_survive_and_proof_artifacts_are_removed(self) -> None:
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
        data["setup"] = [
            sys.executable,
            "-c",
            "from pathlib import Path\n"
            "for name in ('ready', 'ignored-ready'):\n"
            "    Path(name).mkdir()\n"
            "    Path(name, 'output').write_text('setup')\n",
        ]
        self.save(data)
        result = self.prove()
        assert result["status"] == "pass"
        assert [
            run["output"].strip() for run in result["deterministic"]["proofs"]["P1"]
        ] == ["False", "True"]

    def test_reset_restores_tracked_files_and_removes_overlays(self) -> None:
        tree = self.tmp / "scratch"
        git(self.repo, "worktree", "add", "--detach", str(tree), self.parent)
        try:
            baselines: dict[str, Any] = {}
            proof = {
                "id": "P1",
                "cmd": [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; assert Path('overlay').read_text() == 'proof'; "
                    "assert Path('value').read_text() == 'overlaid'",
                ],
            }
            record = workflow._execute(
                workflow.Placement(
                    tree, overlay={"overlay": b"proof", "value": b"overlaid"}
                ),
                proof,
                baselines,
            )
            assert record["ok"], record
            assert (tree / "value").read_text() == "old"
            assert not (tree / "overlay").exists()
            (tree / "value").write_text("residue")
            (tree / "artifact").write_text("residue")
            outside = self.tmp / "outside"
            outside.mkdir()
            (outside / "keep").write_text("safe")
            (tree / "linked").symlink_to(outside, target_is_directory=True)
            (tree / "new-dir").mkdir()
            (tree / "new-dir/link").symlink_to(outside, target_is_directory=True)
            (tree / "odd\nname").write_text("residue")
            proof["cmd"] = [
                sys.executable,
                "-c",
                "from pathlib import Path; assert Path('value').read_text() == 'old'; "
                "assert not Path('artifact').exists(); assert not Path('overlay').exists()",
            ]
            assert workflow._execute(workflow.Placement(tree), proof, baselines)["ok"]
            assert (outside / "keep").read_text() == "safe"
            assert not (tree / "linked").is_symlink()
            assert not (tree / "new-dir").exists()
            assert not (tree / "odd\nname").exists()
        finally:
            git(self.repo, "worktree", "remove", "--force", str(tree))

    def test_baseline_recording_failure_is_attributed_without_repeating_setup(
        self,
    ) -> None:
        data = self.change()
        counter = self.tmp / "setup-count"
        data["setup"] = [
            sys.executable,
            "-c",
            f"from pathlib import Path; p = Path({str(counter)!r}); "
            "p.write_text(p.read_text() + 'x' if p.exists() else 'x')",
        ]
        self.save(data)
        with patch.object(
            workflow, "_entries", side_effect=OSError("baseline listing failed")
        ):
            result = self.prove()
        self.assert_block(
            result, "P1: head run 1: infrastructure: baseline listing failed"
        )
        assert counter.read_text() == "x"

    def test_execution_cleanup_failure_prevents_pass(self) -> None:
        self.change()
        entries = workflow._entries
        calls = 0

        def fail_cleanup(tree: Path) -> Any:
            nonlocal calls
            calls += 1
            if calls == 3:  # Setup baseline, before execution, finally.
                raise OSError("artifact cleanup failed")
            return entries(tree)

        with patch.object(workflow, "_entries", side_effect=fail_cleanup):
            result = self.prove()
        self.assert_block(result, "cleanup infrastructure: artifact cleanup failed")
        assert result["deterministic"]["proofs"]["P1"][0]["ok"] is None

    def test_setup_mutation_is_infrastructure(self) -> None:
        data = self.change()
        data["setup"] = [
            sys.executable,
            "-c",
            "from pathlib import Path; Path('value').write_text('bad')",
        ]
        self.save(data)
        result = self.prove()
        self.assert_block(result, "setup changed tracked files")
        assert result["deterministic"]["proofs"]["P1"][0]["ok"] is None

    def test_launch_and_timeout_are_not_red_evidence(self) -> None:
        data = self.change()
        for cmd, timeout, fragment in (
            (["/missing-proof-executable"], 1, "launch error"),
            ([sys.executable, "-c", "import time; time.sleep(2)"], 0.01, "timeout"),
        ):
            with self.subTest(cmd=cmd):
                data["proofs"][0]["cmd"] = cmd
                self.save(data)
                with patch.object(workflow, "CHECK_TIMEOUT", timeout):
                    result = self.prove()
                self.assert_block(result, fragment)
                record = result["deterministic"]["proofs"]["P1"][0]
                assert record["ok"] is None
                assert "P1: head run 1" in record["error"]
        proof = self.proof(code="import time; time.sleep(2)\n")
        with (
            patch.object(workflow, "CHECK_TIMEOUT", 0.01),
            pytest.raises(workflow.WorkflowError, match="infrastructure: timeout"),
        ):
            self.freeze([proof])

    def test_replacement_decode_and_resolved_cwd_scrubbing(self) -> None:
        self.change(
            "import os\nfrom pathlib import Path\nos.write(1,b'\\xff')\nprint(Path.cwd().resolve())\n"
        )
        result = self.prove()
        assert result["status"] == "pass"
        output = result["deterministic"]["proofs"]["P1"][0]["output"]
        assert "�" in output
        assert "<tree>" in output
        assert "omp-prove-" not in output

    def test_head_tracked_mutation_blocks(self) -> None:
        self.change("from pathlib import Path\nPath('value').write_text('mutated')\n")
        self.assert_block(self.prove(), "modified tracked files")

    def test_hash_and_missing_file_blocks(self) -> None:
        self.change()
        for change in ("mutate", "delete"):
            with self.subTest(change=change):
                if change == "mutate":
                    self.write("proof.py", "assert True\n")
                else:
                    (self.repo / "proof.py").unlink()
                self.head = self.commit(change)
                self.assert_block(self.prove(), "hash mismatch")

    def test_missing_and_duplicate_ids(self) -> None:
        data = self.change()
        for proofs in (
            [{k: v for k, v in data["proofs"][0].items() if k != "id"}],
            data["proofs"] * 2,
        ):
            self.save(dict(data, proofs=proofs))
            self.assert_block(self.prove(), "missing or duplicate proof id")

    def test_head_first_revocation_on_invalid_inputs(self) -> None:
        self.change()
        for kwargs in (
            {"parent": "missing"},
            {"target_ref": "missing"},
            {"digest": "0" * 64},
        ):
            workflow._atomic_json(
                workflow.result_path(self.repo, self.head),
                {"head": self.head, "status": "pass"},
            )
            result = workflow.prove(
                self.repo,
                kwargs.get("parent", self.parent),
                self.head,
                (
                    self.manifest_path,
                    kwargs.get("digest", workflow._sha(self.manifest_path)),
                ),
                "fix",
                target_ref=kwargs.get("target_ref", self.parent),
            )
            assert result["status"] == "fail"
            assert self.stored_status() == "fail"

    def test_strict_range_and_proof_ancestry(self) -> None:
        data = self.change()
        for parent, target in ((self.head, self.parent), (self.parent, self.head)):
            result = workflow.prove(
                self.repo,
                parent,
                self.head,
                (self.manifest_path, workflow._sha(self.manifest_path)),
                "fix",
                target_ref=target,
            )
            assert result["status"] == "fail"
        data["proofs"][0]["parent"] = self.head
        self.save(data)
        self.assert_block(self.prove(), "proof parent")
        unrelated = git(
            self.repo, "commit-tree", f"{self.head}^{{tree}}", "-m", "unrelated"
        )
        data["proofs"][0]["parent"] = unrelated
        self.save(data)
        self.assert_block(self.prove(), "proof parent")
        self.save(dict(data, base=self.head))
        self.assert_block(self.prove(), "base must be")


class FreezeTests(RepositoryCase):
    """freeze(): lineage, revisions, path guards, caps and the result signal."""

    def test_invalid_head_gate_precedes_tree_creation_and_setup(self) -> None:
        for text in (
            "null",
            "{",
            json.dumps({"schemaVersion": 3}),
            json.dumps({"schemaVersion": 2, "checks": {"commit": []}}),
            json.dumps({"schemaVersion": 2}),
            json.dumps({
                "schemaVersion": 2,
                "checks": {"commit": ["true"]},
                "unknownField": 3,
            }),
            json.dumps({
                "schemaVersion": 2,
                "checks": {"commit": ["true"]},
                "dynaflows": {"enabled": "yes"},
            }),
        ):
            self.write_gate(text)
            self.change()
            with patch.object(
                workflow, "_setup", side_effect=AssertionError("setup must not run")
            ):
                assert self.prove()["status"] == "fail"

    def test_cleanup_failure_blocks_and_keeps_evidence(self) -> None:
        self.change()
        original = workflow._git
        removed = []

        def fail_remove(repo: Path, *args: str) -> Any:
            if args[:2] == ("worktree", "remove"):
                removed.append(args[-1])
                raise InjectedRemovalError("injected removal failure")
            return original(repo, *args)

        with patch.object(workflow, "_git", side_effect=fail_remove):
            result = workflow.prove(
                self.repo,
                self.parent,
                self.head,
                (self.manifest_path, workflow._sha(self.manifest_path)),
                "fix",
                target_ref=self.parent,
            )
        # TemporaryDirectory has removed test-owned scratch bytes; prune repairs its metadata.
        git(self.repo, "worktree", "prune")
        assert len(removed) == 1
        assert result["deterministic"]["proofs"]
        self.assert_block(result, "cleanup infrastructure")

    def test_freeze_retention_lineage_and_revisions(self) -> None:
        proof = self.proof()
        previous = self.freeze([proof], setup=[sys.executable, "-c", "pass"])
        old = json.loads(previous[0].read_text())
        with patch.object(
            workflow, "_execute", side_effect=AssertionError("retention reran")
        ):
            retained = self.freeze([proof], previous=previous)
        assert json.loads(retained[0].read_text()) == old
        self.write("value", "fixed")
        self.commit("implementation")
        for kwargs in ({"base": "HEAD"}, {"setup": ["true"]}):
            with pytest.raises(workflow.WorkflowError, match="immutable"):
                self.freeze([proof], previous=retained, **kwargs)
        modified = dict(proof, criterion="specific value")
        with pytest.raises(workflow.WorkflowError, match="silent proof change"):
            self.freeze([modified], previous=retained)
        updated = self.freeze(
            [modified], previous=retained, revise={"P1": "clarify criterion"}
        )
        revision = json.loads(updated[0].read_text())["revisions"][-1]
        expected = hashlib.sha256(
            json.dumps(old["proofs"][0], sort_keys=True).encode()
        ).hexdigest()
        assert revision["oldSha256"] == expected

    def test_freeze_later_parent_and_metadata_validation(self) -> None:
        proof = self.proof()
        self.write("value", "fixed")
        self.commit("defective implementation")
        with pytest.raises(
            workflow.WorkflowError, match="defective implementation commit as parent"
        ):
            self.freeze([proof])
        revisions: tuple[object, ...] = ({"P1": " "}, {"unknown": "reason"}, [])
        for revise in revisions:
            with pytest.raises(workflow.WorkflowError):
                self.freeze([proof], revise=revise)

    def test_freeze_paths_command_files_and_option_guards(self) -> None:
        proof = self.proof(filename="-proof")
        proof["cmd"] = [sys.executable, "./-proof"]
        self.freeze([proof])
        self.write("runner", "print('runner')")
        self.commit("runner")
        proof = self.proof()
        for token in (
            "runner",
            "./runner",
            "--config=runner",
            str(self.repo / "runner"),
            "--config=" + str(self.repo / "runner"),
        ):
            with self.subTest(token=token):
                candidate = dict(proof, cmd=[sys.executable, token])
                with pytest.raises(workflow.WorkflowError):
                    self.freeze([candidate])

    def test_freeze_ignored_symlink_and_check_ignore_error(self) -> None:
        proof = self.proof()
        self.write(".gitignore", "proof.py\n")
        self.commit("ignore")
        with pytest.raises(workflow.WorkflowError, match="ignored"):
            self.freeze([proof])
        self.write(".gitignore", "")
        self.commit("unignore")
        (self.repo / "proof.py").unlink(missing_ok=True)
        (self.repo / "proof.py").symlink_to(self.repo / "value")
        with pytest.raises(workflow.WorkflowError, match="symlink"):
            self.freeze([proof])
        (self.repo / "proof.py").unlink()
        proof = self.proof()
        original = subprocess.run

        def fail_ignore(cmd: list[str], **kwargs: Any) -> Any:
            if "check-ignore" in cmd:
                return subprocess.CompletedProcess(cmd, 128, b"", b"ignore failed")
            return original(cmd, **kwargs)

        with (
            patch.object(workflow.subprocess, "run", side_effect=fail_ignore),
            pytest.raises(workflow.WorkflowError, match="check-ignore infrastructure"),
        ):
            self.freeze([proof])

    def test_caps_are_proof_specific(self) -> None:
        proofs = [self.proof(f"proof{i}.py", ident=f"P{i}") for i in range(17)]
        self.freeze(proofs, max_checks=17)
        with pytest.raises(workflow.WorkflowError):
            self.freeze(proofs)
        self.change(proofs=proofs)
        assert self.prove(max_checks=17)["status"] == "pass"
        self.assert_block(self.prove(), "within max_checks")
        self.assert_block(self.prove(max_checks=1), "within max_checks")
        for cap in (0, -1, True, 1.5, "2"):
            with pytest.raises(workflow.WorkflowError):
                self.freeze(proofs, max_checks=cap)
            self.assert_block(self.prove(max_checks=cap), "positive integer")

    def test_draft_validation_precedes_hashing_and_commands(self) -> None:
        proof = self.proof()
        for invalid in (
            [dict(proof, files=[["not a path"]])],
            [dict(proof, cmd="shell")],
            [proof, dict(proof)],
            [dict(proof, kind="defect")],
            [dict(proof, expectRed="expected fixed")],
        ):
            with (
                patch.object(
                    workflow,
                    "_git",
                    side_effect=AssertionError("Git ran before draft validation"),
                ),
                pytest.raises(workflow.WorkflowError),
            ):
                self.freeze(invalid)

    def test_frozen_command_tokens_cannot_bypass_source_isolation(self) -> None:
        data = self.change()
        for token in (
            str(self.repo / "proof.py"),
            "--config=" + str(self.repo / "proof.py"),
        ):
            data["proofs"][0]["cmd"] = [sys.executable, token]
            self.save(data)
            self.assert_block(self.prove(), "absolute command reference")
        self.write("runner", "assert True")
        self.head = self.commit("runner")
        data["proofs"][0]["cmd"] = [sys.executable, "--config=runner"]
        self.save(data)
        self.assert_block(self.prove(), "unchanged runners")

    def test_removed_proof_requires_revision_and_retains_entire_old_digest(
        self,
    ) -> None:
        first = self.proof()
        second = self.proof("second.py", ident="P2")
        previous = self.freeze([first, second])
        old_second = json.loads(previous[0].read_text())["proofs"][1]
        self.commit("frozen proofs")
        with pytest.raises(workflow.WorkflowError, match="silent proof change"):
            self.freeze([first], previous=previous)
        self.freeze(
            [first], previous=previous, revise={"P2": "remove duplicate criterion"}
        )
        revision = json.loads(self.manifest_path.read_text())["revisions"][-1]
        assert (
            revision["oldSha256"]
            == hashlib.sha256(
                json.dumps(old_second, sort_keys=True).encode()
            ).hexdigest()
        )

    def test_later_proof_uses_explicit_parent_where_it_is_red(self) -> None:
        initial = self.proof()
        previous = self.freeze([initial])
        defective = self.commit("implementation missing a later-found behaviour")
        later = self.proof(
            "later.py",
            "from pathlib import Path\nassert Path('value').read_text() == 'reviewed', 'later regression'\n",
            "P2",
        )
        later["parent"] = defective
        self.freeze([initial, later], previous=previous)
        frozen = json.loads(self.manifest_path.read_text())
        assert frozen["proofs"][1]["parent"] == defective
        assert frozen["base"] == self.parent

    def test_freeze_parent_before_base_and_slug_change(self) -> None:
        proof = self.proof()
        previous = self.freeze([proof])
        with pytest.raises(workflow.WorkflowError, match="immutable"):
            workflow.freeze(
                self.repo, self.manifest_path, [proof], "different", previous=previous
            )
        self.commit("later base")
        with pytest.raises(workflow.WorkflowError, match="at or after base"):
            self.freeze([dict(proof, parent=self.parent)], base="HEAD")

    def test_result_file_is_the_one_merge_signal_in_the_common_git_directory(
        self,
    ) -> None:
        self.change()
        linked = self.tmp / "linked"
        git(self.repo, "worktree", "add", "-qb", "linked", str(linked), self.head)
        try:
            original = os.replace
            observed = []

            def inspect(source: str, destination: str) -> None:
                observed.append((
                    json.loads(Path(source).read_text()),
                    Path(destination),
                ))
                return original(source, destination)

            with patch.object(workflow.os, "replace", side_effect=inspect):
                result = workflow.prove(
                    linked,
                    self.parent,
                    self.head,
                    (self.manifest_path, workflow._sha(self.manifest_path)),
                    "fix value",
                    target_ref=self.parent,
                )
            assert result["status"] == "pass", result
            path = workflow.result_path(linked, self.head)
            assert (
                path
                == workflow._common(self.repo)
                / "omp-prove"
                / f"{self.head}.result.json"
            )
            assert {destination for _, destination in observed} == {path}, (
                "the result file is the only file prove() writes"
            )
            stored = json.loads(path.read_text())
            assert (stored["head"], stored["status"]) == (self.head, "pass")
            assert sorted(p.name for p in path.parent.iterdir()) == [path.name], (
                "no separate pass marker"
            )
        finally:
            git(self.repo, "worktree", "remove", "--force", str(linked))


class RecordGateTests(RepositoryCase):
    """The record and gate checks freeze() and prove() require, and repo formatting."""

    def test_prove_without_a_record_is_the_guardrail_prerequisite(self) -> None:
        shutil.rmtree(self.record)
        self.change()
        with patch.object(
            workflow, "_setup", side_effect=AssertionError("trees must not be built")
        ):
            result = self.prove()
        self.assert_block(result, workflow.GUARDRAIL_PREREQUISITE)
        self.assert_block(
            result,
            f"dynaflows off for example-owner/example-repo: no per-repo record at {self.record / 'gate.json'}",
        )

    def test_prove_requires_commit_check(self) -> None:
        self.change()
        for gate, named in (
            ({"schemaVersion": 2, "checks": {}}, "checks.commit must be argv"),
            ({"schemaVersion": 2}, "checks must be an object with checks.commit"),
        ):
            with self.subTest(named=named):
                self.write_gate(json.dumps(gate))
                self.assert_block(self.prove(), named)
        self.write_gate(
            json.dumps({"schemaVersion": 2, "checks": {"commit": ["true"]}})
        )
        assert self.prove()["status"] == "pass"

    def test_prove_without_origin_finds_no_record(self) -> None:
        git(self.repo, "remote", "remove", "origin")
        self.change()
        self.assert_block(self.prove(), "dynaflows off for none: no origin remote")

    def test_linked_worktree_resolves_to_the_main_checkout_record(self) -> None:
        linked = self.tmp / "linked"
        git(self.repo, "worktree", "add", "-qb", "linked", str(linked))
        try:
            record, gate = workflow.read_record(linked)
            assert record == self.record
            assert gate["checks"]["commit"] == ["false"]
        finally:
            git(self.repo, "worktree", "remove", "--force", str(linked))

    def test_unreadable_or_rejected_record_raises(self) -> None:
        complete = {
            "schemaVersion": 2,
            "checks": {"commit": ["true"]},
            "ownerOnlyPaths": [],
        }
        for text, fragment in (
            ("{", "record "),
            (
                json.dumps({**complete, "dynaflows": {"enabled": "yes"}}),
                'dynaflows must be {"enabled": true|false}',
            ),
        ):
            with self.subTest(text=text):
                (self.record / "gate.json").write_text(text)
                with pytest.raises(workflow.WorkflowError, match=re.escape(fragment)):
                    workflow.read_record(self.repo)

    def test_record_with_an_unknown_field_blocks_freeze_and_prove(self) -> None:
        for field, value in (
            ("unknownField", 3),
            ("extraField", 5000),
            ("otherField", "github"),
        ):
            with self.subTest(field=field):
                self.manifest_path.unlink(missing_ok=True)
                self.write_gate(
                    json.dumps({
                        "schemaVersion": 2,
                        "checks": {"commit": ["false"]},
                        field: value,
                    })
                )
                with pytest.raises(
                    workflow.WorkflowError,
                    match=f"unknown field {field} must be removed",
                ):
                    workflow.read_record(self.repo)
                with pytest.raises(
                    workflow.WorkflowError,
                    match=f"unknown field {field} must be removed",
                ):
                    self.freeze([self.proof()])
                assert not self.manifest_path.exists()

    def test_freeze_without_record_refuses_unless_project_setup(self) -> None:
        (self.record / "gate.json").unlink()
        no_record = f"dynaflows off for example-owner/example-repo: no per-repo record at {self.record / 'gate.json'}"
        with pytest.raises(workflow.WorkflowError) as raised:
            self.freeze([self.proof()])
        assert str(raised.value) == no_record
        assert not self.manifest_path.exists()
        with pytest.raises(
            workflow.WorkflowError, match=re.escape(workflow.FEATURE_MAP_SETUP)
        ):
            self.freeze([self.proof()], project_setup=True)
        assert not self.manifest_path.exists(), (
            "without a record there is no feature map to link proofs from"
        )
        (self.repo / "proof.py").unlink()
        self.freeze([], project_setup=True)
        assert self.manifest_path.exists()
        with pytest.raises(
            workflow.WorkflowError, match="project_setup must be a bool"
        ):
            self.freeze([self.proof()], project_setup="yes")

    def test_run_check_strips_harness_variables_from_repo_commands(self) -> None:
        probe = [
            sys.executable,
            "-c",
            "import os; print(repr(os.environ.get('AGENT')), os.environ.get('KEEP'))",
        ]
        with patch.dict(os.environ, {"AGENT": "1", "KEEP": "kept"}):
            ok, out = workflow.run_check(probe, self.repo)
            assert os.environ["AGENT"] == "1", (
                "the harness's own environment is unchanged"
            )
        assert ok
        assert out.strip() == "None kept"

    def test_run_check_stdout_only_preserves_failure_diagnostics(self) -> None:
        for code in (0, 1):
            with self.subTest(code=code):
                cmd = [
                    sys.executable,
                    "-c",
                    f"import sys; print('data'); print('status', file=sys.stderr); sys.exit({code})",
                ]
                ok, output = workflow.run_check(cmd, self.repo, stdout_only=True)
                assert ok == (code == 0)
                assert output == ("data\n" if ok else "data\nstatus\n")
        assert workflow.run_check(cmd, self.repo) == (False, "data\nstatus\n")

    def test_freeze_formats_before_hashing_and_reports_changed_files(self) -> None:
        self.write(
            "fmt.py",
            "from pathlib import Path\nfor name in ('proof.py', 'other.txt'):\n"
            "    p = Path(name)\n    if p.exists() and not p.read_text().endswith('# formatted\\n'):\n"
            "        p.write_text(p.read_text() + '# formatted\\n')\n",
        )
        self.write("other.txt", "untouched\n")
        self.gate(format=[sys.executable, "fmt.py"])
        self.rebase_parent()
        proof = self.proof()
        result = self.freeze([proof])
        path, digest = result
        assert result.formatted == ["other.txt", "proof.py"]
        assert (self.repo / "proof.py").read_text().endswith("# formatted\n")
        manifest = json.loads(path.read_text())
        assert manifest["proofs"][0]["sha256"]["proof.py"] == workflow._sha(
            self.repo / "proof.py"
        )
        assert workflow._sha(path) == digest
        with pytest.raises(workflow.WorkflowError, match="unfrozen change: other.txt"):
            self.freeze([proof])
        git(self.repo, "commit", "-qm", "commit formatter output", "--", "other.txt")
        assert self.freeze([proof]).formatted == []

    def test_freeze_format_failure_refuses(self) -> None:
        self.gate(
            format=[
                sys.executable,
                "-c",
                "import sys; print('cannot format'); sys.exit(3)",
            ]
        )
        self.rebase_parent()
        with pytest.raises(
            workflow.WorkflowError, match="checks.format failed:\n(.|\n)*cannot format"
        ):
            self.freeze([self.proof()])
        assert not self.manifest_path.exists()

    def test_freeze_without_format_returns_no_formatted_paths(self) -> None:
        result = self.freeze([self.proof()])
        assert result.formatted == []
        assert len(result) == 2


class FeatureMapTests(RepositoryCase):
    """The environment doctor, feature-map links and zero-proof prechecks."""

    def test_freeze_refuses_red_from_environment_doctor(self) -> None:
        self.profile("DOCTOR:")
        self.rebase_parent()
        doctor = self.proof(
            code="from pathlib import Path\nprint('DOCTOR: database not reachable')\n"
            + RED_AT_BASE
        )
        with pytest.raises(
            workflow.WorkflowError,
            match="P1: red came from the environment doctor, not the behaviour",
        ):
            self.freeze([doctor])
        assert not self.manifest_path.exists()
        path, _ = self.freeze([self.proof()])
        assert path.exists()

    def test_freeze_skips_doctor_check_for_placeholder_or_unparsed_profile(
        self,
    ) -> None:
        doctor = (
            "from pathlib import Path\nprint('<doctor failure marker>')\n" + RED_AT_BASE
        )
        for marker in ("<doctor failure marker>", None):
            with self.subTest(marker=marker):
                self.profile(marker)
                self.rebase_parent(f"profile {marker}")
                path, _ = self.freeze([self.proof(code=doctor)], base=self.parent)
                assert path.exists()
                path.unlink()
        self.write_profile("---\n{not json\n---\n")
        self.rebase_parent("broken profile")
        with pytest.raises(
            workflow.WorkflowError, match="front matter is not valid JSON"
        ):
            self.freeze([self.proof()], base=self.parent)

    def test_freeze_refuses_a_proof_no_feature_map_document_links(self) -> None:
        self.write(f"{MAP_PATH}/value.md", map_doc("../../journey.py"))
        self.rebase_parent("map links only the journey")
        unmapped = self.proof("unmapped.py")
        with pytest.raises(
            workflow.WorkflowError,
            match=re.escape(
                f"proof files not linked from any feature-map document under {MAP_PATH}: P1: unmapped.py"
            ),
        ):
            self.freeze([unmapped])
        assert not self.manifest_path.exists()
        # Naming a file in prose is not a link; only a resolving link from a map document maps a proof.
        self.write(
            f"{MAP_PATH}/value.md", map_doc("../../journey.py") + "\nSee unmapped.py.\n"
        )
        self.rebase_parent("prose mention")
        with pytest.raises(
            workflow.WorkflowError, match="not linked from any feature-map document"
        ):
            self.freeze([unmapped])

    def test_freeze_accepts_a_proof_linked_from_an_uncommitted_map_entry(self) -> None:
        self.write(f"{MAP_PATH}/value.md", map_doc())
        self.rebase_parent("empty map")
        proof = self.proof("tests/value_test.py")
        self.write(f"{MAP_PATH}/value.md", map_doc("../../tests/value_test.py"))
        path, _ = self.freeze([proof])
        assert json.loads(path.read_text())["proofs"][0]["files"] == [
            "tests/value_test.py"
        ], "the map entry is documentation, not a frozen proof file"

    def test_freeze_without_a_feature_map_refuses_proofs_but_not_zero_proofs(
        self,
    ) -> None:
        for surface in (
            {"feature_map_path": None},
            {"feature_map_path": "<feature map path>"},
        ):
            with self.subTest(surface=surface):
                self.profile(None, **surface)
                with pytest.raises(
                    workflow.WorkflowError, match=re.escape(workflow.FEATURE_MAP_SETUP)
                ):
                    self.freeze([self.proof()])
                assert not self.manifest_path.exists()
        self.write_profile(
            "---\n"
            + json.dumps({
                "verification_surface": {"none": "library", "approval": "owner"}
            })
            + "\n---\n"
        )
        with pytest.raises(workflow.WorkflowError, match="project-setup"):
            self.freeze([self.proof()])
        self.profile(None, feature_map_path="docs/missing")
        with pytest.raises(
            workflow.WorkflowError, match="feature map docs/missing .* does not exist"
        ):
            self.freeze([self.proof()])
        (self.repo / "proof.py").unlink()
        path, _ = self.freeze([])
        assert json.loads(path.read_text())["proofs"] == []

    def test_prove_blocks_a_changed_map_document_that_links_no_frozen_proof(
        self,
    ) -> None:
        self.write(
            f"{MAP_PATH}/other.md", map_doc("../../other_test.py", title="Other")
        )
        self.write("other_test.py", "assert True\n")
        data = self.change()
        self.assert_block(
            self.prove(),
            f"feature-map documents changed in {self.parent}..{self.head} link no frozen proof: "
            f"{MAP_PATH}/other.md",
        )
        self.write(
            f"{MAP_PATH}/other.md",
            map_doc("../../other_test.py", "../../proof.py", title="Other"),
        )
        self.head = self.commit("link the frozen proof")
        self.save(data)
        assert self.prove()["status"] == "pass"

    def test_prove_passes_when_every_changed_map_document_links_a_proof(self) -> None:
        self.write(
            f"{MAP_PATH}/value.md", map_doc("../../proof.py", "../../journey.py")
        )
        self.write(f"{MAP_PATH}/README.md", "# Features\n\n- [Value](value.md)\n")
        self.profile(None)
        front = json.loads((self.record / "profile.md").read_text().split("---")[1])
        front["guardrails"]["feature_map"] = {"index_files": ["README.md"]}
        self.write_profile(f"---\n{json.dumps(front)}\n---\n")
        self.change()
        result = self.prove()
        assert result["status"] == "pass", result
        front["guardrails"].pop("feature_map")
        (self.record / "profile.md").write_text("---\n" + json.dumps(front) + "\n---\n")
        self.assert_block(self.prove(), f"link no frozen proof: {MAP_PATH}/README.md")

    def test_zero_proof_pr_without_a_map_change_passes(self) -> None:
        self.write("docs.md", "docs\n")
        self.commit("docs")
        path, digest = self.freeze([], base=self.parent)
        self.head = git(self.repo, "rev-parse", "HEAD")
        result = workflow.prove(
            self.repo,
            self.parent,
            self.head,
            (path, digest),
            "docs",
            target_ref=self.parent,
        )
        assert result["status"] == "pass", result
        self.write(
            f"{MAP_PATH}/value.md", map_doc("../../proof.py") + "\nMore behaviour.\n"
        )
        self.head = self.commit("behaviour change without proof")
        result = workflow.prove(
            self.repo,
            self.parent,
            self.head,
            (path, digest),
            "docs",
            target_ref=self.parent,
        )
        self.assert_block(result, f"link no frozen proof: {MAP_PATH}/value.md")

    def test_zero_proof_rename_residue_blocks_after_green_ci(self) -> None:
        self.gate()
        self.write("oracle/grader.py", "def run_oracle():\n    return 1\n")
        self.write("oracle/runtime.py", "class OraclePlugin:\n    pass\n")
        self.rebase_parent("oracle owner")
        path, digest = self.freeze([], base=self.parent)
        (self.repo / "oracle").rename(self.repo / "verifier")
        self.head = self.commit("rename oracle to verifier")
        with patch.object(
            workflow,
            "deterministic_checks",
            side_effect=AssertionError("no frozen proofs"),
        ):
            result = workflow.prove(
                self.repo,
                self.parent,
                self.head,
                (path, digest),
                "rename oracle to verifier",
                target_ref=self.parent,
            )
        assert result["manifest"]["proofs"] == []
        assert result["deterministic"] == {}
        self.assert_block(result, "RENAME.residue")
        assert any("run_oracle" in line for line in result["blocking"]), result
        assert any("OraclePlugin" in line for line in result["blocking"]), result
        self.ci.assert_called_once_with(self.repo.resolve(), self.head, "gate")
        assert self.comment.call_args.args[2]["status"] == "fail"

    def test_zero_proof_clean_rename_passes_after_green_ci(self) -> None:
        self.gate()
        # The moved files keep enough unchanged bytes for Git to detect both renames.
        padding = "".join(f"# owner implementation detail {n}\n" for n in range(20))
        self.write("oracle/grader.py", padding + "def run_oracle():\n    return 1\n")
        self.write("oracle/runtime.py", padding + "class OraclePlugin:\n    pass\n")
        self.rebase_parent("oracle owner")
        path, digest = self.freeze([], base=self.parent)
        (self.repo / "oracle").rename(self.repo / "verifier")
        self.write(
            "verifier/grader.py", padding + "def run_verifier():\n    return 1\n"
        )
        self.write("verifier/runtime.py", padding + "class VerifierPlugin:\n    pass\n")
        self.head = self.commit("rename all oracle owner definitions")
        with patch.object(
            workflow,
            "deterministic_checks",
            side_effect=AssertionError("no frozen proofs"),
        ):
            result = workflow.prove(
                self.repo,
                self.parent,
                self.head,
                (path, digest),
                "rename oracle to verifier",
                target_ref=self.parent,
            )
        assert result["manifest"]["proofs"] == []
        assert result["deterministic"] == {}
        assert result["status"] == "pass", result
        assert result["blocking"] == []
        assert self.stored_status() == "pass"
        self.ci.assert_called_once_with(self.repo.resolve(), self.head, "gate")
        assert self.comment.call_args.args[2]["status"] == "pass"

    def test_zero_proof_precheck_launch_failure_blocks(self) -> None:
        self.gate()
        path, digest = self.freeze([], base=self.parent)
        self.write("docs.md", "Documentation-only change.\n")
        self.head = self.commit("docs")
        missing_python = str(self.tmp / "missing-harness-python")
        with patch.object(workflow.sys, "executable", missing_python):
            result = workflow.prove(
                self.repo,
                self.parent,
                self.head,
                (path, digest),
                "update docs",
                target_ref=self.parent,
            )
        self.assert_block(result, "infrastructure: launch error")
        assert result["deterministic"] == {}
        assert self.stored_status() == "fail"
        assert self.comment.call_args.args[2]["status"] == "fail"

    def test_precheck_launches_without_site_or_repo_imports(self) -> None:
        self.gate()
        path, digest = self.freeze([], base=self.parent)
        # A repository module must never shadow the harness's stdlib imports.
        self.write(
            "json.py", "raise AssertionError('repository json imported by harness')\n"
        )
        self.head = self.commit("repo module named like stdlib")
        original = subprocess.run
        launches = []

        def inspect_launch(cmd: list[str], **kwargs: Any) -> Any:
            if "rename-residue" in cmd:
                assert cmd[:4] == [sys.executable, "-I", "-S", "-c"]
                assert (
                    Path(cmd[5])
                    == workflow._harness_root() / workflow.GUARDRAILS_REFERENCE
                )
                launches.append(list(cmd))
                cmd = list(cmd)
                cmd[4] = (
                    "import sys; assert 'site' not in sys.modules; "
                    "assert sys.flags.isolated; assert sys.flags.no_site; " + cmd[4]
                )
            return original(cmd, **kwargs)

        with patch.object(workflow.subprocess, "run", side_effect=inspect_launch):
            result = workflow.prove(
                self.repo,
                self.parent,
                self.head,
                (path, digest),
                "add repo module",
                target_ref=self.parent,
            )
        assert len(launches) == 1
        assert result["status"] == "pass", result
        assert result["blocking"] == []


class JourneyAndResultTests(RepositoryCase):
    """Journey proofs, the required CI context and the published prove result."""

    def journey(self, counter: Path) -> dict[str, Any]:
        code = (
            "import sys\nfrom pathlib import Path\ncount = Path(sys.argv[1])\n"
            "n = int(count.read_text()) + 1 if count.exists() else 1\ncount.write_text(str(n))\n"
            "assert Path('value').read_text() == 'fixed', 'journey not reached'\n"
        )
        self.write("journey.py", code)
        return {
            "id": "J1",
            "criterion": "journey completes",
            "cmd": [sys.executable, "journey.py", str(counter)],
            "files": ["journey.py"],
            "journey": True,
        }

    def test_freeze_runs_journey_once_at_baseline(self) -> None:
        counter = self.tmp / "journey-count"
        path, _ = self.freeze([self.journey(counter)])
        assert counter.read_text() == "1", (
            "freeze runs only the baseline; stability is a prove() check at head"
        )
        assert json.loads(path.read_text())["proofs"][0]["journey"]

    def test_prove_runs_journey_three_times_at_head(self) -> None:
        counter = self.tmp / "journey-count"
        self.change(proofs=[self.journey(counter)])
        result = self.prove()
        assert result["status"] == "pass", result
        locations = [
            (run["location"], run.get("run"))
            for run in result["deterministic"]["proofs"]["J1"]
        ]
        assert locations == [("head", 1), ("head", 2), ("head", 3)]
        assert counter.read_text() == "3"

    def test_prove_blocks_journey_that_fails_any_head_run(self) -> None:
        counter = self.tmp / "journey-count"
        proof = self.journey(counter)
        self.write(
            "journey.py",
            "import sys\nfrom pathlib import Path\ncount = Path(sys.argv[1])\n"
            "n = int(count.read_text()) + 1 if count.exists() else 1\ncount.write_text(str(n))\n"
            "assert n != 2, 'journey flaked'\n",
        )
        self.change(proofs=[proof])
        result = self.prove()
        self.assert_block(result, "J1: head run 2 failed")
        assert (
            "J1: journey proof passed 2 of 3 clean head runs; journeys must pass every run"
            in result["blocking"]
        )

    def test_non_journey_proofs_keep_two_head_runs(self) -> None:
        self.change()
        runs = [
            run.get("run")
            for run in self.prove()["deterministic"]["proofs"]["P1"]
            if run["location"] == "head"
        ]
        assert runs == [1, 2]

    def test_prove_never_runs_the_profile_check(self) -> None:
        self.gate(profile=[sys.executable, "-c", "import sys; sys.exit(1)"])
        self.change()
        assert self.prove()["status"] == "pass", "CI already ran the repo's gates"

    def test_prove_refuses_before_any_spend_when_required_context_is_not_success(
        self,
    ) -> None:
        self.gate()
        self.change()
        for state in ("pending", "failure", "missing", "in_progress/"):
            with self.subTest(state=state):
                self.ci.reset_mock()
                self.ci.return_value = state
                with patch.object(
                    workflow,
                    "_setup",
                    side_effect=AssertionError("trees must not be built"),
                ):
                    result = self.prove()
                self.assert_block(
                    result,
                    f'required CI context "gate" is {state} for {self.head}; prove() runs only after CI is green on this exact head',
                )
                assert not any(
                    item.startswith("infrastructure") for item in result["blocking"]
                ), result
                self.ci.assert_called_once_with(self.repo.resolve(), self.head, "gate")
        self.ci.return_value = "success"
        assert self.prove()["status"] == "pass"

    def test_prove_skips_ci_read_when_gate_declares_no_required_context(self) -> None:
        self.change()
        assert self.prove()["status"] == "pass"
        self.ci.assert_not_called()

    def test_required_context_state_reads_statuses_and_check_runs_on_exact_head(
        self,
    ) -> None:
        self.ci_patch.stop()
        self.addCleanup(self.ci_patch.start)
        git(
            self.repo,
            "remote",
            "set-url",
            "origin",
            "https://github.com/example-owner/example-repo.git",
        )
        head = "a" * 40

        def respond(statuses: Any, runs: Any) -> Callable[..., dict[str, Any]]:
            def github(repo: Path, *args: str) -> dict[str, Any]:
                assert args[0] == "api"
                if "/status?" in args[1]:
                    assert (
                        args[1]
                        == f"repos/example-owner/example-repo/commits/{head}/status?per_page=100"
                    )
                    return {"statuses": statuses}
                assert (
                    args[1]
                    == f"repos/example-owner/example-repo/commits/{head}/check-runs?check_name=ci%2Fgate&per_page=100"
                )
                return {"check_runs": runs}

            return github

        cases = [
            ([{"context": "ci/gate", "state": "success"}], [], "success"),
            (
                [],
                [{"name": "ci/gate", "status": "completed", "conclusion": "success"}],
                "success",
            ),
            ([{"context": "ci/gate", "state": "pending"}], [], "pending"),
            (
                [{"context": "other", "state": "success"}],
                [{"name": "other", "status": "completed", "conclusion": "success"}],
                "missing",
            ),
            (
                [],
                [{"name": "ci/gate", "status": "in_progress", "conclusion": None}],
                "in_progress/",
            ),
            (
                [],
                [{"name": "ci/gate", "status": "completed", "conclusion": "failure"}],
                "completed/failure",
            ),
        ]
        for statuses, runs, expected in cases:
            with (
                self.subTest(expected=expected, statuses=statuses, runs=runs),
                patch.object(workflow, "_github", side_effect=respond(statuses, runs)),
            ):
                assert (
                    workflow._required_context_state(self.repo, head, "ci/gate")
                    == expected
                )

    def test_prove_writes_result_and_posts_it_on_the_pr(self) -> None:
        self.change()
        result = self.prove()
        assert result["status"] == "pass", result
        path = workflow.result_path(self.repo, self.head)
        assert path.parent == workflow._common(self.repo) / "omp-prove"
        assert json.loads(path.read_text()) == result
        assert result["prComment"] == self.comment.return_value
        assert (result["head"], result["objective"], result["manifestSha256"]) == (
            self.head,
            "fix value",
            workflow._sha(self.manifest_path),
        )
        repo, head, posted = self.comment.call_args.args
        assert (repo, head, posted["status"]) == (
            self.repo.resolve(),
            self.head,
            "pass",
        )
        self.write("value", "regressed")
        self.head = self.commit("regression")
        failed = self.prove()
        self.assert_block(failed, "head run 1 failed")
        assert self.stored_status() == "fail", (
            "a failed prove also writes and posts its result"
        )
        assert self.comment.call_args.args[2]["status"] == "fail"

    def test_unpublished_pass_fails(self) -> None:
        self.change()
        self.comment.side_effect = workflow.WorkflowError(
            "no unique open PR at requested head"
        )
        result = self.prove()
        self.assert_block(
            result,
            "infrastructure: publishing prove result: no unique open PR at requested head",
        )
        assert self.stored_status() == "fail"

    def test_unwritable_result_never_leaves_a_stale_pass(self) -> None:
        self.change()
        assert self.prove()["status"] == "pass"
        writes = []
        original = workflow._atomic_json

        def fail_second(path: Path, value: object) -> Any:
            writes.append(path)
            if len(writes) == 2:
                raise OSError("disk full")
            return original(path, value)

        with patch.object(workflow, "_atomic_json", side_effect=fail_second):
            result = self.prove()
        self.assert_block(result, "infrastructure: publishing prove result: disk full")
        assert self.stored_status() is None

    def test_result_comment_fits_github_limit(self) -> None:
        result: dict[str, Any] = {
            "status": "pass",
            "head": "a" * 40,
            "blocking": [],
            "deterministic": {
                "proofs": {"P1": [{"location": "head", "output": "x" * 5000 + "END"}]}
            },
        }
        body = workflow._result_comment(result)
        assert body.startswith(workflow.RESULT_MARKER)
        assert "[TRUNCATED to last 1000 chars]" in body
        assert "END" in body
        assert (
            result["deterministic"]["proofs"]["P1"][0]["output"] == "x" * 5000 + "END"
        ), "the returned result stays complete"
        result["deterministic"]["proofs"] = {
            f"P{n}": [{"output": "y" * 5000}] * 3 for n in range(30)
        }
        assert len(workflow._result_comment(result)) <= workflow.COMMENT_LIMIT
        result["blocking"] = ["z" * workflow.COMMENT_LIMIT]
        assert "too large for a PR comment" in workflow._result_comment(result)

    def test_post_result_comment_targets_the_one_open_pr_at_head(self) -> None:
        self.comment_patch.stop()
        self.addCleanup(self.comment_patch.start)
        head = "a" * 40
        bodies = []

        def gh(repo: Path, *args: str) -> str:
            bodies.append(Path(args[-1]).read_text())
            return (
                "https://github.com/example-owner/example-repo/pull/7#issuecomment-9\n"
            )

        with (
            patch.object(
                workflow,
                "_github",
                return_value=[
                    {"number": 7, "headRefOid": head},
                    {"number": 8, "headRefOid": "b" * 40},
                ],
            ) as github,
            patch.object(workflow, "_gh", side_effect=gh) as text,
        ):
            url = workflow._post_result_comment(
                self.repo, head, {"status": "pass", "head": head}
            )
        assert (
            url == "https://github.com/example-owner/example-repo/pull/7#issuecomment-9"
        )
        github.assert_called_once_with(
            self.repo,
            "pr",
            "list",
            "--search",
            head,
            "--state",
            "open",
            "--json",
            "headRefOid,number",
        )
        assert text.call_args.args[1:4] == ("pr", "comment", "7")
        assert '"status": "pass"' in bodies[0]
        with (
            patch.object(workflow, "_github", return_value=[]),
            pytest.raises(workflow.WorkflowError, match="no unique open PR"),
        ):
            workflow._post_result_comment(
                self.repo, head, {"status": "pass", "head": head}
            )


class InjectedRemovalError(RuntimeError):
    pass


class SchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.path = Path(scratch.name) / "manifest.json"
        self.data: dict[str, Any] = {
            "schemaVersion": 1,
            "slug": "valid",
            "base": "a" * 40,
            "proofs": [
                {
                    "id": "P",
                    "criterion": "works",
                    "cmd": [sys.executable, "proof.py"],
                    "files": ["proof.py"],
                    "sha256": {"proof.py": "b" * 64},
                }
            ],
        }

    def load(self, data: dict[str, Any]) -> Any:
        self.path.write_text(json.dumps(data))
        return workflow.load_manifest(self.path, workflow._sha(self.path))

    def test_manifest_invalid_shapes(self) -> None:
        mutations = [
            ("slug", []),
            ("schemaVersion", True),
            ("base", None),
            ("setup", "shell"),
            ("revisions", {}),
            ("revisions", [{"id": [], "reason": "why", "oldSha256": "a" * 64}]),
            ("proofs", None),
            ("proofs", {}),
        ]
        for key, value in mutations:
            with (
                self.subTest(key=key, value=value),
                pytest.raises(workflow.WorkflowError),
            ):
                self.load(dict(self.data, **{key: value}))
        for key, value in (
            ("files", [["unhashable"]]),
            ("files", ["a", "a"]),
            ("cmd", "shell"),
            ("criterion", " "),
            ("sha256", []),
            ("sha256", {"proof.py": 3}),
            ("parent", "HEAD"),
        ):
            data = copy.deepcopy(self.data)
            data["proofs"][0][key] = value
            with (
                self.subTest(key=key, value=value),
                pytest.raises(workflow.WorkflowError),
            ):
                self.load(data)
        self.path.write_text("{")
        with pytest.raises(workflow.WorkflowError):
            workflow.load_manifest(self.path, workflow._sha(self.path))

    def test_zero_proofs_are_valid(self) -> None:
        data = dict(self.data, proofs=[])
        assert self.load(data) == data

    def test_unknown_proof_fields_are_refused_by_name(self) -> None:
        for key, value in (("kind", "defect"), ("expectRed", None), ("note", "x")):
            data = copy.deepcopy(self.data)
            data["proofs"][0][key] = value
            with (
                self.subTest(key=key),
                pytest.raises(
                    workflow.WorkflowError, match=f"P: unknown proof field {key}"
                ),
            ):
                self.load(data)

    def test_unknown_manifest_fields_are_refused_by_name(self) -> None:
        data = dict(self.data, unknownField=2)
        with pytest.raises(
            workflow.WorkflowError, match="unknown manifest field unknownField"
        ):
            self.load(data)

    def test_noncanonical_paths_and_tmp_independence(self) -> None:
        for name in (
            "./proof.py",
            "../proof.py",
            "a//b",
            "a/./b",
            "a/../b",
            "/proof.py",
            "a/",
            "C:proof",
            "a\\b",
            "a/.git/b",
            "a\0b",
            "",
        ):
            data = copy.deepcopy(self.data)
            data["proofs"][0].update(files=[name], sha256={name: "b" * 64})
            with self.subTest(name=name), pytest.raises(workflow.WorkflowError):
                self.load(data)
        with patch.object(
            workflow.tempfile,
            "gettempdir",
            side_effect=AssertionError("lexical validation touched host"),
        ):
            assert self.load(self.data) == self.data

    def test_gate_policy_uses_the_harness_gate_validation(self) -> None:
        # repo_record.gate_reason owns the schema (tests/test_repo_record.py); gate_policy accepts
        # exactly what it accepts and returns the fields freeze() and prove() consume.
        gate = {
            "schemaVersion": 2,
            "checks": {"commit": ["c"], "format": ["ruff", "format"]},
            "ownerOnlyPaths": [],
            "ci": {"requiredContext": "gate", "runner": "ci-gate"},
            "dynaflows": {"enabled": False},
        }
        assert workflow.gate_policy(gate) == {
            "format": ["ruff", "format"],
            "ci": {"requiredContext": "gate", "runner": "ci-gate"},
        }
        for invalid in (
            {k: v for k, v in gate.items() if k != "checks"},
            {**gate, "checks": {"ci": ["c"]}},
            {**gate, "unknownField": 3},
            {k: v for k, v in gate.items() if k != "ownerOnlyPaths"},
        ):
            with self.subTest(gate=invalid):
                assert workflow.gate_policy(invalid) is None

    def test_journey_flag_must_be_boolean(self) -> None:
        for value in (True, False):
            data = copy.deepcopy(self.data)
            data["proofs"][0]["journey"] = value
            assert self.load(data) == data
        data = copy.deepcopy(self.data)
        data["proofs"][0]["journey"] = "yes"
        with pytest.raises(workflow.WorkflowError, match="journey must be a boolean"):
            self.load(data)

    def test_harness_root_survives_a_later_kernel_load(self) -> None:
        loaded: dict[str, Any] = {"__name__": "kernel"}
        exec(compile(MODULE.read_text(), "<cell>", "exec"), loaded)
        with pytest.raises(loaded["WorkflowError"], match="without __file__"):
            loaded["_harness_root"]()
        # The eval kernel's %load sets __file__ in the shared namespace; a later %load replaces it.
        kernel: dict[str, Any] = {"__name__": "kernel", "__file__": str(MODULE)}
        exec(compile(MODULE.read_text(), str(MODULE), "exec"), kernel)
        kernel["__file__"] = "/elsewhere/other.py"
        assert kernel["_harness_root"]() == Path(__file__).resolve().parents[1]

    def test_workflow_code_holds_no_model_ids_or_hosts(self) -> None:
        text = MODULE.read_text()
        for pattern in (
            r"gpt-\d",
            r"claude-",
            r"sonnet",
            r"opus",
            r"openai-codex",
            r"\bnuc\b",
            r"omarchy",
            r"/Users/",
        ):
            assert re.search(pattern, text, re.IGNORECASE) is None, pattern

    def test_load_safe_no_bang_lines(self) -> None:
        assert re.search(r"^\s*!", MODULE.read_text(), re.MULTILINE) is None


class LabelTests(unittest.TestCase):
    def setUp(self) -> None:
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

    def answer(self, ident: str, label: str = "yes") -> dict[str, str]:
        return {"id": ident, "label": label, "evidence": "quote", "rationale": "reason"}

    def label(self, results: list[Any]) -> Any:
        self.wait.return_value = results
        result = workflow.blind_label([self.path], "rubric", ["yes", "no"])
        json.dumps(result)
        assert sorted(r["id"] for r in result["agreed"] + result["disputed"]) == [
            "a",
            "b",
        ]
        return result

    def test_readonly_selectors_and_json_lists(self) -> None:
        answer = {"cases": [self.answer("a"), self.answer("b")]}
        result = self.label([answer, answer])
        assert len(result["agreed"]) == 2
        assert result["failed"] == []
        assert {c.kwargs["agent"] for c in self.agent.call_args_list} == {
            "scout",
            "reviewer",
        }
        assert "Read only those two files" in self.agent.call_args.args[0]

    def test_disputes_missing_duplicates_and_unknown(self) -> None:
        first = {"cases": [self.answer("a"), self.answer("b")]}
        for second in (
            {"cases": [self.answer("a", "no")]},
            {"cases": [self.answer("a"), self.answer("a"), self.answer("b")]},
            {"cases": [self.answer("unknown")]},
            None,
            RuntimeError("failed"),
            {"cases": [self.answer("a", "invalid")]},
        ):
            result = self.label([first, second])
            assert result["disputed"]
            if (
                not isinstance(second, dict)
                or second.get("cases", [{}])[0].get("id") == "unknown"
            ):
                assert result["failed"]

    def test_inputs_validated_before_spawn(self) -> None:
        for cases in (None, [{"id": ""}], [{"id": "a"}, {"id": "a"}], [{"id": 1}]):
            self.path.write_text(json.dumps(cases))
            with pytest.raises(workflow.WorkflowError):
                workflow.blind_label([self.path], "rubric", ["yes"])
        for selectors in (
            ("scout", "task"),
            ("scout", "scout"),
            ("designer", "reviewer"),
        ):
            with pytest.raises(workflow.WorkflowError):
                workflow.blind_label([], "rubric", ["yes"], selectors)
        with pytest.raises(workflow.WorkflowError):
            workflow.blind_label([self.path] * 9, "rubric", ["yes"])
        self.agent.assert_not_called()


CATALOGUE = Path(__file__).resolve().parents[1] / (
    "agent/extensions/dynaflows/engine/guardrails/data/guardrail-rules.json"
)
ADVISORY = (
    "SECURITY.dependency-audit lock=uv.lock ecosystem=PyPI package=PyYAML version=5.3.1 "
    "advisory=GHSA-8q59-q68h-6hv4 aliases=CVE-2020-14343,PYSEC-2021-142 fix=5.4"
)
NO_FIX = (
    "SECURITY.dependency-audit lock=web/package-lock.json ecosystem=npm package=node-serialize version=0.0.4 "
    "advisory=GHSA-q4v7-4rhw-9hqm aliases= fix="
)
BLOCKED_FIX = {
    "blocked_by": "scanner@4.0",
    "constraint": "scanner==4.0 requires PyYAML<5.4",
    "constraint_source": "https://example.org/scanner/4.0/metadata",
}


def evidence(
    scope: str = "runtime",
    hits: bool = True,
    fix_options: str = "",
    blocked_fix: object = None,
) -> dict[str, Any]:
    return {
        "advisory_text": "arbitrary code execution through full_load",
        "advisory_source": "https://osv.dev/x",
        "scope": scope,
        "scope_source": "pyproject.toml:12",
        "fix_options": fix_options,
        "blocked_fix": blocked_fix,
        "code_search": {
            "queries": ["yaml.full_load"],
            "hits": [{"location": "api/imports.py:88", "line": "yaml.full_load(body)"}]
            if hits
            else [],
        },
    }


# Judge answers for local_only, needs_privileges, needs_interaction and reached unless a case overrides one.
DEFAULT_PRECONDITIONS = {
    "local": 0.02,
    "privileges": 0.02,
    "interaction": 0.2,
    "reached": 0.95,
}


def answers(
    impact: str = "rce", spread: dict[str, float] | None = None, **overrides: float
) -> dict[str, Any]:
    unknown = set(overrides) - set(DEFAULT_PRECONDITIONS)
    if unknown:
        message = f"unknown answer overrides: {sorted(unknown)}"
        raise TypeError(message)
    values = {**DEFAULT_PRECONDITIONS, **overrides}
    probabilities = dict.fromkeys(workflow.IMPACT_LEVEL, 0.0)
    probabilities.update(spread or {impact: 1.0})
    return {
        "impact": {
            "type": "choice",
            "choice": impact,
            "probabilities": probabilities,
            "confidence": 0.9,
        },
        "local_only": {"type": "bool", "bool": values["local"]},
        "needs_privileges": {"type": "bool", "bool": values["privileges"]},
        "needs_interaction": {"type": "bool", "bool": values["interaction"]},
        "reached": {"type": "bool", "bool": values["reached"]},
    }


class DependencyAuditParsingTests(unittest.TestCase):
    def test_advisory_and_failure_lines_parse_by_the_catalogue_contract(self) -> None:
        output = "\n".join([
            "make: entering gate",
            ADVISORY,
            NO_FIX,
            ADVISORY,
            "SECURITY.dependency-audit failure=manifest-without-lock path=tools/package.json reason=dependencies but no lock",
            "test_security.py::test_dependency PASSED mentions SECURITY.dependency-audit mid-line",
        ])
        found = workflow.parse_dependency_audit(output)
        assert found["advisories"] == [
            {
                "lock": "uv.lock",
                "ecosystem": "PyPI",
                "package": "PyYAML",
                "version": "5.3.1",
                "advisory": "GHSA-8q59-q68h-6hv4",
                "aliases": ["CVE-2020-14343", "PYSEC-2021-142"],
                "fix": ["5.4"],
            },
            {
                "lock": "web/package-lock.json",
                "ecosystem": "npm",
                "package": "node-serialize",
                "version": "0.0.4",
                "advisory": "GHSA-q4v7-4rhw-9hqm",
                "aliases": [],
                "fix": [],
            },
        ]
        assert found["failures"] == [
            {
                "kind": "manifest-without-lock",
                "path": "tools/package.json",
                "reason": "dependencies but no lock",
            }
        ]
        assert (
            workflow.advisory_key(found["advisories"][0])
            == "uv.lock:PyYAML@5.3.1:GHSA-8q59-q68h-6hv4"
        )

    def test_off_contract_rule_lines_and_truncated_output_fail_closed(self) -> None:
        reordered = ADVISORY.replace(
            "lock=uv.lock ecosystem=PyPI", "ecosystem=PyPI lock=uv.lock"
        )
        for line in (
            reordered,
            ADVISORY.replace(" fix=5.4", ""),
            ADVISORY + " extra=1",
            ADVISORY.replace(
                "aliases=CVE-2020-14343,PYSEC-2021-142", "aliases=CVE-2020-14343,,X"
            ),
            ADVISORY.replace("version=5.3.1", "version="),
            "SECURITY.dependency-audit",
            "SECURITY.dependency-audit PyYAML 5.3.1 has GHSA-8q59-q68h-6hv4",
            "SECURITY.dependency-audit failure=other path=a reason=b",
            "SECURITY.dependency-audit failure=auditor path=a reason=",
        ):
            with (
                self.subTest(line=line),
                pytest.raises(
                    workflow.WorkflowError, match="SECURITY.dependency-audit"
                ),
            ):
                workflow.parse_dependency_audit(line)
        with pytest.raises(workflow.WorkflowError, match="truncated"):
            workflow.parse_dependency_audit("…" + ADVISORY)

    def test_the_parser_matches_the_catalogue_contract(self) -> None:
        rows = json.loads(CATALOGUE.read_text())["rules"]
        contract = next(
            rule for rule in rows if rule["id"] == workflow.DEPENDENCY_AUDIT
        )["contract"]
        assert list(workflow.ADVISORY_FIELDS) == contract["advisory_fields_in_order"]
        assert list(workflow.AUDIT_FAILURE_KINDS) == contract["failure_kinds"]
        example = re.sub(r"<[^>]+>", "x", contract["advisory_line"])
        assert len(workflow.parse_dependency_audit(example)["advisories"]) == 1


class SeverityRubricTests(unittest.TestCase):
    def setUp(self) -> None:
        self.decisions = workflow.severity_thresholds()
        self.advisory = workflow.parse_dependency_audit(ADVISORY)["advisories"][0]
        self.no_fix = workflow.parse_dependency_audit(NO_FIX)["advisories"][0]

    def rate(self, answer: Any, advisory: Any = None, **kwargs: Any) -> Any:
        return workflow.rate_advisory(
            advisory or self.advisory, evidence(**kwargs), answer, self.decisions
        )

    def test_thresholds_belong_to_the_current_questions(self) -> None:
        assert json.loads((workflow._severity_dir() / "thresholds.json").read_text())[
            "version_key"
        ] == workflow.severity_version_key("typesafe/jev-1.14")
        assert set(self.decisions) == {"impact", *workflow.PRECONDITIONS, "reached"}
        thresholds = json.loads(
            (workflow._severity_dir() / "thresholds.json").read_text()
        )
        provenance = thresholds["score_provenance"]
        questions, state = workflow.severity_questions()
        source_questions = copy.deepcopy(questions)
        for ident in (*workflow.PRECONDITIONS, "reached"):
            source_questions[ident]["type"] = "noul"
        source_payload = json.dumps(
            {
                "questions": source_questions,
                "state": state,
                "model": thresholds["model"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        assert (
            hashlib.sha256(source_payload.encode()).hexdigest()
            == provenance["source_version_key"]
        )
        assert (
            hashlib.sha256(
                (workflow._severity_dir() / "scores.csv").read_bytes()
            ).hexdigest()
            == provenance["scores_sha256"]
        )

    def test_the_rubric_spans_every_level_and_both_exposures(self) -> None:
        cases = [
            (answers(), {}, "critical", "owner"),
            (answers(reached=0.1), {}, "high", "owner"),
            (answers(), {"scope": "dev"}, "high", "owner"),
            (answers(local=0.9, reached=0.1), {}, "medium", "fix"),
            (answers(impact="dos"), {}, "medium", "fix"),
            (answers(impact="dos", reached=0.1), {"scope": "dev"}, "low", "fix"),
            (
                answers(impact="info_leak", interaction=0.95, reached=0.05),
                {"scope": "dev"},
                "low",
                "fix",
            ),
        ]
        for answer, kwargs, severity, route in cases:
            for blocker in (None, BLOCKED_FIX):
                with self.subTest(severity=severity, kwargs=kwargs, blocker=blocker):
                    rated = self.rate(answer, **kwargs, blocked_fix=blocker)
                    expected_route = "owner" if blocker is not None else route
                    assert (rated["severity"], rated["route"]) == (
                        severity,
                        expected_route,
                    )
                    if blocker is not None:
                        assert "dated exception" in rated["suggested_fix"]
                        assert rated["dimensions"]["fix"]["blocked_fix"] == blocker

    def test_unclear_evidence_rounds_up(self) -> None:
        assert self.rate(answers(impact="unclear"))["severity"] == "critical"
        spread = self.rate(answers(spread={"dos": 0.6, "rce": 0.35, "info_leak": 0.05}))
        assert (spread["dimensions"]["impact"]["class"], spread["severity"]) == (
            "rce",
            "critical",
        )
        doubtful = self.rate(answers(local=0.6, reached=0.45))
        assert (
            doubtful["dimensions"]["preconditions"]["cleared"],
            doubtful["severity"],
        ) == ([], "critical")

    def test_an_advisory_with_no_fix_always_goes_to_the_owner(self) -> None:
        rated = self.rate(
            answers(impact="dos", reached=0.1),
            advisory=self.no_fix,
            scope="dev",
            fix_options="replace node-serialize with JSON.parse",
        )
        assert (rated["severity"], rated["route"]) == ("low", "owner")
        assert "dated exception" in rated["reasons"][0]
        assert rated["suggested_fix"] == "replace node-serialize with JSON.parse"
        with pytest.raises(workflow.WorkflowError, match="fix_options"):
            self.rate(answers(), advisory=self.no_fix)

    def test_invalid_judgment_answers_or_evidence_raise(self) -> None:
        broken = answers()
        broken["reached"] = {"type": "bool", "bool": "yes"}
        for answer in (
            broken,
            {},
            None,
            {**answers(), "impact": {"probabilities": {"rce": 1.0}}},
        ):
            with self.subTest(answer=answer), pytest.raises(workflow.WorkflowError):
                self.rate(answer)
        bad = evidence()
        bad["scope"] = "test"
        with pytest.raises(workflow.WorkflowError, match="scope"):
            workflow.rate_advisory(self.advisory, bad, answers(), self.decisions)
        bad = {**evidence(), "extra": 1}
        with pytest.raises(workflow.WorkflowError, match="evidence needs"):
            workflow.rate_advisory(self.advisory, bad, answers(), self.decisions)
        for invalid in (
            {},
            {**BLOCKED_FIX, "constraint": ""},
            {**BLOCKED_FIX, "blocked_by": "scanner"},
            {**BLOCKED_FIX, "constraint_source": "http://example.org"},
            {**BLOCKED_FIX, "unexpected": "value"},
        ):
            with self.subTest(blocker=invalid), pytest.raises(workflow.WorkflowError):
                self.rate(answers(), blocked_fix=invalid)
        with pytest.raises(workflow.WorkflowError):
            self.rate(
                answers(),
                advisory=self.no_fix,
                blocked_fix=BLOCKED_FIX,
                fix_options="replace it",
            )


MALICIOUS = (
    "SECURITY.dependency-audit lock=package-lock.json ecosystem=npm package=eslint-scope version=3.7.2 "
    "advisory=GHSA-hxxf-q3w9-4xgw aliases=MAL-GHSA-hxxf-q3w9-4xgw fix=3.7.3"
)


class MaliciousPackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.decisions = workflow.severity_thresholds()

    def advisory(self, line: str) -> Any:
        return workflow.parse_dependency_audit(line)["advisories"][0]

    def test_a_mal_id_or_alias_marks_a_malicious_package(self) -> None:
        assert workflow.malicious_package(self.advisory(MALICIOUS))
        own = MALICIOUS.replace(
            "advisory=GHSA-hxxf-q3w9-4xgw aliases=MAL-GHSA-hxxf-q3w9-4xgw",
            "advisory=MAL-2025-1 aliases=",
        )
        assert workflow.malicious_package(self.advisory(own))
        assert not workflow.malicious_package(self.advisory(ADVISORY))
        assert not workflow.malicious_package(
            self.advisory(MALICIOUS.replace("MAL-GHSA", "GHSA-MAL"))
        )

    def test_malicious_is_critical_for_the_owner_whatever_scope_reach_or_fix(
        self,
    ) -> None:
        mild = answers(impact="info_leak", local=0.9, reached=0.01)
        for line, kwargs in (
            (MALICIOUS, {"scope": "dev", "hits": False}),
            (
                MALICIOUS.replace(" fix=3.7.3", " fix="),
                {"scope": "dev", "hits": False, "fix_options": "drop it"},
            ),
            (MALICIOUS, {"scope": "runtime"}),
        ):
            with self.subTest(line=line, kwargs=kwargs):
                rated = workflow.rate_advisory(
                    self.advisory(line), evidence(**kwargs), mild, self.decisions
                )
                assert (rated["severity"], rated["route"]) == ("critical", "owner")
                assert "malicious package" in rated["reasons"][0]
                assert rated["suggested_fix"].startswith(
                    "remove eslint-scope or replace it"
                )
                assert "rotate every credential" in rated["suggested_fix"]

    def test_the_judgment_is_never_asked_for_a_malicious_package(self) -> None:
        async def judge(state: object, questions: object) -> Never:
            raise AssertionError("judge() must not run for a malicious package")

        import asyncio

        found = workflow.parse_dependency_audit(MALICIOUS)
        key = workflow.advisory_key(found["advisories"][0])
        with patch.object(workflow, "judge", judge, create=True):
            result = asyncio.run(
                workflow.triage_advisories(
                    found, {key: evidence(scope="dev", hits=False)}
                )
            )
        assert result["halt"]
        assert [item["severity"] for item in result["owner"]] == ["critical"]
        for text in (
            "Severity: critical",
            "malicious package",
            "rotate every credential",
            "not judged",
        ):
            assert text in result["report"]

    def test_the_contract_states_the_malicious_mark(self) -> None:
        rows = json.loads(CATALOGUE.read_text())["rules"]
        contract = next(
            rule for rule in rows if rule["id"] == workflow.DEPENDENCY_AUDIT
        )["contract"]
        assert workflow.MALICIOUS_PREFIX in contract["malicious_package_mark"]


class TriageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.found = workflow.parse_dependency_audit(
            "\n".join([
                ADVISORY,
                ADVISORY.replace(
                    "PyYAML version=5.3.1", "black version=23.12.1"
                ).replace("GHSA-8q59-q68h-6hv4", "PYSEC-B"),
                "SECURITY.dependency-audit failure=auditor path=uv.lock reason=auditor exited 2",
            ])
        )
        self.keys = [workflow.advisory_key(a) for a in self.found["advisories"]]
        self.calls: list[Any] = []

    def run_triage(self, replies: dict[str, Any], evidence_map: dict[str, Any]) -> Any:
        async def judge(state: Any, questions: dict[str, Any]) -> Any:
            # Exercise the real frozen questions at the judge() boundary, not just the rubric.
            # Contract: xd://eval/judge; TypeSafe's wire name 'noul' is not accepted here.
            frozen = json.loads(
                (workflow._severity_dir() / "questions.json").read_text()
            )["questions"]
            assert questions == frozen
            for ident, question in questions.items():
                assert question["type"] in ("choice", "bool", "score"), ident
                assert isinstance(question["instructions"], str), ident
                assert question["instructions"].strip(), ident
                if question["type"] == "choice":
                    assert isinstance(question["criteria"], dict), ident
                    assert len(question["criteria"]) >= 2, ident
                elif question["type"] == "score":
                    assert isinstance(question["criteria"], list), ident
                    assert len(question["criteria"]) >= 2, ident
                elif "criteria" in question:
                    assert isinstance(question["criteria"], dict), ident
                    assert set(question["criteria"]) <= {"true", "false"}, ident
            self.calls.append((state, questions))
            reply = replies[state["advisory"]["package"]]
            if isinstance(reply, Exception):
                raise reply
            return reply

        import asyncio

        with patch.object(workflow, "judge", judge, create=True):
            return asyncio.run(workflow.triage_advisories(self.found, evidence_map))

    def test_high_severity_halts_with_an_owner_report_and_low_is_fixed_in_flow(
        self,
    ) -> None:
        result = self.run_triage(
            {"PyYAML": answers(), "black": answers(impact="dos", reached=0.02)},
            {self.keys[0]: evidence(), self.keys[1]: evidence(scope="dev", hits=False)},
        )
        assert result["halt"]
        assert [item["key"] for item in result["owner"]] == [self.keys[0]]
        assert [(item["key"], item["severity"]) for item in result["fix"]] == [
            (self.keys[1], "low")
        ]
        assert result["failures"] == [
            {"kind": "auditor", "path": "uv.lock", "reason": "auditor exited 2"}
        ]
        for text in (
            "GHSA-8q59-q68h-6hv4",
            "CVE-2020-14343",
            "Severity: critical",
            "api/imports.py:88",
            "Suggested fix: upgrade PyYAML from 5.3.1 to a fixed version (5.4)",
            "pyproject.toml:12",
        ):
            assert text in result["report"]
        state, questions = self.calls[0]
        assert set(questions) == {"impact", *workflow.PRECONDITIONS, "reached"}
        assert state["advisory"]["text"] == "arbitrary code execution through full_load"

    def test_low_or_medium_routes_by_fix_availability(self) -> None:
        for answer, severity in (
            (answers(impact="dos", reached=0.02), "low"),
            (answers(impact="dos"), "medium"),
        ):
            for blocker in (None, BLOCKED_FIX):
                with self.subTest(severity=severity, blocker=blocker):
                    result = self.run_triage(
                        {"PyYAML": answer, "black": answer},
                        {
                            self.keys[0]: evidence(blocked_fix=blocker),
                            self.keys[1]: evidence(),
                        },
                    )
                    assert result["halt"] == (blocker is not None)
                    assert [item["key"] for item in result["owner"]] == (
                        [self.keys[0]] if blocker is not None else []
                    )
                    assert [item["key"] for item in result["fix"]] == (
                        [self.keys[1]] if blocker is not None else self.keys
                    )
                    assert all(
                        item["severity"] == severity
                        for item in result["owner"] + result["fix"]
                    )
                    if blocker is None:
                        assert result["report"] == ""
                    else:
                        for text in (
                            *blocker.values(),
                            "dated exception",
                            "Pinned dependent",
                        ):
                            assert text in result["report"]

    def test_exception_file_failures_halt_for_the_owner(self) -> None:
        self.found["failures"].append({
            "kind": "exception",
            "path": "scripts/checks/exceptions.json",
            "reason": "expired",
        })
        low = answers(impact="dos", reached=0.02)
        result = self.run_triage(
            {"PyYAML": low, "black": low}, {key: evidence() for key in self.keys}
        )
        assert result["halt"]
        assert (
            "Exception file failure at scripts/checks/exceptions.json: expired (owner-only path)"
            in result["report"]
        )
        assert result["owner"] == [], "the low advisories themselves stay in-flow fixes"

    def test_missing_evidence_or_a_failed_judgment_never_passes(self) -> None:
        with pytest.raises(workflow.WorkflowError, match="missing"):
            self.run_triage({}, {self.keys[0]: evidence()})
        with pytest.raises(workflow.WorkflowError, match="severity judgment failed"):
            self.run_triage(
                {"PyYAML": RuntimeError("judge offline"), "black": answers()},
                {key: evidence() for key in self.keys},
            )


class StructureReviewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory(prefix="omp-structure-test-")
        self.addCleanup(scratch.cleanup)
        self.repo = Path(scratch.name)
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "structure@example.test")
        git(self.repo, "config", "user.name", "Structure Test")
        (self.repo / "src").mkdir()
        (self.repo / "src/__init__.py").write_text("")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "--allow-empty", "-qm", "base")
        self.parent = git(self.repo, "rev-parse", "HEAD")
        (self.repo / "src/orders").mkdir()
        (self.repo / "src/orders/api.py").write_text("ORDER_LIMIT = 10\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "head")
        self.head = git(self.repo, "rev-parse", "HEAD")
        self.questions, self.thresholds = workflow._structure_config()
        self.states = workflow._structure_states(self.repo, self.parent, self.head)
        self.calls: list[Any] = []

    async def review(
        self,
        *,
        score: float = 0.9,
        error: Exception | None = None,
        post_error: Exception | None = None,
    ) -> Any:
        async def judge(state: Any, questions: dict[str, Any]) -> dict[str, Any]:
            self.calls.append((state, questions))
            if error:
                raise error
            return {ident: {"bool": score} for ident in questions}

        with (
            patch.object(
                workflow,
                "_structure_config",
                return_value=(self.questions, self.thresholds),
            ),
            patch.object(workflow, "_structure_states", return_value=self.states),
            patch.object(workflow, "judge", judge, create=True),
            patch.object(
                workflow,
                "_post_structure_comment",
                side_effect=post_error,
                return_value="https://example.test/comment",
            ) as post,
        ):
            result = await workflow.structure_review(
                self.repo, self.parent, self.head, target_ref=self.parent
            )
        post.assert_called_once()
        saved = json.loads(
            (
                workflow._common(self.repo) / "omp-structure" / f"{self.head}.json"
            ).read_text()
        )
        assert saved == result
        return result

    async def test_provisional_findings_never_block_or_change_prove_signal(
        self,
    ) -> None:
        signal = workflow.result_path(self.repo, self.head)
        signal.parent.mkdir()
        signal.write_text('{"status":"pass"}')
        result = await self.review()
        assert result["status"] == "shadow"
        assert not result["halt"]
        assert result["head"] == self.head
        assert len(result["findings"]) == 7
        assert len(result["units"]["src/orders"]["digest"]) == 64
        assert signal.read_text() == '{"status":"pass"}'
        assert set(self.calls[0][1]) == set(self.questions) - {
            "prefix_should_be_folder"
        }
        assert result["thresholds"] == self.thresholds
        assert "raw judgment probabilities" in workflow._structure_comment(result)

    async def test_ready_finding_sets_persisted_merge_halt(self) -> None:
        ident = "folder_name_coverage"
        self.thresholds["decisions"][ident]["status"] = "ready"
        result = await self.review(score=0.7)
        assert result["halt"]
        assert result["status"] == "blocked"
        assert result["head"] == self.head
        assert any(
            item["question"] == ident and item["status"] == "ready"
            for item in result["findings"]
        )
        below = await self.review(score=0.69)
        assert not below["halt"]

    async def test_prefix_question_only_sees_eligible_clusters(self) -> None:
        (self.repo / "src/orders/order_reader.py").write_text("ORDER_LIMIT = 10\n")
        (self.repo / "src/orders/order_writer.py").write_text("ORDER_LIMIT = 10\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "add order siblings")
        self.head = git(self.repo, "rev-parse", "HEAD")
        self.states = workflow._structure_states(self.repo, self.parent, self.head)
        eligible = self.states["src/orders"]["cluster"]
        result = await self.review()
        assert set(self.calls[0][1]) == set(self.questions)
        assert eligible
        assert all(cluster["eligible"] for cluster in eligible)
        assert self.calls[0][0]["unit_state"]["cluster"] == eligible
        assert len(result["findings"]) == 8

    async def test_judge_and_publication_errors_report_without_shadow_blocking(
        self,
    ) -> None:
        result = await self.review(
            error=RuntimeError("judge offline"),
            post_error=RuntimeError("comment offline"),
        )
        assert not result["halt"]
        assert result["status"] == "incomplete"
        assert not result["findings"]
        assert any("judge offline" in error for error in result["errors"])
        assert any("comment offline" in error for error in result["errors"])

    async def test_invalid_answer_is_incomplete_not_clean(self) -> None:
        result = await self.review(score=float("nan"))
        assert result["status"] == "incomplete"
        assert not result["halt"]
        assert not result["findings"]

    async def test_shared_question_sets_use_batch(self) -> None:
        from types import SimpleNamespace

        (self.repo / "src/billing").mkdir()
        (self.repo / "src/billing/api.py").write_text("BILLING_LIMIT = 10\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "add billing unit")
        self.head = git(self.repo, "rev-parse", "HEAD")
        self.states = workflow._structure_states(self.repo, self.parent, self.head)
        calls: list[Any] = []
        states, questions = self.states, self.questions

        class Batch:
            closed = False

            async def drain_iter(self) -> AsyncIterator[tuple[Any, SimpleNamespace]]:
                for unit in states:
                    yield (
                        unit,
                        SimpleNamespace(
                            ok=True,
                            answers={
                                ident: {"bool": 0.9}
                                for ident in questions
                                if ident != "prefix_should_be_folder"
                            },
                        ),
                    )

            def close(self) -> None:
                self.closed = True

        batch = Batch()

        def judge_batch(states: Any, questions: Any, **kwargs: Any) -> Batch:
            calls.append((states, questions))
            return batch

        with patch.object(workflow, "judge_batch", judge_batch, create=True):
            result = await self.review()
        assert len(calls) == 1
        assert len(calls[0][0]) == 2
        assert len(result["findings"]) == 14
        assert self.calls == []
        assert batch.closed

    def test_extractor_uses_isolated_harness_cli_and_exact_commit_range(self) -> None:
        with patch.object(
            workflow, "run_check", return_value=(True, json.dumps(self.states))
        ) as run:
            assert (
                workflow._structure_states(self.repo, self.parent, self.head)
                == self.states
            )
        argv = run.call_args.args[0]
        assert argv[:3] == [sys.executable, "-I", "-S"]
        assert argv[-7:] == [
            "structure-state",
            "--repo",
            str(self.repo),
            "--base",
            self.parent,
            "--head",
            self.head,
        ]
        assert run.call_args.kwargs["stdout_only"]

    def test_extractor_parses_real_structure_state_stdout_without_status_line(
        self,
    ) -> None:
        source = self.repo / "src/orders/api.py"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text('"""Order API."""\nORDER_LIMIT = 10\n')
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "add order unit")
        head = git(self.repo, "rev-parse", "HEAD")

        states = workflow._structure_states(self.repo, self.parent, head)

        assert set(states) == {"src/orders"}
        assert states["src/orders"]["folder_path"] == "src/orders"
        assert states["src/orders"]["files"] == ["src/orders/api.py"]
        assert states["src/orders"]["evidence_limits"]["base"] == self.parent
        assert states["src/orders"]["evidence_limits"]["head"] == head

    def test_shadow_comment_updates_existing_marker_instead_of_posting_again(
        self,
    ) -> None:
        with (
            patch.object(workflow, "_pr_at_head", return_value={"number": 12}),
            patch.object(workflow, "_github_repo", return_value="owner/repo"),
            patch.object(
                workflow,
                "_github",
                side_effect=[
                    [{"id": 7, "body": workflow.STRUCTURE_MARKER}],
                    {"html_url": "https://example.test/comment"},
                ],
            ) as github,
        ):
            result = {"head": self.head, "halt": False, "findings": [], "errors": []}
            assert (
                workflow._post_structure_comment(self.repo, self.head, result)
                == "https://example.test/comment"
            )
        assert "repos/owner/repo/issues/comments/7" in github.call_args.args
        assert "PATCH" in github.call_args.args


if __name__ == "__main__":
    unittest.main()
