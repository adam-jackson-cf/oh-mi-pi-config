# pyright: reportUndefinedVariable=false
# agent, wait and judge are OMP eval kernel globals, injected when this file is %load-ed.
"""Proof-first verification (TDD: red at base, green at head) and blind labels; load in the OMP Python eval kernel."""
import copy
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any
from urllib.parse import quote, urlsplit

MAX_AGENTS = 16
MAX_PROOFS = 16
CHECK_TIMEOUT = 900
# Harness prechecks have their own budget, independent of repo proof execution.
HARNESS_CHECK_TIMEOUT = 900
GUARDRAIL_PREREQUISITE = (
    "missing per-repo record; follow skill://omp-dyna-workflows/SKILL.md "
    "owner-approval prerequisite (skill://setup-repo-proofs)")
HEAD_RUNS = 2
JOURNEY_RUNS = 3
# Every current manifest and proof field; any other field is rejected by name.
MANIFEST_FIELDS = frozenset({"schemaVersion", "slug", "base", "setup", "revisions", "proofs"})
PROOF_FIELDS = frozenset({"id", "criterion", "parent", "cmd", "journey", "files", "sha256"})
FEATURE_MAP_SETUP = (
    "the record profile declares no verification_surface.feature_map_path; proofs must be "
    "outcome tests linked from the feature map, so set the map up first "
    "(skill://omp-dyna-workflows/references/playbooks/project-setup.md). "
    "A PR with no behaviour change freezes no proofs")


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


_SURFACE = []
GUARDRAILS_REFERENCE = "agent/skills/setup-repo-proofs/assets/python-reference"


def _surface():
    """omp_guardrails' feature-map and journey modules from the harness's python reference, loaded once."""
    if not _SURFACE:
        reference = (_harness_root() / GUARDRAILS_REFERENCE).resolve()
        loaded = sys.modules.get("omp_guardrails")
        if loaded is not None and not Path(loaded.__file__ or "").resolve().is_relative_to(reference):
            raise WorkflowError(f"omp_guardrails already loaded from {loaded.__file__}, not the harness reference {reference}")
        sys.path.insert(0, str(reference))
        try:
            feature_map = importlib.import_module("omp_guardrails.surface.feature_map")
            journeys = importlib.import_module("omp_guardrails.surface.journeys")
        except (OSError, SyntaxError, ImportError) as exc:
            raise WorkflowError(f"cannot load the feature-map checker: {exc}") from exc
        finally:
            sys.path.remove(str(reference))
        _SURFACE.extend((feature_map, journeys))
    return _SURFACE


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
    return record, gate


def no_record_reason(repo):
    """Why the clone at `repo` has no record, in rails' words: the slug (or "none") and the reason."""
    resolver = _resolver()
    slug, _, reason = resolver.resolve_record(repo, _records_root())
    return resolver.off_text(slug, reason or "no per-repo record")


def _is_argv(value):
    return isinstance(value, list) and bool(value) and all(
        isinstance(arg, str) and arg.strip() and "\0" not in arg for arg in value)


def run_check(cmd, cwd, *, timeout=None):
    """Run an argv proof or repo command without harness variables, retaining complete, replacement-decoded diagnostics."""
    if not _is_argv(cmd):
        raise WorkflowError("cmd must be argv")
    env = {key: value for key, value in os.environ.items() if key not in HARNESS_ENV}
    try:
        p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True,
                           errors="replace", timeout=CHECK_TIMEOUT if timeout is None else timeout)
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
    unknown = sorted(set(manifest) - MANIFEST_FIELDS)
    if unknown:
        raise WorkflowError(f"unknown manifest field {', '.join(unknown)}")
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
        unknown = sorted(set(proof) - PROOF_FIELDS)
        if unknown:
            raise WorkflowError(f"{ident}: unknown proof field {', '.join(unknown)}")
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


