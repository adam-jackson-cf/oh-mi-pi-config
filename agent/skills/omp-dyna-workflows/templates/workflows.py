# pyright: reportUndefinedVariable=false
# agent and wait are OMP eval kernel globals, injected when this file is %load-ed.
"""Proof-first verification and blind labels; load in the OMP Python eval kernel."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any

MAX_AGENTS = 16
MAX_PROOFS = 16
CHECK_TIMEOUT = 900
GUARDRAIL_PREREQUISITE = (
    "missing checks.commit or pathCheck; follow skill://omp-dyna-workflows/SKILL.md "
    "owner-approval prerequisite (skill://setup-repository-guardrails)")
BLOCKING_RULE = (
    "Block failing head proofs, wrong/stale docs caused by this PR, base defects copied or newly "
    "reached by this PR, missing/weakened proofs, or unauthorized check weakening. A changed gate "
    "path alone is not a blocker. Infrastructure failures prevent pass, but are not red/green evidence. "
    "Everything else is a parked follow-up, including uncovered naming/structure on added/renamed paths.")


class WorkflowError(RuntimeError):
    pass


def _is_argv(value):
    return isinstance(value, list) and bool(value) and all(
        isinstance(arg, str) and arg.strip() and "\0" not in arg for arg in value)


def run_check(cmd, cwd):
    """Run an argv proof, retaining complete, replacement-decoded diagnostics."""
    if not _is_argv(cmd):
        raise WorkflowError("cmd must be argv")
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           errors="replace", timeout=CHECK_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        def decode(value):
            return value.decode(errors="replace") if isinstance(value, bytes) else value or ""
        raise WorkflowError(f"timeout: {exc}\n{decode(exc.stdout)}{decode(exc.stderr)}") from exc
    except (OSError, ValueError) as exc:
        raise WorkflowError(f"launch error: {exc}") from exc
    return p.returncode == 0, p.stdout + p.stderr


def _git(repo, *args):
    try:
        p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                           text=True, errors="surrogateescape")
    except OSError as exc:
        raise WorkflowError(f"git infrastructure: {exc}") from exc
    if p.returncode:
        raise WorkflowError(p.stderr.strip() or f"git exited {p.returncode}: {args}")
    return p.stdout


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _canonical(name):
    if (not isinstance(name, str) or not name or "\0" in name or "\\" in name
            or ":" in name or any(part in ("", ".", "..", ".git") for part in name.split("/"))):
        raise WorkflowError(f"invalid proof path: {name!r}; require canonical relative POSIX path")
    return name


def _proof_path(root, name):
    _canonical(name)
    root = Path(root).resolve()
    path = root / name
    for component in (path, *path.parents):
        if component == root:
            break
        if component.is_symlink():
            raise WorkflowError(f"symlink proof path: {name}")
    return path


def _limit(value):
    if type(value) is not int or value <= 0:
        raise WorkflowError("max_checks must be a positive integer")


def _hex(value, length):
    return isinstance(value, str) and re.fullmatch(rf"[0-9a-f]{{{length}}}", value) is not None


def _validate(manifest, max_checks, *, frozen=True):
    _limit(max_checks)
    if not isinstance(manifest, dict) or type(manifest.get("schemaVersion")) is not int or manifest["schemaVersion"] != 1:
        raise WorkflowError("unsupported manifest schema")
    slug = manifest.get("slug")
    if not isinstance(slug, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", slug):
        raise WorkflowError("invalid manifest slug")
    if frozen and not _hex(manifest.get("base"), 40):
        raise WorkflowError("base must be 40-hex")
    if "setup" in manifest and not _is_argv(manifest["setup"]):
        raise WorkflowError("setup must be argv")
    revisions = manifest.get("revisions", [])
    if not isinstance(revisions, list):
        raise WorkflowError("revisions must be a list")
    for revision in revisions:
        if (not isinstance(revision, dict) or not isinstance(revision.get("id"), str)
                or not revision["id"].strip() or not _hex(revision.get("oldSha256"), 64)
                or not isinstance(revision.get("reason"), str) or not revision["reason"].strip()):
            raise WorkflowError("invalid revision")
    proofs = manifest.get("proofs")
    if not isinstance(proofs, list) or not proofs or len(proofs) > max_checks:
        raise WorkflowError("proof count must be nonzero and within max_checks")
    ids = set()
    for proof in proofs:
        if not isinstance(proof, dict):
            raise WorkflowError("invalid proof")
        ident = proof.get("id")
        if not isinstance(ident, str) or not ident.strip() or ident in ids:
            raise WorkflowError("missing or duplicate proof id")
        ids.add(ident)
        if not isinstance(proof.get("criterion"), str) or not proof["criterion"].strip():
            raise WorkflowError(f"{ident}: missing criterion")
        if proof.get("kind") not in ("defect", "new-surface", "preserve"):
            raise WorkflowError(f"{ident}: unknown proof kind")
        if "parent" in proof and (not isinstance(proof["parent"], str) or not proof["parent"].strip() or "\0" in proof["parent"]
                                  or (frozen and not _hex(proof["parent"], 40))):
            raise WorkflowError(f"{ident}: parent must be 40-hex" if frozen else f"{ident}: invalid parent")
        if not _is_argv(proof.get("cmd")):
            raise WorkflowError(f"{ident}: cmd must be argv")
        files = proof.get("files")
        if not isinstance(files, list) or not files:
            raise WorkflowError(f"{ident}: missing or duplicate proof files")
        for name in files:
            _canonical(name)
        if len(set(files)) != len(files):
            raise WorkflowError(f"{ident}: missing or duplicate proof files")
        if frozen:
            hashes = proof.get("sha256")
            if not isinstance(hashes, dict) or set(hashes) != set(files):
                raise WorkflowError(f"{ident}: proof hash coverage mismatch")
            if any(not _hex(value, 64) for value in hashes.values()):
                raise WorkflowError(f"{ident}: invalid sha256")
        red = proof.get("expectRed")
        if proof["kind"] == "defect":
            if not isinstance(red, str) or not red.strip():
                raise WorkflowError(f"{ident}: defect requires expectRed")
        elif red is not None:
            raise WorkflowError(f"{ident}: expectRed must be null")
    return manifest


def load_manifest(path, digest, max_checks=MAX_PROOFS):
    """Validate lexical structure without inspecting arbitrary host paths."""
    _limit(max_checks)
    try:
        frozen = Path(path).read_bytes()
        if not _hex(digest, 64) or hashlib.sha256(frozen).hexdigest() != digest:
            raise WorkflowError("manifest hash mismatch")
        return _validate(json.loads(frozen), max_checks)
    except (OSError, ValueError, TypeError) as exc:
        raise WorkflowError(f"invalid manifest: {exc}") from exc


def _command_files(proof, roots):
    for arg in proof["cmd"]:
        for token in (arg, arg.split("=", 1)[1]) if "=" in arg else (arg,):
            candidate = Path(token)
            if candidate.is_absolute():
                for root in roots:
                    if (candidate.resolve().is_relative_to(Path(root).resolve())
                            or candidate.absolute().is_relative_to(Path(root).absolute())
                            or (candidate.parent.resolve() / candidate.name).is_relative_to(Path(root).resolve())):
                        raise WorkflowError(f"{proof['id']}: absolute command reference into worktree: {token}")
                continue
            name = token.removeprefix("./")
            try:
                _canonical(name)
            except WorkflowError:
                continue
            for root in roots:
                path = _proof_path(root, name)
                if path.is_file() and name not in proof["files"]:
                    raise WorkflowError(f"{proof['id']}: command file not frozen: {name}; unchanged runners must also be frozen")


def _commit(repo, ref):
    return _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").strip()


def _ancestor(repo, older, newer, strict=False):
    p = subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", older, newer], capture_output=True)
    if p.returncode not in (0, 1):
        raise WorkflowError(f"ancestry infrastructure: {p.stderr.decode(errors='replace')}")
    return (not strict or older != newer) and p.returncode == 0


def _scrub(text, tree):
    for prefix in sorted({str(tree), str(Path(tree).resolve())}, key=len, reverse=True):
        text = text.replace(prefix, "<tree>")
    return text


def _setup(tree, setup):
    if setup:
        ok, out = run_check(setup, tree)
        if not ok:
            raise WorkflowError(f"setup failed:\n{_scrub(out, tree)}")
    if _git(tree, "status", "--porcelain", "--untracked-files=no"):
        raise WorkflowError("setup changed tracked files")
    return _entries(tree)


def _entries(tree):
    status = _git(tree, "status", "--porcelain=v1", "--ignored=matching",
                  "--untracked-files=normal", "-z")
    return {entry[3:] for entry in status.split("\0") if entry.startswith(("?? ", "!! "))}


def _reset(tree, baseline):
    _git(tree, "reset", "--hard", "HEAD")
    if baseline is None:
        return
    for name in _entries(tree) - baseline:
        path = tree / name.rstrip("/")
        if not path.parent.resolve().is_relative_to(tree.resolve()):
            raise WorkflowError(f"cleanup path escapes tree: {name}")
        if path.is_symlink() or not path.is_dir():
            path.unlink()
        else:
            shutil.rmtree(path)


def _hash_failures(root, manifest):
    failures = []
    for proof in manifest["proofs"]:
        for name in proof["files"]:
            path = _proof_path(root, name)
            if not path.is_file() or _sha(path) != proof["sha256"][name]:
                failures.append(f"{proof['id']}: missing proof or hash mismatch: {name}")
    return failures


def _execute(tree, proof, baselines, setup=None, overlay=None, *, truncate=False, location="head", run=None, roots=()):
    """Reuse each tree's setup; reset tracked files and remove non-baseline entries."""
    record = {"tree": location, "location": location, "ok": None, "output": ""}
    if run is not None:
        record["run"] = run
    try:
        if tree not in baselines:
            try:
                baselines[tree] = _setup(tree, setup)
            except Exception as exc:
                baselines[tree] = exc
        baseline = baselines[tree]
        if isinstance(baseline, Exception):
            raise baseline
        _reset(tree, baseline)
        if overlay is not None:
            for name, data in overlay.items():
                dest = _proof_path(tree, name)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(b"" if truncate else data)
        _command_files(proof, (tree, *roots))
        before = _git(tree, "diff", "--binary", "HEAD")
        record["ok"], record["output"] = run_check(proof["cmd"], tree)
        if before != _git(tree, "diff", "--binary", "HEAD"):
            raise WorkflowError("proof run modified tracked files")
        if overlay is not None:
            for name, data in overlay.items():
                path = _proof_path(tree, name)
                if not path.is_file() or path.read_bytes() != (b"" if truncate else data):
                    raise WorkflowError(f"proof run modified overlay: {name}")
    except Exception as exc:
        record["ok"] = None
        record["error"] = f"{proof['id']}: {location}" + (f" run {run}" if run else "") + f": infrastructure: {exc}"
    finally:
        try:
            baseline = baselines.get(tree)
            _reset(tree, None if isinstance(baseline, Exception) else baseline)
        except Exception as exc:
            record["ok"] = None
            record["error"] = record.get("error", "") + f" {proof['id']}: {location}: cleanup infrastructure: {exc}"
    record["output"] = _scrub(record["output"], tree)
    if "error" in record:
        record["error"] = _scrub(record["error"], tree)
    return record


