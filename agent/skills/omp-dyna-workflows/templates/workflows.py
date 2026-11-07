# pyright: reportUndefinedVariable=false
# agent and wait are OMP eval kernel globals, injected when this file is %load-ed.
"""Proof-first verification and blind labels; load in the OMP Python eval kernel."""
import hashlib
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile

MAX_AGENTS = 16


class WorkflowError(RuntimeError):
    pass


def run_check(cmd, cwd):
    """Run a gate or argv proof; return success and the complete diagnostic output."""
    p = subprocess.run(cmd, cwd=cwd, shell=isinstance(cmd, str), capture_output=True,
                       text=True, timeout=900)
    return p.returncode == 0, p.stdout + p.stderr


def _git(repo, *args):
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if p.returncode:
        raise WorkflowError(p.stderr.strip())
    return p.stdout


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _proof_path(root, name):
    if not isinstance(name, str) or not name or Path(name).is_absolute():
        raise WorkflowError(f"invalid proof path: {name!r}")
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()):
        raise WorkflowError(f"proof path escapes worktree: {name}")
    return path


def load_manifest(path, digest, max_checks):
    """Validate the frozen manifest before executing any repository command."""
    frozen = Path(path).read_bytes()
    if hashlib.sha256(frozen).hexdigest() != digest:
        raise WorkflowError("manifest hash mismatch")
    manifest = json.loads(frozen)
    if not isinstance(manifest, dict) or manifest.get("schemaVersion") != 1:
        raise WorkflowError("unsupported manifest schema")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", manifest.get("slug", "")):
        raise WorkflowError("invalid manifest slug")
    proofs = manifest.get("proofs")
    if not isinstance(proofs, list) or not proofs or len(proofs) > max_checks:
        raise WorkflowError("proof count must be nonzero and within max_checks")
    ids = set()
    for proof in proofs:
        if not isinstance(proof, dict):
            raise WorkflowError("invalid proof")
        ident = proof.get("id")
        if not isinstance(ident, str) or not ident or ident in ids:
            raise WorkflowError("missing or duplicate proof id")
        ids.add(ident)
        if not isinstance(proof.get("criterion"), str) or not proof["criterion"]:
            raise WorkflowError(f"{ident}: missing criterion")
        if proof.get("kind") not in ("defect", "new-surface", "preserve"):
            raise WorkflowError(f"{ident}: unknown proof kind")
        cmd, files, hashes = proof.get("cmd"), proof.get("files"), proof.get("sha256")
        if not isinstance(cmd, list) or not cmd or not all(isinstance(x, str) and x for x in cmd):
            raise WorkflowError(f"{ident}: cmd must be argv")
        if not isinstance(files, list) or not files or len(set(files)) != len(files):
            raise WorkflowError(f"{ident}: missing or duplicate proof files")
        if not isinstance(hashes, dict) or set(hashes) != set(files):
            raise WorkflowError(f"{ident}: proof hash coverage mismatch")
        for name in files:
            _proof_path(Path(tempfile.gettempdir()), name)
            if not isinstance(hashes[name], str) or not re.fullmatch(r"[0-9a-f]{64}", hashes[name]):
                raise WorkflowError(f"{ident}: invalid sha256")
        red = proof.get("expectRed")
        if proof["kind"] == "defect":
            if not isinstance(red, str) or not red:
                raise WorkflowError(f"{ident}: defect requires expectRed")
        elif red is not None:
            raise WorkflowError(f"{ident}: expectRed must be null")
    return manifest


def _hash_failures(root, manifest):
    failures = []
    for proof in manifest["proofs"]:
        for name in proof["files"]:
            path = _proof_path(root, name)
            if not path.is_file() or _sha(path) != proof["sha256"][name]:
                failures.append(f"{proof['id']}: missing proof or hash mismatch: {name}")
    return failures


def _is_argv(value):
    return isinstance(value, list) and bool(value) and all(
        isinstance(arg, str) and arg.strip() for arg in value)


def gate_policy(gate):
    """Normalise .omp/gate.json to {commit, ci, pathCheck}; None when invalid.

    Mirrors agent-rails' schema: commands are optional nonempty argv arrays, `ciHost` and
    `agentMerge` optional enums. Any other shape fails closed."""
    if not isinstance(gate, dict) or gate.get("schemaVersion") != 2:
        return None
    checks = gate.get("checks", {})
    if not isinstance(checks, dict):
        return None
    commit, ci = checks.get("commit"), checks.get("ci")
    path_check = gate.get("pathCheck")
    if any(value is not None and not _is_argv(value) for value in (commit, ci, path_check)):
        return None
    if gate.get("ciHost", "github") not in ("github", "nuc") or (
            gate.get("agentMerge", "allowed") not in ("allowed", "disabled")):
        return None
    return {"commit": commit, "ci": ci, "pathCheck": path_check}