def _profile(record):
    """The record profile's front-matter object; an absent record, profile or front matter is {}."""
    if record is None:
        return {}
    path = _profile_path(record)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError) as exc:
        raise WorkflowError(f"{path} unreadable: {exc}") from exc
    if not lines or lines[0].strip() != "---":
        return {}
    end = next((index for index, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
    if end is None:
        raise WorkflowError(f"{path} front matter has no closing '---' line")
    try:
        values = json.loads("\n".join(lines[1:end]))
    except ValueError as exc:
        raise WorkflowError(f"{path} front matter is not valid JSON: {exc}") from exc
    return values if isinstance(values, dict) else {}


def _doctor_marker(profile):
    """verification_surface.doctor_marker from the profile front matter; an absent or placeholder value skips."""
    surface = profile.get("verification_surface")
    marker = surface.get("doctor_marker") if isinstance(surface, dict) else None
    if not isinstance(marker, str) or re.fullmatch(r"\s*(<.*>|none\b.*|n/?a|tbd)?\s*", marker, re.IGNORECASE | re.DOTALL):
        return None
    return marker


def _feature_map_path(profile):
    """verification_surface.feature_map_path (repo-relative, no trailing slash), or None when the
    profile declares none, exempts the surface or leaves a placeholder."""
    surface = _surface()[0].surface(profile)
    value = surface.get("feature_map_path") if surface is not None else None
    if not isinstance(value, str) or re.fullmatch(r"\s*(<.*>|none\b.*|n/?a|tbd)?\s*", value, re.IGNORECASE | re.DOTALL):
        return None
    return value.strip().rstrip("/") or None


def _unmapped_proof_files(root, map_path, proofs):
    """Proof files that no feature-map document in the working tree links to."""
    if not (Path(root) / map_path).exists():
        raise WorkflowError(f"feature map {map_path} (verification_surface.feature_map_path) does not exist; "
                            "write the feature map before freezing proofs")
    linked, _ = _surface()[1].mapped_paths(Path(root), map_path)
    return [f"{proof['id']}: {name}" for proof in proofs for name in proof["files"] if name not in linked]


def _map_document(map_path, name, indexes):
    """Whether a changed repo path is a feature document of the map at map_path (index files are not)."""
    if PurePosixPath(name).name in indexes:
        return False
    return name == map_path or (name.startswith(f"{map_path}/") and name.endswith(".md"))


def _unproven_map_docs(repo, parent, head, profile, manifest):
    """Feature-map documents changed in parent..head that link no proof file in the manifest."""
    map_path = _feature_map_path(profile)
    if map_path is None:
        return []
    feature_map = _surface()[0]
    indexes = feature_map.index_files(profile)
    changed = _git(repo, "diff", "--name-only", "--no-renames", "--diff-filter=d", "-z", parent, head, "--", map_path)
    proof_files = {name for proof in manifest["proofs"] for name in proof["files"]}
    unproven = []
    for doc in sorted(name for name in changed.split("\0") if name and _map_document(map_path, name, indexes)):
        text = _git(repo, "show", f"{head}:{doc}")
        if not feature_map.linked_files(repo, doc, text) & proof_files:
            unproven.append(doc)
    return unproven


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
    being drafted. `proofs` may be empty: a PR with no behaviour change (refactor, docs) freezes
    none, and prove() then checks CI green unless the PR changes a feature-map doc, which must
    link a frozen proof. Every proof must be linked from a feature-map doc.
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
    profile = _profile(record)
    marker = _doctor_marker(profile)
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
    map_path = None
    if manifest["proofs"]:
        map_path = _feature_map_path(profile)
        if map_path is None:
            raise WorkflowError(FEATURE_MAP_SETUP)
        unmapped = _unmapped_proof_files(root, map_path, manifest["proofs"])
        if unmapped:
            raise WorkflowError(f"proof files not linked from any feature-map document under {map_path}: "
                                f"{', '.join(unmapped)}; link each proof from its feature's map entry")
    names = {name for proof in manifest["proofs"] for name in proof["files"]}
    for entry in _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames").split("\0"):
        name = entry[3:]
        if (entry and name not in names and name not in format_dirtied
                and not (map_path and (name == map_path or name.startswith(f"{map_path}/")))):
            raise WorkflowError(f"unfrozen change: {name}; add it to a proof's files or revert")
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


def gate_policy(gate):
    """The fields consumed here from a gate the harness's repo_record.gate_reason accepts, else None."""
    if _resolver().gate_reason(gate) is not None:
        return None
    return {"format": gate["checks"].get("format"), "ci": gate.get("ci")}


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
    """At the PR event: refuse unless the record's required CI context is green on head, then check
    rename residue and frozen proofs at head (unchanged bytes, every run passing).

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
        if policy["ci"]:
            context = policy["ci"]["requiredContext"]
            state = _required_context_state(repo, head, context)
            if state != "success":
                raise ProveBlocked(f'required CI context "{context}" is {state} for {head}; '
                                   "prove() runs only after CI is green on this exact head")
        ok, output = run_check(
            [sys.executable, "-I", "-S", "-c",
             "import sys; sys.path.insert(0, sys.argv.pop(1)); "
             "from omp_guardrails.cli import main; raise SystemExit(main())",
             str(_harness_root() / GUARDRAILS_REFERENCE),
             "rename-residue", "--repo", str(repo), "--record", str(record),
             "--base", parent, "--head", head], repo, timeout=HARNESS_CHECK_TIMEOUT)
        if not ok:
            result["blocking"].extend(line for line in output.splitlines() if line.strip())
            if not output.strip():
                result["blocking"].append("rename-residue failed without diagnostics")
        unproven = _unproven_map_docs(repo, parent, head, _profile(record), manifest)
        if unproven:
            raise ProveBlocked(f"feature-map documents changed in {parent}..{head} link no frozen proof: "
                               f"{', '.join(unproven)}; add an outcome test for each changed entry, "
                               "link it from the entry and re-freeze")
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


# Dependency advisories: the commit gate's SECURITY.dependency-audit findings, assessed by the
# calibrated severity judgment and routed to the owner or to an in-flow fix. The line format is the
# catalogue row's contract (omp_guardrails/data/guardrail-rules.json); repos print exactly it.
DEPENDENCY_AUDIT = "SECURITY.dependency-audit"
ADVISORY_FIELDS = ("lock", "ecosystem", "package", "version", "advisory", "aliases", "fix")
ADVISORY_LISTS = ("aliases", "fix")
AUDIT_FAILURE_KINDS = ("unknown-lock-format", "manifest-without-lock", "exception", "auditor")
_ADVISORY_LINE = re.compile(
    re.escape(DEPENDENCY_AUDIT) + "".join(
        rf" {field}=(?P<{field}>\S*)" if field in ADVISORY_LISTS else rf" {field}=(?P<{field}>\S+)"
        for field in ADVISORY_FIELDS))
_FAILURE_LINE = re.compile(re.escape(DEPENDENCY_AUDIT) + r" failure=(?P<kind>\S+) path=(?P<path>\S+) reason=(?P<reason>\S.*)")
SEVERITY_JUDGMENT = "agent/skills/omp-dyna-workflows/judgments/dependency-severity"
SEVERITIES = ("low", "medium", "high", "critical")
OWNER_SEVERITIES = ("high", "critical")
# The fixed rubric: an impact class sets the base level; each mitigating dimension that the
# judgment clears lowers it one level, never below low. Unclear evidence never lowers it.
IMPACT_LEVEL = {"rce": 4, "auth_bypass": 4, "unclear": 4, "data_exposure": 3, "integrity_loss": 3, "dos": 2, "info_leak": 2}
PRECONDITIONS = ("local_only", "needs_privileges", "needs_interaction")
MALICIOUS_PREFIX = "MAL-"
EVIDENCE_FIELDS = frozenset({"advisory_text", "advisory_source", "scope", "scope_source", "code_search", "fix_options", "blocked_fix"})
BLOCKED_FIX_FIELDS = frozenset({"blocked_by", "constraint", "constraint_source"})


def advisory_key(advisory):
    """One advisory finding's identity: lock, package at version, advisory ID."""
    return f"{advisory['lock']}:{advisory['package']}@{advisory['version']}:{advisory['advisory']}"


def _listed(value, field, line):
    items = value.split(",") if value else []
    if any(not item for item in items):
        raise WorkflowError(f"{DEPENDENCY_AUDIT} line has an empty {field} item: {line}")
    return items


def parse_dependency_audit(output):
    """Every SECURITY.dependency-audit finding in commit-gate output: ``{advisories, failures}``.

    A line that starts with the rule ID must match the catalogue contract exactly; any other such
    line, or output the rails truncated, raises: a finding is never skipped.
    """
    if not isinstance(output, str):
        raise WorkflowError("commit-gate output must be text")
    if output.startswith("…"):
        raise WorkflowError("commit-gate output is truncated; parse the full output of the record's checks.commit")
    advisories, failures, seen = [], [], set()
    for line in output.splitlines():
        line = line.rstrip("\r")
        if line != DEPENDENCY_AUDIT and not line.startswith(DEPENDENCY_AUDIT + " "):
            continue
        if line in seen:
            continue
        seen.add(line)
        match = _ADVISORY_LINE.fullmatch(line)
        if match:
            advisory = match.groupdict()
            for field in ADVISORY_LISTS:
                advisory[field] = _listed(advisory[field], field, line)
            advisories.append(advisory)
            continue
        match = _FAILURE_LINE.fullmatch(line)
        if match and match["kind"] in AUDIT_FAILURE_KINDS:
            failures.append(match.groupdict())
            continue
        raise WorkflowError(f"{DEPENDENCY_AUDIT} line does not match the catalogue contract: {line}")
    return {"advisories": advisories, "failures": failures}


def dependency_audit_findings(repo):
    """Run the record's checks.commit in `repo` and parse its SECURITY.dependency-audit lines."""
    _, gate = read_record(repo)
    if gate is None:
        raise WorkflowError(no_record_reason(repo))
    ok, output = run_check(gate["checks"]["commit"], repo)
    found = parse_dependency_audit(output)
    if ok and (found["advisories"] or found["failures"]):
        raise WorkflowError(f"checks.commit passed but printed {DEPENDENCY_AUDIT} findings; every finding must block")
    return {"ok": ok, **found}


def _severity_dir():
    return _harness_root() / SEVERITY_JUDGMENT


def severity_questions():
    """The frozen severity questions as judge() takes them, and their state shape."""
    try:
        document = json.loads((_severity_dir() / "questions.json").read_text())
    except (OSError, ValueError) as exc:
        raise WorkflowError(f"cannot read the severity questions: {exc}") from exc
    return document["questions"], document["state"]


def severity_version_key(model):
    """sha256 over the questions, the state shape and the pinned model (compose-and-integrate)."""
    questions, state = severity_questions()
    payload = json.dumps({"questions": questions, "state": state, "model": model}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def severity_thresholds():
    """thresholds.json for the current question version; a stale version key raises (re-score, re-tune)."""
    try:
        thresholds = json.loads((_severity_dir() / "thresholds.json").read_text())
    except (OSError, ValueError) as exc:
        raise WorkflowError(f"cannot read the severity thresholds: {exc}") from exc
    if thresholds.get("version_key") != severity_version_key(thresholds.get("model")):
        raise WorkflowError("severity thresholds are stale for the current questions; re-score the cases and re-tune")
    return thresholds["decisions"]


def _evidence(advisory, evidence):
    """The cited evidence for one advisory, validated; a gap raises naming the advisory."""
    key = advisory_key(advisory)
    if not isinstance(evidence, dict) or set(evidence) != EVIDENCE_FIELDS:
        raise WorkflowError(f"{key}: evidence needs exactly {sorted(EVIDENCE_FIELDS)}")
    for field in ("advisory_text", "advisory_source", "scope_source"):
        if not isinstance(evidence[field], str) or not evidence[field].strip():
            raise WorkflowError(f"{key}: evidence.{field} must be nonblank")
    if evidence["scope"] not in ("runtime", "dev"):
        raise WorkflowError(f"{key}: evidence.scope must be runtime or dev")
    search = evidence["code_search"]
    if (not isinstance(search, dict) or set(search) != {"queries", "hits"}
            or not isinstance(search["queries"], list) or not search["queries"]
            or any(not isinstance(q, str) or not q.strip() for q in search["queries"])
            or not isinstance(search["hits"], list)
            or any(not isinstance(h, dict) or set(h) != {"location", "line"} or not all(isinstance(h[k], str) and h[k].strip() for k in h)
                   for h in search["hits"])):
        raise WorkflowError(f"{key}: evidence.code_search needs nonblank queries and hits of {{location, line}}")
    if not isinstance(evidence["fix_options"], str) or (not advisory["fix"] and not evidence["fix_options"].strip()):
        raise WorkflowError(f"{key}: evidence.fix_options must name a replacement or mitigation when no fixed version exists")
    blocked = evidence["blocked_fix"]
    if blocked is not None:
        if (not advisory["fix"] or not isinstance(blocked, dict) or set(blocked) != BLOCKED_FIX_FIELDS
                or any(not isinstance(v, str) or not v.strip() for v in blocked.values())):
            raise WorkflowError(f"{key}: evidence.blocked_fix needs exactly {sorted(BLOCKED_FIX_FIELDS)} for a fixed release, or null")
        package, separator, version = blocked["blocked_by"].rpartition("@")
        source = urlsplit(blocked["constraint_source"])
        if (not separator or not package or not version or any(c.isspace() for c in blocked["blocked_by"])
                or source.scheme != "https" or not source.netloc):
            raise WorkflowError(f"{key}: blocked_fix needs package@exact-version and an absolute HTTPS constraint_source")
    return evidence


def _probability(answers, question, kind):
    answer = answers.get(question) if isinstance(answers, dict) else None
    if kind == "choice":
        probabilities = answer.get("probabilities") if isinstance(answer, dict) else None
        if (not isinstance(probabilities, dict) or set(probabilities) != set(IMPACT_LEVEL)
                or not all(isinstance(p, (int, float)) and 0 <= p <= 1 for p in probabilities.values())):
            raise WorkflowError(f"severity judgment returned no valid {question} answer")
        return probabilities
    value = answer.get("bool") if isinstance(answer, dict) else None
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 1:
        raise WorkflowError(f"severity judgment returned no valid {question} answer")
    return value


def rate_advisory(advisory, evidence, answers, decisions):
    """Apply the fixed rubric to the judgment's answers: severity, route and the cited evidence.

    Rounds up: the impact is the most severe class at or above the round-up floor, and a
    mitigating dimension lowers the level only when the judgment clears its threshold. A malicious
    package never reaches the rubric.
    """
    if malicious_package(advisory):
        return malicious_assessment(advisory, evidence)
    evidence = _evidence(advisory, evidence)
    probabilities = _probability(answers, "impact", "choice")
    floor = decisions["impact"]["round_up_floor"]
    impact = max((c for c, p in probabilities.items() if p >= floor), key=IMPACT_LEVEL.get,
                 default=max(probabilities, key=probabilities.get))
    preconditions = {q: _probability(answers, q, "bool") for q in PRECONDITIONS}
    reached = _probability(answers, "reached", "bool")
    cleared = [q for q in PRECONDITIONS if preconditions[q] >= decisions[q]["threshold"]]
    unreached = reached <= decisions["reached"]["unreached_at_most"]
    level = IMPACT_LEVEL[impact] - bool(cleared) - (evidence["scope"] == "dev") - unreached
    severity = SEVERITIES[max(1, level) - 1]
    reasons = []
    if severity in OWNER_SEVERITIES:
        reasons.append(f"severity {severity}")
    if not advisory["fix"]:
        reasons.append("no fixed release: blocked until the owner approves a dated exception")
    if evidence["blocked_fix"] is not None:
        reasons.append("fixed releases blocked by a pinned dependent: blocked until the owner approves a dated exception")
    upgrade = f"upgrade {advisory['package']} from {advisory['version']} to a fixed version ({', '.join(advisory['fix'])})"
    if advisory["fix"]:
        suggested = upgrade + (f"; also: {evidence['fix_options']}" if evidence["fix_options"].strip() else "")
    else:
        suggested = evidence["fix_options"]
    if evidence["blocked_fix"] is not None:
        blocked = evidence["blocked_fix"]
        suggested = (f"owner: approve a dated exception with blocked_by={blocked['blocked_by']}, "
                     f"constraint={blocked['constraint']}, constraint_source={blocked['constraint_source']}; "
                     "upgrade when the dependent's declared constraint permits a fixed release")
    return {
        "key": advisory_key(advisory),
        "advisory": advisory,
        "severity": severity,
        "route": "owner" if reasons else "fix",
        "reasons": reasons,
        "dimensions": {
            "impact": {"class": impact, "probabilities": probabilities, "source": evidence["advisory_source"]},
            "preconditions": {"probabilities": preconditions, "cleared": cleared, "source": evidence["advisory_source"]},
            "exposure": {"scope": evidence["scope"], "scope_source": evidence["scope_source"],
                         "reached": reached, "unreached": unreached, "code_search": evidence["code_search"]},
            "fix": {"versions": advisory["fix"], "options": evidence["fix_options"], "blocked_fix": evidence["blocked_fix"]},
        },
        "suggested_fix": suggested,
    }


def malicious_package(advisory):
    """True when the advisory is an OSV malicious-package record: its ID or an alias starts with MAL-.

    The catalogue contract has the checker report an auditor's own malicious mark the same way.
    """
    return any(ident.upper().startswith(MALICIOUS_PREFIX) for ident in (advisory["advisory"], *advisory["aliases"]))


def malicious_assessment(advisory, evidence):
    """A malicious package is a supply-chain compromise: critical and the owner's, whatever its
    scope, reachability or fix. Decided in code; the severity judgment is never asked."""
    evidence = _evidence(advisory, evidence)
    return {
        "key": advisory_key(advisory),
        "advisory": advisory,
        "severity": "critical",
        "route": "owner",
        "reasons": ["malicious package (OSV MAL- record): supply-chain compromise"],
        "dimensions": {
            "impact": {"class": "malicious_package", "probabilities": {}, "source": evidence["advisory_source"]},
            "preconditions": {"probabilities": {}, "cleared": [], "source": evidence["advisory_source"]},
            "exposure": {"scope": evidence["scope"], "scope_source": evidence["scope_source"],
                         "reached": None, "unreached": False, "code_search": evidence["code_search"]},
            "fix": {"versions": advisory["fix"], "options": evidence["fix_options"], "blocked_fix": evidence["blocked_fix"]},
        },
        "suggested_fix": (f"remove {advisory['package']} or replace it with a trusted package, and rotate every "
                          "credential the environments that installed it exposed (CI and developer tokens, keys)"
                          + (f"; also: {evidence['fix_options']}" if evidence["fix_options"].strip() else "")),
    }


async def assess_advisory(advisory, evidence):
    """Severity of one advisory: the frozen questions through judge(), then the fixed rubric in code.

    A malicious package is decided in code before any judgment. A failed or invalid judgment
    raises; it is never read as a low severity.
    """
    if malicious_package(advisory):
        return malicious_assessment(advisory, evidence)
    evidence = _evidence(advisory, evidence)
    questions, _ = severity_questions()
    decisions = severity_thresholds()
    state = {"advisory": {"id": advisory["advisory"], "aliases": advisory["aliases"], "ecosystem": advisory["ecosystem"],
                          "package": advisory["package"], "version": advisory["version"], "fix": advisory["fix"],
                          "text": evidence["advisory_text"], "source": evidence["advisory_source"]},
             "code_search": evidence["code_search"]}
    try:
        answers = await judge(state, questions)
    except Exception as exc:
        raise WorkflowError(f"{advisory_key(advisory)}: severity judgment failed: {exc}") from exc
    return rate_advisory(advisory, evidence, answers, decisions)


def owner_report(assessments, failures=()):
    """The report the workflow halts with: each advisory, severity, evidence and suggested fix."""
    lines = [f"Dependency advisories need the owner ({DEPENDENCY_AUDIT}). The workflow stopped; nothing was changed for them."]
    for item in assessments:
        a, d = item["advisory"], item["dimensions"]
        aliases = f" ({', '.join(a['aliases'])})" if a["aliases"] else ""
        hits = ", ".join(h["location"] for h in d["exposure"]["code_search"]["hits"]) or "no hits"
        pre = ", ".join(f"{q} {p:.2f}" for q, p in d["preconditions"]["probabilities"].items()) or "not judged"
        lines += [
            f"- {a['advisory']}{aliases}: {a['package']} {a['version']} ({a['ecosystem']}), lock {a['lock']}",
            f"  - Severity: {item['severity']}; why the owner: {'; '.join(item['reasons'])}",
            f"  - Impact: {d['impact']['class']} (advisory: {d['impact']['source']})",
            f"  - Preconditions (raw judgment probabilities, not calibrated confidence): {pre}; "
            f"cleared: {', '.join(d['preconditions']['cleared']) or 'none'}",
            f"  - Exposure: {d['exposure']['scope']} dependency ({d['exposure']['scope_source']}); owned code reaches "
            f"the vulnerable feature: {'no' if d['exposure']['unreached'] else 'yes or unclear'} "
            f"(queries: {', '.join(d['exposure']['code_search']['queries'])}; hits: {hits})",
            f"  - Fix availability: {', '.join(d['fix']['versions']) or 'no fixed release'}",
            f"  - Suggested fix: {item['suggested_fix']}",
        ]
        blocked = d["fix"]["blocked_fix"]
        if blocked is not None:
            lines.append(f"  - Pinned dependent: {blocked['blocked_by']}; declaration: {blocked['constraint']} "
                         f"(source: {blocked['constraint_source']})")
    for failure in failures:
        lines.append(f"- Exception file failure at {failure['path']}: {failure['reason']} (owner-only path)")
    return "\n".join(lines)


async def triage_advisories(found, evidence):
    """Assess every advisory in `found` (from dependency_audit_findings) with its cited evidence.

    Returns ``{halt, owner, fix, failures, report}``. Any high or critical advisory, any advisory
    with no fixed release or a fix blocked by a pinned dependent, and any exception-file failure
    halts the workflow with ``report`` for the owner; ``fix`` lists only unblocked low and medium
    advisories to upgrade in the flow, and ``failures`` the other checker failures to fix.
    """
    keys = [advisory_key(a) for a in found["advisories"]]
    missing = sorted(set(keys) - set(evidence))
    extra = sorted(set(evidence) - set(keys))
    if missing or extra:
        raise WorkflowError(f"evidence must cover exactly the advisories found; missing {missing}, unknown {extra}")
    assessed = [await assess_advisory(a, evidence[k]) for a, k in zip(found["advisories"], keys)]
    owner = [item for item in assessed if item["route"] == "owner"]
    exceptions = [f for f in found["failures"] if f["kind"] == "exception"]
    halt = bool(owner or exceptions)
    return {"halt": halt, "owner": owner, "fix": [item for item in assessed if item["route"] == "fix"],
            "failures": [f for f in found["failures"] if f["kind"] != "exception"],
            "report": owner_report(owner, exceptions) if halt else ""}


STRUCTURE_JUDGMENT = "agent/skills/omp-dyna-workflows/judgments/structure-naming"
STRUCTURE_MARKER = "<!-- omp-structure-review -->"


def _structure_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _structure_config():
    directory = _harness_root() / STRUCTURE_JUDGMENT
    document = json.loads((directory / "questions.json").read_text())
    thresholds = json.loads((directory / "thresholds.json").read_text())
    questions = document["questions"]
    if len(questions) != 8 or any(q.get("type") != "bool" for q in questions.values()):
        raise WorkflowError("structure questions must contain eight eval bool questions")
    version = _structure_digest({"questions": questions, "state": document["state"],
                                 "model": thresholds["model"]})
    if thresholds["version_key"] != version or thresholds["score_space"] != "raw":
        raise WorkflowError("structure thresholds are stale or not in raw score space")
    if set(thresholds["decisions"]) != set(questions):
        raise WorkflowError("structure thresholds must cover every question")
    for ident, question in questions.items():
        decision = thresholds["decisions"][ident]
        expected = _structure_digest({"question": question, "state": document["state"],
                                      "model": thresholds["model"]})
        threshold = decision["threshold"]
        if (decision["version_key"] != expected or decision["status"] not in ("provisional", "ready")
                or isinstance(threshold, bool) or not isinstance(threshold, (int, float))
                or not 0 <= threshold <= 1):
            raise WorkflowError(f"invalid or stale structure decision: {ident}")
    return questions, thresholds


def _structure_states(repo, parent, head):
    # Only the harness reference is importable; never import or execute repository code.
    ok, output = run_check(
        [sys.executable, "-I", "-S", "-c",
         "import sys; sys.path.insert(0, sys.argv.pop(1)); "
         "from omp_guardrails.cli import main; raise SystemExit(main())",
         str(_harness_root() / GUARDRAILS_REFERENCE),
         "structure-state", "--repo", str(repo), "--base", parent, "--head", head],
        repo, timeout=HARNESS_CHECK_TIMEOUT)
    if not ok:
        raise WorkflowError(f"structure-state failed: {output}")
    states = json.loads(output)
    if not isinstance(states, dict) or any(not isinstance(v, dict) for v in states.values()):
        raise WorkflowError("structure-state must emit {unit_path: unit_state}")
    return states


def _structure_comment(result):
    lines = [STRUCTURE_MARKER, f"**Structure/naming shadow review** for `{result['head']}`.",
             "Scores are raw judgment probabilities, not calibrated confidence. "
             "Provisional findings never affect prove or merge."]
    for item in result["findings"]:
        lines.append(f"- `{item['unit']}` · `{item['question']}`: raw {item['score']:.3f} "
                     f"≥ {item['threshold']:.3f} ({item['status']})")
    if not result["findings"]:
        lines.append("- No findings recorded." if not result["errors"] else "- Review incomplete.")
    lines.extend(f"- Infrastructure: {error}" for error in result["errors"])
    if result["halt"]:
        lines.append("**Blocked:** ready decisions have findings; include `not result['halt']` "
                     "in the caller's prove/merge guard.")
    lines.append(f"Full result: `<git-common-dir>/omp-structure/{result['head']}.json`.")
    body = "\n".join(lines)
    if len(body) > COMMENT_LIMIT:
        body = body[:COMMENT_LIMIT - 200] + "\n[Truncated; read the full result file.]"
    return body


def _post_structure_comment(repo, head, result):
    pr = _pr_at_head(repo, head, ("number",))
    endpoint = f"repos/{_github_repo(repo)}/issues/{pr['number']}/comments"
    comments = _github(repo, "api", endpoint, "--paginate")
    previous = [c for c in comments if STRUCTURE_MARKER in (c.get("body") or "")]
    endpoint = f"repos/{_github_repo(repo)}/issues/comments/{previous[-1]['id']}" if previous else endpoint
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".json") as stream:
        json.dump({"body": _structure_comment(result)}, stream)
        stream.flush()
        response = _github(repo, "api", endpoint, "--method", "PATCH" if previous else "POST",
                           "--input", stream.name)
    return response["html_url"]


async def structure_review(repo, parent, head, *, target_ref):
    """PR-time structure judgment, separate from prove(). Callers must guard merge with
    ``prove_result['status'] == 'pass' and not structure_result['halt']``.

    Only ready decisions can halt. Infrastructure is reported, never turned into a shadow
    blocker or a clean evaluation. No proof result or merge-signal file is modified.
    """
    result = {"head": None, "parent": None, "status": "shadow", "halt": False,
              "units": {}, "findings": [], "errors": [], "thresholds": None,
              "version_key": None, "model_reported": None}
    published = None
    try:
        repo = Path(repo).resolve()
        head = _commit(repo, head)
        result["head"] = head
        published = _common(repo) / "omp-structure" / f"{head}.json"
        parent, _ = _range(repo, parent, head, target_ref)
        result["parent"] = parent
        questions, thresholds = _structure_config()
        result["thresholds"] = thresholds
        result["version_key"] = thresholds["version_key"]
        # eval's judge API exposes answers, not a pinned/reported model. Do not invent one.
        result["model_note"] = "eval judge selects the runtime model; response model metadata unavailable"
        states = _structure_states(repo, parent, head)
        groups = {False: {}, True: {}}
        for unit, state in sorted(states.items()):
            digest = _structure_digest(state)
            clusters = [c for c in state.get("cluster", []) if c.get("eligible")]
            state = {**state, "cluster": clusters}
            result["units"][unit] = {"digest": digest, "judgment_digest": _structure_digest(state),
                                     "scores": {}}
            groups[bool(clusters)][unit] = {"unit_state": state}

        def consume(unit, answers, selected):
            scores = {ident: _probability(answers, ident, "bool") for ident in selected}
            result["units"][unit]["scores"] = scores
            for ident, score in scores.items():
                decision = thresholds["decisions"][ident]
                if score >= decision["threshold"]:
                    finding = {"unit": unit, "question": ident, "score": score,
                               "threshold": decision["threshold"], "status": decision["status"]}
                    result["findings"].append(finding)
                    if decision["status"] == "ready":
                        result["halt"] = True

        for has_clusters, group in groups.items():
            if not group:
                continue
            selected = {k: q for k, q in questions.items()
                        if has_clusters or k != "prefix_should_be_folder"}
            if len(group) == 1:
                unit, state = next(iter(group.items()))
                try:
                    consume(unit, await judge(state, selected), selected)
                except Exception as exc:
                    result["errors"].append(f"{unit}: judgment: {exc}")
            else:
                batch = judge_batch(group, selected, intent="Structure/naming shadow review")
                try:
                    async for unit, item in batch.drain_iter():
                        try:
                            if not item.ok:
                                raise WorkflowError(item.error)
                            consume(unit, item.answers, selected)
                        except Exception as exc:
                            result["errors"].append(f"{unit}: judgment: {exc}")
                finally:
                    batch.close()
    except Exception as exc:
        result["errors"].append(f"infrastructure: {exc}")
    result["status"] = "blocked" if result["halt"] else "incomplete" if result["errors"] else "shadow"
    if published is not None:
        try:
            _atomic_json(published, result)
        except Exception as exc:
            result["errors"].append(f"writing structure result: {exc}")
        try:
            result["prComment"] = _post_structure_comment(repo, head, result)
        except Exception as exc:
            result["errors"].append(f"posting structure result: {exc}")
        result["status"] = "blocked" if result["halt"] else "incomplete" if result["errors"] else "shadow"
        try:
            _atomic_json(published, result)
        except Exception as exc:
            result["errors"].append(f"writing structure result: {exc}")
    return result