def _kind_failure(proof, ok, out, where):
    ident = proof["id"]
    if proof["kind"] == "preserve":
        return None if ok else f"{ident}: preserve proof fails at {where}"
    if ok:
        return f"{ident}: tautology; passes at {where}; for a review-added regression supply the defective implementation commit as parent"
    if proof["kind"] == "defect" and proof["expectRed"] not in out:
        return f"{ident}: wrong-reason red at {where}"
    return None


def _proof_digest(proof):
    return hashlib.sha256(json.dumps(proof, sort_keys=True).encode()).hexdigest()


def _remove_trees(repo, trees):
    errors = []
    for tree in reversed(trees):
        try:
            _git(repo, "worktree", "remove", "--force", str(tree))
        except Exception as exc:
            errors.append(f"cleanup infrastructure {tree}: {exc}")
    if errors:
        try:
            _git(repo, "worktree", "prune")
        except Exception as exc:
            errors.append(f"cleanup prune infrastructure: {exc}")
    return errors


def freeze(worktree, manifest_path, proofs, slug, *, base=None, setup=None,
           previous=None, revise=None, max_checks=MAX_PROOFS):
    """Freeze working-tree proofs against pinned, setup-preserving isolated baselines."""
    _limit(max_checks)
    if base is not None and (not isinstance(base, str) or not base.strip() or "\0" in base):
        raise WorkflowError("base must be a nonblank commit reference")
    draft = {"schemaVersion": 1, "slug": slug, "proofs": proofs}
    if setup is not None:
        draft["setup"] = setup
    _validate(draft, max_checks, frozen=False)
    if previous is not None and (not isinstance(previous, (tuple, list)) or len(previous) != 2):
        raise WorkflowError("previous must be (manifest_path, sha256)")
    if revise is None:
        revise = {}
    if not isinstance(revise, dict) or any(not isinstance(k, str) or not isinstance(v, str) or not v.strip() for k, v in revise.items()):
        raise WorkflowError("revise must map proof IDs to nonblank reasons")
    root, path = Path(worktree).resolve(), Path(manifest_path).resolve()
    old = load_manifest(previous[0], previous[1], max_checks) if previous else {"proofs": []}
    old_proofs = {p["id"]: p for p in old["proofs"]}
    if set(revise) - set(old_proofs):
        raise WorkflowError("revision names an unknown previous proof")
    resolved_base = _commit(root, base if base is not None else old.get("base", "HEAD"))
    setup = old.get("setup") if previous and setup is None else setup
    if previous and (slug != old["slug"] or resolved_base != old["base"] or setup != old.get("setup")):
        raise WorkflowError("re-freeze slug, base and setup are immutable; start a new lineage")
    head = _commit(root, "HEAD")
    if not _ancestor(root, resolved_base, head):
        raise WorkflowError("base is not an ancestor of HEAD")
    manifest = {"schemaVersion": 1, "slug": slug, "base": resolved_base, "proofs": []}
    if setup is not None:
        manifest["setup"] = setup
    for proof in proofs:
        ident = proof["id"]
        baseline = proof.get("parent", old_proofs.get(ident, {}).get("parent", resolved_base))
        item = dict(proof, parent=_commit(root, baseline), sha256={})
        if not _ancestor(root, resolved_base, item["parent"]) or not _ancestor(root, item["parent"], head):
            raise WorkflowError(f"{ident}: parent is not an ancestor of HEAD at or after base")
        _command_files(item, (root,))
        for name in item["files"]:
            source = _proof_path(root, name)
            if not source.is_file():
                raise WorkflowError(f"{ident}: missing regular proof file: {name}")
            ignored = subprocess.run(["git", "-C", str(root), "check-ignore", "-q", "--", name], capture_output=True)
            if ignored.returncode == 0:
                raise WorkflowError(f"{ident}: ignored proof file: {name}")
            if ignored.returncode != 1:
                raise WorkflowError(f"{ident}: check-ignore infrastructure: {ignored.stderr.decode(errors='replace')}")
            if _git(root, "hash-object", f"--path={name}", "--", name) != _git(root, "hash-object", "--no-filters", "--", name):
                raise WorkflowError(f"{ident}: normalise line endings/clean filters: {name}")
            item["sha256"][name] = _sha(source)
        manifest["proofs"].append(item)
    names = {name for proof in manifest["proofs"] for name in proof["files"]}
    for entry in _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames").split("\0"):
        if entry and entry[3:] not in names:
            raise WorkflowError(f"unfrozen change: {entry[3:]}; add it to a proof's files or revert")
    current = {p["id"]: p for p in manifest["proofs"]}
    revisions, changed = list(old.get("revisions", [])), set()
    for ident, proof in old_proofs.items():
        if current.get(ident) != proof:
            if ident not in revise:
                raise WorkflowError(f"{ident}: silent proof change; supply revise reason")
            revisions.append({"id": ident, "oldSha256": _proof_digest(proof), "reason": revise[ident]})
            changed.add(ident)
    if revisions:
        manifest["revisions"] = revisions
    _validate(manifest, max_checks)
    trees, baselines, failure = [], {}, None
    with tempfile.TemporaryDirectory(prefix="omp-freeze-") as scratch:
        try:
            for proof in manifest["proofs"]:
                if proof["id"] in old_proofs and proof["id"] not in changed:
                    continue
                tree = Path(scratch) / f"tree-{len(trees)}"
                _git(root, "worktree", "add", "--detach", str(tree), proof["parent"])
                trees.append(tree)
                overlay = {name: _proof_path(root, name).read_bytes() for name in proof["files"]}
                record = _execute(tree, proof, baselines, setup, overlay, location="baseline", roots=(root,))
                error = record.get("error") or _kind_failure(proof, record["ok"], record["output"], "baseline")
                if error:
                    raise WorkflowError(error)
                if proof["kind"] == "defect":
                    control = _execute(tree, proof, baselines, setup, overlay, truncate=True, location="negative control", roots=(root,))
                    if control.get("error"):
                        raise WorkflowError(control["error"])
                    if proof["expectRed"] in control["output"]:
                        raise WorkflowError(f"{proof['id']}: expectRed survives negative control")
            errors = _hash_failures(root, manifest)
            if errors:
                raise WorkflowError("; ".join(errors))
        except Exception as exc:
            failure = str(exc)
        finally:
            cleanup = _remove_trees(root, trees)
        if failure or cleanup:
            raise WorkflowError("; ".join(([failure] if failure else []) + cleanup))
    _atomic_json(path, manifest)
    return path, _sha(path)


