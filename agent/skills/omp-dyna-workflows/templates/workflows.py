# pyright: reportUndefinedVariable=false
# agent and wait are OMP eval kernel globals, injected when this file is %load-ed.
"""Proof-first verification and blind labels; load in the OMP Python eval kernel."""
import hashlib
import json
from pathlib import Path
import re
import shlex
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
    root = root.resolve()
    path = root / name
    if ".." in Path(name).parts:
        raise WorkflowError(f"proof path escapes worktree: {name}")
    for component in (path, *path.parents):
        if component == root:
            break
        if component.is_symlink():
            raise WorkflowError(f"symlink proof path: {name}")
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
    if "base" in manifest and not re.fullmatch(r"[0-9a-f]{40}", str(manifest["base"])):
        raise WorkflowError("base must be 40-hex")
    if "setup" in manifest and not _is_argv(manifest["setup"]):
        raise WorkflowError("setup must be argv")
    if not isinstance(manifest.get("revisions", []), list):
        raise WorkflowError("revisions must be a list")
    for revision in manifest.get("revisions", []):
        if (not isinstance(revision, dict) or not revision.get("id")
                or not re.fullmatch(r"[0-9a-f]{64}", str(revision.get("oldSha256", "")))
                or not isinstance(revision.get("reason"), str) or not revision["reason"].strip()):
            raise WorkflowError("invalid revision")
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
        if "parent" in proof and not re.fullmatch(r"[0-9a-f]{40}", str(proof["parent"])):
            raise WorkflowError(f"{ident}: parent must be 40-hex")
        cmd, files, hashes = proof.get("cmd"), proof.get("files"), proof.get("sha256")
        if not isinstance(cmd, list) or not cmd or not all(isinstance(x, str) and x for x in cmd):
            raise WorkflowError(f"{ident}: cmd must be argv")
        if not isinstance(files, list) or not files or len(set(files)) != len(files):
            raise WorkflowError(f"{ident}: missing or duplicate proof files")
        if not isinstance(hashes, dict) or set(hashes) != set(files):
            raise WorkflowError(f"{ident}: proof hash coverage mismatch")
        for name in files:
            try:
                _proof_path(Path(tempfile.gettempdir()), name)
            except WorkflowError as exc:
                raise WorkflowError(f"{ident}: {exc}") from exc
            if not isinstance(hashes[name], str) or not re.fullmatch(r"[0-9a-f]{64}", hashes[name]):
                raise WorkflowError(f"{ident}: invalid sha256")
        red = proof.get("expectRed")
        if proof["kind"] == "defect":
            if not isinstance(red, str) or not red:
                raise WorkflowError(f"{ident}: defect requires expectRed")
        elif red is not None:
            raise WorkflowError(f"{ident}: expectRed must be null")
    return manifest


def _overlay_run(src_root, tree, proof, truncate=False):
    """Run only this proof's overlay, restoring the baseline even on runner failure."""
    try:
        for name in proof["files"]:
            dest = _proof_path(tree, name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"" if truncate else _proof_path(src_root, name).read_bytes())
        ok, out = run_check(proof["cmd"], tree)
        return ok, out.replace(str(tree), "<tree>")
    finally:
        _git(tree, "checkout", "--force", "--", ".")
        _git(tree, "clean", "-fdq")


def _kind_failure(proof, ok, out, where):
    ident = proof["id"]
    if proof["kind"] == "preserve":
        return None if ok else f"{ident}: preserve proof fails at {where}"
    if ok:
        return f"{ident}: tautology; passes at {where}: nothing to prove, return to Plan"
    if proof["kind"] == "defect" and proof["expectRed"] not in out:
        return f"{ident}: wrong-reason red at {where}"
    return None


def _ancestor(repo, older, newer, strict=False):
    return (not strict or older != newer) and subprocess.run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", older, newer],
        capture_output=True).returncode == 0


def _setup(tree, setup):
    if setup:
        ok, out = run_check(setup, tree)
        if not ok:
            raise WorkflowError(f"setup failed:\n{out.replace(str(tree), '<tree>')}")


