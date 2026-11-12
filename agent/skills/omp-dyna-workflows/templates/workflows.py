# pyright: reportUndefinedVariable=false
# agent and wait are OMP eval kernel globals, injected when this file is %load-ed.
"""Proof-first verification (TDD: red at base, green at head) and blind labels; load in the OMP Python eval kernel."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any
from urllib.parse import quote

MAX_AGENTS = 16
MAX_PROOFS = 16
CHECK_TIMEOUT = 900
GUARDRAIL_PREREQUISITE = (
    "missing pathCheck; follow skill://omp-dyna-workflows/SKILL.md "
    "owner-approval prerequisite (skill://setup-repository-guardrails)")
HEAD_RUNS = 2
JOURNEY_RUNS = 3
RETIRED_PROOF_FIELDS = ("kind", "expectRed")


class WorkflowError(RuntimeError):
    pass


class ProveBlocked(WorkflowError):
    """A prerequisite failed; report the reason itself rather than as infrastructure."""


MAX_SAFE_INTEGER = 9007199254740991
# Harness variables the omp runtime exports to its tools. Repo commands must behave as they do
# without the harness, so run_check removes them at the exec boundary.
HARNESS_ENV = ("AGENT",)
# Captured at load: the eval kernel's %load sets __file__ in the shared namespace, and a later
# %load of another script would overwrite it.
_LOADED_FROM = globals().get("__file__")


def _harness_root():
    """The oh-mi-pi-config checkout this file belongs to (<root>/agent/skills/omp-dyna-workflows/templates/)."""
    if not _LOADED_FROM:
        raise WorkflowError("cannot locate the harness: workflows.py was loaded without __file__")
    return Path(_LOADED_FROM).resolve().parents[4]


def _records_root():
    """Root that holds repos/<owner>/<name>/ records: the harness checkout itself."""
    return _harness_root()


_RESOLVER = []


def _resolver():
    """The harness's repo_record module (agent/lib/repo_record.py), loaded once by path."""
    if not _RESOLVER:
        path = _harness_root() / "agent/lib/repo_record.py"
        spec = importlib.util.spec_from_file_location("omp_repo_record", path)
        if spec is None or spec.loader is None:
            raise WorkflowError(f"cannot load the record resolver: {path}")
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except (OSError, SyntaxError, ImportError) as exc:
            raise WorkflowError(f"cannot load the record resolver: {exc}") from exc
        _RESOLVER.append(module)
    return _RESOLVER[0]


def read_record(repo):
    """(record directory, gate.json object) for the clone at `repo`, or (None, None) without a record.

    The record is the personal, gitignored `repos/<owner>/<name>/` directory under the harness,
    named by the clone's origin remote; worktrees resolve to their main checkout's record. A record
    that exists but is unreadable or that rails would reject raises.
    """
    resolver = _resolver()
    record = resolver.find_record(repo, _records_root())
    if record is None:
        return None, None
    try:
        gate = resolver.load_gate(record)
    except resolver.RecordError as exc:
        raise WorkflowError(f"record {exc}") from exc
    reason = gate_reason(gate)
    if reason is not None:
        raise WorkflowError(f"record {record / resolver.GATE_FILE}: {reason}")
    return record, gate


def no_record_reason(repo):
    """Why the clone at `repo` has no record, in rails' words: the slug (or "none") and the reason."""
    resolver = _resolver()
    slug, _, reason = resolver.resolve_record(repo, _records_root())
    return resolver.off_text(slug, reason or "no per-repo record")


def _is_argv(value):
    return isinstance(value, list) and bool(value) and all(
        isinstance(arg, str) and arg.strip() and "\0" not in arg for arg in value)


def run_check(cmd, cwd):
    """Run an argv proof or repo command without harness variables, retaining complete, replacement-decoded diagnostics."""
    if not _is_argv(cmd):
        raise WorkflowError("cmd must be argv")
    env = {key: value for key, value in os.environ.items() if key not in HARNESS_ENV}
    try:
        p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True,
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