def _common(repo):
    common = Path(_git(repo, "rev-parse", "--git-common-dir").strip())
    return (common if common.is_absolute() else Path(repo) / common).resolve()


def _atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            name = stream.name
            json.dump(value, stream, indent=2)
            stream.write("\n")
        os.replace(name, path)
    finally:
        if name:
            Path(name).unlink(missing_ok=True)


def _write_pass_marker(repo, head, kind):
    marker = _common(repo) / "omp-prove" / f"{head}.json"
    _atomic_json(marker, {"schemaVersion": 1, "head": head, "kind": kind, "status": "pass"})
    return marker


def _range(repo, parent, head, target_ref):
    parent, target = _commit(repo, parent), _commit(repo, target_ref)
    if not _ancestor(repo, parent, head, strict=True):
        raise WorkflowError("parent must be a strict ancestor of head")
    if parent != _git(repo, "merge-base", target, head).strip():
        raise WorkflowError("parent must equal merge-base(target_ref, head)")
    return parent, target


def exempt_docs_only(repo, parent, head, *, target_ref):
    """Record narrow path-based eligibility, not an inference about runtime usage."""
    repo = Path(repo).resolve()
    head = _commit(repo, head)
    (_common(repo) / "omp-prove" / f"{head}.json").unlink(missing_ok=True)
    parent, _ = _range(repo, parent, head, target_ref)
    paths = [p for p in _git(repo, "diff", "--name-only", "--no-renames", "-z", parent, head).split("\0") if p]
    if not paths:
        raise WorkflowError("docs-only exemption requires changed documentation")
    for name in paths:
        path = Path(name.casefold())
        forbidden = {"skills", "agents", "rules", "prompts", "commands", ".agents", ".claude", ".omp", ".github"}
        basenames = {"skill.md", "claude.md", "codex.md", "gemini.md", "agents.md", "agents.override.md"}
        root_doc = len(path.parts) == 1 and path.name in {"readme.md", "changelog.md", "contributing.md", "license", "license.md", "license.txt"}
        nested_doc = len(path.parts) > 1 and path.parts[0] == "docs" and path.suffix in {".md", ".txt", ".png", ".jpg", ".jpeg", ".svg", ".gif"}
        if set(path.parts) & forbidden or path.name in basenames or path.name.endswith("instructions.md") or not (root_doc or nested_doc):
            raise WorkflowError(f"docs-only exemption rejected ineligible/non-documentation path: {name}")
    return _write_pass_marker(repo, head, "docs-only")