def _proof_digest(proof):
    return hashlib.sha256(json.dumps(proof, sort_keys=True).encode()).hexdigest()


def freeze(worktree, manifest_path, proofs, slug, *, base="HEAD", setup=None,
           previous=None, revise=None):
    """Freeze working-tree proofs against isolated pinned baselines, before implementation."""
    root, path = Path(worktree).resolve(), Path(manifest_path).resolve()
    old = load_manifest(*previous, MAX_AGENTS) if previous else {"proofs": []}
    old_proofs = {p["id"]: p for p in old["proofs"]}
    if previous and base == "HEAD":
        base = old.get("base", base)
    if previous and setup is None:
        setup = old.get("setup")
    base, head = _commit(root, base), _commit(root, "HEAD")
    if not _ancestor(root, base, head):
        raise WorkflowError("base is not an ancestor of HEAD")
    manifest = {"schemaVersion": 1, "slug": slug, "base": base, "proofs": []}
    if setup is not None:
        manifest["setup"] = setup
    for proof in proofs:
        ident = proof.get("id", "<missing>")
        try:
            baseline = proof.get("parent", old_proofs.get(ident, {}).get("parent", base))
            item = dict(proof, parent=_commit(root, baseline), sha256={})
            if not _ancestor(root, base, item["parent"]) or not _ancestor(root, item["parent"], head):
                raise WorkflowError("parent is not an ancestor of HEAD at or after base")
            for name in item.get("files", []):
                source = _proof_path(root, name)
                if not source.is_file():
                    raise WorkflowError(f"missing regular proof file: {name}")
                if subprocess.run(["git", "-C", str(root), "check-ignore", "-q", "--", name]).returncode == 0:
                    raise WorkflowError(f"ignored proof file: {name}")
                if (_git(root, "hash-object", f"--path={name}", name)
                        != _git(root, "hash-object", "--no-filters", name)):
                    raise WorkflowError(f"normalise line endings/clean filters: {name}")
                item["sha256"][name] = _sha(source)
            for token in item.get("cmd", []):
                token = token.split("=", 1)[-1].removeprefix("./")
                if token and not Path(token).is_absolute() and (root / token).is_file() and token not in item["files"]:
                    raise WorkflowError(f"command file not frozen: {token}")
            manifest["proofs"].append(item)
        except Exception as exc:
            raise WorkflowError(f"{ident}: {exc}") from exc
    names = {name for proof in manifest["proofs"] for name in proof["files"]}
    for entry in _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames").split("\0"):
        if entry and entry[3:] not in names:
            raise WorkflowError(f"unfrozen change: {entry[3:]}; add it to a proof's files or revert")
    revise = revise or {}
    current = {p["id"]: p for p in manifest["proofs"]}
    if set(revise) - set(old_proofs):
        raise WorkflowError("revision names an unknown previous proof")
    revisions = list(old.get("revisions", []))
    changed = set()
    for ident, proof in old_proofs.items():
        if current.get(ident) != proof:
            if not isinstance(revise.get(ident), str) or not revise[ident].strip():
                raise WorkflowError(f"{ident}: silent proof change; supply revise reason")
            revisions.append({"id": ident, "oldSha256": _proof_digest(proof), "reason": revise[ident]})
            changed.add(ident)
    if revisions:
        manifest["revisions"] = revisions
    trees = {}
    with tempfile.TemporaryDirectory(prefix="omp-freeze-") as scratch:
        draft = Path(scratch) / "manifest.json"
        draft.write_text(json.dumps(manifest, indent=2) + "\n")
        load_manifest(draft, _sha(draft), MAX_AGENTS)
        try:
            for proof in manifest["proofs"]:
                if proof["id"] in old_proofs and proof["id"] not in changed:
                    continue
                parent = proof["parent"]
                if parent not in trees:
                    tree = Path(scratch) / f"tree-{len(trees)}"
                    _git(root, "worktree", "add", "--detach", str(tree), parent)
                    trees[parent] = tree
                    _setup(tree, setup)
                tree = trees[parent]
                ok, out = _overlay_run(root, tree, proof)
                failure = _kind_failure(proof, ok, out, "baseline")
                if failure:
                    raise WorkflowError(failure)
                if proof["kind"] == "defect":
                    _, control = _overlay_run(root, tree, proof, truncate=True)
                    if proof["expectRed"] in control:
                        raise WorkflowError(f"{proof['id']}: expectRed survives negative control")
            failures = _hash_failures(root, manifest)
            if failures:
                raise WorkflowError("; ".join(failures))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(draft.read_bytes())
        finally:
            for tree in reversed(list(trees.values())):
                _git(root, "worktree", "remove", "--force", str(tree))
    return path, _sha(path)