def _gh(repo, *args):
    try:
        process = subprocess.run(["gh", *args], cwd=repo, capture_output=True,
                                 text=True, errors="replace", timeout=20)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        raise WorkflowError(f"gh unavailable: {exc}") from exc
    if process.returncode:
        raise WorkflowError(f"gh failed: {process.stderr.strip()}")
    return process.stdout


def _github(repo, *args):
    output = _gh(repo, *args)
    try:
        return json.loads(output)
    except ValueError as exc:
        raise WorkflowError(f"gh unavailable or invalid response: {exc}") from exc


def _github_repo(repo):
    """Return OWNER/REPO of origin (or the only remote) when it is a GitHub URL."""
    remotes = _git(repo, "remote").splitlines()
    remote = "origin" if "origin" in remotes else remotes[0] if len(remotes) == 1 else None
    if remote is None:
        raise WorkflowError("cannot resolve primary GitHub remote")
    url = _git(repo, "remote", "get-url", remote).strip()
    match = re.fullmatch(r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?", url)
    if not match:
        raise WorkflowError("primary remote is not a validated GitHub repository")
    return match[1]


def _pr_at_head(repo, head, fields):
    """The one open PR whose head is exactly `head`, with the requested gh JSON fields."""
    prs = _github(repo, "pr", "list", "--search", head, "--state", "open", "--json", ",".join(("headRefOid", *fields)))
    matches = [pr for pr in prs if isinstance(pr, dict) and pr.get("headRefOid") == head] if isinstance(prs, list) else []
    if len(matches) != 1:
        raise WorkflowError("no unique open PR at requested head")
    return matches[0]


def _required_context_state(repo, head, context):
    """State of the gate's required CI context on the exact head, from any poster: a commit status or a check run.

    Mirrors the rails merge rule: "success" when a status with that context is success or a check run with
    that name completed with conclusion success.
    """
    slug = _github_repo(repo)
    combined = _github(repo, "api", f"repos/{slug}/commits/{head}/status?per_page=100")
    statuses = [item for item in (combined.get("statuses") if isinstance(combined, dict) else None) or []
                if isinstance(item, dict) and item.get("context") == context]
    listed = _github(repo, "api", f"repos/{slug}/commits/{head}/check-runs?check_name={quote(context, safe='')}&per_page=100")
    runs = [item for item in (listed.get("check_runs") if isinstance(listed, dict) else None) or []
            if isinstance(item, dict) and item.get("name") == context]
    if (any(item.get("state") == "success" for item in statuses)
            or any(item.get("status") == "completed" and item.get("conclusion") == "success" for item in runs)):
        return "success"
    if statuses:
        return str(statuses[0].get("state"))
    if runs:
        return f"{runs[0].get('status')}/{runs[0].get('conclusion') or ''}"
    return "missing"


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
    """Lexical manifest checks. Zero proofs is valid: a PR with no behaviour change freezes none."""
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
    if not isinstance(proofs, list) or len(proofs) > max_checks:
        raise WorkflowError("proofs must be a list within max_checks")
    ids = set()
    for proof in proofs:
        if not isinstance(proof, dict):
            raise WorkflowError("invalid proof")
        ident = proof.get("id")
        if not isinstance(ident, str) or not ident.strip() or ident in ids:
            raise WorkflowError("missing or duplicate proof id")
        ids.add(ident)
        retired = [field for field in RETIRED_PROOF_FIELDS if field in proof]
        if retired:
            raise WorkflowError(f"{ident}: {', '.join(retired)} retired; every proof is red at base and green at head")
        if not isinstance(proof.get("criterion"), str) or not proof["criterion"].strip():
            raise WorkflowError(f"{ident}: missing criterion")
        if "parent" in proof and (not isinstance(proof["parent"], str) or not proof["parent"].strip() or "\0" in proof["parent"]
                                  or (frozen and not _hex(proof["parent"], 40))):
            raise WorkflowError(f"{ident}: parent must be 40-hex" if frozen else f"{ident}: invalid parent")
        if not _is_argv(proof.get("cmd")):
            raise WorkflowError(f"{ident}: cmd must be argv")
        if "journey" in proof and type(proof["journey"]) is not bool:
            raise WorkflowError(f"{ident}: journey must be a boolean")
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


def _execute(tree, proof, baselines, setup=None, overlay=None, *, location="head", run=None, roots=()):
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
                dest.write_bytes(data)
        _command_files(proof, (tree, *roots))
        before = _git(tree, "diff", "--binary", "HEAD")
        record["ok"], record["output"] = run_check(proof["cmd"], tree)
        if before != _git(tree, "diff", "--binary", "HEAD"):
            raise WorkflowError("proof run modified tracked files")
        if overlay is not None:
            for name, data in overlay.items():
                path = _proof_path(tree, name)
                if not path.is_file() or path.read_bytes() != data:
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


def _red_at_base_failure(proof, record, marker):
    """The one proof rule at base: any failing run is red, unless the record's doctor marker shows the environment failed."""
    if record.get("error"):
        return record["error"]
    if record["ok"]:
        return (f"{proof['id']}: passes at baseline; a proof must fail at base and pass at head. "
                "For a regression found after implementation, supply the defective implementation commit as parent")
    if marker and marker in record["output"]:
        return f"{proof['id']}: red came from the environment doctor, not the behaviour"
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


class FreezeResult(tuple):
    """`(manifest_path, sha256)`; `formatted` lists paths checks.format changed, for the agent to commit."""
    def __new__(cls, path, digest, formatted=()):
        result = super().__new__(cls, (path, digest))
        result.formatted = list(formatted)
        return result


def _profile_path(record):
    return Path(record) / _resolver().PROFILE_FILE


def _doctor_marker(record):
    """verification_surface.doctor_marker from the record profile's front matter; absent record, profile or value skips."""
    if record is None:
        return None
    path = _profile_path(record)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError) as exc:
        raise WorkflowError(f"{path} unreadable: {exc}") from exc
    if not lines or lines[0].strip() != "---":
        return None
    end = next((index for index, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
    if end is None:
        raise WorkflowError(f"{path} front matter has no closing '---' line")
    try:
        values = json.loads("\n".join(lines[1:end]))
    except ValueError as exc:
        raise WorkflowError(f"{path} front matter is not valid JSON: {exc}") from exc
    surface = values.get("verification_surface") if isinstance(values, dict) else None
    marker = surface.get("doctor_marker") if isinstance(surface, dict) else None
    if not isinstance(marker, str) or re.fullmatch(r"\s*(<.*>|none\b.*|n/?a|tbd)?\s*", marker, re.IGNORECASE | re.DOTALL):
        return None
    return marker


def _dirty(root):
    status = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames")
    names = [entry[3:] for entry in status.split("\0") if entry]
    return {name: _sha(root / name) if (root / name).is_file() and not (root / name).is_symlink() else None for name in names}


def _format(root, argv):
    """Run checks.format; return (paths it changed, paths it made dirty that were clean)."""
    before = _dirty(root)
    ok, out = run_check(argv, root)
    if not ok:
        raise WorkflowError(f"checks.format failed:\n{_scrub(out, root)}")
    after = _dirty(root)
    newly = set(after) - set(before)
    changed = {name for name in after if name in newly or before[name] != after[name]} | (set(before) - set(after))
    return sorted(changed), newly


def freeze(worktree, manifest_path, proofs, slug, *, base=None, setup=None,
           previous=None, revise=None, max_checks=MAX_PROOFS, project_setup=False):
    """Freeze working-tree proofs after showing each one fails at its baseline (red at base).

    Policy comes from the clone's per-repo record (read_record), never from the repo tree. A
    clone without a record refuses, except with `project_setup=True`, where the record is still
    being drafted. `proofs` may be empty: a PR with no behaviour change (refactor, docs, project
    setup) freezes none, and prove() then checks CI green only.
    Journey stability is a prove() check on the head being merged, not a freeze() check.
    """
    if not isinstance(project_setup, bool):
        raise WorkflowError("project_setup must be a bool")
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
    if any(path.is_relative_to(directory) for directory in
           (Path(tempfile.gettempdir()).resolve(), Path("/tmp").resolve())):
        raise WorkflowError("manifest must be durable; use <git-common-dir>/omp-manifests/<slug>.json")
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
    record, gate = read_record(root)
    if record is None and not project_setup:
        raise WorkflowError(no_record_reason(root))
    marker = _doctor_marker(record)
    policy = gate_policy(gate) if gate is not None else None
    formatted, format_dirtied = _format(root, policy["format"]) if policy and policy["format"] else ([], set())
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
        if entry and entry[3:] not in names and entry[3:] not in format_dirtied:
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
    trees, parent_trees, baselines, failure = [], {}, {}, None
    with tempfile.TemporaryDirectory(prefix="omp-freeze-") as scratch:
        try:
            for proof in manifest["proofs"]:
                if proof["id"] in old_proofs and proof["id"] not in changed:
                    continue
                commit = proof["parent"]
                if commit not in parent_trees:
                    tree = Path(scratch) / f"tree-{len(trees)}"
                    _git(root, "worktree", "add", "--detach", str(tree), commit)
                    trees.append(tree)
                    parent_trees[commit] = tree
                overlay = {name: _proof_path(root, name).read_bytes() for name in proof["files"]}
                record = _execute(parent_trees[commit], proof, baselines, setup, overlay, location="baseline", roots=(root,))
                error = _red_at_base_failure(proof, record, marker)
                if error:
                    raise WorkflowError(error)
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
    return FreezeResult(path, _sha(path), formatted)


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


def _range(repo, parent, head, target_ref):
    parent, target = _commit(repo, parent), _commit(repo, target_ref)
    if not _ancestor(repo, parent, head, strict=True):
        raise WorkflowError("parent must be a strict ancestor of head")
    if parent != _git(repo, "merge-base", target, head).strip():
        raise WorkflowError("parent must equal merge-base(target_ref, head)")
    return parent, target


CI_HOST_RETIRED = "ciHost is retired; declare ci.requiredContext and ci.runner"


def gate_reason(gate):
    """Why rails would reject this gate, or None when it is accepted. Retired fields (such as
    dynaflowsContract or pathCheckTimeoutMs) in an older record are ignored, not rejected."""
    if not isinstance(gate, dict) or type(gate.get("schemaVersion")) is not int or gate["schemaVersion"] != 2:
        return "unsupported gate schemaVersion"
    if "ciHost" in gate:
        return CI_HOST_RETIRED
    checks = gate.get("checks", {})
    if not isinstance(checks, dict):
        return "checks must be an object"
    def argv(value):
        return isinstance(value, list) and bool(value) and all(isinstance(arg, str) and arg.strip() for arg in value)
    for key in ("commit", "ci", "format", "profile"):
        if key in checks and not argv(checks[key]):
            return f"checks.{key} must be argv"
    if "pathCheck" in gate and not argv(gate["pathCheck"]):
        return "pathCheck must be argv"
    ci = gate.get("ci")
    if "ci" in gate:
        if not isinstance(ci, dict) or any(not isinstance(ci.get(k), str) or not ci[k].strip() for k in ("requiredContext", "runner")):
            return "ci must declare nonblank requiredContext and runner"
    if gate.get("agentMerge", "allowed") not in ("allowed", "disabled"):
        return "agentMerge must be allowed or disabled"
    if "testGlobs" in gate and (not isinstance(gate["testGlobs"], list) or not all(isinstance(x, str) for x in gate["testGlobs"])):
        return "testGlobs must be a string list"
    dynaflows = gate.get("dynaflows")
    if not isinstance(dynaflows, dict) or type(dynaflows.get("enabled")) is not bool:
        return 'dynaflows must be {"enabled": true|false}'
    return None


def gate_policy(gate):
    """Mirror rails gate acceptance (None when rejected); return only the fields consumed here."""
    if gate_reason(gate) is not None:
        return None
    checks = gate.get("checks", {})
    return {"pathCheck": gate.get("pathCheck"), "format": checks.get("format"), "ci": gate.get("ci")}


def deterministic_checks(head_tree, manifest, roots=(), baselines=None):
    """Run every frozen proof at head: frozen bytes unchanged, then HEAD_RUNS passing runs, or for a
    journey proof JOURNEY_RUNS. Red at base was shown at freeze()."""
    result = {"proofs": {}, "blocking": []}
    failures = result["blocking"]
    failures.extend(_hash_failures(head_tree, manifest))
    if failures:
        return result
    baselines = {} if baselines is None else baselines
    for proof in manifest["proofs"]:
        runs = result["proofs"].setdefault(proof["id"], [])
        count = JOURNEY_RUNS if proof.get("journey") else HEAD_RUNS
        for n in range(1, count + 1):
            record = _execute(head_tree, proof, baselines, manifest.get("setup"), location="head", run=n, roots=(head_tree, *roots))
            runs.append(record)
            if record.get("error"):
                failures.append(record["error"])
            elif not record["ok"]:
                failures.append(f"{proof['id']}: head run {n} failed")
        passed = sum(record["ok"] is True for record in runs)
        if proof.get("journey") and passed != count:
            failures.append(f"{proof['id']}: journey proof passed {passed} of {count} clean head runs; journeys must pass every run")
    return result


def _json_safe(value) -> Any:
    try:
        return json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError):
        if isinstance(value, dict):
            return {str(k): _json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_json_safe(v) for v in value]
        return repr(value)


RESULT_MARKER = "<!-- omp-prove-result -->"
COMMENT_LIMIT = 60000
COMMENT_OUTPUT_TAIL = 1000


def result_path(repo, head):
    """prove()'s JSON result for `head` in the Git common directory: the one merge signal.

    Rails' merge guard reads `status: "pass"` for the exact head from this file.
    """
    return _common(repo) / "omp-prove" / f"{head}.result.json"


def _write_result(result, repo, head):
    try:
        _atomic_json(result_path(repo, head), result)
    except Exception as exc:
        return exc
    return None


def _result_comment(result):
    """PR comment body: the machine result as JSON, with long proof outputs tail-truncated to fit GitHub's limit."""
    header = (f"{RESULT_MARKER}\n**prove() {result['status']}** for `{result['head']}`. "
              f"Full JSON: `<git-common-dir>/omp-prove/{result['head']}.result.json` on the proving machine.\n\n")
    for tail in (COMMENT_OUTPUT_TAIL, 0):
        compact = copy.deepcopy(result)
        proofs = compact.get("deterministic", {}).get("proofs", {}) if isinstance(compact.get("deterministic"), dict) else {}
        for runs in proofs.values():
            for record in runs:
                output = record.get("output", "")
                if len(output) > tail:
                    record["output"] = f"[TRUNCATED to last {tail} chars]" + (output[-tail:] if tail else "")
        body = f"{header}```json\n{json.dumps(compact, indent=2)}\n```\n"
        if len(body) <= COMMENT_LIMIT:
            return body
    return f"{header}The result is too large for a PR comment; read the full JSON file.\n"


def _post_result_comment(repo, head, result):
    """Post the result on the one open PR at `head`; return the comment URL gh prints."""
    pr = _pr_at_head(repo, head, ("number",))
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".md") as body:
        body.write(_result_comment(result))
        body.flush()
        return _gh(repo, "pr", "comment", str(pr["number"]), "--body-file", body.name).strip()