def gate_policy(gate):
    """Mirror rails gate acceptance; return only the fields consumed here."""
    if not isinstance(gate, dict) or type(gate.get("schemaVersion")) is not int or gate["schemaVersion"] != 2:
        return None
    checks = gate.get("checks", {})
    if not isinstance(checks, dict):
        return None
    def argv(value):
        return isinstance(value, list) and bool(value) and all(isinstance(arg, str) and arg.strip() for arg in value)
    if any(key in checks and not argv(checks[key]) for key in ("commit", "ci")) or ("pathCheck" in gate and not argv(gate["pathCheck"])):
        return None
    if gate.get("ciHost", "github") not in ("github", "nuc") or gate.get("agentMerge", "allowed") not in ("allowed", "disabled"):
        return None
    if "pathCheckTimeoutMs" in gate and (type(gate["pathCheckTimeoutMs"]) is not int or not 0 < gate["pathCheckTimeoutMs"] <= 9007199254740991):
        return None
    if "testGlobs" in gate and (not isinstance(gate["testGlobs"], list) or not all(isinstance(x, str) for x in gate["testGlobs"])):
        return None
    return {"commit": checks.get("commit"), "pathCheck": gate.get("pathCheck")}


def gate_changes(paths, root, gate, diagnostics=None):
    """Discover bounded literal file references; never interpret shell/source syntax."""
    diagnostics = diagnostics if diagnostics is not None else []
    named = set()
    def candidate(token):
        name = token.removeprefix("./")
        try:
            return name if _proof_path(root, name).is_file() else None
        except (WorkflowError, OSError):
            return None
    commands = gate.get("checks", {}) if isinstance(gate, dict) else {}
    for argv in (commands.get("commit"), commands.get("ci"), gate.get("pathCheck")):
        for arg in argv or []:
            for token in (arg, arg.split("=", 1)[1]) if "=" in arg else (arg,):
                name = candidate(token)
                if name:
                    named.add(name)
    for name in tuple(sorted(named)):
        try:
            text = _proof_path(root, name).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            diagnostics.append(f"gate reference {name}: {exc}")
            continue
        for token in re.findall(r"[\w./-]+", text):
            found = candidate(token)
            if found:
                named.add(found)
    return [path for path in paths if path in named or path in (".omp/gate.json", "lefthook.yml", ".pre-commit-config.yaml") or path.startswith((".github/workflows/", ".githooks/"))]