def _commit(repo, ref):
    return _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").strip()


def _write_pass_marker(repo, head, kind):
    common = Path(_git(repo, "rev-parse", "--git-common-dir").strip())
    if not common.is_absolute():
        common = Path(repo) / common
    marker = common / "omp-prove" / f"{head}.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({
        "schemaVersion": 1, "head": head, "kind": kind, "status": "pass"}) + "\n")
    return marker


def exempt_docs_only(repo, parent, head):
    """Record a merge exemption only for a nonempty, non-runtime documentation diff."""
    repo = Path(repo).resolve()
    parent, head = _commit(repo, parent), _commit(repo, head)
    paths = _git(repo, "diff", "--name-only", "--no-renames", "-z", parent, head).split("\0")
    paths = [name for name in paths if name]
    if not paths:
        raise WorkflowError("docs-only exemption requires changed documentation")
    for name in paths:
        path = Path(name)
        if (path.suffix not in {".md", ".txt", ".png", ".jpg", ".jpeg", ".svg", ".gif"}
                or path.name in {"AGENTS.md", "CLAUDE.md", "SKILL.md"}
                or set(path.parts) & {"skills", "agents", "rules", "prompts", ".agents", ".claude", ".omp"}):
            raise WorkflowError(f"docs-only exemption rejected runtime path: {name}")
    return _write_pass_marker(repo, head, "docs-only")


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


def deterministic_checks(parent_tree, head_tree, manifest, gate, paths, proof_parents=None):
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
            out = out.replace(str(head_tree), "<tree>")
            runs.append({"tree": "head", "run": n + 1, "ok": ok, "output": out})
            if not ok:
                failures.append(f"{proof['id']}: head run {n + 1} failed")
        if proof["kind"] == "defect" and any(proof["expectRed"] in run["output"] for run in runs):
            failures.append(f"{proof['id']}: expectRed present at head")
    if _git(head_tree, "status", "--porcelain", "--untracked-files=no"):
        failures.append("proof run modified tracked files")
    failures.extend(_hash_failures(head_tree, manifest))
    if failures:
        return result
    for proof in manifest["proofs"]:
        overlay_tree = (proof_parents or {}).get(proof.get("parent"), parent_tree)
        ok, out = _overlay_run(head_tree, overlay_tree, proof)
        result["proofs"][proof["id"]].append({"tree": "parent", "ok": ok, "output": out})
        failure = _kind_failure(proof, ok, out, "parent")
        if failure:
            failures.append(failure)
    return result


BLOCKING_RULE = (
    "Block only failing head proof runs, doc lines changed or made wrong, base defects "
    "copied or newly reached by this PR, missing/weakened proofs, or check loosening the objective "
    "does not call for; a changed gate path is not itself a blocker; failed gates and unauthorized weakening are. "
    "Everything else is a parked follow-up, including uncovered naming/structure on added/renamed paths.")