def _publish_result(result, repo, head):
    """Write the result file and post it on the PR. A pass that cannot be published becomes a fail;
    a result file that cannot be rewritten is removed, so a stale pass never survives."""
    def fail(reason):
        result["status"] = "fail"
        result["blocking"].append(f"infrastructure: publishing prove result: {reason}")
    if _write_result(result, repo, head) is not None:
        fail("writing the result file")
    try:
        result["prComment"] = _post_result_comment(repo, head, result)
    except Exception as exc:
        fail(exc)
    error = _write_result(result, repo, head)
    if error is not None:
        fail(error)
        try:
            result_path(repo, head).unlink(missing_ok=True)
        except Exception as exc:
            result["blocking"].append(f"infrastructure: removing the unwritable result file: {exc}")


def prove(repo, parent, head, manifest_path, manifest_sha256, objective, *, target_ref, max_checks=MAX_PROOFS):
    """At the PR event: refuse unless the record's required CI context is green on head, then run the
    frozen proofs at head (unchanged bytes, every run passing). A manifest with zero proofs checks CI only.

    The result is written to result_path(repo, head), the merge signal rails reads, and posted on the PR at head.
    """
    result = {"status": "fail", "head": None, "objective": _json_safe(objective), "manifestSha256": _json_safe(manifest_sha256),
              "deterministic": {}, "blocking": []}
    trees, published = [], None
    try:
        repo = Path(repo).resolve()
        head = _commit(repo, head)
        result["head"] = head
        published = (repo, head)
        result_path(repo, head).unlink(missing_ok=True)
        _limit(max_checks)
        if not isinstance(objective, str) or not objective.strip():
            raise WorkflowError("objective must be nonblank")
        parent, _ = _range(repo, parent, head, target_ref)
        manifest = load_manifest(manifest_path, manifest_sha256, max_checks)
        result["manifest"] = manifest
        base = manifest["base"]
        if not _ancestor(repo, base, parent):
            raise WorkflowError("base must be an ancestor of parent")
        record, gate = read_record(repo)
        if record is None:
            raise WorkflowError(f"{GUARDRAIL_PREREQUISITE}; {no_record_reason(repo)}")
        policy = gate_policy(gate)
        if not policy["pathCheck"]:
            raise WorkflowError(GUARDRAIL_PREREQUISITE)
        if policy["ci"]:
            context = policy["ci"]["requiredContext"]
            state = _required_context_state(repo, head, context)
            if state != "success":
                raise ProveBlocked(f'required CI context "{context}" is {state} for {head}; '
                                   "prove() runs only after CI is green on this exact head")
        for proof in manifest["proofs"]:
            commit = proof.get("parent", base)
            if not _ancestor(repo, commit, head, strict=True) or not _ancestor(repo, base, commit):
                raise WorkflowError(f"{proof['id']}: proof parent must be an ancestor of head at or after base")
            _command_files(proof, (repo,))
        if manifest["proofs"]:
            with tempfile.TemporaryDirectory(prefix="omp-prove-") as scratch:
                try:
                    head_tree = Path(scratch) / "head"
                    _git(repo, "worktree", "add", "--detach", str(head_tree), head)
                    trees.append(head_tree)
                    deterministic = deterministic_checks(head_tree, manifest, (repo,))
                    result["deterministic"] = deterministic
                    result["blocking"].extend(deterministic["blocking"])
                finally:
                    result["blocking"].extend(_remove_trees(repo, trees))
        if not result["blocking"]:
            result["status"] = "pass"
    except ProveBlocked as exc:
        result["blocking"].append(str(exc))
    except Exception as exc:
        result["blocking"].append(f"infrastructure: {exc}")
    if published is not None:
        _publish_result(result, *published)
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