def deterministic_checks(parent_tree, head_tree, manifest, gate, paths, proof_parents=None, source_root=None):
    """Run proof executions only; repository gates belong to hook/CI."""
    result = {"proofs": {}, "blocking": [], "gateChanges": [], "gateDiagnostics": []}
    diagnostics = result["gateDiagnostics"]
    try:
        parent_gate = json.loads((parent_tree / ".omp/gate.json").read_text())
        if gate_policy(parent_gate) is None:
            raise WorkflowError("invalid parent gate schema")
    except (OSError, ValueError, WorkflowError) as exc:
        diagnostics.append(f"parent .omp/gate.json: {exc}")
        parent_gate = {}
    result["gateChanges"] = sorted(set(gate_changes(paths, head_tree, gate, diagnostics)) | set(gate_changes(paths, parent_tree, parent_gate, diagnostics)))
    failures = result["blocking"]
    failures.extend(_hash_failures(head_tree, manifest))
    if failures:
        return result
    # Preserve proof bytes before any scratch tree reset or overlay.
    overlays = {p["id"]: {name: _proof_path(head_tree, name).read_bytes() for name in p["files"]} for p in manifest["proofs"]}
    roots = tuple(x for x in (source_root, parent_tree, head_tree, *(proof_parents or {}).values()) if x is not None)
    baselines = {}
    for proof in manifest["proofs"]:
        runs = result["proofs"].setdefault(proof["id"], [])
        for n in (1, 2):
            record = _execute(head_tree, proof, baselines, manifest.get("setup"), location="head", run=n, roots=roots)
            runs.append(record)
            if record.get("error"):
                failures.append(record["error"])
            elif not record["ok"]:
                failures.append(f"{proof['id']}: head run {n} failed")
            elif proof["kind"] == "defect" and proof["expectRed"] in record["output"]:
                failures.append(f"{proof['id']}: expectRed present at head")
        if failures:
            continue
        tree = (proof_parents or {}).get(proof.get("parent"), parent_tree)
        record = _execute(tree, proof, baselines, manifest.get("setup"), overlays[proof["id"]], location="parent", roots=roots)
        runs.append(record)
        failure = record.get("error") or _kind_failure(proof, record["ok"], record["output"], "parent")
        if failure:
            failures.append(failure)
        if proof["kind"] == "defect" and not record.get("error"):
            control = _execute(tree, proof, baselines, manifest.get("setup"), overlays[proof["id"]], truncate=True, location="negative control", roots=roots)
            runs.append(control)
            if control.get("error"):
                failures.append(control["error"])
            elif proof["expectRed"] in control["output"]:
                failures.append(f"{proof['id']}: expectRed survives negative control")
    return result


