"""Boundary tests for frozen proofs, isolated git trees and verifier ownership."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

MODULE = Path(__file__).resolve().parents[1] / "agent/skills/omp-dyna-workflows/templates/workflows.py"
spec = importlib.util.spec_from_file_location("workflows", MODULE)
workflow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workflow)


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


@pytest.fixture
def change(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "proof@example.test")
    git(repo, "config", "user.name", "Proof Test")
    (repo / ".omp").mkdir()
    (repo / ".omp/gate.json").write_text(json.dumps({
        "schemaVersion": 1, "fastGate": "true", "pathCheck": "true", "sensitive": ["auth/"]}))
    (repo / "value").write_text("old")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    parent = git(repo, "rev-parse", "HEAD")
    monkeypatch.setattr(workflow, "_run_verifier", lambda brief: {
        "status": "pass", "blocking": [], "followUps": []})

    def finish(kind="defect", code=None, files=None, gate_change=False):
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
            gate["fastGate"] = "printf gate"
            path.write_text(json.dumps(gate))
        git(repo, "add", ".")
        git(repo, "commit", "-qm", "change")
        head = git(repo, "rev-parse", "HEAD")
        manifest = {"schemaVersion": 1, "slug": "example", "proofs": [{
            "id": "P1", "criterion": "value is fixed", "kind": kind,
            "cmd": [sys.executable, "proof.py"], "files": ["proof.py"],
            "expectRed": "expected fixed" if kind == "defect" else None,
            "sha256": {"proof.py": workflow._sha(repo / "proof.py")}}]}
        path = tmp_path / "manifest.json"
        path.write_text(json.dumps(manifest))
        return repo, parent, head, path
    return finish


def verify(change_args):
    repo, parent, head, manifest = change_args
    result = workflow.verify(repo, parent, head, manifest, workflow._sha(manifest), "fix value")
    # Scratch trees must be removed on success and refusal alike.
    assert git(repo, "worktree", "list", "--porcelain").count("worktree ") == 1
    return result


@pytest.mark.parametrize("kind", ["defect", "preserve", "new-surface"])
def test_valid_proof_kinds(change, kind):
    code = "assert True\n" if kind == "preserve" else None
    assert verify(change(kind=kind, code=code))["status"] == "pass"


@pytest.mark.parametrize("code, reason", [
    ("from pathlib import Path\nassert Path('value').read_text() == 'fixed', 'different failure'\n",
     "wrong-reason red"),
    ("assert True\n", "tautology"),
    ("from pathlib import Path\np = Path('counter')\nn = int(p.read_text()) if p.exists() else 0\n"
     "p.write_text(str(n + 1))\nassert n == 0, 'flaky'\n", "head run 2 failed"),
])
def test_rejects_invalid_proof_evidence(change, code, reason):
    result = verify(change(code=code))
    assert result["status"] == "fail"
    assert any(reason in item for item in result["blocking"])


@pytest.mark.parametrize("mismatch", ["manifest", "proof"])
def test_hash_mismatch_blocks(change, mismatch):
    args = change()
    repo, parent, head, manifest = args
    if mismatch == "manifest":
        result = workflow.verify(repo, parent, head, manifest, "0" * 64, "fix")
    else:
        data = json.loads(manifest.read_text())
        data["proofs"][0]["sha256"]["proof.py"] = "0" * 64
        manifest.write_text(json.dumps(data))
        result = verify(args)
    assert result["status"] == "fail"
    assert any("hash mismatch" in item for item in result["blocking"])


def test_gate_change_requires_countersign(change):
    result = verify(change(gate_change=True))
    assert result["status"] == "needs_owner"
    assert result["deterministic"]["needsCountersign"] == [".omp/gate.json"]


@pytest.mark.parametrize("name", ["auth/login.py", "auth/deep/token.py"])
def test_sensitive_changes_require_owner(change, name):
    result = verify(change(files={name: "# protected\n"}))
    assert result["status"] == "needs_owner"
    assert result["deterministic"]["needsOwner"] == [name]


def test_verifier_mutation_detected(change, monkeypatch):
    args = change()
    def mutate(brief):
        (args[0] / "unauthorized").write_text("mutation")
        return {"status": "pass", "blocking": [], "followUps": []}
    monkeypatch.setattr(workflow, "_run_verifier", mutate)
    result = verify(args)
    assert result["status"] == "fail"
    assert "verifier mutated the repo" in result["blocking"]


def test_unknown_proof_id_rejected(change):
    args = change()
    data = json.loads(args[3].read_text())
    data["proofs"][0].pop("id")
    args[3].write_text(json.dumps(data))
    result = verify(args)
    assert result["status"] == "fail"
    assert "missing or duplicate proof id" in result["blocking"]


def test_sensitive_pattern_contract():
    assert workflow.sensitive_paths(["auth/token", "else/auth", "x/secret.env", "x/public"],
                                    ["auth/", "secret"]) == ["auth/token", "x/secret.env"]

@pytest.mark.parametrize("failure", ["exception", "off-schema"])
def test_verifier_failure_is_closed(change, monkeypatch, failure):
    def broken(brief):
        if failure == "exception":
            raise RuntimeError("verifier unavailable")
        return {"status": "pass"}
    monkeypatch.setattr(workflow, "_run_verifier", broken)
    result = verify(change())
    assert result["status"] == "fail"
    assert result["blocking"]