def _run_verifier(brief):
    schema = {"type": "object", "required": ["status", "blocking", "followUps", "criteria"],
              "additionalProperties": False, "properties": {
                  "status": {"enum": ["pass", "fail"]},
                  "blocking": {"type": "array", "items": {"type": "string"}},
                  "followUps": {"type": "array", "items": {"type": "string"}},
                  "criteria": {"type": "array", "items": {"type": "object",
                      "required": ["proofId", "command", "observed", "verdict"],
                      "additionalProperties": False, "properties": {
                          key: {"type": "string"} for key in
                          ("proofId", "command", "observed", "verdict")}}}}}
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
        parent, head = _commit(repo, parent), _commit(repo, head)
        common = Path(_git(repo, "rev-parse", "--git-common-dir").strip())
        if not common.is_absolute():
            common = repo / common
        (common / "omp-prove" / f"{head}.json").unlink(missing_ok=True)
        manifest = load_manifest(manifest_path, manifest_sha256, max_checks)
        base = manifest.get("base", parent)
        if not _ancestor(repo, parent, head, strict=True):
            raise WorkflowError("parent must be a strict ancestor of head")
        if not _ancestor(repo, base, parent):
            raise WorkflowError("base must be an ancestor of parent")
        # Unique scratch parent prevents concurrent proof runs of the same slug colliding.
        with tempfile.TemporaryDirectory(prefix="omp-prove-") as scratch:
            try:
                for label, ref in (("parent", parent), ("head", head)):
                    tree = Path(scratch) / f"omp-prove-{manifest['slug']}-{label}"
                    _git(repo, "worktree", "add", "--detach", str(tree), ref)
                    trees.append(tree)
                    _setup(tree, manifest.get("setup"))
                parent_tree, head_tree = trees
                parent_trees = {parent: parent_tree}
                proof_parents = {}
                for proof in manifest["proofs"]:
                    try:
                        commit = proof.get("parent", parent)
                        if not _ancestor(repo, commit, head, strict=True) or not _ancestor(repo, base, commit):
                            raise WorkflowError("proof parent must be an ancestor of head at or after base")
                        if commit not in parent_trees:
                            tree = Path(scratch) / f"parent-{len(parent_trees)}"
                            _git(repo, "worktree", "add", "--detach", str(tree), commit)
                            trees.append(tree)
                            parent_trees[commit] = tree
                            _setup(tree, manifest.get("setup"))
                        proof_parents[proof.get("parent")] = parent_trees[commit]
                    except Exception as exc:
                        raise WorkflowError(f"{proof['id']}: {exc}") from exc
                gate_path = head_tree / ".omp/gate.json"
                if not gate_path.is_file():
                    raise WorkflowError(
                        "missing checks.commit or pathCheck; use skill://setup-repository-guardrails")
                gate = json.loads(gate_path.read_text())
                if not isinstance(gate, dict):
                    raise WorkflowError(".omp/gate.json must be an object")
                paths = _git(repo, "diff", "--name-only", "--no-renames", parent, head).splitlines()
                deterministic = deterministic_checks(
                    parent_tree, head_tree, manifest, gate, paths, proof_parents)
                result["deterministic"] = deterministic
                result["blocking"].extend(deterministic["blocking"])
                if result["blocking"]:
                    return result
                for proof in manifest["proofs"]:
                    baseline = proof.get("parent", parent)
                    commits = _git(repo, "rev-list", "--reverse", f"{baseline}..{head}",
                                   "--", *proof["files"]).splitlines()
                    for commit in commits:
                        if commit == head:
                            continue
                        tree = Path(scratch) / f"intro-{len(trees)}"
                        _git(repo, "worktree", "add", "--detach", str(tree), commit)
                        trees.append(tree)
                        _setup(tree, manifest.get("setup"))
                        errors = _hash_failures(tree, {"proofs": [proof]})
                        ok, out = run_check(proof["cmd"], tree) if not errors else (False, "")
                        deterministic["proofs"].setdefault(proof["id"], []).append({
                            "tree": commit, "ok": ok, "output": out.replace(str(tree), "<tree>")})
                        if errors or not ok or _git(tree, "status", "--porcelain", "--untracked-files=no"):
                            result["blocking"].append(
                                f"{proof['id']}: proof committed before its fix or frozen bytes at {commit}")
                if result["blocking"]:
                    return result
                snapshot = (_git(repo, "status", "--porcelain=v2"), _git(repo, "rev-parse", "HEAD"))
                full_path = Path(scratch) / "deterministic.json"
                full_path.write_text(json.dumps(deterministic, indent=2))
                brief_checks = json.loads(json.dumps(deterministic))
                for runs in brief_checks["proofs"].values():
                    for run in runs:
                        run["output"] = run["output"][-4096:]
                brief = (
                    "Read skill://codegraph/references/readiness-and-bootstrap.md, "
                    "skill://codegraph/references/query-selection.md and skill://git-tool before discovery/git. "
                    f"Read-only: work only in {', '.join(map(str, trees))} and your TMPDIR scratch. "
                    f"Never edit {repo}; never spawn agents. Objective: {objective}\n"
                    f"Manifest: {json.dumps(manifest)}\nDeterministic: {json.dumps(brief_checks)}\n"
                    f"Full deterministic evidence: {full_path}\n"
                    f"Diff stat: {_git(repo, 'diff', '--stat', parent, head)}\n"
                    "Drive every criterion live and return criteria entries with proofId, command, observed, verdict; "
                    "audit undeclared harness/config tampering, docs changed or made wrong, and diff scope/coverage. "
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
                        or not isinstance(verdict.get("followUps"), list)
                        or not isinstance(verdict.get("criteria"), list)
                        or any(not isinstance(c, dict) or any(
                            not isinstance(c.get(key), str) or not c[key].strip()
                            for key in ("proofId", "command", "observed", "verdict"))
                            for c in verdict.get("criteria", []))):
                    result["blocking"].append("verifier returned invalid or failed result")
                else:
                    result["blocking"].extend(verdict["blocking"])
                    result["followUps"] = verdict["followUps"]
                    covered = {c["proofId"] for c in verdict["criteria"]}
                    for proof in manifest["proofs"]:
                        if proof["id"] not in covered:
                            result["blocking"].append(f"verifier omitted {proof['id']}")
                    for criterion in verdict["criteria"]:
                        if criterion["verdict"] != "pass":
                            result["blocking"].append(f"verifier criterion failed: {criterion['proofId']}")
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
        if result["status"] == "pass":
            _write_pass_marker(repo, head, "prove")
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
    votes, duplicates = {}, set()
    for n, filename in enumerate(batch_files):
        batch = json.loads(Path(filename).read_text())
        cases = batch.get("cases", []) if isinstance(batch, dict) else batch
        for case in cases:
            key = (n, case["id"])
            if key in votes:
                duplicates.add(key)
            votes[key] = {}
    jobs = [(n, who, agent(
        f"Read-only. Label every case in {filename} with the rubric {rubric_path}. "
        "Read only those two files. Quote deciding evidence; rationale in 1-2 plain sentences.",
        agent=who, label=f"Label{n:02d}{who[:3]}", schema=schema))
        for n, filename in enumerate(batch_files) for who in labellers]
    results = wait([handle for _, _, handle in jobs], raise_errors=False)
    for (n, who, _), result in zip(jobs, results):
        cases = result.get("cases") if isinstance(result, dict) else None
        if not isinstance(cases, list) or any(
                not isinstance(case, dict) or any(not isinstance(case.get(key), str)
                    for key in ("id", "label", "evidence", "rationale"))
                or case["label"] not in labels for case in cases):
            continue
        for case in cases:
            key = (n, case["id"])
            if key not in votes or who in votes[key]:
                duplicates.add(key)
            votes.setdefault(key, {})[who] = case
    agreed = {key: value[labellers[0]]["label"] for key, value in votes.items()
              if key not in duplicates and len(value) == 2
              and value[labellers[0]]["label"] in labels
              and value[labellers[0]]["label"] == value[labellers[1]]["label"]}
    return {"agreed": agreed, "disputed": {
        key: value for key, value in votes.items() if key not in agreed}}