def _run_verifier(brief, proof_ids, model=None):
    schema = {"type": "object", "required": ["status", "blocking", "followUps", "criteria"],
              "additionalProperties": False, "properties": {
                  "status": {"enum": ["pass", "fail"]},
                  "blocking": {"type": "array", "items": {"type": "string"}},
                  "followUps": {"type": "array", "items": {"type": "string"}},
                  "criteria": {"type": "array", "items": {"type": "object", "required": ["proofId", "command", "observed", "verdict"],
                      "additionalProperties": False, "properties": {
                          "proofId": {"enum": sorted(proof_ids)}, "command": {"type": "string", "minLength": 1},
                          "observed": {"type": "string", "minLength": 1}, "verdict": {"enum": ["pass", "fail"]}}}}}}
    kwargs = {"agent": "verifier", "label": "Prove", "schema": schema}
    if model is not None:
        kwargs["model"] = model
    return wait([agent(brief, **kwargs)], raise_errors=False)[0]


def _authors(repo, head):
    worktree = str(Path(repo).resolve())
    branch = _git(repo, "symbolic-ref", "--quiet", "HEAD").strip()
    if not branch.startswith("refs/heads/") or _commit(repo, "HEAD") != head:
        raise WorkflowError("prove source must be on a named branch at requested head")
    key = hashlib.sha256((worktree + "\0" + branch).encode()).hexdigest()
    path = _common(repo) / "omp-authors" / f"{key}.json"
    if not path.exists():
        raise WorkflowError(f"no author ledger for {worktree} {branch}; authorship unknown")
    try:
        ledger = json.loads(path.read_text())
        if (not isinstance(ledger, dict) or type(ledger.get("schemaVersion")) is not int or ledger["schemaVersion"] != 1 or ledger.get("worktree") != worktree
                or ledger.get("branch") != branch or not isinstance(ledger.get("authors"), list) or not ledger["authors"]
                or any(not isinstance(a, dict) or set(a) != {"family", "model", "agent"}
                       or any(not isinstance(v, str) or not v.strip() for v in a.values()) for a in ledger["authors"])):
            raise WorkflowError("invalid author ledger")
        identities = [(a["family"], a["model"], a["agent"]) for a in ledger["authors"]]
        if identities != sorted(set(identities)):
            raise WorkflowError("invalid author ledger: authors must be sorted and deduplicated")
    except (OSError, ValueError) as exc:
        raise WorkflowError(f"unreadable author ledger: {exc}") from exc
    return branch, ledger["authors"]


def _json_safe(value) -> Any:
    try:
        return json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError):
        if isinstance(value, dict):
            return {str(k): _json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_json_safe(v) for v in value]
        return repr(value)


def _verdict(result, verdict, ids):
    result["verifier"] = _json_safe(verdict) if isinstance(verdict, dict) else {"error": repr(verdict)}
    valid = (isinstance(verdict, dict) and set(verdict) == {"status", "blocking", "followUps", "criteria"}
             and verdict.get("status") in ("pass", "fail")
             and all(isinstance(verdict.get(k), list) and all(isinstance(v, str) for v in verdict[k]) for k in ("blocking", "followUps"))
             and isinstance(verdict.get("criteria"), list)
             and all(isinstance(c, dict) and set(c) == {"proofId", "command", "observed", "verdict"}
                     and all(isinstance(c.get(k), str) and c[k].strip() for k in ("proofId", "command", "observed"))
                     and c.get("verdict") in ("pass", "fail") for c in verdict["criteria"]))
    if not valid:
        result["blocking"].append(f"verifier returned invalid or failed result: {result['verifier']!r}")
        return
    result["blocking"].extend(verdict["blocking"])
    result["followUps"] = verdict["followUps"]
    covered = {c["proofId"] for c in verdict["criteria"]}
    for ident in sorted(ids - covered):
        result["blocking"].append(f"verifier omitted {ident}")
    for ident in sorted(covered - ids):
        result["blocking"].append(f"verifier unknown proof ID: {ident}")
    for criterion in verdict["criteria"]:
        if criterion["verdict"] == "fail":
            result["blocking"].append(f"verifier criterion failed: {criterion['proofId']}")
    if verdict["status"] == "fail" and not verdict["blocking"]:
        result["blocking"].append("verifier failed without reason")


