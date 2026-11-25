import contextlib
import hashlib
import io
import json
import os
import re
import runpy
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from test_agentrails_fixture import approval, config, fixture, write_config
from test_reference_decision_store import (
    no_unclosed_connections as no_unclosed_connections,
)
from test_workflows import workflow

from guardrails.host.process import run_text

# A sqlite3 connection left open fails the test where it is freed, and the guard fixture finds the rest.
pytestmark = [
    pytest.mark.seam,
    pytest.mark.filterwarnings("error::ResourceWarning"),
    pytest.mark.filterwarnings("error::pytest.PytestUnraisableExceptionWarning"),
]


@pytest.fixture(autouse=True)
def isolated_decision_store(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every test writes the decision store under a temporary agent dir, never the real one."""
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path_factory.mktemp("agent")))


ORIGIN = "https://github.com/Example-Owner/Example-Repo.git"


RED_AT_BASE = "from pathlib import Path\nassert Path('value').read_text() == 'fixed', 'expected fixed'\n"


MAP_PATH = "docs/features"


MAPPED = [
    "proof.py",
    "-proof",
    "second.py",
    "later.py",
    "journey.py",
    *(f"proof{n}.py" for n in range(17)),
]


def map_doc(*targets: str, title: str = "Value") -> str:
    links = "\n".join(f"- [{target}]({target})" for target in targets) or "- none yet"
    return (
        f"# {title}\n\n## Sub-features\n\n- value\n\n## How to get to it (user POV)\n\n- read value\n\n"
        f"## Driving it with lever\n\n{links}\n\n## Gotchas\n\n- none\n"
    )


def git(repo: Path, *args: str) -> str:
    return run_text(
        ["git", "-C", str(repo), *args], timeout=60, check=True
    ).stdout.strip()


class RepositoryCase(unittest.TestCase):
    def setUp(self) -> None:
        environment = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        env = patch.dict(os.environ, environment, clear=True)
        env.start()
        self.addCleanup(env.stop)
        scratch = tempfile.TemporaryDirectory(prefix="omp-proof-test-")
        self.addCleanup(scratch.cleanup)
        self.tmp = Path(scratch.name)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "proof@example.test")
        git(self.repo, "config", "user.name", "Proof Test")
        git(self.repo, "remote", "add", "origin", ORIGIN)
        self.sections: dict[str, Any] = {
            "commands": {"commit": ["false"], "ci": ["false"]},
            "ci": {"enabled": False},
        }
        self.profile(None)
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

    def write_config(self) -> None:
        """The repo's .agentrails/config.json: the fixture config with this case's sections."""
        write_config(self.repo, config(**self.sections))

    def commit(self, message: str) -> str:
        git(self.repo, "add", ".")
        git(self.repo, "commit", "--allow-empty", "-qm", message)
        return git(self.repo, "rev-parse", "HEAD")

    def assert_cleaned(self) -> None:
        assert git(self.repo, "worktree", "list", "--porcelain").count("worktree ") == 1

    def proof(
        self, filename: str = "proof.py", code: str | None = None, ident: str = "P1"
    ) -> dict[str, Any]:
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

    def gate(self, **commands: list[str]) -> None:
        self.sections["commands"] = {"commit": ["false"], "ci": ["false"], **commands}
        self.sections["ci"] = {
            "enabled": True,
            "runner": "ci-gate",
            "requiredContext": "gate",
        }
        self.write_config()

    def rebase_parent(self, message: str = "parent fixtures") -> None:
        self.parent = self.commit(message)

    def profile(self, marker: str | None, **surface: str | None) -> None:
        values = {
            "lever_command": "lever",
            "doctor_marker": marker,
            "feature_map_path": f"{MAP_PATH}/",
            **surface,
        }
        self.sections.update({
            "tests": {
                "verification_surface": {
                    k: v for k, v in values.items() if v is not None
                }
            },
            "structure": {"owners": {"patterns": {"**": "proof engine"}}},
            "checkers": {
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
        self.write_config()


class ProofRunTests(RepositoryCase):
    def test_freeze_writes_only_the_slug_manifest_in_the_common_dir_but_reads_temporary_previous(
        self,
    ) -> None:
        proof = self.proof()
        durable = self.manifest_path
        for elsewhere in (
            Path(tempfile.gettempdir()) / "manifest.json",
            Path("/tmp") / "manifest.json",
            self.repo / "manifest.json",
            durable.with_name("other.json"),
        ):
            with self.subTest(elsewhere=str(elsewhere)):
                self.manifest_path = elsewhere
                with pytest.raises(
                    workflow.WorkflowError,
                    match=re.escape("<git-common-dir>/omp-manifests/<slug>.json"),
                ):
                    self.freeze([proof])
                assert not elsewhere.exists()
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
        self.gate(commit=["true"])
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
            assert result["decisionLogErrors"] == []
            store = (
                Path(os.environ["PI_CODING_AGENT_DIR"]) / "agent-rails/agent-rails.db"
            )
            with closing(sqlite3.connect(store)) as connection:
                logged = connection.execute(
                    "SELECT DISTINCT question, outcome, trigger, repo, commit_sha "
                    "FROM decisions WHERE source = 'check' ORDER BY question"
                ).fetchall()
            assert logged == [
                (checker, "pass", "prove", "example-owner/example-repo", self.head)
                for checker in (
                    "path-check",
                    "prefix-folder",
                    "rename-residue",
                    "responsibility-overlap",
                    "structure",
                )
            ], "each prove checker logs its rule results as passes"
            with closing(sqlite3.connect(store)) as connection:
                rules = {
                    row[0]
                    for row in connection.execute(
                        "SELECT judgment FROM decisions WHERE source = 'check'"
                    )
                }
            assert {
                "STRUCT.prefix-folder",
                "STRUCT.overlapping-responsibilities",
                "STRUCT.runtime-data-placement",
                "STRUCT.entry-point-placement",
            } <= rules, "the deterministic structure checks log a row per rule"
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
        assert len(counter.read_text().splitlines()) == 1
        data = self.change(proofs=proofs)
        data["setup"] = setup
        self.save(data)
        assert self.prove()["status"] == "pass"
        trees = counter.read_text().splitlines()
        assert len(trees) == 2
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
            if calls == 3:
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
    def test_invalid_head_config_precedes_tree_creation_and_setup(self) -> None:
        path = self.repo / ".agentrails/config.json"
        valid = json.loads(path.read_text())
        for text in (
            "null",
            "{",
            json.dumps({**valid, "schemaVersion": 3}),
            json.dumps({**valid, "commands": {"commit": []}}),
            json.dumps({**valid, "commands": {}}),
            json.dumps({**valid, "unknownField": 3}),
            json.dumps({**valid, "agentrails": {"mode": "structured"}}),
        ):
            path.write_text(text)
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
                self.repo,
                self.manifest_path.with_name("different.json"),
                [proof],
                "different",
                previous=previous,
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


class ConfigGateTests(RepositoryCase):
    def config_file(self) -> Path:
        return self.repo / ".agentrails/config.json"

    def test_prove_without_a_config_routes_to_onboarding(self) -> None:
        self.config_file().unlink()
        self.change()
        with patch.object(
            workflow, "_setup", side_effect=AssertionError("trees must not be built")
        ):
            result = self.prove()
        self.assert_block(result, workflow.GUARDRAIL_PREREQUISITE)
        self.assert_block(result, "is not onboarded (.agentrails/config.json missing)")

    def test_prove_requires_a_commit_command(self) -> None:
        self.change()
        self.sections["commands"] = {"ci": ["false"]}
        self.write_config()
        self.assert_block(
            self.prove(), "is not onboarded (.agentrails/config.json invalid)"
        )
        self.sections["commands"] = {"commit": ["true"]}
        self.write_config()
        assert self.prove()["status"] == "pass"

    def test_prove_without_origin_is_outside_the_rails(self) -> None:
        git(self.repo, "remote", "remove", "origin")
        self.change()
        self.assert_block(
            self.prove(), "is not onboarded (.agentrails/config.json outside)"
        )

    def test_linked_worktree_reads_the_main_checkout_config(self) -> None:
        linked = self.tmp / "linked"
        git(self.repo, "worktree", "add", "-qb", "linked", str(linked))
        try:
            assert workflow.read_config(linked)["commands"]["commit"] == ["false"]
        finally:
            git(self.repo, "worktree", "remove", "--force", str(linked))

    def test_an_unreadable_or_invalid_config_refuses_freeze(self) -> None:
        valid = self.config_file().read_text()
        for text in ("{", json.dumps({**json.loads(valid), "unknownField": 3})):
            with self.subTest(text=text[:20]):
                self.manifest_path.unlink(missing_ok=True)
                self.config_file().write_text(text)
                with pytest.raises(
                    workflow.WorkflowError,
                    match=re.escape("(.agentrails/config.json invalid)"),
                ):
                    workflow.read_config(self.repo)
                with pytest.raises(workflow.WorkflowError, match="not onboarded"):
                    self.freeze([self.proof()])
                assert not self.manifest_path.exists()

    def test_freeze_without_a_config_refuses_and_has_no_setup_escape(self) -> None:
        self.config_file().unlink()
        with pytest.raises(workflow.WorkflowError) as raised:
            self.freeze([self.proof()])
        assert "is not onboarded (.agentrails/config.json missing)" in str(raised.value)
        assert not self.manifest_path.exists()
        with pytest.raises(TypeError, match="project_setup"):
            self.freeze([self.proof()], project_setup=True)

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

    def test_freeze_skips_the_doctor_check_without_the_marker_in_the_output(
        self,
    ) -> None:
        doctor = "from pathlib import Path\nprint('DOCTOR:')\n" + RED_AT_BASE
        self.profile("DOCTOR FAILED")
        self.rebase_parent("profile marker")
        path, _ = self.freeze([self.proof(code=doctor)], base=self.parent)
        assert path.exists()
        path.unlink()
        self.profile("<doctor failure marker>")
        with pytest.raises(workflow.WorkflowError, match="config.json invalid"):
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
        self.sections["tests"] = {"verification_surface": {"none": True}}
        self.sections["approvals"] = [
            *fixture()["approvals"],
            approval("exemption", "verification_surface", "library"),
        ]
        self.write_config()
        with pytest.raises(
            workflow.WorkflowError, match=re.escape(workflow.FEATURE_MAP_SETUP)
        ):
            self.freeze([self.proof()])
        assert not self.manifest_path.exists()
        del self.sections["approvals"]
        self.profile(None, feature_map_path="<feature map path>")
        with pytest.raises(workflow.WorkflowError, match="config.json invalid"):
            self.freeze([self.proof()])
        self.profile(None, feature_map_path="docs/missing")
        with pytest.raises(
            workflow.WorkflowError, match="feature map docs/missing .* does not exist"
        ):
            self.freeze([self.proof()])
        assert not self.manifest_path.exists()
        self.profile(None)
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
        self.sections["checkers"]["feature_map"] = {"index_files": ["README.md"]}
        self.write_config()
        self.change()
        result = self.prove()
        assert result["status"] == "pass", result
        self.sections["checkers"].pop("feature_map")
        self.write_config()
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

    def test_unexpected_bug_publishes_a_fail_result_then_propagates(self) -> None:
        self.gate()
        path, digest = self.freeze([], base=self.parent)
        self.write("docs.md", "Documentation-only change.\n")
        self.head = self.commit("docs")
        with (
            patch.object(workflow, "load_manifest", side_effect=AttributeError("bug")),
            pytest.raises(AttributeError, match="bug"),
        ):
            workflow.prove(
                self.repo,
                self.parent,
                self.head,
                (path, digest),
                "update docs",
                target_ref=self.parent,
            )
        assert self.stored_status() == "fail"
        posted = self.comment.call_args.args[2]
        assert posted["status"] == "fail"
        assert "unexpected AttributeError: bug" in posted["blocking"]

    def test_precheck_launches_without_site_or_repo_imports(self) -> None:
        self.gate()
        path, digest = self.freeze([], base=self.parent)
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


DECIDE = (
    Path(__file__).resolve().parents[1] / "agent/extensions/agent-rails/lib/decide.py"
)


class JudgmentConfigTests(unittest.TestCase):
    """The repo config a cwd resolves to (judgment_config) and the rail entry, lib/decide.py,
    which runs the registry bridge under it."""

    def setUp(self) -> None:
        environment = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith("GIT_") and k != "TYPESAFE_BASE_URL"
        }
        environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        env = patch.dict(os.environ, environment, clear=True)
        env.start()
        self.addCleanup(env.stop)
        scratch = tempfile.TemporaryDirectory(prefix="omp-judgment-config-")
        self.addCleanup(scratch.cleanup)
        self.tmp = Path(scratch.name).resolve()
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "judgment@example.test")
        git(self.repo, "config", "user.name", "Judgment Test")
        git(self.repo, "remote", "add", "origin", ORIGIN)
        git(self.repo, "commit", "--allow-empty", "-qm", "base")
        self.main = runpy.run_path(str(DECIDE))["main"]

    def bridge(self, cwd: Path, *argv: str) -> tuple[int, Any]:
        out = io.StringIO()
        with contextlib.chdir(cwd), patch("sys.stdout", out):
            code = self.main(list(argv))
        return code, json.loads(out.getvalue())

    def store(self, sql: str) -> list[dict[str, Any]]:
        path = Path(os.environ["PI_CODING_AGENT_DIR"]) / "agent-rails/agent-rails.db"
        with closing(sqlite3.connect(path)) as connection:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute(sql)]

    def test_rail_entry_runs_the_registry_of_the_shipped_judgments(self) -> None:
        for name, question in (
            ("extension-impact", "impact"),
            ("in-scope", "ask_first"),
            ("implementation-integrity", "weakens_verification"),
            ("completion-evidence", "evidence_gap"),
        ):
            with self.subTest(name=name):
                code, asked = self.bridge(self.tmp, "questions", name)
                assert code == 0
                assert asked["model"] == "typesafe/jev-1.14.0"
                assert list(asked["questions"]) == [question]
                thresholds = json.loads(
                    (
                        DECIDE.parents[1] / "judgments" / name / "thresholds.json"
                    ).read_text()
                )
                assert thresholds["wired"] is True
                assert "mode" not in thresholds
                assert "status" not in thresholds
                wiring = thresholds.get("wiring") or thresholds["framing"]["wiring"]
                assert "enforced from the start" in wiring
                assert "not wired" not in json.dumps(thresholds)

    def test_an_unconfigured_cwd_uses_global_modes(self) -> None:
        bare = self.tmp / "bare.git"
        git(self.tmp, "init", "-q", "--bare", str(bare))
        for cwd in (self.tmp, self.repo, bare):
            with self.subTest(cwd=cwd.name):
                assert workflow.judgment_config(cwd) is None
                code, asked = self.bridge(cwd, "questions", "implementation-integrity")
                assert (code, list(asked["questions"])) == (0, ["weakens_verification"])

    def test_a_worktree_cwd_resolves_the_main_checkout_overrides(self) -> None:
        overrides = {
            "implementation-integrity/weakens_verification": {"enabled": False},
            "completion-evidence/evidence_gap": {"enabled": True, "mode": "shadow"},
        }
        write_config(self.repo, config(judgments=overrides))
        linked = self.tmp / "linked"
        git(self.repo, "worktree", "add", "-qb", "linked", str(linked))
        self.addCleanup(git, self.repo, "worktree", "remove", "--force", str(linked))
        resolved = workflow.judgment_config(linked)
        assert resolved is not None
        assert resolved["judgments"] == overrides
        assert self.bridge(linked, "questions", "implementation-integrity") == (
            0,
            {"judgment": "implementation-integrity", "off": True},
        )
        request = self.tmp / "request.json"
        request.write_text(
            json.dumps({
                "state": {"work_request": {"title": "x"}},
                "answers": {"evidence_gap": {"type": "bool", "bool": 0.95}},
                "model": workflow.load_judgment("completion-evidence").model,
                "context": {"trigger": "pr", "repo": "example-owner/example-repo"},
            })
        )
        code, record = self.bridge(
            linked, "record", "completion-evidence", str(request)
        )
        assert code == 0, record
        assert (record["outcomes"], record["modes"]) == (
            {"evidence_gap": "yes"},
            {"evidence_gap": "shadow"},
        )
        assert [
            row["mode"]
            for row in self.store(
                "SELECT mode FROM decisions WHERE judgment = 'completion-evidence'"
            )
        ] == ["shadow"]
        assert [
            row["mode"]
            for row in self.store(
                "SELECT mode FROM questions WHERE judgment = 'completion-evidence'"
            )
        ] == ["enforced"], "the questions table keeps the global mode"

    def test_an_invalid_config_refuses_instead_of_falling_back(self) -> None:
        path = write_config(self.repo, config())
        path.write_text("{")
        with pytest.raises(
            workflow.WorkflowError,
            match=re.escape("(.agentrails/config.json invalid)"),
        ):
            workflow.judgment_config(self.repo)
        code, refused = self.bridge(self.repo, "questions", "in-scope")
        assert code == 1
        assert "is not onboarded" in refused["error"]


class InjectedRemovalError(RuntimeError):
    pass


PAIR_ID = "src/a.py::get_user<>src/b.py::fetch_user"


class StructureReviewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory(prefix="omp-structure-test-")
        self.addCleanup(scratch.cleanup)
        self.repo = Path(scratch.name)
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "structure@example.test")
        git(self.repo, "config", "user.name", "Structure Test")
        git(self.repo, "remote", "add", "origin", "https://github.com/owner/repo.git")
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
        self.judgment = workflow._structure_config()
        self.questions, self.thresholds = (
            self.judgment.questions,
            self.judgment.thresholds,
        )
        self.calls: list[Any] = []
        agent = tempfile.TemporaryDirectory(prefix="omp-structure-agent-")
        self.addCleanup(agent.cleanup)
        self.agent = Path(agent.name)
        self.judge_role = self.judgment.model
        self.kinds: list[dict[str, Any]] = [
            {
                "path": "src/models/order.py",
                "state": {"file": {}, "folder": "src/models", "importers": []},
            }
        ]
        self.pairs: list[dict[str, Any]] = [
            {
                "id": PAIR_ID,
                "det_score": 0.5,
                "state": {"first": {"name": "x"}, "second": {"name": "y"}},
            }
        ]
        self.consumers: list[dict[str, Any]] = [
            {"path": "src/orders/dash.py", "state": {"file": {}, "package": {}}}
        ]

    async def review(
        self,
        *,
        score: float = 0.9,
        error: Exception | None = None,
        post_error: Exception | None = None,
    ) -> Any:
        from types import SimpleNamespace

        calls = self.calls

        def answer(questions: dict[str, Any]) -> dict[str, Any]:
            ident = next(iter(questions))
            if questions[ident]["type"] == "bool":
                return {ident: {"bool": score}}
            options = list(questions[ident]["criteria"])
            rest = (1 - score) / (len(options) - 1) if score == score else score
            return {
                ident: {
                    "probabilities": {
                        option: score if option == "finished_tool" else rest
                        for option in options
                    }
                }
            }

        def judge_batch(states: Any, questions: Any, **kwargs: Any) -> Any:
            calls.append((states, questions))

            class Batch:
                async def drain_iter(self) -> Any:
                    for key in states:
                        yield (
                            key,
                            SimpleNamespace(
                                ok=error is None,
                                error=str(error),
                                answers=None if error else answer(questions),
                            ),
                        )

                def close(self) -> None:
                    calls.append("closed")

            return Batch()

        with (
            patch.object(workflow, "_structure_config", return_value=self.judgment),
            patch.object(
                workflow, "_configured_judge_model", return_value=self.judge_role
            ),
            patch.dict(os.environ, {"PI_CODING_AGENT_DIR": str(self.agent)}),
            patch.object(workflow, "_kind_candidates", return_value=self.kinds),
            patch.object(workflow, "_name_pairs", return_value=self.pairs),
            patch.object(workflow, "_consumer_candidates", return_value=self.consumers),
            patch.object(workflow, "judge_batch", judge_batch, create=True),
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

    def logged(self) -> list[dict[str, Any]]:
        with closing(
            sqlite3.connect(self.agent / "agent-rails/agent-rails.db")
        ) as store:
            store.row_factory = sqlite3.Row
            return [
                dict(row)
                for row in store.execute("SELECT * FROM decisions ORDER BY rowid")
            ]

    def shadow(self, *idents: str) -> None:
        for ident in idents:
            self.judgment.decisions[ident].update(
                mode="shadow", shadow_exit={"labelled_flags": 10}
            )

    def enforce(self, ident: str) -> None:
        self.judgment.decisions[ident]["mode"] = "enforced"
        self.judgment.decisions[ident].pop("shadow_exit", None)

    async def test_every_enforced_finding_blocks_and_never_changes_prove_signal(
        self,
    ) -> None:
        signal = workflow.result_path(self.repo, self.head)
        signal.parent.mkdir()
        signal.write_text('{"status":"pass"}')
        result = await self.review()
        assert (result["status"], result["halt"], result["head"]) == (
            "blocked",
            True,
            self.head,
        )
        assert {(item["question"], item["mode"]) for item in result["findings"]} == {
            (workflow.KIND_QUESTION, "shadow"),
            (workflow.PAIR_QUESTION, "enforced"),
            (workflow.CONSUMER_QUESTION, "shadow"),
        }
        assert len(result["pairs"][PAIR_ID]["digest"]) == 64
        assert "units" not in result
        assert signal.read_text() == '{"status":"pass"}'
        assert result["thresholds"] == self.thresholds
        comment = workflow._structure_comment(result)
        assert "raw judgment probabilities" in comment
        assert "Every enforced finding blocks merge" in comment
        assert comment.count("(shadow, not blocking)") == 2
        logged = self.logged()
        assert {
            (row["question"], row["outcome"], row["mode"], row["trigger"], row["item"])
            for row in logged
        } == {
            (workflow.KIND_QUESTION, "flag", "shadow", "pr", "src/models/order.py"),
            (workflow.PAIR_QUESTION, "flag", "enforced", "pr", PAIR_ID),
            (
                workflow.CONSUMER_QUESTION,
                "flag",
                "shadow",
                "pr",
                "src/orders/dash.py",
            ),
        }
        assert {(row["repo"], row["commit_sha"]) for row in logged} == {
            ("owner/repo", self.head)
        }

    async def test_shadow_findings_are_reported_and_logged_but_never_block(
        self,
    ) -> None:
        self.shadow(workflow.PAIR_QUESTION, workflow.CONSUMER_QUESTION)
        result = await self.review()
        assert (result["status"], result["halt"]) == ("pass", False)
        assert {item["mode"] for item in result["findings"]} == {"shadow"}
        comment = workflow._structure_comment(result)
        assert comment.count("(shadow, not blocking)") == 3
        assert "**Blocked:**" not in comment
        assert {(row["mode"], row["outcome"]) for row in self.logged()} == {
            ("shadow", "flag")
        }
        self.enforce(workflow.PAIR_QUESTION)
        mixed = await self.review()
        assert (mixed["status"], mixed["halt"]) == ("blocked", True)
        assert [
            item["question"] for item in mixed["findings"] if item["mode"] == "enforced"
        ] == [workflow.PAIR_QUESTION]

    async def test_the_repo_config_turns_questions_off_or_to_shadow(self) -> None:
        write_config(
            self.repo,
            config(
                judgments={
                    "structure-naming/technical_kind_grouping": {"enabled": False},
                    "structure-naming/same_thing": {"enabled": True, "mode": "shadow"},
                }
            ),
        )
        result = await self.review()
        asked = [list(call[1]) for call in self.calls if call != "closed"]
        assert asked == [["same_thing"], ["kind"], ["kind"]], (
            "an off question is never asked"
        )
        assert result["kind_files"] == {}
        assert (result["status"], result["halt"]) == ("pass", False)
        assert {(item["question"], item["mode"]) for item in result["findings"]} == {
            (workflow.PAIR_QUESTION, "shadow"),
            (workflow.CONSUMER_QUESTION, "shadow"),
        }
        assert {(row["question"], row["mode"]) for row in self.logged()} == {
            (workflow.PAIR_QUESTION, "shadow"),
            (workflow.CONSUMER_QUESTION, "shadow"),
        }

    async def test_scores_below_the_threshold_pass_without_findings(self) -> None:
        result = await self.review(score=0.05)
        assert not result["findings"]
        assert {
            item["outcome"]
            for group in ("kind_files", "pairs", "consumers")
            for item in result[group].values()
        } == {"no"}
        assert (result["status"], result["halt"], result["errors"]) == (
            "pass",
            False,
            [],
        )

    async def test_an_escalated_band_decision_leaves_the_review_incomplete(
        self,
    ) -> None:
        decision = self.judgment.decisions[workflow.KIND_QUESTION]
        del decision["threshold"]
        decision.update(no_at_most=0.3, yes_at_least=0.9, fallback="backup")
        self.pairs, self.consumers = [], []
        result = await self.review(score=0.5)
        assert result["kind_files"]["src/models/order.py"]["outcome"] == "escalate"
        assert (result["status"], result["halt"]) == ("incomplete", False)
        assert any("escalated to backup" in e for e in result["errors"])

    async def test_the_inspector_route_never_serves_the_gate_judgment(self) -> None:
        with patch.dict(os.environ, {"TYPESAFE_BASE_URL": "http://127.0.0.1:8787"}):
            result = await self.review()
        assert result["status"] == "incomplete"
        assert self.calls == []
        assert any("TYPESAFE_BASE_URL" in error for error in result["errors"])

    async def test_another_judge_role_is_incomplete_and_never_judged(self) -> None:
        self.judge_role = "openrouter/typesafe/jev-1.14"
        result = await self.review()
        assert result["status"] == "incomplete"
        assert self.calls == []
        assert any("judge role is openrouter" in error for error in result["errors"])

    async def test_a_finding_at_the_threshold_sets_persisted_merge_halt(self) -> None:
        self.kinds, self.consumers = [], []
        self.pairs[0]["det_score"] = 0.051143
        result = await self.review(score=1.0)
        assert (result["halt"], result["status"]) == (True, "blocked")
        assert {
            "item": PAIR_ID,
            "question": workflow.PAIR_QUESTION,
            "mode": "enforced",
            "score": 0.051143,
            "threshold": 0.051143,
        } in result["findings"]
        below = await self.review(score=0.99)
        assert not below["halt"]

    async def test_judge_and_publication_errors_leave_the_review_incomplete(
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
        assert {(row["status"], row["error"]) for row in self.logged()} == {
            ("error", "judge offline")
        }

    async def test_invalid_answer_is_incomplete_not_clean(self) -> None:
        result = await self.review(score=float("nan"))
        assert result["status"] == "incomplete"
        assert not result["halt"]
        assert not result["findings"]

    async def test_every_proposal_question_is_asked_through_one_batch_per_order(
        self,
    ) -> None:
        await self.review()
        asked = [list(call[1]) for call in self.calls if call != "closed"]
        assert asked == [["own_capability"], ["same_thing"], ["kind"], ["kind"]]
        assert self.calls.count("closed") == 4
        assert set(self.calls[2][0]) == {PAIR_ID + "##ab", PAIR_ID + "##ba"}

    def test_proposers_use_isolated_harness_cli_and_exact_commit_range(self) -> None:
        with patch.object(
            workflow, "run_check", return_value=(True, json.dumps({"pairs": []}))
        ) as run:
            assert workflow._name_pairs(self.repo, self.parent, self.head) == []
        argv = run.call_args.args[0]
        assert argv[:3] == [sys.executable, "-I", "-S"]
        assert "name-pairs" in argv
        assert argv[-6:] == [
            "--repo",
            str(self.repo),
            "--base",
            self.parent,
            "--head",
            self.head,
        ]
        assert run.call_args.kwargs["stdout_only"]

    def test_proposers_parse_real_stdout_without_status_line(self) -> None:
        write_config(self.repo, config())
        source = self.repo / "src/models/order.py"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text('class Order:\n    """A customer order."""\n')
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "add order model")
        head = git(self.repo, "rev-parse", "HEAD")

        found = workflow._kind_candidates(self.repo, self.parent, head)

        assert [item["path"] for item in found] == ["src/models/order.py"]
        assert found[0]["state"]["folder"] == "src/models"

    def test_comment_updates_existing_marker_instead_of_posting_again(
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