def gate_changes(paths, root, gate):
    """Include gate entrypoints and lint/test config paths named by the gate contract."""
    named = set()
    def collect(value):
        if isinstance(value, dict):
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)
        elif isinstance(value, str):
            for token in shlex.split(value):
                token = token.split("=", 1)[-1].removeprefix("./")
                if token and (root / token).is_file():
                    named.add(token)
    collect(gate)
    # Also inspect referenced gate scripts for explicitly named lint/test configs.
    for name in tuple(named):
        if Path(name).suffix in (".sh", ".js", ".ts", ".py"):
            for token in re.findall(r"[\w./-]+", (root / name).read_text()):
                if ("config" in token or "lint" in token) and (root / token).is_file():
                    named.add(token.removeprefix("./"))
    return [path for path in paths if path in named or path in (
        ".omp/gate.json", "lefthook.yml", ".pre-commit-config.yaml")
        or path.startswith((".github/workflows/", ".githooks/"))]


def deterministic_checks(parent_tree, head_tree, manifest, gate, paths):
    """Run frozen proofs against real detached trees; no eval globals are needed.

    Repository checks are not run here: the commit hook runs checks.commit and CI runs checks.ci.
    `gateChanges` lists changed gate files for the PR description; it never blocks a merge.
    """
    parent_gate_path = parent_tree / ".omp/gate.json"
    parent_gate = json.loads(parent_gate_path.read_text()) if parent_gate_path.is_file() else {}
    policy = gate_policy(gate)
    changed_gates = set(gate_changes(paths, head_tree, gate))
    changed_gates.update(gate_changes(paths, parent_tree, parent_gate))
    result = {"proofs": {}, "blocking": [], "gateChanges": sorted(changed_gates)}
    failures = result["blocking"]
    failures.extend(_hash_failures(head_tree, manifest))
    if failures:
        return result
    if policy is None:
        failures.append(".omp/gate.json has an unsupported schemaVersion or invalid fields")
        return result
    if not policy["commit"] or not policy["pathCheck"]:
        failures.append("missing checks.commit or pathCheck; use skill://setup-repository-guardrails")
        return result
    for proof in manifest["proofs"]:
        runs = []
        result["proofs"][proof["id"]] = runs
        for n in range(2):
            ok, out = run_check(proof["cmd"], head_tree)
            runs.append({"tree": "head", "run": n + 1, "ok": ok, "output": out})
            if not ok:
                failures.append(f"{proof['id']}: head run {n + 1} failed")
    failures.extend(_hash_failures(head_tree, manifest))
    for proof in manifest["proofs"]:
        for name in proof["files"]:
            dest = _proof_path(parent_tree, name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(_proof_path(head_tree, name), dest)
    for proof in manifest["proofs"]:
        if proof["kind"] == "new-surface":
            continue
        ok, out = run_check(proof["cmd"], parent_tree)
        result["proofs"][proof["id"]].append({"tree": "parent", "ok": ok, "output": out})
        if proof["kind"] == "defect":
            if ok:
                failures.append(f"{proof['id']}: tautology; defect proof passes at parent")
            elif proof["expectRed"] not in out:
                failures.append(f"{proof['id']}: wrong-reason red at parent")
        elif not ok:
            failures.append(f"{proof['id']}: preserve proof fails at parent")
    return result


BLOCKING_RULE = (
    "Block only failing head proof runs, doc lines changed or made wrong, base defects "
    "copied or newly reached by this PR, missing/weakened proofs, or check loosening the objective "
    "does not call for. "
    "Everything else is a parked follow-up, including uncovered naming/structure on added/renamed paths.")


def _run_verifier(brief):
    schema = {"type": "object", "required": ["status", "blocking", "followUps"],
              "additionalProperties": False, "properties": {
                  "status": {"enum": ["pass", "fail"]},
                  "blocking": {"type": "array", "items": {"type": "string"}},
                  "followUps": {"type": "array", "items": {"type": "string"}}}}
    return wait([agent(brief, agent="verifier", label="Prove", schema=schema)], raise_errors=False)[0]


def prove(repo, parent, head, manifest_path, manifest_sha256, objective, max_checks=MAX_AGENTS):
    """Run frozen proofs then one cross-family verifier; always remove detached worktrees.

    CI owns the test suite: merge additionally requires the CI status on the exact head.
    """
    # Initialize the failure envelope before any operation that can raise.
    result = {"status": "fail", "deterministic": {}, "verifier": {}, "blocking": [], "followUps": []}
    trees = []
    try:
        repo = Path(repo).resolve()
        manifest = load_manifest(manifest_path, manifest_sha256, max_checks)
        # Unique scratch parent prevents concurrent proof runs of the same slug colliding.
        with tempfile.TemporaryDirectory(prefix="omp-prove-") as scratch:
            try:
                for label, ref in (("parent", parent), ("head", head)):
                    tree = Path(scratch) / f"omp-prove-{manifest['slug']}-{label}"
                    _git(repo, "worktree", "add", "--detach", str(tree), ref)
                    trees.append(tree)
                parent_tree, head_tree = trees
                gate_path = head_tree / ".omp/gate.json"
                if not gate_path.is_file():
                    raise WorkflowError(
                        "missing checks.commit or pathCheck; use skill://setup-repository-guardrails")
                gate = json.loads(gate_path.read_text())
                if not isinstance(gate, dict):
                    raise WorkflowError(".omp/gate.json must be an object")
                paths = _git(repo, "diff", "--name-only", parent, head).splitlines()
                deterministic = deterministic_checks(parent_tree, head_tree, manifest, gate, paths)
                result["deterministic"] = deterministic
                result["blocking"].extend(deterministic["blocking"])
                if result["blocking"]:
                    return result
                snapshot = (_git(repo, "status", "--porcelain=v2"), _git(repo, "rev-parse", "HEAD"))
                brief = (
                    "Read skill://codegraph/references/readiness-and-bootstrap.md, "
                    "skill://codegraph/references/query-selection.md and skill://git-tool before discovery/git. "
                    f"Read-only: work only in {parent_tree}, {head_tree} and your TMPDIR scratch. "
                    f"Never edit {repo}; never spawn agents. Objective: {objective}\n"
                    f"Manifest: {json.dumps(manifest)}\nDeterministic: {json.dumps(deterministic)}\n"
                    f"Diff stat: {_git(repo, 'diff', '--stat', parent, head)}\n"
                    "Drive every criterion live; check docs changed or made wrong; report-only diff scope/coverage audit. "
                    "Each finding needs exact command, actual output and required output; omit anything not run. "
                    f"{BLOCKING_RULE}")
                try:
                    verdict = _run_verifier(brief)
                finally:
                    after = (_git(repo, "status", "--porcelain=v2"), _git(repo, "rev-parse", "HEAD"))
                    if after != snapshot:
                        result["blocking"].append("verifier mutated the repo")
                result["verifier"] = verdict
                if (not isinstance(verdict, dict) or verdict.get("status") not in ("pass", "fail")
                        or not isinstance(verdict.get("blocking"), list)
                        or not isinstance(verdict.get("followUps"), list)):
                    result["blocking"].append("verifier returned invalid or failed result")
                else:
                    result["blocking"].extend(verdict["blocking"])
                    result["followUps"] = verdict["followUps"]
                    if verdict["status"] == "fail" and not verdict["blocking"]:
                        result["blocking"].append("verifier failed")
                if _sha(manifest_path) != manifest_sha256:
                    result["blocking"].append("manifest edited after freeze")
                result["blocking"].extend(_hash_failures(head_tree, manifest))
                result["status"] = "fail" if result["blocking"] else "pass"
            finally:
                for tree in reversed(trees):
                    try:
                        _git(repo, "worktree", "remove", "--force", str(tree))
                    except Exception as exc:
                        result["blocking"].append(str(exc))
                        result["status"] = "fail"
    except Exception as exc:
        result["blocking"].append(str(exc))
        result["status"] = "fail"
    return result


def blind_label(batch_files, rubric_path, labels, labellers=("scout", "task")):
    """Two blind read-only labellers per frozen batch; a human confirms disputes."""
    if len(labellers) != 2 or labellers[0] == labellers[1]:
        raise WorkflowError(f"labellers must be two distinct agents, got {labellers!r}")
    if 2 * len(batch_files) > MAX_AGENTS:
        raise WorkflowError(f"{2 * len(batch_files)} labellers exceeds cap {MAX_AGENTS}; merge batches")
    schema = {"type": "object", "required": ["cases"], "additionalProperties": False, "properties": {"cases": {
        "type": "array", "items": {"type": "object", "required": ["id", "label", "evidence", "rationale"],
                                   "additionalProperties": False, "properties": {
                                       "id": {"type": "string"}, "label": {"enum": list(labels)},
                                       "evidence": {"type": "string"}, "rationale": {"type": "string"}}}}}}
    jobs = [(who, agent(f"Read-only. Label every case in {f} with the rubric {rubric_path}. Read only those two "
                        "files. Quote deciding evidence; rationale in 1-2 plain sentences.",
                        agent=who, label=f"Label{n:02d}{who[:3]}", schema=schema))
            for n, f in enumerate(batch_files) for who in labellers]
    results = wait([h for _, h in jobs], raise_errors=False)
    votes = {}
    for (who, _), r in zip(jobs, results):
        for c in (r.get("cases", []) if isinstance(r, dict) else []):
            votes.setdefault(c["id"], {})[who] = c
    agreed = {i: v[labellers[0]]["label"] for i, v in votes.items()
              if len(v) == 2 and v[labellers[0]]["label"] == v[labellers[1]]["label"]}
    return {"agreed": agreed, "disputed": {i: v for i, v in votes.items() if i not in agreed}}