def prove(repo, parent, head, manifest_path, manifest_sha256, objective, *, target_ref,
          max_checks=MAX_PROOFS, verifier_model=None):
    """Run frozen proofs then one cross-family verifier; CI independently owns repository gates."""
    result = {"status": "fail", "deterministic": {}, "verifier": {}, "blocking": [], "followUps": [],
              "authors": [], "verifierModel": _json_safe(verifier_model)}
    trees = []
    try:
        repo = Path(repo).resolve()
        head = _commit(repo, head)
        common = _common(repo)
        (common / "omp-prove" / f"{head}.json").unlink(missing_ok=True)
        _limit(max_checks)
        if not isinstance(objective, str) or not objective.strip():
            raise WorkflowError("objective must be nonblank")
        if verifier_model is not None and (not isinstance(verifier_model, str) or not verifier_model.strip()):
            raise WorkflowError("verifier_model must be an exact model selector")
        parent, target = _range(repo, parent, head, target_ref)
        manifest = load_manifest(manifest_path, manifest_sha256, max_checks)
        base = manifest["base"]
        if not _ancestor(repo, base, parent):
            raise WorkflowError("base must be an ancestor of parent")
        branch, result["authors"] = _authors(repo, head)
        result.update(worktree=str(repo), branch=branch)
        try:
            gate_process = subprocess.run(["git", "-C", str(repo), "show", f"{head}:.omp/gate.json"], capture_output=True)
            if gate_process.returncode:
                raise WorkflowError(gate_process.stderr.decode(errors="replace"))
            gate = json.loads(gate_process.stdout.decode("utf-8"))
        except WorkflowError as exc:
            raise WorkflowError(f"{GUARDRAIL_PREREQUISITE}; head gate unreadable: {exc}") from exc
        except (ValueError, UnicodeError) as exc:
            raise WorkflowError(f"head .omp/gate.json invalid JSON/encoding: {exc}") from exc
        policy = gate_policy(gate)
        if policy is None:
            raise WorkflowError(".omp/gate.json has an unsupported schemaVersion or invalid fields")
        if not policy["commit"] or not policy["pathCheck"]:
            raise WorkflowError(GUARDRAIL_PREREQUISITE)
        for proof in manifest["proofs"]:
            commit = proof.get("parent", base)
            if not _ancestor(repo, commit, head, strict=True) or not _ancestor(repo, base, commit):
                raise WorkflowError(f"{proof['id']}: proof parent must be an ancestor of head at or after base")
            _command_files(proof, (repo,))
        snapshot = (_git(repo, "status", "--porcelain=v2"), _commit(repo, "HEAD"))
        with tempfile.TemporaryDirectory(prefix="omp-prove-") as scratch:
            try:
                labels = []
                def tree_at(label, ref):
                    tree = Path(scratch) / f"tree-{len(trees)}"
                    _git(repo, "worktree", "add", "--detach", str(tree), ref)
                    trees.append(tree)
                    labels.append({"label": label, "sha": ref, "path": str(tree)})
                    return tree
                parent_tree, head_tree = tree_at("PR parent", parent), tree_at("head", head)
                parent_trees = {parent: parent_tree}
                for proof in manifest["proofs"]:
                    commit = proof.get("parent", base)
                    if commit not in parent_trees:
                        parent_trees[commit] = tree_at(f"baseline {proof['id']}", commit)
                    labels.append({"label": f"baseline {proof['id']}", "sha": commit, "path": str(parent_trees[commit])})
                paths = [p for p in _git(repo, "diff", "--name-only", "--no-renames", "-z", parent, head).split("\0") if p]
                deterministic = deterministic_checks(parent_tree, head_tree, manifest, gate, paths, parent_trees, repo)
                result["deterministic"] = deterministic
                result["blocking"].extend(deterministic["blocking"])
                if not result["blocking"]:
                    for proof in manifest["proofs"]:
                        commits = _git(repo, "rev-list", "--reverse", f"{proof.get('parent', base)}..{head}", "--", *proof["files"]).splitlines()
                        for commit in commits:
                            if commit == head:
                                continue
                            tree = tree_at(f"earlier {proof['id']}", commit)
                            present = [_proof_path(tree, name).is_file() for name in proof["files"]]
                            if not any(present):
                                continue
                            if not all(present):
                                result["blocking"].append(f"{proof['id']}: partial proof at {commit}")
                                continue
                            record = _execute(tree, proof, {}, manifest.get("setup"), location=f"earlier commit {commit}", roots=(repo, *trees))
                            deterministic["proofs"][proof["id"]].append(record)
                            if record.get("error"):
                                result["blocking"].append(record["error"])
                            elif not record["ok"]:
                                result["blocking"].append(f"{proof['id']}: proof red at earlier commit {commit}")
                if not result["blocking"]:
                    full_path = Path(scratch) / "deterministic.json"
                    _atomic_json(full_path, deterministic)
                    brief_checks = _json_safe(deterministic)
                    for runs in brief_checks["proofs"].values():
                        for record in runs:
                            if len(record["output"]) > 4096:
                                record["output"] = f"[TRUNCATED; full evidence: {full_path}]\n" + record["output"][-4096:]
                    proof_ids = sorted(p["id"] for p in manifest["proofs"])
                    brief = (
                        "Read skill://codegraph/references/readiness-and-bootstrap.md, "
                        "skill://codegraph/references/query-selection.md and skill://git-tool before discovery/git.\n"
                        f"Source repo (never edit): {repo}\nPR target/ref: {target_ref} ({target})\n"
                        f"PR parent SHA: {parent}\nHead SHA: {head}\nDiff range: {parent}..{head}\n"
                        f"Read-only scratch trees: {json.dumps(labels)}; your TMPDIR is allowed. Never spawn agents.\n"
                        f"Objective: {objective}\nAuthors: {json.dumps(result['authors'])}\nVerifier model override: {verifier_model}\n"
                        f"Manifest: {json.dumps(manifest)}\nDeterministic: {json.dumps(brief_checks)}\n"
                        f"Full deterministic evidence: {full_path}\nDiff stat: {_git(repo, 'diff', '--stat', parent, head)}\n"
                        "Drive every criterion live; return proofId, command, observed, verdict. "
                        f"Each criteria entry's proofId must be one of: {', '.join(proof_ids)}; "
                        "put any other observation in blocking or followUps. Audit undeclared "
                        "harness/config tampering, wrong/stale docs caused by this PR and diff scope/coverage. "
                        "Each finding needs exact command, actual and required output; omit anything not run. " + BLOCKING_RULE)
                    context = common / "omp-prove" / "verifier-context.json"
                    try:
                        _atomic_json(context, {"schemaVersion": 1, "worktree": str(repo), "branch": branch, "head": head})
                        try:
                            verdict = _run_verifier(brief, proof_ids) if verifier_model is None else _run_verifier(brief, proof_ids, model=verifier_model)
                        except Exception as exc:
                            verdict = exc
                        _verdict(result, verdict, set(proof_ids))
                    finally:
                        context.unlink(missing_ok=True)
                        after = (_git(repo, "status", "--porcelain=v2"), _commit(repo, "HEAD"))
                        if after != snapshot:
                            result["blocking"].append("source checkout changed during verification")
                    if _sha(manifest_path) != manifest_sha256:
                        result["blocking"].append("manifest edited after freeze")
                    result["blocking"].extend(_hash_failures(head_tree, manifest))
            finally:
                result["blocking"].extend(_remove_trees(repo, trees))
        if not result["blocking"]:
            _write_pass_marker(repo, head, "prove")
            result["status"] = "pass"
    except Exception as exc:
        result["blocking"].append(f"infrastructure: {exc}")
    return result


def blind_label(batch_files, rubric_path, labels, labellers=("scout", "reviewer")):
    """Two independent read-only sessions; humans confirm every label before writing."""
    if (not isinstance(labellers, (list, tuple)) or len(labellers) != 2
            or not all(isinstance(who, str) for who in labellers) or set(labellers) != {"scout", "reviewer"}):
        raise WorkflowError("labellers must be two distinct read-only agents: scout and reviewer")
    if not isinstance(batch_files, (list, tuple)) or 2 * len(batch_files) > MAX_AGENTS:
        raise WorkflowError(f"labellers exceeds cap {MAX_AGENTS}; merge batches")
    if not isinstance(labels, (list, tuple)) or not labels or any(not isinstance(x, str) or not x.strip() for x in labels):
        raise WorkflowError("labels must be nonblank strings")
    batches = []
    for filename in batch_files:
        try:
            batch = json.loads(Path(filename).read_text())
        except (OSError, ValueError) as exc:
            raise WorkflowError(f"invalid batch: {exc}") from exc
        cases = batch.get("cases") if isinstance(batch, dict) else batch
        if not isinstance(cases, list) or any(not isinstance(c, dict) or not isinstance(c.get("id"), str) or not c["id"].strip() for c in cases):
            raise WorkflowError("batch must contain cases with nonblank string IDs")
        ids = [c["id"] for c in cases]
        if len(set(ids)) != len(ids):
            raise WorkflowError("duplicate case ID")
        batches.append(ids)
    schema = {"type": "object", "required": ["cases"], "additionalProperties": False, "properties": {"cases": {
        "type": "array", "items": {"type": "object", "required": ["id", "label", "evidence", "rationale"], "additionalProperties": False,
        "properties": {"id": {"type": "string"}, "label": {"enum": list(labels)}, "evidence": {"type": "string"}, "rationale": {"type": "string"}}}}}}
    jobs, failed, votes, duplicates = [], [], {}, set()
    for n, filename in enumerate(batch_files):
        for who in labellers:
            try:
                handle = agent(f"Read-only. Label every case in {filename} using rubric {rubric_path}. Read only those two files. "
                               "Quote deciding evidence; rationale in 1-2 plain sentences. Human confirms every label before writing.",
                               agent=who, label=f"Label{n:02d}{who[:3]}", schema=schema)
                jobs.append((n, who, handle))
            except Exception as exc:
                failed.append({"batch": n, "labeller": who, "error": repr(exc)})
    try:
        results = wait([handle for _, _, handle in jobs], raise_errors=False)
    except Exception as exc:
        results = [exc] * len(jobs)
    for index, (n, who, _) in enumerate(jobs):
        result = results[index] if index < len(results) else None
        cases = result.get("cases") if isinstance(result, dict) else None
        if (not isinstance(cases, list) or any(not isinstance(c, dict) or set(c) != {"id", "label", "evidence", "rationale"}
                or any(not isinstance(c.get(k), str) for k in ("id", "label", "evidence", "rationale"))
                or c["label"] not in labels or c["id"] not in batches[n] for c in cases)):
            failed.append({"batch": n, "labeller": who, "error": f"invalid labeller result: {result!r}"})
            continue
        for case in cases:
            key = (n, case["id"])
            if who in votes.setdefault(key, {}):
                duplicates.add(key)
            votes[key][who] = case
    agreed, disputed = [], []
    for n, ids in enumerate(batches):
        for ident in ids:
            key = (n, ident)
            value = votes.get(key, {})
            if key not in duplicates and len(value) == 2 and value[labellers[0]]["label"] == value[labellers[1]]["label"]:
                agreed.append({"batch": n, "id": ident, "label": value[labellers[0]]["label"]})
            else:
                disputed.append({"batch": n, "id": ident, "votes": value})
    return {"agreed": agreed, "disputed": disputed, "failed": failed}
