"""Proof-first verification (TDD: red at base, green at head) and blind labels; load in the OMP Python eval kernel."""

import copy
import dataclasses
import hashlib
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from types import ModuleType
from typing import Any, Required, TypedDict, TypeGuard, Unpack
from urllib.parse import quote, urlsplit

MAX_AGENTS = 16
MAX_PROOFS = 16
CHECK_TIMEOUT = 900
# Harness prechecks have their own budget, independent of repo proof execution.
HARNESS_CHECK_TIMEOUT = 900
# Every refusal for a repo without a valid .agentrails/config.json routes to onboarding.
GUARDRAIL_PREREQUISITE = "ask the owner to run /agentrails onboard"
HEAD_RUNS = 2
JOURNEY_RUNS = 3
# Every current manifest and proof field; any other field is rejected by name.
MANIFEST_FIELDS = frozenset({
    "schemaVersion",
    "slug",
    "base",
    "setup",
    "revisions",
    "proofs",
})
PROOF_FIELDS = frozenset({
    "id",
    "criterion",
    "parent",
    "cmd",
    "journey",
    "files",
    "sha256",
})
FEATURE_MAP_SETUP = (
    "the repo config declares no tests.verification_surface.feature_map_path; proofs must be "
    "outcome tests linked from the feature map, so record the map in the config's tests "
    "section first (oh-mi-pi-config agent/extensions/agent-rails/references/proofs-and-merge.md). "
    "A PR with no behaviour change freezes no proofs"
)


class WorkflowError(RuntimeError):
    pass


class ProveBlockedError(WorkflowError):
    """A prerequisite failed; report the reason itself rather than as infrastructure."""


StrPath = str | os.PathLike[str]
# What a proof run, Git, gh, a file write or an eval kernel call (agent, wait, judge) raises when
# its infrastructure fails. Each is recorded as a blocking error; any other exception is a bug and
# propagates.
INFRASTRUCTURE_ERRORS = (
    RuntimeError,
    OSError,
    ValueError,
    LookupError,
    TypeError,
    subprocess.SubprocessError,
)
# Each tree's setup outcome: the untracked entries after setup, or the infrastructure error it raised.
Baselines = dict[Path, set[str] | Exception]


# Harness variables the omp runtime exports to its tools. Repo commands must behave as they do
# without the harness, so run_check removes them at the exec boundary.
HARNESS_ENV = ("AGENT",)
# The line prefix a guardrails checker prints when it cannot log its results to the decision store.
STORE_ERROR = "decision store: "
# Captured at load: the eval kernel's %load sets __file__ in the shared namespace, and a later
# %load of another script would overwrite it.
_LOADED_FROM = globals().get("__file__")


def _kernel(name: str) -> Callable[..., Any]:
    """An OMP eval kernel global (agent, wait, judge, judge_batch); the kernel injects them when it %load-s this file."""
    function: object = globals().get(name)
    if not callable(function):
        raise WorkflowError(
            f"{name}() is unavailable: %load workflows.py in the OMP Python eval kernel"
        )
    return function


def _check_options(function: str, options: Mapping[str, object], accepted: Any) -> None:
    """Refuse unknown and missing keyword arguments as Python does for a plain signature."""
    unknown = sorted(
        set(options) - accepted.__required_keys__ - accepted.__optional_keys__
    )
    if unknown:
        raise TypeError(
            f"{function}() got an unexpected keyword argument {unknown[0]!r}"
        )
    missing = sorted(accepted.__required_keys__ - set(options))
    if missing:
        raise TypeError(
            f"{function}() missing required keyword argument: {missing[0]!r}"
        )


def _harness_root() -> Path:
    if not _LOADED_FROM:
        raise WorkflowError(
            "cannot locate the harness: workflows.py was loaded without __file__"
        )
    return Path(_LOADED_FROM).resolve().parents[4]


_SURFACE: list[ModuleType] = []
_STORE: list[ModuleType] = []
_REPO_CONFIG: list[ModuleType] = []
ENGINE_PATH = "agent/extensions/agent-rails/engine"


def _guardrails_modules(names: tuple[str, ...], purpose: str) -> list[ModuleType]:
    """guardrails modules from the harness engine; an older guardrails copy is refused."""
    engine = (_harness_root() / ENGINE_PATH).resolve()
    package = (engine / "guardrails").resolve()
    loaded = sys.modules.get("guardrails")
    if loaded is not None and not Path(loaded.__file__ or "").resolve().is_relative_to(
        package
    ):
        raise WorkflowError(
            f"guardrails already loaded from {loaded.__file__}, not the harness engine {engine}"
        )
    sys.path.insert(0, str(engine))
    try:
        return [importlib.import_module(name) for name in names]
    except (OSError, SyntaxError, ImportError) as exc:
        raise WorkflowError(f"cannot load the {purpose}: {exc}") from exc
    finally:
        sys.path.remove(str(engine))


def _surface() -> list[ModuleType]:
    """guardrails' feature-map and journey modules, loaded once."""
    if not _SURFACE:
        _SURFACE.extend(
            _guardrails_modules(
                (
                    "guardrails.commands.surface.feature_map",
                    "guardrails.commands.surface.journeys",
                ),
                "feature-map checker",
            )
        )
    return _SURFACE


def _store() -> Any:
    """guardrails' decision store module, loaded once."""
    if not _STORE:
        _STORE.extend(
            _guardrails_modules(
                ("guardrails.foundation.decision_store",), "decision store"
            )
        )
    return _STORE[0]


def _repo_config() -> Any:
    """guardrails' repo config module (the one .agentrails/config.json validator), loaded once."""
    if not _REPO_CONFIG:
        _REPO_CONFIG.extend(
            _guardrails_modules(
                ("guardrails.context.config.repo_config",), "repo config validator"
            )
        )
    return _REPO_CONFIG[0]


def not_onboarded_reason(repo: StrPath, kind: str) -> str:
    """The refusal for a repo whose config is missing, invalid, unresolved or outside."""
    return (
        f"agent rails: {repo} is not onboarded (.agentrails/config.json {kind}); "
        f"{GUARDRAIL_PREREQUISITE}"
    )


# A config of these kinds does not apply: global judgment modes hold (judgment_config).
UNCONFIGURED_KINDS = frozenset({"missing", "unresolved", "outside"})
# A repo config can only soften a judgment question's global mode.
MODE_RANK = {"off": 0, "shadow": 1, "enforced": 2}


def read_config(repo: StrPath) -> dict[str, Any]:
    """The validated .agentrails/config.json of the clone at `repo` (its main checkout's file).

    A missing, invalid or unresolvable config raises: there is no default policy.
    """
    module = _repo_config()
    try:
        config: dict[str, Any] = module.load_config(repo)
    except module.ConfigError as exc:
        raise WorkflowError(not_onboarded_reason(repo, exc.kind)) from exc
    return config


def judgment_config(cwd: StrPath) -> dict[str, Any] | None:
    """The repo config whose `judgments` overrides apply at `cwd`, or None when none applies.

    An unconfigured repo (missing), an unresolvable main checkout and a cwd outside a GitHub git
    repository give None, so the global modes hold. An invalid config raises.
    """
    module = _repo_config()
    try:
        config: dict[str, Any] = module.load_config(cwd)
    except module.ConfigError as exc:
        if exc.kind in UNCONFIGURED_KINDS:
            return None
        raise WorkflowError(not_onboarded_reason(cwd, exc.kind)) from exc
    return config


def _is_argv(value: object) -> TypeGuard[list[str]]:
    return (
        isinstance(value, list)
        and bool(value)
        and all(
            isinstance(arg, str) and arg.strip() and "\0" not in arg for arg in value
        )
    )


def _run_text(
    argv: list[str],
    *,
    cwd: StrPath | None = None,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
    errors: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        errors=errors,
        timeout=timeout,
    )


def _run_bytes(argv: list[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(argv, capture_output=True)


def run_check(
    cmd: object,
    cwd: StrPath,
    *,
    timeout: float | None = None,
    stdout_only: bool = False,
) -> tuple[bool, str]:
    """Run without harness variables; retain all diagnostics unless stdout_only selects successful stdout."""
    if not _is_argv(cmd):
        raise WorkflowError("cmd must be argv")
    env = {key: value for key, value in os.environ.items() if key not in HARNESS_ENV}
    try:
        p = _run_text(
            cmd,
            cwd=cwd,
            env=env,
            errors="replace",
            timeout=CHECK_TIMEOUT if timeout is None else timeout,
        )
    except subprocess.TimeoutExpired as exc:

        def decode(value: bytes | str | None) -> str:
            return (
                value.decode(errors="replace")
                if isinstance(value, bytes)
                else value or ""
            )

        raise WorkflowError(
            f"timeout: {exc}\n{decode(exc.stdout)}{decode(exc.stderr)}"
        ) from exc
    except (OSError, ValueError) as exc:
        raise WorkflowError(f"launch error: {exc}") from exc
    ok = p.returncode == 0
    return ok, p.stdout if ok and stdout_only else p.stdout + p.stderr


def _git(repo: StrPath, *args: str) -> str:
    try:
        p = _run_text(["git", "-C", str(repo), *args], errors="surrogateescape")
    except OSError as exc:
        raise WorkflowError(f"git infrastructure: {exc}") from exc
    if p.returncode:
        raise WorkflowError(p.stderr.strip() or f"git exited {p.returncode}: {args}")
    return p.stdout


def _gh(repo: StrPath, *args: str) -> str:
    try:
        process = _run_text(["gh", *args], cwd=repo, errors="replace", timeout=20)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        raise WorkflowError(f"gh unavailable: {exc}") from exc
    if process.returncode:
        raise WorkflowError(f"gh failed: {process.stderr.strip()}")
    return process.stdout


def _github(repo: StrPath, *args: str) -> Any:
    output = _gh(repo, *args)
    try:
        return json.loads(output)
    except ValueError as exc:
        raise WorkflowError(f"gh unavailable or invalid response: {exc}") from exc


def _github_repo(repo: StrPath) -> str:
    """Return OWNER/REPO of origin (or the only remote) when it is a GitHub URL."""
    remotes = _git(repo, "remote").splitlines()
    remote = (
        "origin" if "origin" in remotes else remotes[0] if len(remotes) == 1 else None
    )
    if remote is None:
        raise WorkflowError("cannot resolve primary GitHub remote")
    url = _git(repo, "remote", "get-url", remote).strip()
    match = re.fullmatch(
        r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?",
        url,
    )
    if not match:
        raise WorkflowError("primary remote is not a validated GitHub repository")
    return match[1]


def _pr_at_head(repo: StrPath, head: str, fields: tuple[str, ...]) -> dict[str, Any]:
    """The one open PR whose head is exactly `head`, with the requested gh JSON fields."""
    prs = _github(
        repo,
        "pr",
        "list",
        "--search",
        head,
        "--state",
        "open",
        "--json",
        ",".join(("headRefOid", *fields)),
    )
    matches = (
        [pr for pr in prs if isinstance(pr, dict) and pr.get("headRefOid") == head]
        if isinstance(prs, list)
        else []
    )
    if len(matches) != 1:
        raise WorkflowError("no unique open PR at requested head")
    return matches[0]


def _required_context_state(repo: StrPath, head: str, context: str) -> str:
    """State of the gate's required CI context on the exact head, from any poster: a commit status or a check run.

    Mirrors the rails merge rule: "success" when a status with that context is success or a check run with
    that name completed with conclusion success.
    """
    slug = _github_repo(repo)
    combined = _github(repo, "api", f"repos/{slug}/commits/{head}/status?per_page=100")
    statuses = [
        item
        for item in (combined.get("statuses") if isinstance(combined, dict) else None)
        or []
        if isinstance(item, dict) and item.get("context") == context
    ]
    listed = _github(
        repo,
        "api",
        f"repos/{slug}/commits/{head}/check-runs?check_name={quote(context, safe='')}&per_page=100",
    )
    runs = [
        item
        for item in (listed.get("check_runs") if isinstance(listed, dict) else None)
        or []
        if isinstance(item, dict) and item.get("name") == context
    ]
    if any(item.get("state") == "success" for item in statuses) or any(
        item.get("status") == "completed" and item.get("conclusion") == "success"
        for item in runs
    ):
        return "success"
    if statuses:
        return str(statuses[0].get("state"))
    if runs:
        return f"{runs[0].get('status')}/{runs[0].get('conclusion') or ''}"
    return "missing"


def _sha(path: StrPath) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _canonical(name: object) -> str:
    if (
        not isinstance(name, str)
        or not name
        or "\0" in name
        or "\\" in name
        or ":" in name
        or any(part in ("", ".", "..", ".git") for part in name.split("/"))
    ):
        raise WorkflowError(
            f"invalid proof path: {name!r}; require canonical relative POSIX path"
        )
    return name


def _proof_path(root: StrPath, name: str) -> Path:
    _canonical(name)
    root = Path(root).resolve()
    path = root / name
    for component in (path, *path.parents):
        if component == root:
            break
        if component.is_symlink():
            raise WorkflowError(f"symlink proof path: {name}")
    return path


def _limit(value: object) -> None:
    if type(value) is not int or value <= 0:
        raise WorkflowError("max_checks must be a positive integer")


def _hex(value: object, length: int) -> bool:
    return (
        isinstance(value, str)
        and re.fullmatch(rf"[0-9a-f]{{{length}}}", value) is not None
    )


def _validate(
    manifest: object, max_checks: int, *, frozen: bool = True
) -> dict[str, Any]:
    """Lexical manifest checks. Zero proofs is valid: a PR with no behaviour change freezes none."""
    _limit(max_checks)
    if (
        not isinstance(manifest, dict)
        or type(manifest.get("schemaVersion")) is not int
        or manifest["schemaVersion"] != 1
    ):
        raise WorkflowError("unsupported manifest schema")
    _validate_fields(manifest, frozen=frozen)
    proofs = manifest.get("proofs")
    if not isinstance(proofs, list) or len(proofs) > max_checks:
        raise WorkflowError("proofs must be a list within max_checks")
    ids: set[str] = set()
    for proof in proofs:
        _validate_proof(proof, ids, frozen=frozen)
    return manifest


def _validate_fields(manifest: dict[str, Any], *, frozen: bool) -> None:
    """The manifest's own fields: no unknown field, slug, base, setup and revisions."""
    unknown = sorted(set(manifest) - MANIFEST_FIELDS)
    if unknown:
        raise WorkflowError(f"unknown manifest field {', '.join(unknown)}")
    slug = manifest.get("slug")
    if not isinstance(slug, str) or not re.fullmatch(
        r"[a-zA-Z0-9][a-zA-Z0-9_-]*", slug
    ):
        raise WorkflowError("invalid manifest slug")
    if frozen and not _hex(manifest.get("base"), 40):
        raise WorkflowError("base must be 40-hex")
    if "setup" in manifest and not _is_argv(manifest["setup"]):
        raise WorkflowError("setup must be argv")
    revisions = manifest.get("revisions", [])
    if not isinstance(revisions, list):
        raise WorkflowError("revisions must be a list")
    if any(not _valid_revision(revision) for revision in revisions):
        raise WorkflowError("invalid revision")


def _valid_revision(revision: object) -> bool:
    return (
        isinstance(revision, dict)
        and isinstance(revision.get("id"), str)
        and bool(revision["id"].strip())
        and _hex(revision.get("oldSha256"), 64)
        and isinstance(revision.get("reason"), str)
        and bool(revision["reason"].strip())
    )


def _validate_proof(proof: object, ids: set[str], *, frozen: bool) -> None:
    """One proof entry; its ID joins `ids`, which must not hold it already."""
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
    if "parent" in proof and (
        not isinstance(proof["parent"], str)
        or not proof["parent"].strip()
        or "\0" in proof["parent"]
        or (frozen and not _hex(proof["parent"], 40))
    ):
        raise WorkflowError(
            f"{ident}: parent must be 40-hex" if frozen else f"{ident}: invalid parent"
        )
    if not _is_argv(proof.get("cmd")):
        raise WorkflowError(f"{ident}: cmd must be argv")
    if "journey" in proof and type(proof["journey"]) is not bool:
        raise WorkflowError(f"{ident}: journey must be a boolean")
    files = _validate_files(ident, proof.get("files"))
    if frozen:
        _validate_hashes(ident, proof.get("sha256"), files)


def _validate_files(ident: str, files: object) -> list[str]:
    """A nonempty list of distinct canonical proof paths."""
    if not isinstance(files, list) or not files:
        raise WorkflowError(f"{ident}: missing or duplicate proof files")
    for name in files:
        _canonical(name)
    if len(set(files)) != len(files):
        raise WorkflowError(f"{ident}: missing or duplicate proof files")
    return files


def _validate_hashes(ident: str, hashes: object, files: list[str]) -> None:
    if not isinstance(hashes, dict) or set(hashes) != set(files):
        raise WorkflowError(f"{ident}: proof hash coverage mismatch")
    if any(not _hex(value, 64) for value in hashes.values()):
        raise WorkflowError(f"{ident}: invalid sha256")


def load_manifest(
    path: StrPath, digest: object, max_checks: int = MAX_PROOFS
) -> dict[str, Any]:
    """Validate lexical structure without inspecting arbitrary host paths."""
    _limit(max_checks)
    try:
        frozen = Path(path).read_bytes()
        if not _hex(digest, 64) or hashlib.sha256(frozen).hexdigest() != digest:
            raise WorkflowError("manifest hash mismatch")
        return _validate(json.loads(frozen), max_checks)
    except (OSError, ValueError, TypeError) as exc:
        raise WorkflowError(f"invalid manifest: {exc}") from exc


def _command_files(proof: dict[str, Any], roots: tuple[StrPath, ...]) -> None:
    for arg in proof["cmd"]:
        for token in (arg, arg.split("=", 1)[1]) if "=" in arg else (arg,):
            _command_token(proof, token, roots)


def _inside_roots(candidate: Path, roots: tuple[StrPath, ...]) -> bool:
    return any(
        candidate.resolve().is_relative_to(Path(root).resolve())
        or candidate.absolute().is_relative_to(Path(root).absolute())
        or (candidate.parent.resolve() / candidate.name).is_relative_to(
            Path(root).resolve()
        )
        for root in roots
    )


def _command_token(
    proof: dict[str, Any], token: str, roots: tuple[StrPath, ...]
) -> None:
    candidate = Path(token)
    if candidate.is_absolute():
        if _inside_roots(candidate, roots):
            raise WorkflowError(
                f"{proof['id']}: absolute command reference into worktree: {token}"
            )
        return
    name = token.removeprefix("./")
    try:
        _canonical(name)
    except WorkflowError:
        return
    for root in roots:
        path = _proof_path(root, name)
        if path.is_file() and name not in proof["files"]:
            raise WorkflowError(
                f"{proof['id']}: command file not frozen: {name}; unchanged runners must also be frozen"
            )


def _commit(repo: StrPath, ref: str) -> str:
    return _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").strip()


def _ancestor(repo: StrPath, older: str, newer: str, strict: bool = False) -> bool:
    p = _run_bytes([
        "git",
        "-C",
        str(repo),
        "merge-base",
        "--is-ancestor",
        older,
        newer,
    ])
    if p.returncode not in (0, 1):
        raise WorkflowError(
            f"ancestry infrastructure: {p.stderr.decode(errors='replace')}"
        )
    return (not strict or older != newer) and p.returncode == 0


def _scrub(text: str, tree: StrPath) -> str:
    for prefix in sorted({str(tree), str(Path(tree).resolve())}, key=len, reverse=True):
        text = text.replace(prefix, "<tree>")
    return text


def _setup(tree: Path, setup: list[str] | None) -> set[str]:
    if setup:
        ok, out = run_check(setup, tree)
        if not ok:
            raise WorkflowError(f"setup failed:\n{_scrub(out, tree)}")
    if _git(tree, "status", "--porcelain", "--untracked-files=no"):
        raise WorkflowError("setup changed tracked files")
    return _entries(tree)


def _entries(tree: Path) -> set[str]:
    status = _git(
        tree,
        "status",
        "--porcelain=v1",
        "--ignored=matching",
        "--untracked-files=normal",
        "-z",
    )
    return {
        entry[3:] for entry in status.split("\0") if entry.startswith(("?? ", "!! "))
    }


def _reset(tree: Path, baseline: set[str] | None) -> None:
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


def _hash_failures(root: StrPath, manifest: dict[str, Any]) -> list[str]:
    failures = []
    for proof in manifest["proofs"]:
        for name in proof["files"]:
            path = _proof_path(root, name)
            if not path.is_file() or _sha(path) != proof["sha256"][name]:
                failures.append(
                    f"{proof['id']}: missing proof or hash mismatch: {name}"
                )
    return failures


@dataclass(frozen=True)
class Placement:
    """Where one proof execution runs and how its record is labelled.

    `overlay` maps proof paths to the bytes written over the tree before the run; `roots` are
    other trees the command must not reference by absolute path.
    """

    tree: Path
    location: str = "head"
    run: int | None = None
    overlay: dict[str, bytes] | None = None
    roots: tuple[StrPath, ...] = ()


def _execute(
    placement: Placement,
    proof: dict[str, Any],
    baselines: Baselines,
    setup: list[str] | None = None,
) -> dict[str, Any]:
    """Reuse each tree's setup; reset tracked files and remove non-baseline entries."""
    tree, location, run = placement.tree, placement.location, placement.run
    record: dict[str, Any] = {
        "location": location,
        "ok": None,
        "output": "",
    }
    if run is not None:
        record["run"] = run
    try:
        _run_placed(placement, proof, _baseline(tree, baselines, setup), record)
    except INFRASTRUCTURE_ERRORS as exc:
        record["ok"] = None
        record["error"] = (
            f"{proof['id']}: {location}"
            + (f" run {run}" if run else "")
            + f": infrastructure: {exc}"
        )
    finally:
        try:
            baseline = baselines.get(tree)
            _reset(tree, None if isinstance(baseline, Exception) else baseline)
        except INFRASTRUCTURE_ERRORS as exc:
            record["ok"] = None
            record["error"] = (
                record.get("error", "")
                + f" {proof['id']}: {location}: cleanup infrastructure: {exc}"
            )
    record["output"] = _scrub(record["output"], tree)
    if "error" in record:
        record["error"] = _scrub(record["error"], tree)
    return record


def _baseline(tree: Path, baselines: Baselines, setup: list[str] | None) -> set[str]:
    """The tree's untracked entries after its one setup run; a failed setup raises on every use."""
    if tree not in baselines:
        try:
            baselines[tree] = _setup(tree, setup)
        except INFRASTRUCTURE_ERRORS as exc:
            baselines[tree] = exc
    baseline = baselines[tree]
    if isinstance(baseline, Exception):
        raise baseline
    return baseline


def _run_placed(
    placement: Placement,
    proof: dict[str, Any],
    baseline: set[str],
    record: dict[str, Any],
) -> None:
    """Reset the tree, write the overlay, run the proof command and check it left no trace."""
    tree, overlay = placement.tree, placement.overlay
    _reset(tree, baseline)
    for name, data in (overlay or {}).items():
        dest = _proof_path(tree, name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    _command_files(proof, (tree, *placement.roots))
    before = _git(tree, "diff", "--binary", "HEAD")
    record["ok"], record["output"] = run_check(proof["cmd"], tree)
    if before != _git(tree, "diff", "--binary", "HEAD"):
        raise WorkflowError("proof run modified tracked files")
    for name, data in (overlay or {}).items():
        path = _proof_path(tree, name)
        if not path.is_file() or path.read_bytes() != data:
            raise WorkflowError(f"proof run modified overlay: {name}")


def _red_at_base_failure(
    proof: dict[str, Any], record: dict[str, Any], marker: str | None
) -> str | None:
    """The one proof rule at base: any failing run is red, unless the config's doctor marker shows the environment failed."""
    error: str | None = record.get("error")
    if error:
        return error
    if record["ok"]:
        return (
            f"{proof['id']}: passes at baseline; a proof must fail at base and pass at head. "
            "For a regression found after implementation, supply the defective implementation commit as parent"
        )
    if marker and marker in record["output"]:
        return f"{proof['id']}: red came from the environment doctor, not the behaviour"
    return None


def _proof_digest(proof: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(proof, sort_keys=True).encode()).hexdigest()


def _remove_trees(repo: StrPath, trees: list[Path]) -> list[str]:
    errors = []
    for tree in reversed(trees):
        try:
            _git(repo, "worktree", "remove", "--force", str(tree))
        except INFRASTRUCTURE_ERRORS as exc:
            errors.append(f"cleanup infrastructure {tree}: {exc}")
    if errors:
        try:
            _git(repo, "worktree", "prune")
        except INFRASTRUCTURE_ERRORS as exc:
            errors.append(f"cleanup prune infrastructure: {exc}")
    return errors


class FreezeResult(tuple[Path, str]):
    """`(manifest_path, sha256)`; `formatted` lists paths checks.format changed, for the agent to commit."""

    formatted: list[str]

    def __new__(
        cls, path: Path, digest: str, formatted: Iterable[str] = ()
    ) -> "FreezeResult":
        result = super().__new__(cls, (path, digest))
        result.formatted = list(formatted)
        return result


def _doctor_marker(config: dict[str, Any]) -> str | None:
    """tests.verification_surface.doctor_marker from the repo config; an absent or exempt surface skips."""
    tests = config.get("tests")
    surface = tests.get("verification_surface") if isinstance(tests, dict) else None
    marker = surface.get("doctor_marker") if isinstance(surface, dict) else None
    return marker if isinstance(marker, str) else None


def _feature_map_path(config: dict[str, Any]) -> str | None:
    """verification_surface.feature_map_path (repo-relative, no trailing slash), or None when the
    config declares none or exempts the surface."""
    surface = _surface()[0].surface(config)
    value = surface.get("feature_map_path") if surface is not None else None
    return value.strip().rstrip("/") if isinstance(value, str) else None


def _unmapped_proof_files(
    root: StrPath, map_path: str, proofs: list[dict[str, Any]]
) -> list[str]:
    """Proof files that no feature-map document in the working tree links to."""
    if not (Path(root) / map_path).exists():
        raise WorkflowError(
            f"feature map {map_path} (verification_surface.feature_map_path) does not exist; "
            "write the feature map before freezing proofs"
        )
    linked, _ = _surface()[1].mapped_paths(Path(root), map_path)
    return [
        f"{proof['id']}: {name}"
        for proof in proofs
        for name in proof["files"]
        if name not in linked
    ]


def _map_document(map_path: str, name: str, indexes: Collection[str]) -> bool:
    """Whether a changed repo path is a feature document of the map at map_path (index files are not)."""
    if PurePosixPath(name).name in indexes:
        return False
    return name == map_path or (
        name.startswith(f"{map_path}/") and name.endswith(".md")
    )


def _unproven_map_docs(
    repo: Path,
    parent: str,
    head: str,
    config: dict[str, Any],
    manifest: dict[str, Any],
) -> list[str]:
    """Feature-map documents changed in parent..head that link no proof file in the manifest."""
    map_path = _feature_map_path(config)
    if map_path is None:
        return []
    feature_map = _surface()[0]
    indexes = feature_map.index_files(config)
    changed = _git(
        repo,
        "diff",
        "--name-only",
        "--no-renames",
        "--diff-filter=d",
        "-z",
        parent,
        head,
        "--",
        map_path,
    )
    proof_files = {name for proof in manifest["proofs"] for name in proof["files"]}
    unproven = []
    for doc in sorted(
        name
        for name in changed.split("\0")
        if name and _map_document(map_path, name, indexes)
    ):
        text = _git(repo, "show", f"{head}:{doc}")
        if not feature_map.linked_files(doc, text) & proof_files:
            unproven.append(doc)
    return unproven


def _dirty(root: Path) -> dict[str, str | None]:
    status = _git(
        root, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames"
    )
    names = [entry[3:] for entry in status.split("\0") if entry]
    return {
        name: _sha(root / name)
        if (root / name).is_file() and not (root / name).is_symlink()
        else None
        for name in names
    }


def _format(root: Path, argv: list[str]) -> tuple[list[str], set[str]]:
    """Run checks.format; return (paths it changed, paths it made dirty that were clean)."""
    before = _dirty(root)
    ok, out = run_check(argv, root)
    if not ok:
        raise WorkflowError(f"checks.format failed:\n{_scrub(out, root)}")
    after = _dirty(root)
    newly = set(after) - set(before)
    changed = {
        name for name in after if name in newly or before[name] != after[name]
    } | (set(before) - set(after))
    return sorted(changed), newly


class FreezeOptions(TypedDict, total=False):
    """freeze()'s keyword arguments."""

    base: str | None
    setup: list[str] | None
    previous: tuple[StrPath, str] | None
    revise: dict[str, str] | None
    max_checks: int


@dataclass(frozen=True)
class FreezeRequest:
    """freeze()'s validated arguments."""

    slug: str
    base: str | None
    setup: list[str] | None
    previous: tuple[StrPath, str] | None
    revise: dict[str, str]
    max_checks: int


@dataclass(frozen=True)
class Lineage:
    """The manifest a freeze replaces (empty for a first freeze) and the commits it binds."""

    old: dict[str, Any]
    old_proofs: dict[str, dict[str, Any]]
    base: str
    setup: list[str] | None
    head: str


def freeze(
    worktree: StrPath,
    manifest_path: StrPath,
    proofs: list[dict[str, Any]],
    slug: str,
    **options: Unpack[FreezeOptions],
) -> FreezeResult:
    """Freeze working-tree proofs after showing each one fails at its baseline (red at base).

    Keyword arguments: base=None, setup=None, previous=None, revise=None,
    max_checks=MAX_PROOFS.

    Policy comes from the clone's .agentrails/config.json (read_config), never from the repo
    tree. A clone that is not onboarded refuses; onboarding merges are owner merges. `proofs`
    may be empty: a PR with no behaviour change (refactor, docs) freezes
    none, and prove() then checks CI green unless the PR changes a feature-map doc, which must
    link a frozen proof. Every proof must be linked from a feature-map doc.
    Journey stability is a prove() check on the head being merged, not a freeze() check.
    """
    _check_options("freeze", options, FreezeOptions)
    request = _freeze_request(slug, proofs, options)
    root, path = Path(worktree).resolve(), Path(manifest_path).resolve()
    if path != _common(root) / "omp-manifests" / f"{slug}.json":
        raise WorkflowError(
            "manifest must be durable; use <git-common-dir>/omp-manifests/<slug>.json"
        )
    lineage = _lineage(root, request)
    config = read_config(root)
    marker = _doctor_marker(config)
    policy = gate_policy(config)
    formatted, format_dirtied = (
        _format(root, policy["format"]) if policy["format"] else ([], set())
    )
    manifest: dict[str, Any] = {
        "schemaVersion": 1,
        "slug": slug,
        "base": lineage.base,
        "proofs": [],
    }
    if lineage.setup is not None:
        manifest["setup"] = lineage.setup
    manifest["proofs"].extend(_frozen_proof(root, proof, lineage) for proof in proofs)
    map_path = _mapped_proofs(root, config, manifest)
    _refuse_unfrozen(root, manifest, format_dirtied, map_path)
    changed = _record_revisions(manifest, lineage, request.revise)
    _validate(manifest, request.max_checks)
    _show_red_at_base(root, manifest, lineage, changed, marker)
    _atomic_json(path, manifest)
    return FreezeResult(path, _sha(path), formatted)


def _freeze_request(
    slug: str, proofs: list[dict[str, Any]], options: FreezeOptions
) -> FreezeRequest:
    """Validate freeze()'s arguments before any Git or filesystem access."""
    max_checks = options.get("max_checks", MAX_PROOFS)
    _limit(max_checks)
    base = options.get("base")
    if base is not None and (
        not isinstance(base, str) or not base.strip() or "\0" in base
    ):
        raise WorkflowError("base must be a nonblank commit reference")
    setup = options.get("setup")
    draft: dict[str, Any] = {"schemaVersion": 1, "slug": slug, "proofs": proofs}
    if setup is not None:
        draft["setup"] = setup
    _validate(draft, max_checks, frozen=False)
    previous = options.get("previous")
    if previous is not None and (
        not isinstance(previous, (tuple, list)) or len(previous) != 2
    ):
        raise WorkflowError("previous must be (manifest_path, sha256)")
    revise = options.get("revise")
    if revise is None:
        revise = {}
    if not isinstance(revise, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) or not v.strip()
        for k, v in revise.items()
    ):
        raise WorkflowError("revise must map proof IDs to nonblank reasons")
    return FreezeRequest(slug, base, setup, previous, revise, max_checks)


def _lineage(root: Path, request: FreezeRequest) -> Lineage:
    """The previous manifest and the base, setup and head this freeze binds; a re-freeze keeps its lineage."""
    previous = request.previous
    old = (
        load_manifest(previous[0], previous[1], request.max_checks)
        if previous
        else {"proofs": []}
    )
    old_proofs = {p["id"]: p for p in old["proofs"]}
    if set(request.revise) - set(old_proofs):
        raise WorkflowError("revision names an unknown previous proof")
    resolved_base = _commit(
        root, request.base if request.base is not None else old.get("base", "HEAD")
    )
    setup = old.get("setup") if previous and request.setup is None else request.setup
    if previous and (
        request.slug != old["slug"]
        or resolved_base != old["base"]
        or setup != old.get("setup")
    ):
        raise WorkflowError(
            "re-freeze slug, base and setup are immutable; start a new lineage"
        )
    head = _commit(root, "HEAD")
    if not _ancestor(root, resolved_base, head):
        raise WorkflowError("base is not an ancestor of HEAD")
    return Lineage(old, old_proofs, resolved_base, setup, head)


def _frozen_proof(
    root: Path, proof: dict[str, Any], lineage: Lineage
) -> dict[str, Any]:
    """The proof with its resolved parent commit and the sha256 of each of its files."""
    ident = proof["id"]
    baseline = proof.get(
        "parent", lineage.old_proofs.get(ident, {}).get("parent", lineage.base)
    )
    item = dict(proof, parent=_commit(root, baseline), sha256={})
    if not _ancestor(root, lineage.base, item["parent"]) or not _ancestor(
        root, item["parent"], lineage.head
    ):
        raise WorkflowError(
            f"{ident}: parent is not an ancestor of HEAD at or after base"
        )
    _command_files(item, (root,))
    for name in item["files"]:
        item["sha256"][name] = _frozen_file(root, ident, name)
    return item


def _frozen_file(root: Path, ident: str, name: str) -> str:
    """The sha256 of a regular, tracked-eligible proof file whose bytes Git stores unchanged."""
    source = _proof_path(root, name)
    if not source.is_file():
        raise WorkflowError(f"{ident}: missing regular proof file: {name}")
    ignored = _run_bytes(["git", "-C", str(root), "check-ignore", "-q", "--", name])
    if ignored.returncode == 0:
        raise WorkflowError(f"{ident}: ignored proof file: {name}")
    if ignored.returncode != 1:
        raise WorkflowError(
            f"{ident}: check-ignore infrastructure: {ignored.stderr.decode(errors='replace')}"
        )
    if _git(root, "hash-object", f"--path={name}", "--", name) != _git(
        root, "hash-object", "--no-filters", "--", name
    ):
        raise WorkflowError(f"{ident}: normalise line endings/clean filters: {name}")
    return _sha(source)


def _mapped_proofs(
    root: Path, config: dict[str, Any], manifest: dict[str, Any]
) -> str | None:
    """The feature map path when there are proofs, after checking a map document links each proof file."""
    if not manifest["proofs"]:
        return None
    map_path = _feature_map_path(config)
    if map_path is None:
        raise WorkflowError(FEATURE_MAP_SETUP)
    unmapped = _unmapped_proof_files(root, map_path, manifest["proofs"])
    if unmapped:
        raise WorkflowError(
            f"proof files not linked from any feature-map document under {map_path}: "
            f"{', '.join(unmapped)}; link each proof from its feature's map entry"
        )
    return map_path


def _refuse_unfrozen(
    root: Path, manifest: dict[str, Any], format_dirtied: set[str], map_path: str | None
) -> None:
    """Every working-tree change is a proof file, a formatter change or a feature-map document."""
    names = {name for proof in manifest["proofs"] for name in proof["files"]}
    for entry in _git(
        root, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames"
    ).split("\0"):
        name = entry[3:]
        if (
            entry
            and name not in names
            and name not in format_dirtied
            and not (map_path and (name == map_path or name.startswith(f"{map_path}/")))
        ):
            raise WorkflowError(
                f"unfrozen change: {name}; add it to a proof's files or revert"
            )


def _record_revisions(
    manifest: dict[str, Any], lineage: Lineage, revise: dict[str, str]
) -> set[str]:
    """Append a revision for each changed previous proof; return the IDs of those proofs."""
    current = {p["id"]: p for p in manifest["proofs"]}
    revisions, changed = list(lineage.old.get("revisions", [])), set()
    for ident, proof in lineage.old_proofs.items():
        if current.get(ident) != proof:
            if ident not in revise:
                raise WorkflowError(
                    f"{ident}: silent proof change; supply revise reason"
                )
            revisions.append({
                "id": ident,
                "oldSha256": _proof_digest(proof),
                "reason": revise[ident],
            })
            changed.add(ident)
    if revisions:
        manifest["revisions"] = revisions
    return changed


def _show_red_at_base(
    root: Path,
    manifest: dict[str, Any],
    lineage: Lineage,
    changed: set[str],
    marker: str | None,
) -> None:
    """Run each new or revised proof at its parent with the frozen bytes overlaid; each must fail."""
    trees: list[Path] = []
    parent_trees: dict[str, Path] = {}
    baselines: Baselines = {}
    failure = None
    with tempfile.TemporaryDirectory(prefix="omp-freeze-") as scratch:
        try:
            for proof in manifest["proofs"]:
                if proof["id"] in lineage.old_proofs and proof["id"] not in changed:
                    continue
                commit = proof["parent"]
                if commit not in parent_trees:
                    tree = Path(scratch) / f"tree-{len(trees)}"
                    _git(root, "worktree", "add", "--detach", str(tree), commit)
                    trees.append(tree)
                    parent_trees[commit] = tree
                overlay = {
                    name: _proof_path(root, name).read_bytes()
                    for name in proof["files"]
                }
                placement = Placement(
                    parent_trees[commit], "baseline", overlay=overlay, roots=(root,)
                )
                error = _red_at_base_failure(
                    proof, _execute(placement, proof, baselines, lineage.setup), marker
                )
                if error:
                    raise WorkflowError(error)
            errors = _hash_failures(root, manifest)
            if errors:
                raise WorkflowError("; ".join(errors))
        except INFRASTRUCTURE_ERRORS as exc:
            failure = str(exc)
        finally:
            cleanup = _remove_trees(root, trees)
        if failure or cleanup:
            raise WorkflowError("; ".join(([failure] if failure else []) + cleanup))


def _common(repo: StrPath) -> Path:
    common = Path(_git(repo, "rev-parse", "--git-common-dir").strip())
    return (common if common.is_absolute() else Path(repo) / common).resolve()


def _atomic_json(path: StrPath, value: object) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            name = stream.name
            json.dump(value, stream, indent=2)
            stream.write("\n")
        os.replace(name, path)
    finally:
        if name:
            Path(name).unlink(missing_ok=True)


def _range(repo: StrPath, parent: str, head: str, target_ref: str) -> tuple[str, str]:
    parent, target = _commit(repo, parent), _commit(repo, target_ref)
    if not _ancestor(repo, parent, head, strict=True):
        raise WorkflowError("parent must be a strict ancestor of head")
    if parent != _git(repo, "merge-base", target, head).strip():
        raise WorkflowError("parent must equal merge-base(target_ref, head)")
    return parent, target


def gate_policy(config: dict[str, Any]) -> dict[str, Any]:
    """The policy consumed here from a validated repo config: the format command and CI."""
    return {"format": config["commands"].get("format"), "ci": config["ci"]}


def deterministic_checks(
    head_tree: Path,
    manifest: dict[str, Any],
    roots: tuple[StrPath, ...] = (),
) -> dict[str, Any]:
    """Run every frozen proof at head: frozen bytes unchanged, then HEAD_RUNS passing runs, or for a
    journey proof JOURNEY_RUNS. Red at base was shown at freeze()."""
    result: dict[str, Any] = {"proofs": {}, "blocking": []}
    failures: list[str] = result["blocking"]
    failures.extend(_hash_failures(head_tree, manifest))
    if failures:
        return result
    baselines: Baselines = {}
    for proof in manifest["proofs"]:
        runs = result["proofs"].setdefault(proof["id"], [])
        count = JOURNEY_RUNS if proof.get("journey") else HEAD_RUNS
        for n in range(1, count + 1):
            placement = Placement(head_tree, "head", run=n, roots=(head_tree, *roots))
            record = _execute(placement, proof, baselines, manifest.get("setup"))
            runs.append(record)
            if record.get("error"):
                failures.append(record["error"])
            elif not record["ok"]:
                failures.append(f"{proof['id']}: head run {n} failed")
        passed = sum(record["ok"] is True for record in runs)
        if proof.get("journey") and passed != count:
            failures.append(
                f"{proof['id']}: journey proof passed {passed} of {count} clean head runs; journeys must pass every run"
            )
    return result


def _json_safe(value: object) -> Any:
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


def result_path(repo: StrPath, head: str) -> Path:
    """prove()'s JSON result for `head` in the Git common directory: the one merge signal.

    Rails' merge guard reads `status: "pass"` for the exact head from this file.
    """
    return _common(repo) / "omp-prove" / f"{head}.result.json"


def _write_result(result: dict[str, Any], repo: StrPath, head: str) -> Exception | None:
    try:
        _atomic_json(result_path(repo, head), result)
    except INFRASTRUCTURE_ERRORS as exc:
        return exc
    return None


def _truncate_outputs(runs: list[dict[str, Any]], tail: int) -> None:
    for record in runs:
        output = record.get("output", "")
        if len(output) > tail:
            record["output"] = f"[TRUNCATED to last {tail} chars]" + (
                output[-tail:] if tail else ""
            )


def _result_comment(result: dict[str, Any]) -> str:
    """PR comment body: the machine result as JSON, with long proof outputs tail-truncated to fit GitHub's limit."""
    header = (
        f"{RESULT_MARKER}\n**prove() {result['status']}** for `{result['head']}`. "
        f"Full JSON: `<git-common-dir>/omp-prove/{result['head']}.result.json` on the proving machine.\n\n"
    )
    for tail in (COMMENT_OUTPUT_TAIL, 0):
        compact = copy.deepcopy(result)
        proofs = (
            compact.get("deterministic", {}).get("proofs", {})
            if isinstance(compact.get("deterministic"), dict)
            else {}
        )
        for runs in proofs.values():
            _truncate_outputs(runs, tail)
        body = f"{header}```json\n{json.dumps(compact, indent=2)}\n```\n"
        if len(body) <= COMMENT_LIMIT:
            return body
    return (
        f"{header}The result is too large for a PR comment; read the full JSON file.\n"
    )


def _post_result_comment(repo: StrPath, head: str, result: dict[str, Any]) -> str:
    """Post the result on the one open PR at `head`; return the comment URL gh prints."""
    pr = _pr_at_head(repo, head, ("number",))
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".md") as body:
        body.write(_result_comment(result))
        body.flush()
        return _gh(
            repo, "pr", "comment", str(pr["number"]), "--body-file", body.name
        ).strip()


def _publish_result(result: dict[str, Any], repo: StrPath, head: str) -> None:
    """Write the result file and post it on the PR. A pass that cannot be published becomes a fail;
    a result file that cannot be rewritten is removed, so a stale pass never survives."""

    def fail(reason: object) -> None:
        result["status"] = "fail"
        result["blocking"].append(f"infrastructure: publishing prove result: {reason}")

    if _write_result(result, repo, head) is not None:
        fail("writing the result file")
    try:
        result["prComment"] = _post_result_comment(repo, head, result)
    except INFRASTRUCTURE_ERRORS as exc:
        fail(exc)
    error = _write_result(result, repo, head)
    if error is not None:
        fail(error)
        try:
            result_path(repo, head).unlink(missing_ok=True)
        except INFRASTRUCTURE_ERRORS as exc:
            result["blocking"].append(
                f"infrastructure: removing the unwritable result file: {exc}"
            )


class ProveOptions(TypedDict, total=False):
    """prove()'s keyword arguments."""

    target_ref: Required[str]
    max_checks: int


@dataclass(frozen=True)
class ProveRequest:
    """prove()'s arguments for one exact head."""

    parent: str
    frozen: object
    objective: object
    target_ref: str
    max_checks: int


def prove(
    repo: StrPath,
    parent: str,
    head: str,
    frozen: tuple[StrPath, str],
    objective: str,
    **options: Unpack[ProveOptions],
) -> dict[str, Any]:
    """At the PR event: refuse unless the config's required CI context is green on head, then check
    rename residue, new-path path-check and structure over parent..head, feature-map coverage, and
    frozen proofs at head (unchanged bytes, every run passing).

    `frozen` is freeze()'s result, `(manifest_path, sha256)`. Keyword arguments: target_ref
    (required) and max_checks=MAX_PROOFS.

    The result is written to result_path(repo, head), the merge signal rails reads, and posted on the PR at head.
    """
    _check_options("prove", options, ProveOptions)
    manifest_path, manifest_sha256 = (
        frozen
        if isinstance(frozen, (tuple, list)) and len(frozen) == 2
        else (None, None)
    )
    request = ProveRequest(
        parent,
        frozen,
        objective,
        options["target_ref"],
        options.get("max_checks", MAX_PROOFS),
    )
    result: dict[str, Any] = {
        "status": "fail",
        "head": None,
        "objective": _json_safe(objective),
        "manifestSha256": _json_safe(manifest_sha256),
        "deterministic": {},
        "blocking": [],
        "decisionLogErrors": [],
    }
    published = None
    try:
        repo = Path(repo).resolve()
        head = _commit(repo, head)
        result["head"] = head
        published = (repo, head)
        result_path(repo, head).unlink(missing_ok=True)
        _prove_head(repo, head, request, result)
        if not result["blocking"]:
            result["status"] = "pass"
    except ProveBlockedError as exc:
        result["blocking"].append(str(exc))
    except INFRASTRUCTURE_ERRORS as exc:
        result["blocking"].append(f"infrastructure: {exc}")
    except Exception as exc:
        # A bug still leaves a fail result and PR comment explaining it, then propagates.
        result["blocking"].append(f"unexpected {type(exc).__name__}: {exc}")
        if published is not None:
            _publish_result(result, *published)
        raise
    if published is not None:
        _publish_result(result, *published)
    return result


def _prove_head(
    repo: Path, head: str, request: ProveRequest, result: dict[str, Any]
) -> None:
    """prove()'s checks on the exact head; blocking findings join `result`, prerequisites raise."""
    _limit(request.max_checks)
    if not isinstance(request.objective, str) or not request.objective.strip():
        raise WorkflowError("objective must be nonblank")
    parent, _ = _range(repo, request.parent, head, request.target_ref)
    frozen = request.frozen
    if not isinstance(frozen, (tuple, list)) or len(frozen) != 2:
        raise WorkflowError("frozen must be freeze()'s (manifest_path, sha256)")
    manifest = load_manifest(frozen[0], frozen[1], request.max_checks)
    result["manifest"] = manifest
    base = manifest["base"]
    if not _ancestor(repo, base, parent):
        raise WorkflowError("base must be an ancestor of parent")
    config = _config_with_green_ci(repo, head)
    findings, log_errors = _harness_check_findings(repo, parent, head)
    result["blocking"].extend(findings)
    result["decisionLogErrors"].extend(log_errors)
    unproven = _unproven_map_docs(repo, parent, head, config, manifest)
    if unproven:
        raise ProveBlockedError(
            f"feature-map documents changed in {parent}..{head} link no frozen proof: "
            f"{', '.join(unproven)}; add an outcome test for each changed entry, "
            "link it from the entry and re-freeze"
        )
    for proof in manifest["proofs"]:
        commit = proof.get("parent", base)
        if not _ancestor(repo, commit, head, strict=True) or not _ancestor(
            repo, base, commit
        ):
            raise WorkflowError(
                f"{proof['id']}: proof parent must be an ancestor of head at or after base"
            )
        _command_files(proof, (repo,))
    if manifest["proofs"]:
        _run_head_proofs(repo, head, manifest, result)


def _config_with_green_ci(repo: Path, head: str) -> dict[str, Any]:
    """The clone's repo config; with ci.enabled its required CI context must be green on head.

    ci.enabled false means the repo has no CI requirement at merge.
    """
    config = read_config(repo)
    ci = gate_policy(config)["ci"]
    if ci["enabled"]:
        context = ci["requiredContext"]
        state = _required_context_state(repo, head, context)
        if state != "success":
            raise ProveBlockedError(
                f'required CI context "{context}" is {state} for {head}; '
                "prove() runs only after CI is green on this exact head"
            )
    return config


def _harness_check_findings(
    repo: Path, parent: str, head: str
) -> tuple[list[str], list[str]]:
    """Run the harness's rename, path, structure, overlap and prefix checkers over parent..head.

    Returns the blocking findings and the decision store errors: each checker logs its rule results
    as source=check decisions, and a store failure is reported without changing a verdict.
    """
    found: list[str] = []
    log_errors: list[str] = []
    for checker in (
        "rename-residue",
        "path-check",
        "structure",
        "responsibility-overlap",
        "prefix-folder",
    ):
        ok, output = run_check(
            [
                sys.executable,
                "-I",
                "-S",
                "-c",
                "import sys; sys.path.insert(0, sys.argv.pop(1)); "
                "from guardrails.commands.cli import main; raise SystemExit(main())",
                str(_harness_root() / ENGINE_PATH),
                checker,
                "--repo",
                str(repo),
                "--log-decisions",
                "prove",
                "--base",
                parent,
                "--head",
                head,
            ],
            repo,
            timeout=HARNESS_CHECK_TIMEOUT,
        )
        lines = [line for line in output.splitlines() if line.strip()]
        log_errors.extend(
            f"{checker}: {line}" for line in lines if line.startswith(STORE_ERROR)
        )
        if not ok:
            found.extend(line for line in lines if not line.startswith(STORE_ERROR))
            if not any(not line.startswith(STORE_ERROR) for line in lines):
                found.append(f"{checker} failed without diagnostics")
    return found, log_errors


def _run_head_proofs(
    repo: Path, head: str, manifest: dict[str, Any], result: dict[str, Any]
) -> None:
    """Run the frozen proofs in a detached worktree at head; failures and cleanup errors block."""
    trees: list[Path] = []
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


LABEL_FIELDS = frozenset({"id", "label", "evidence", "rationale"})
LABELLERS = ("scout", "reviewer")


def blind_label(
    batch_files: list[StrPath],
    rubric_path: StrPath,
    labels: list[str],
) -> dict[str, list[dict[str, Any]]]:
    """Two independent read-only sessions; humans confirm every label before writing."""
    if not isinstance(batch_files, (list, tuple)) or 2 * len(batch_files) > MAX_AGENTS:
        raise WorkflowError(f"labellers exceeds cap {MAX_AGENTS}; merge batches")
    if (
        not isinstance(labels, (list, tuple))
        or not labels
        or any(not isinstance(x, str) or not x.strip() for x in labels)
    ):
        raise WorkflowError("labels must be nonblank strings")
    batches = [_label_batch(filename) for filename in batch_files]
    failed: list[dict[str, Any]] = []
    jobs = _start_labellers(batch_files, rubric_path, labels, LABELLERS, failed)
    try:
        results = _kernel("wait")([handle for _, _, handle in jobs], raise_errors=False)
    except INFRASTRUCTURE_ERRORS as exc:
        results = [exc] * len(jobs)
    votes: dict[tuple[int, str], dict[str, dict[str, Any]]] = {}
    duplicates: set[tuple[int, str]] = set()
    for index, (n, who, _) in enumerate(jobs):
        result = results[index] if index < len(results) else None
        cases = result.get("cases") if isinstance(result, dict) else None
        if not isinstance(cases, list) or any(
            not _valid_label(c, labels, batches[n]) for c in cases
        ):
            failed.append({
                "batch": n,
                "labeller": who,
                "error": f"invalid labeller result: {result!r}",
            })
            continue
        for case in cases:
            key = (n, case["id"])
            if who in votes.setdefault(key, {}):
                duplicates.add(key)
            votes[key][who] = case
    agreed, disputed = _tally(batches, votes, duplicates, LABELLERS)
    return {"agreed": agreed, "disputed": disputed, "failed": failed}


def _label_batch(filename: StrPath) -> list[str]:
    """The case IDs of one batch file: nonblank, unique strings."""
    try:
        batch = json.loads(Path(filename).read_text())
    except (OSError, ValueError) as exc:
        raise WorkflowError(f"invalid batch: {exc}") from exc
    cases = batch.get("cases") if isinstance(batch, dict) else batch
    if not isinstance(cases, list) or any(
        not isinstance(c, dict)
        or not isinstance(c.get("id"), str)
        or not c["id"].strip()
        for c in cases
    ):
        raise WorkflowError("batch must contain cases with nonblank string IDs")
    ids = [c["id"] for c in cases]
    if len(set(ids)) != len(ids):
        raise WorkflowError("duplicate case ID")
    return ids


def _start_labellers(
    batch_files: list[StrPath],
    rubric_path: StrPath,
    labels: list[str],
    labellers: tuple[str, str],
    failed: list[dict[str, Any]],
) -> list[tuple[int, str, Any]]:
    """Spawn one read-only labeller per batch and selector; a spawn failure joins `failed`."""
    schema = {
        "type": "object",
        "required": ["cases"],
        "additionalProperties": False,
        "properties": {
            "cases": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["id", "label", "evidence", "rationale"],
                    "additionalProperties": False,
                    "properties": {
                        "id": {"type": "string"},
                        "label": {"enum": list(labels)},
                        "evidence": {"type": "string"},
                        "rationale": {"type": "string"},
                    },
                },
            }
        },
    }
    jobs = []
    for n, filename in enumerate(batch_files):
        for who in labellers:
            try:
                handle = _kernel("agent")(
                    f"Read-only. Label every case in {filename} using rubric {rubric_path}. Read only those two files. "
                    "Quote deciding evidence; rationale in 1-2 plain sentences. Human confirms every label before writing.",
                    agent=who,
                    label=f"Label{n:02d}{who[:3]}",
                    schema=schema,
                )
                jobs.append((n, who, handle))
            except INFRASTRUCTURE_ERRORS as exc:
                failed.append({"batch": n, "labeller": who, "error": repr(exc)})
    return jobs


def _valid_label(case: object, labels: list[str], ids: list[str]) -> bool:
    return (
        isinstance(case, dict)
        and set(case) == LABEL_FIELDS
        and all(
            isinstance(case.get(k), str)
            for k in ("id", "label", "evidence", "rationale")
        )
        and case["label"] in labels
        and case["id"] in ids
    )


def _tally(
    batches: list[list[str]],
    votes: dict[tuple[int, str], dict[str, dict[str, Any]]],
    duplicates: set[tuple[int, str]],
    labellers: tuple[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Cases both labellers gave the same single label, and every other case with its votes."""
    agreed, disputed = [], []
    for n, ids in enumerate(batches):
        for ident in ids:
            key = (n, ident)
            value = votes.get(key, {})
            if (
                key not in duplicates
                and len(value) == 2
                and value[labellers[0]]["label"] == value[labellers[1]]["label"]
            ):
                agreed.append({
                    "batch": n,
                    "id": ident,
                    "label": value[labellers[0]]["label"],
                })
            else:
                disputed.append({"batch": n, "id": ident, "votes": value})
    return agreed, disputed


# Dependency advisories: the commit gate's SECURITY.dependency-audit findings, assessed by the
# calibrated severity judgment and routed to the owner or to an in-flow fix. The line format is the
# catalogue row's contract (guardrails/data/guardrail-rules.json); repos print exactly it.
DEPENDENCY_AUDIT = "SECURITY.dependency-audit"
ADVISORY_FIELDS = (
    "lock",
    "ecosystem",
    "package",
    "version",
    "advisory",
    "aliases",
    "fix",
)
ADVISORY_LISTS = ("aliases", "fix")
AUDIT_FAILURE_KINDS = (
    "unknown-lock-format",
    "manifest-without-lock",
    "exception",
    "auditor",
)
_ADVISORY_LINE = re.compile(
    re.escape(DEPENDENCY_AUDIT)
    + "".join(
        rf" {field}=(?P<{field}>\S*)"
        if field in ADVISORY_LISTS
        else rf" {field}=(?P<{field}>\S+)"
        for field in ADVISORY_FIELDS
    )
)
_FAILURE_LINE = re.compile(
    re.escape(DEPENDENCY_AUDIT)
    + r" failure=(?P<kind>\S+) path=(?P<path>\S+) reason=(?P<reason>\S.*)"
)
JUDGMENTS = "agent/extensions/agent-rails/judgments"
JUDGMENT_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
# A bool decision is a single cutoff, or a band whose escalations go to a named fallback judgment.
CUTOFF_KEYS = frozenset({"threshold"})
BAND_KEYS = frozenset({"no_at_most", "yes_at_least", "fallback"})
DECISION_KEYS = (
    "option",
    "threshold",
    "no_at_most",
    "yes_at_least",
    "fallback",
    "round_up_floor",
)
# Each decision's place in the jev-actions vocabulary (skill://jev-actions/references/design-patterns.md),
# so the decision store can compare judgments by kind.
TAXONOMY_ENUMS: dict[str, tuple[str, ...]] = {
    "approach": ("Gate", "Route", "Rerank", "Select", "Grade", "Verify", "Eval judge"),
    "evidence_unit": ("item", "pair", "placement"),
    "combination": ("jev_only", "proposer_product", "chain_all", "any", "max_per_unit"),
    "action": ("block", "confirm", "review_queue", "log", "route"),
    "mode": ("enforced", "shadow"),
}
OVERLAYS = ("Face-value", "Changing state", "Chain", "Place")
PRIMITIVES = {"bool": "noul", "choice": "choice", "score": "score"}
POLICY_AREA = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
TAXONOMY_FIELDS = (
    "approach",
    "primitive",
    "evidence_unit",
    "combination",
    "action",
    "policy_area",
    "mode",
)


def _judgment_digest(value: object) -> str:
    """sha256 of compact, sorted-key JSON: the registry's version keys and state digests."""
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


@dataclass(frozen=True)
class Judgment:
    """One judgments/<name>/ folder, validated: questions, state shape, pinned model, decisions."""

    name: str
    questions: dict[str, Any]
    state: dict[str, Any]
    model: str
    version_key: str
    decisions: dict[str, Any]
    thresholds: dict[str, Any]


def _fraction(value: object) -> TypeGuard[int | float]:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and 0 <= value <= 1
    )


def _check_fallback(name: str, ident: str, fallback: object) -> None:
    """A band's fallback names another registered judgment, which answers what the band escalates."""
    if (
        not isinstance(fallback, str)
        or not JUDGMENT_NAME.fullmatch(fallback)
        or fallback == name
        or not (_harness_root() / JUDGMENTS / fallback / "thresholds.json").is_file()
    ):
        raise WorkflowError(
            f"{name}: decision {ident} is a band without a fallback judge; "
            "use a single threshold, or name a registered fallback judgment"
        )


def _check_bool_decision(name: str, ident: str, decision: dict[str, Any]) -> None:
    """A single cutoff {threshold}, or a band {no_at_most < yes_at_least, fallback}; never both."""
    keys = set(decision)
    if keys & CUTOFF_KEYS and keys & BAND_KEYS:
        raise WorkflowError(
            f"{name}: decision {ident} holds both a threshold and a band"
        )
    if "threshold" in decision:
        if not _fraction(decision["threshold"]):
            raise WorkflowError(f"{name}: decision {ident} needs a threshold in [0, 1]")
        return
    low, high = decision.get("no_at_most"), decision.get("yes_at_least")
    if not keys & BAND_KEYS:
        raise WorkflowError(f"{name}: decision {ident} needs a threshold in [0, 1]")
    if not (_fraction(low) and _fraction(high) and low < high):
        raise WorkflowError(
            f"{name}: decision {ident} needs 0 <= no_at_most < yes_at_least <= 1"
        )
    _check_fallback(name, ident, decision.get("fallback"))


def _check_decision(name: str, ident: str, question: Any, decision: Any) -> None:
    """A decision fits its question type and carries its taxonomy and mode."""
    if not isinstance(decision, dict):
        raise WorkflowError(f"{name}: decision {ident} must be an object")
    kind = question.get("type") if isinstance(question, dict) else None
    if kind == "bool":
        _check_bool_decision(name, ident, decision)
    elif kind == "choice":
        if "threshold" in decision:
            if not _fraction(decision["threshold"]) or decision.get("option") not in (
                question.get("criteria") or {}
            ):
                raise WorkflowError(
                    f"{name}: decision {ident} needs an option of the question "
                    "and a threshold in [0, 1]"
                )
        elif not _fraction(decision.get("round_up_floor")):
            raise WorkflowError(
                f"{name}: decision {ident} needs a round_up_floor in [0, 1]"
            )
    else:
        raise WorkflowError(
            f"{name}: question {ident} must be a bool or choice question"
        )
    _check_taxonomy(name, ident, kind, decision)
    _check_mode(name, ident, kind, decision)


def _check_taxonomy(name: str, ident: str, kind: str, decision: dict[str, Any]) -> None:
    """Every taxonomy field is present and inside the jev-actions vocabulary."""
    for field, allowed in TAXONOMY_ENUMS.items():
        if decision.get(field) not in allowed:
            raise WorkflowError(
                f"{name}: decision {ident} needs {field} in {list(allowed)}"
            )
    overlays = decision.get("overlays")
    if (
        not isinstance(overlays, list)
        or any(overlay not in OVERLAYS for overlay in overlays)
        or len(set(overlays)) != len(overlays)
    ):
        raise WorkflowError(
            f"{name}: decision {ident} needs overlays, a list of distinct {list(OVERLAYS)}"
        )
    if decision.get("primitive") != PRIMITIVES[kind]:
        raise WorkflowError(
            f"{name}: decision {ident} answers a {kind} question, so its primitive is {PRIMITIVES[kind]}"
        )
    area = decision.get("policy_area")
    if not isinstance(area, str) or not POLICY_AREA.fullmatch(area):
        raise WorkflowError(f"{name}: decision {ident} needs a kebab-case policy_area")


def _check_mode(name: str, ident: str, kind: str, decision: dict[str, Any]) -> None:
    """An enforced decision has no shadow_exit; a shadow one is a bool with {labelled_flags: n}."""
    if decision["mode"] == "enforced":
        if "shadow_exit" in decision:
            raise WorkflowError(
                f"{name}: decision {ident} is enforced; remove its shadow_exit"
            )
        return
    if not _store().shadow_capable(kind, decision):
        raise WorkflowError(
            f"{name}: decision {ident} cannot run in shadow mode: only a bool answer or a "
            "choice option threshold has a non-blocking outcome"
        )
    shadow_exit = decision.get("shadow_exit")
    flags = shadow_exit.get("labelled_flags") if isinstance(shadow_exit, dict) else None
    if (
        not isinstance(shadow_exit, dict)
        or set(shadow_exit) != {"labelled_flags"}
        or not isinstance(flags, int)
        or isinstance(flags, bool)
        or flags < 1
    ):
        raise WorkflowError(
            f'{name}: decision {ident} runs in shadow mode and needs shadow_exit {{"labelled_flags": n}}, n >= 1'
        )


def load_judgment(name: str) -> Judgment:
    """judgments/<name>/ with its thresholds; stale version keys raise (re-score, then re-tune)."""
    if not isinstance(name, str) or not JUDGMENT_NAME.fullmatch(name):
        raise WorkflowError(f"invalid judgment name: {name!r}")
    directory = _harness_root() / JUDGMENTS / name
    try:
        document = json.loads((directory / "questions.json").read_text())
        thresholds = json.loads((directory / "thresholds.json").read_text())
        questions, state = document["questions"], document["state"]
        model, decisions = thresholds["model"], thresholds["decisions"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise WorkflowError(f"cannot read judgment {name}: {exc}") from exc
    if (
        not isinstance(questions, dict)
        or not questions
        or not isinstance(decisions, dict)
    ):
        raise WorkflowError(
            f"{name}: questions and decisions must be non-empty objects"
        )
    if (
        not isinstance(model, str)
        or not model
        or thresholds.get("score_space") != "raw"
    ):
        raise WorkflowError(
            f"{name}: thresholds need a pinned model and raw score space"
        )
    if thresholds.get("version_key") != _judgment_digest({
        "questions": questions,
        "state": state,
        "model": model,
    }):
        raise WorkflowError(
            f"{name}: thresholds are stale for the current questions or model; re-score the cases and re-tune"
        )
    if set(decisions) != set(questions):
        raise WorkflowError(f"{name}: thresholds must hold one decision per question")
    for ident, question in questions.items():
        decision = decisions[ident]
        _check_decision(name, ident, question, decision)
        expected = _judgment_digest({
            "question": question,
            "state": state,
            "model": model,
        })
        if decision.get("version_key") != expected:
            raise WorkflowError(
                f"{name}: decision {ident} is stale; re-score and re-tune it"
            )
    judgment = Judgment(
        name, questions, state, model, thresholds["version_key"], decisions, thresholds
    )
    _register_questions(judgment)
    return judgment


def _question_rows(judgment: Judgment) -> list[dict[str, Any]]:
    """One decision store questions row per question: taxonomy, mode and the decision in force."""
    loaded = datetime.now(UTC).isoformat(timespec="seconds")
    shared = judgment.thresholds.get("minimums")
    rows = []
    for ident, decision in judgment.decisions.items():
        minimums = decision.get("minimums", shared)
        override = decision.get("override", judgment.thresholds.get("override"))
        rows.append({
            "judgment": judgment.name,
            "question": ident,
            "question_version": decision["version_key"],
            **{field: decision[field] for field in TAXONOMY_FIELDS},
            "overlays": json.dumps(decision["overlays"]),
            "decision": json.dumps(
                {key: decision[key] for key in DECISION_KEYS if key in decision},
                sort_keys=True,
            ),
            "precision_target": minimums.get("precision")
            if isinstance(minimums, dict)
            else None,
            "override": None
            if override is None
            else json.dumps(override, sort_keys=True),
            "shadow_exit": json.dumps(decision["shadow_exit"])
            if "shadow_exit" in decision
            else None,
            "model": judgment.model,
            "loaded_at": loaded,
        })
    return rows


def _register_questions(judgment: Judgment) -> None:
    store = _store()
    try:
        store.record_questions(_question_rows(judgment))
    except store.StoreError as exc:
        raise WorkflowError(f"{judgment.name}: {exc}") from exc


def effective_judgment(
    judgment: Judgment, config: Mapping[str, Any] | None
) -> Judgment:
    """`judgment` as the repo config runs it: each question at the lower of its global mode and
    the config's `judgments["<judgment>/<question>"]` override (off < shadow < enforced).

    An off question leaves both questions and decisions, so it is never asked and writes no
    decisions row. None (no config applies) returns the judgment unchanged. The questions table
    keeps the global decisions: load_judgment registered them, and this writes nothing.
    """
    if config is None:
        return judgment
    overrides = config["judgments"]
    decisions: dict[str, Any] = {}
    for ident, decision in judgment.decisions.items():
        setting = overrides.get(f"{judgment.name}/{ident}")
        mode = (
            decision["mode"]
            if setting is None
            else setting["mode"]
            if setting["enabled"]
            else "off"
        )
        if MODE_RANK[mode] > MODE_RANK[decision["mode"]]:
            # The config validator refuses this; a config that reached here was not validated.
            raise WorkflowError(
                f"{judgment.name}/{ident}: the repo config sets {mode} above the global "
                f"{decision['mode']} mode; only measurement promotes a question"
            )
        if mode != "off":
            decisions[ident] = (
                decision if mode == decision["mode"] else {**decision, "mode": mode}
            )
    return dataclasses.replace(
        judgment,
        questions={
            ident: question
            for ident, question in judgment.questions.items()
            if ident in decisions
        },
        decisions=decisions,
    )


def outcome(decision: dict[str, Any], probability: float) -> str:
    """A cutoff: yes at or above threshold, else no. A band: yes at or above yes_at_least,
    no at or below no_at_most, escalate (to its fallback judge) in between."""
    if "threshold" in decision:
        return "yes" if probability >= decision["threshold"] else "no"
    if probability >= decision["yes_at_least"]:
        return "yes"
    if probability <= decision["no_at_most"]:
        return "no"
    return "escalate"


def _configured_judge_model() -> str:
    """The judge role omp resolves for this directory, from `omp config get modelRoles --json`."""
    try:
        completed = subprocess.run(
            ["omp", "config", "get", "modelRoles", "--json"],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        model = json.loads(completed.stdout)["value"]["judge"]
    except (
        OSError,
        subprocess.SubprocessError,
        ValueError,
        KeyError,
        TypeError,
    ) as exc:
        raise WorkflowError(f"cannot read the configured judge model: {exc}") from exc
    if completed.returncode != 0 or not isinstance(model, str) or not model:
        raise WorkflowError("omp config get modelRoles names no judge model")
    return model


def require_direct_judge_route() -> None:
    """Gate judgments run on TypeSafe directly: refuse while TYPESAFE_BASE_URL reroutes judge calls.

    The opt-in Jev Inspector route (inspector.env) sets it; it serves interactive inspection only.
    """
    if os.environ.get("TYPESAFE_BASE_URL", "").strip():
        raise WorkflowError(
            "gate judgments run on the direct TypeSafe endpoint; start omp without "
            "TYPESAFE_BASE_URL (the Jev Inspector route is for interactive inspection only)"
        )


def require_judge_model(judgment: Judgment) -> None:
    """Refuse a judgment whose thresholds were tuned on another model than the judge role."""
    configured = _configured_judge_model()
    if configured != judgment.model:
        raise WorkflowError(
            f"{judgment.name}: thresholds are tuned on {judgment.model} but the judge role is "
            f"{configured}; re-score the cases and re-tune"
        )


def _answers(
    judgment: Judgment, answers: object, idents: list[str]
) -> tuple[dict[str, Any], dict[str, str]]:
    """Each asked question's probability (a choice: its class probabilities), and each invalid answer."""
    parsed: dict[str, Any] = {}
    failures: dict[str, str] = {}
    for ident in idents:
        question = judgment.questions[ident]
        try:
            parsed[ident] = (
                _probability(answers, ident, "bool")
                if question["type"] == "bool"
                else _probability(answers, ident, "choice", question["criteria"])
            )
        except WorkflowError as exc:
            failures[ident] = str(exc)
    return parsed, failures


@dataclass(frozen=True)
class JudgmentCall:
    """One judge call through the registry: the decision store rows share its id and timestamp."""

    judgment: Judgment
    state: object
    context: Any
    call_id: str
    ts: str

    @classmethod
    def start(cls, judgment: Judgment, state: object, context: Any) -> "JudgmentCall":
        store = _store()
        return cls(judgment, state, context, store.new_id(), store.now())

    def row(
        self, ident: str, answer: Any, failure: tuple[str, str] | None
    ) -> dict[str, Any]:
        """The decisions row for one asked question: its answer, or (status, error) when it has none."""
        store = _store()
        decision = self.judgment.decisions[ident]
        row: dict[str, Any] = {
            "id": store.new_id(),
            "call_id": self.call_id,
            "ts": self.ts,
            "source": "jev",
            "judgment": self.judgment.name,
            "question": ident,
            "mode": decision["mode"],
            "model": self.judgment.model,
            "question_version": decision["version_key"],
            "judgment_version": self.judgment.version_key,
            "machine": store.machine_name(),
            "state_sha256": _judgment_digest(self.state),
            "threshold": decision.get("threshold", decision.get("yes_at_least")),
            **self.context.columns(),
        }
        if failure is not None:
            row |= {"status": failure[0], "error": failure[1]}
            if isinstance(answer, dict):
                row["choice_probabilities"] = json.dumps(answer, sort_keys=True)
            elif answer is not None:
                row["probability"] = answer
        elif isinstance(answer, dict):
            row |= {
                "status": "ok",
                "choice_probabilities": json.dumps(answer, sort_keys=True),
                "threshold": decision["round_up_floor"],
            }
        else:
            row |= {
                "status": "ok",
                "probability": answer,
                "outcome": store.outcome_name(outcome(decision, answer)),
            }
        row["state_kept"] = int(store.keeps_payload(row))
        return row

    def log(
        self, rows: list[dict[str, Any]], idents: list[str], response: object
    ) -> None:
        """Write the rows, with the exact state, questions and response as the call's payload."""
        store = _store()
        payload = {
            "state": self.state,
            "questions": {ident: self.judgment.questions[ident] for ident in idents},
            "response": response,
        }
        try:
            store.record_decisions(rows, payload)
        except store.StoreError as exc:
            raise WorkflowError(f"{self.judgment.name}: {exc}") from exc


def judgment_record(
    judgment: Judgment,
    state: object,
    answers: object,
    context: Any,
    asked: Collection[str] | None = None,
) -> dict[str, Any]:
    """Probabilities and outcomes for one state's answers (to `asked`, default all), each written
    to the decision store as one decisions row. An invalid answer is logged, then raises."""
    idents = list(judgment.questions if asked is None else asked)
    call = JudgmentCall.start(judgment, state, context)
    parsed, failures = _answers(judgment, answers, idents)
    incomplete = f"the call decided nothing: no valid {', '.join(failures)} answer"
    call.log(
        [
            call.row(
                ident,
                parsed.get(ident),
                None
                if not failures
                else ("error", failures[ident])
                if ident in failures
                else ("incomplete", incomplete),
            )
            for ident in idents
        ],
        idents,
        answers,
    )
    if failures:
        raise WorkflowError(next(iter(failures.values())))
    return {
        "call_id": call.call_id,
        "judgment": judgment.name,
        "state_sha256": _judgment_digest(state),
        "model": judgment.model,
        "question_version": judgment.version_key,
        "probabilities": parsed,
        "outcomes": {
            ident: outcome(judgment.decisions[ident], probability)
            for ident, probability in parsed.items()
            if judgment.questions[ident]["type"] == "bool"
        },
        "modes": {ident: judgment.decisions[ident]["mode"] for ident in idents},
        "timestamp": call.ts,
    }


def judgment_error(
    judgment: Judgment,
    state: object,
    error: str,
    context: Any,
    asked: Collection[str] | None = None,
) -> str:
    """Log a judge call that returned no answers: one error row per asked question. Returns the call id."""
    idents = list(judgment.questions if asked is None else asked)
    call = JudgmentCall.start(judgment, state, context)
    call.log(
        [call.row(ident, None, ("error", error)) for ident in idents],
        idents,
        {"error": error},
    )
    return call.call_id


def decision_context(value: object) -> Any:
    """A rail's {trigger, repo, commit, pr, item_kind, item} as a decision store context."""
    store = _store()
    if not isinstance(value, dict) or "trigger" not in value:
        raise WorkflowError("decision context needs at least a trigger")
    try:
        return store.DecisionContext(**value)
    except (TypeError, store.StoreError) as exc:
        raise WorkflowError(f"invalid decision context: {exc}") from exc


async def decide(name: str, state: dict[str, Any], context: Any) -> dict[str, Any]:
    """Judge `state` with judgments/<name>/: yes | no | escalate per bool question, logged.

    `context` is a decision store DecisionContext: the event that asked and the subject judged.
    The modes are the effective ones for the repo config at the kernel's cwd (effective_judgment):
    an off question is not asked, and a judgment whose every question is off returns
    `{"judgment": name, "off": True}` with no judge call and no row. Refuses stale thresholds and
    a judge role other than the tuned model. A failed or invalid judgment is logged, then raises;
    it is never read as an outcome. Returns the logged record plus `answers`.
    """
    judgment = effective_judgment(load_judgment(name), judgment_config(os.getcwd()))
    if not judgment.questions:
        return {"judgment": name, "off": True}
    return await _decide(judgment, state, context)


async def _decide(
    judgment: Judgment, state: dict[str, Any], context: Any
) -> dict[str, Any]:
    """decide() for an effective judgment with at least one question asked."""
    name = judgment.name
    require_judge_model(judgment)
    if not isinstance(state, dict) or not state:
        raise WorkflowError(f"{name}: state must be a non-empty object")
    try:
        answers = await _kernel("judge")(state, judgment.questions)
    except Exception as exc:
        judgment_error(judgment, state, f"judgment failed: {exc}", context)
        raise WorkflowError(f"{name}: judgment failed: {exc}") from exc
    return {**judgment_record(judgment, state, answers, context), "answers": answers}


def judgment_questions(name: str, config: Mapping[str, Any] | None) -> dict[str, Any]:
    """What a rail sends to its own judge call: the pinned model and the questions asked under the
    repo `config` (effective_judgment); `{"judgment": name, "off": True}` when every one is off.

    Rails judge from the extension host, which reports the model that answered, so the model check
    happens in record_answers() rather than through `omp config get`.
    """
    judgment = effective_judgment(load_judgment(name), config)
    if not judgment.questions:
        return {"judgment": name, "off": True}
    require_direct_judge_route()
    return {"judgment": name, "model": judgment.model, "questions": judgment.questions}


def _asked_judgment(name: str, config: Mapping[str, Any] | None) -> Judgment:
    """The effective judgment a rail records against; refused when the config turns it all off."""
    judgment = effective_judgment(load_judgment(name), config)
    if not judgment.questions:
        raise WorkflowError(
            f"{name}: every question is off in this repo's config; nothing was asked"
        )
    return judgment


def _rail_state(name: str, state: object) -> dict[str, Any]:
    if not isinstance(state, dict) or not state:
        raise WorkflowError(f"{name}: state must be a non-empty object")
    return state


def record_answers(
    name: str, request: object, config: Mapping[str, Any] | None
) -> dict[str, Any]:
    """decide()'s outcomes and decision store rows for answers a rail's judge call returned,
    under the repo `config`'s effective modes.

    Refuses answers from any model other than the tuned one; a refusal is never an outcome.
    """
    judgment = _asked_judgment(name, config)
    require_direct_judge_route()
    if not isinstance(request, dict) or set(request) != {
        "state",
        "answers",
        "model",
        "context",
    }:
        raise WorkflowError(
            f"{name}: rail answers need exactly state, answers, model and context"
        )
    model = request["model"]
    if model != judgment.model:
        raise WorkflowError(
            f"{name}: thresholds are tuned on {judgment.model} but {model!r} answered; "
            "re-score the cases and re-tune"
        )
    state = _rail_state(name, request["state"])
    context = decision_context(request["context"])
    return judgment_record(judgment, state, request["answers"], context)


def record_error(
    name: str, request: object, config: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Log a rail judge call that failed before it answered, under the repo `config`'s
    effective modes; returns the call id."""
    judgment = _asked_judgment(name, config)
    if not isinstance(request, dict) or set(request) != {"state", "error", "context"}:
        raise WorkflowError(
            f"{name}: a rail judge failure needs exactly state, error and context"
        )
    error = request["error"]
    if not isinstance(error, str) or not error.strip():
        raise WorkflowError(f"{name}: a rail judge failure needs its error text")
    state = _rail_state(name, request["state"])
    context = decision_context(request["context"])
    return {"call_id": judgment_error(judgment, state, error, context)}


def _rail_request(path: str) -> object:
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise WorkflowError(f"cannot read rail answers {path}: {exc}") from exc


def rail_bridge(argv: list[str], cwd: StrPath) -> int:
    """`questions <name>`, `record <name> <request.json>` or `error <name> <request.json>`:
    one JSON object on stdout. A refusal prints {"error": ...} and exits 1.

    `cwd` is the rail's working directory (the session's): its repo config sets the effective
    judgment modes (judgment_config, effective_judgment).
    """
    try:
        match argv:
            case ["questions", name]:
                result = judgment_questions(name, judgment_config(cwd))
            case ["record", name, request]:
                result = record_answers(
                    name, _rail_request(request), judgment_config(cwd)
                )
            case ["error", name, request]:
                result = record_error(
                    name, _rail_request(request), judgment_config(cwd)
                )
            case _:
                raise WorkflowError(
                    "usage: questions <judgment> | record <judgment> <request.json> "
                    "| error <judgment> <request.json>"
                )
    except WorkflowError as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


SEVERITY_JUDGMENT = "dependency-severity"
SEVERITIES = ("low", "medium", "high", "critical")
OWNER_SEVERITIES = ("high", "critical")
# The fixed rubric: an impact class sets the base level; each mitigating dimension that the
# judgment clears lowers it one level, never below low. Unclear evidence never lowers it.
IMPACT_LEVEL = {
    "rce": 4,
    "auth_bypass": 4,
    "unclear": 4,
    "data_exposure": 3,
    "integrity_loss": 3,
    "dos": 2,
    "info_leak": 2,
}
PRECONDITIONS = ("local_only", "needs_privileges", "needs_interaction")
MALICIOUS_PREFIX = "MAL-"
EVIDENCE_FIELDS = frozenset({
    "advisory_text",
    "advisory_source",
    "scope",
    "scope_source",
    "code_search",
    "fix_options",
    "blocked_fix",
})
BLOCKED_FIX_FIELDS = frozenset({"blocked_by", "constraint", "constraint_source"})


def advisory_key(advisory: dict[str, Any]) -> str:
    """One advisory finding's identity: lock, package at version, advisory ID."""
    return f"{advisory['lock']}:{advisory['package']}@{advisory['version']}:{advisory['advisory']}"


def _listed(value: str, field: str, line: str) -> list[str]:
    items = value.split(",") if value else []
    if any(not item for item in items):
        raise WorkflowError(
            f"{DEPENDENCY_AUDIT} line has an empty {field} item: {line}"
        )
    return items


def parse_dependency_audit(output: object) -> dict[str, list[dict[str, Any]]]:
    """Every SECURITY.dependency-audit finding in commit-gate output: ``{advisories, failures}``.

    A line that starts with the rule ID must match the catalogue contract exactly; any other such
    line, or output the rails truncated, raises: a finding is never skipped.
    """
    if not isinstance(output, str):
        raise WorkflowError("commit-gate output must be text")
    if output.startswith("…"):
        raise WorkflowError(
            "commit-gate output is truncated; parse the full output of the config's commands.commit"
        )
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
        raise WorkflowError(
            f"{DEPENDENCY_AUDIT} line does not match the catalogue contract: {line}"
        )
    return {"advisories": advisories, "failures": failures}


def dependency_audit_findings(repo: StrPath) -> dict[str, Any]:
    """Run the config's commands.commit in `repo` and parse its SECURITY.dependency-audit lines.

    The triage runs whatever gates.audit says: gates.audit only decides whether ci_gate runs
    commands.audit, and audit lines in commands.commit output always block (every commit).
    Returns `{ok, repo, advisories, failures}`; triage_advisories() reads the severity
    judgment's effective modes from `repo`'s config.
    """
    config = read_config(repo)
    ok, output = run_check(config["commands"]["commit"], repo)
    found = parse_dependency_audit(output)
    if ok and (found["advisories"] or found["failures"]):
        raise WorkflowError(
            f"commands.commit passed but printed {DEPENDENCY_AUDIT} findings; every finding must block"
        )
    return {"ok": ok, "repo": os.fspath(repo), **found}


def _evidence(advisory: dict[str, Any], evidence: object) -> dict[str, Any]:
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
    if (
        not isinstance(search, dict)
        or set(search) != {"queries", "hits"}
        or not isinstance(search["queries"], list)
        or not search["queries"]
        or any(not isinstance(q, str) or not q.strip() for q in search["queries"])
        or not isinstance(search["hits"], list)
        or any(
            not isinstance(h, dict)
            or set(h) != {"location", "line"}
            or not all(isinstance(h[k], str) and h[k].strip() for k in h)
            for h in search["hits"]
        )
    ):
        raise WorkflowError(
            f"{key}: evidence.code_search needs nonblank queries and hits of {{location, line}}"
        )
    if not isinstance(evidence["fix_options"], str) or (
        not advisory["fix"] and not evidence["fix_options"].strip()
    ):
        raise WorkflowError(
            f"{key}: evidence.fix_options must name a replacement or mitigation when no fixed version exists"
        )
    blocked = evidence["blocked_fix"]
    if blocked is not None:
        if (
            not advisory["fix"]
            or not isinstance(blocked, dict)
            or set(blocked) != BLOCKED_FIX_FIELDS
            or any(not isinstance(v, str) or not v.strip() for v in blocked.values())
        ):
            raise WorkflowError(
                f"{key}: evidence.blocked_fix needs exactly {sorted(BLOCKED_FIX_FIELDS)} for a fixed release, or null"
            )
        package, separator, version = blocked["blocked_by"].rpartition("@")
        source = urlsplit(blocked["constraint_source"])
        if (
            not separator
            or not package
            or not version
            or any(c.isspace() for c in blocked["blocked_by"])
            or source.scheme != "https"
            or not source.netloc
        ):
            raise WorkflowError(
                f"{key}: blocked_fix needs package@exact-version and an absolute HTTPS constraint_source"
            )
    return evidence


def _probability(
    answers: object, question: str, kind: str, labels: Collection[str] = IMPACT_LEVEL
) -> Any:
    answer = answers.get(question) if isinstance(answers, dict) else None
    if kind == "choice":
        probabilities = (
            answer.get("probabilities") if isinstance(answer, dict) else None
        )
        if (
            not isinstance(probabilities, dict)
            or set(probabilities) != set(labels)
            or not all(
                isinstance(p, (int, float)) and 0 <= p <= 1
                for p in probabilities.values()
            )
        ):
            raise WorkflowError(f"judgment returned no valid {question} answer")
        return probabilities
    value = answer.get("bool") if isinstance(answer, dict) else None
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not 0 <= value <= 1
    ):
        raise WorkflowError(f"judgment returned no valid {question} answer")
    return value


def rate_advisory(
    advisory: dict[str, Any],
    evidence: object,
    answers: object,
    decisions: dict[str, Any],
) -> dict[str, Any]:
    """Apply the fixed rubric to the judgment's answers: severity, route and the cited evidence.

    Rounds up: the impact is the most severe class at or above the round-up floor, a
    mitigating dimension lowers the level only on a yes outcome, and exposure only on a no
    outcome for `reached`; escalate never lowers it. assess_advisory decides a malicious package
    before the rubric, so it never reaches it.
    `decisions` are the effective ones: a question the repo config turns off is absent, was not
    asked, and never lowers the level.
    """
    evidence = _evidence(advisory, evidence)
    probabilities = _probability(answers, "impact", "choice")
    floor = decisions["impact"]["round_up_floor"]
    impact = max(
        (c for c, p in probabilities.items() if p >= floor),
        key=IMPACT_LEVEL.__getitem__,
        default=max(probabilities, key=probabilities.get),
    )
    preconditions = {
        q: _probability(answers, q, "bool") for q in PRECONDITIONS if q in decisions
    }
    reached = (
        _probability(answers, "reached", "bool") if "reached" in decisions else None
    )
    # A shadow decision is logged but never acts, and an off one is never asked: neither lowers
    # the severity level.
    cleared = [
        q
        for q, probability in preconditions.items()
        if decisions[q]["mode"] == "enforced"
        and outcome(decisions[q], probability) == "yes"
    ]
    unreached = (
        reached is not None
        and decisions["reached"]["mode"] == "enforced"
        and outcome(decisions["reached"], reached) == "no"
    )
    level = (
        IMPACT_LEVEL[impact] - bool(cleared) - (evidence["scope"] == "dev") - unreached
    )
    severity = SEVERITIES[max(1, level) - 1]
    reasons = []
    if severity in OWNER_SEVERITIES:
        reasons.append(f"severity {severity}")
    if not advisory["fix"]:
        reasons.append(
            "no fixed release: blocked until the owner approves a dated exception"
        )
    if evidence["blocked_fix"] is not None:
        reasons.append(
            "fixed releases blocked by a pinned dependent: blocked until the owner approves a dated exception"
        )
    upgrade = f"upgrade {advisory['package']} from {advisory['version']} to a fixed version ({', '.join(advisory['fix'])})"
    if advisory["fix"]:
        suggested = upgrade + (
            f"; also: {evidence['fix_options']}"
            if evidence["fix_options"].strip()
            else ""
        )
    else:
        suggested = evidence["fix_options"]
    if evidence["blocked_fix"] is not None:
        blocked = evidence["blocked_fix"]
        suggested = (
            f"owner: approve a dated exception with blocked_by={blocked['blocked_by']}, "
            f"constraint={blocked['constraint']}, constraint_source={blocked['constraint_source']}; "
            "upgrade when the dependent's declared constraint permits a fixed release"
        )
    return {
        "key": advisory_key(advisory),
        "advisory": advisory,
        "severity": severity,
        "route": "owner" if reasons else "fix",
        "reasons": reasons,
        "dimensions": {
            "impact": {
                "class": impact,
                "probabilities": probabilities,
                "source": evidence["advisory_source"],
            },
            "preconditions": {
                "probabilities": preconditions,
                "cleared": cleared,
                "source": evidence["advisory_source"],
            },
            "exposure": {
                "scope": evidence["scope"],
                "scope_source": evidence["scope_source"],
                "reached": reached,
                "unreached": unreached,
                "code_search": evidence["code_search"],
            },
            "fix": {
                "versions": advisory["fix"],
                "options": evidence["fix_options"],
                "blocked_fix": evidence["blocked_fix"],
            },
        },
        "suggested_fix": suggested,
    }


def malicious_package(advisory: dict[str, Any]) -> bool:
    """True when the advisory is an OSV malicious-package record: its ID or an alias starts with MAL-.

    The catalogue contract has the checker report an auditor's own malicious mark the same way.
    """
    return any(
        ident.upper().startswith(MALICIOUS_PREFIX)
        for ident in (advisory["advisory"], *advisory["aliases"])
    )


def malicious_assessment(advisory: dict[str, Any], evidence: object) -> dict[str, Any]:
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
            "impact": {
                "class": "malicious_package",
                "probabilities": {},
                "source": evidence["advisory_source"],
            },
            "preconditions": {
                "probabilities": {},
                "cleared": [],
                "source": evidence["advisory_source"],
            },
            "exposure": {
                "scope": evidence["scope"],
                "scope_source": evidence["scope_source"],
                "reached": None,
                "unreached": False,
                "code_search": evidence["code_search"],
            },
            "fix": {
                "versions": advisory["fix"],
                "options": evidence["fix_options"],
                "blocked_fix": evidence["blocked_fix"],
            },
        },
        "suggested_fix": (
            f"remove {advisory['package']} or replace it with a trusted package, and rotate every "
            "credential the environments that installed it exposed (CI and developer tokens, keys)"
            + (
                f"; also: {evidence['fix_options']}"
                if evidence["fix_options"].strip()
                else ""
            )
        ),
    }


async def assess_advisory(
    advisory: dict[str, Any], evidence: object, config: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Severity of one advisory: the frozen questions through judge(), then the fixed rubric in code.

    The questions run at their effective modes under the repo `config` (effective_judgment);
    `impact` is never configurable, so the judge is always asked. A malicious package is decided
    in code before any judgment. A failed or invalid judgment raises; it is never read as a low
    severity.
    """
    if malicious_package(advisory):
        return malicious_assessment(advisory, evidence)
    evidence = _evidence(advisory, evidence)
    require_direct_judge_route()
    state = {
        "advisory": {
            "id": advisory["advisory"],
            "aliases": advisory["aliases"],
            "ecosystem": advisory["ecosystem"],
            "package": advisory["package"],
            "version": advisory["version"],
            "fix": advisory["fix"],
            "text": evidence["advisory_text"],
            "source": evidence["advisory_source"],
        },
        "code_search": evidence["code_search"],
    }
    context = _store().DecisionContext(
        "commit", item_kind="advisory", item=advisory_key(advisory)
    )
    try:
        judgment = effective_judgment(load_judgment(SEVERITY_JUDGMENT), config)
        decided = await _decide(judgment, state, context)
    except WorkflowError as exc:
        raise WorkflowError(
            f"{advisory_key(advisory)}: severity judgment failed: {exc}"
        ) from exc
    return rate_advisory(advisory, evidence, decided["answers"], judgment.decisions)


def owner_report(
    assessments: list[dict[str, Any]], failures: Iterable[dict[str, Any]] = ()
) -> str:
    """The report the workflow halts with: each advisory, severity, evidence and suggested fix."""
    lines = [
        f"Dependency advisories need the owner ({DEPENDENCY_AUDIT}). The workflow stopped; nothing was changed for them."
    ]
    for item in assessments:
        a, d = item["advisory"], item["dimensions"]
        aliases = f" ({', '.join(a['aliases'])})" if a["aliases"] else ""
        hits = (
            ", ".join(h["location"] for h in d["exposure"]["code_search"]["hits"])
            or "no hits"
        )
        pre = (
            ", ".join(
                f"{q} {p:.2f}" for q, p in d["preconditions"]["probabilities"].items()
            )
            or "not judged"
        )
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
            lines.append(
                f"  - Pinned dependent: {blocked['blocked_by']}; declaration: {blocked['constraint']} "
                f"(source: {blocked['constraint_source']})"
            )
    for failure in failures:
        lines.append(
            f"- Exception file failure at {failure['path']}: {failure['reason']} (the owner changes the exception file)"
        )
    return "\n".join(lines)


async def triage_advisories(
    found: dict[str, Any], evidence: dict[str, Any]
) -> dict[str, Any]:
    """Assess every advisory in `found` (from dependency_audit_findings) with its cited evidence.

    The severity questions run at their effective modes under the config of `found["repo"]`.
    Returns ``{halt, owner, fix, failures, report}``. Any high or critical advisory, any advisory
    with no fixed release or a fix blocked by a pinned dependent, and any exception-file failure
    halts the workflow with ``report`` for the owner; ``fix`` lists only unblocked low and medium
    advisories to upgrade in the flow, and ``failures`` the other checker failures to fix.
    """
    repo = found.get("repo")
    if not isinstance(repo, str) or not repo:
        raise WorkflowError(
            "found must be dependency_audit_findings(repo)'s result, which names its repo"
        )
    keys = [advisory_key(a) for a in found["advisories"]]
    missing = sorted(set(keys) - set(evidence))
    extra = sorted(set(evidence) - set(keys))
    if missing or extra:
        raise WorkflowError(
            f"evidence must cover exactly the advisories found; missing {missing}, unknown {extra}"
        )
    config = judgment_config(repo)
    assessed = [
        await assess_advisory(a, evidence[k], config)
        for a, k in zip(found["advisories"], keys, strict=True)
    ]
    owner = [item for item in assessed if item["route"] == "owner"]
    exceptions = [f for f in found["failures"] if f["kind"] == "exception"]
    halt = bool(owner or exceptions)
    return {
        "halt": halt,
        "owner": owner,
        "fix": [item for item in assessed if item["route"] == "fix"],
        "failures": [f for f in found["failures"] if f["kind"] != "exception"],
        "report": owner_report(owner, exceptions) if halt else "",
    }


STRUCTURE_JUDGMENT = "structure-naming"
STRUCTURE_MARKER = "<!-- omp-structure-review -->"


PAIR_QUESTION = "same_thing"
CONSUMER_QUESTION = "consumer_placement"
KIND_QUESTION = "technical_kind_grouping"
STRUCTURE_QUESTIONS = frozenset({KIND_QUESTION, PAIR_QUESTION, CONSUMER_QUESTION})


def _structure_config() -> Judgment:
    """The structure-naming judgment: its proposal questions, consumer_placement a Choice and the rest bool."""
    judgment = load_judgment(STRUCTURE_JUDGMENT)
    if set(judgment.questions) != STRUCTURE_QUESTIONS or any(
        q.get("type") != ("choice" if ident == CONSUMER_QUESTION else "bool")
        for ident, q in judgment.questions.items()
    ):
        raise WorkflowError(
            "structure questions must be exactly these, consumer_placement a choice "
            "and the rest bool: " + ", ".join(sorted(STRUCTURE_QUESTIONS))
        )
    return judgment


def _proposals(repo: Path, subcommand: str, parent: str, head: str) -> Any:
    """JSON a harness proposer subcommand prints; the config's exceptions drop proposals."""
    ok, output = run_check(
        [
            sys.executable,
            "-I",
            "-S",
            "-c",
            "import sys; sys.path.insert(0, sys.argv.pop(1)); "
            "from guardrails.commands.cli import main; raise SystemExit(main())",
            str(_harness_root() / ENGINE_PATH),
            subcommand,
            "--repo",
            str(repo),
            "--base",
            parent,
            "--head",
            head,
        ],
        repo,
        timeout=HARNESS_CHECK_TIMEOUT,
        stdout_only=True,
    )
    if not ok:
        raise WorkflowError(f"{subcommand} failed: {output}")
    return json.loads(output)


def _name_pairs(repo: Path, parent: str, head: str) -> list[dict[str, Any]]:
    """Code-proposed name pairs for the same_thing question."""
    pairs = _proposals(repo, "name-pairs", parent, head).get("pairs")
    if not isinstance(pairs, list) or any(
        not isinstance(pair, dict)
        or not isinstance(pair.get("id"), str)
        or not _fraction(pair.get("proposer_score"))
        or set(pair.get("state") or {}) != {"first", "second"}
        for pair in pairs
    ):
        raise WorkflowError(
            "name-pairs must emit {pairs: [{id, proposer_score, state}]}"
        )
    return pairs


def _consumer_candidates(repo: Path, parent: str, head: str) -> list[dict[str, Any]]:
    """Code-proposed consumer files for the consumer_placement question."""
    found = _proposals(repo, "consumer-candidates", parent, head).get("candidates")
    if not isinstance(found, list) or any(
        not isinstance(item, dict)
        or not isinstance(item.get("path"), str)
        or set(item.get("state") or {}) != {"file", "package"}
        for item in found
    ):
        raise WorkflowError(
            "consumer-candidates must emit {candidates: [{path, state}]}"
        )
    return found


def _kind_candidates(repo: Path, parent: str, head: str) -> list[dict[str, Any]]:
    """Code-proposed files in technical-kind folders for the technical_kind_grouping question."""
    found = _proposals(repo, "kind-candidates", parent, head).get("candidates")
    if not isinstance(found, list) or any(
        not isinstance(item, dict)
        or not isinstance(item.get("path"), str)
        or set(item.get("state") or {}) != {"file", "folder", "importers"}
        for item in found
    ):
        raise WorkflowError("kind-candidates must emit {candidates: [{path, state}]}")
    return found


def _structure_comment(result: dict[str, Any]) -> str:
    lines = [
        STRUCTURE_MARKER,
        f"**Structure/naming review** for `{result['head']}`.",
        "Scores are raw judgment probabilities, not calibrated confidence; a same_thing "
        "score is the pair's proposer_score times its mean probability over both orders. "
        "Every enforced finding blocks merge; shadow findings are logged for review and never block.",
    ]
    for item in result["findings"]:
        shadow = " (shadow, not blocking)" if item["mode"] == "shadow" else ""
        lines.append(
            f"- `{item['item']}` · `{item['question']}`: raw {item['score']:.3f} "
            f"≥ {item['threshold']:.3f}{shadow}"
        )
    if not result["findings"]:
        lines.append(
            "- No findings recorded."
            if not result["errors"]
            else "- Review incomplete."
        )
    lines.extend(f"- Infrastructure: {error}" for error in result["errors"])
    if result["halt"]:
        lines.append(
            "**Blocked:** enforced structure findings; rails refuse merge. "
            "Fix them and rerun prove() and structure_review() on the new head."
        )
    lines.append(
        f"Full result: `<git-common-dir>/omp-structure/{result['head']}.json`."
    )
    body = "\n".join(lines)
    if len(body) > COMMENT_LIMIT:
        body = body[: COMMENT_LIMIT - 200] + "\n[Truncated; read the full result file.]"
    return body


def _post_structure_comment(repo: StrPath, head: str, result: dict[str, Any]) -> str:
    pr = _pr_at_head(repo, head, ("number",))
    endpoint = f"repos/{_github_repo(repo)}/issues/{pr['number']}/comments"
    comments = _github(repo, "api", endpoint, "--paginate")
    previous = [c for c in comments if STRUCTURE_MARKER in (c.get("body") or "")]
    endpoint = (
        f"repos/{_github_repo(repo)}/issues/comments/{previous[-1]['id']}"
        if previous
        else endpoint
    )
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".json"
    ) as stream:
        json.dump({"body": _structure_comment(result)}, stream)
        stream.flush()
        response = _github(
            repo,
            "api",
            endpoint,
            "--method",
            "PATCH" if previous else "POST",
            "--input",
            stream.name,
        )
    url: str = response["html_url"]
    return url


async def structure_review(
    repo: StrPath, parent: str, head: str, *, target_ref: str
) -> dict[str, Any]:
    """PR-time structure judgment, separate from prove().

    Rails require this exact-head result and refuse merge when halt is true or it is incomplete.
    Every enforced finding halts; a shadow finding is reported and logged but never halts. An
    infrastructure error or an escalated decision leaves it incomplete.
    No proof result file is modified.
    """
    result: dict[str, Any] = {
        "head": None,
        "parent": None,
        "status": "incomplete",
        "halt": False,
        "pairs": {},
        "kind_files": {},
        "consumers": {},
        "findings": [],
        "errors": [],
        "thresholds": None,
        "version_key": None,
    }
    published = None
    try:
        repo = Path(repo).resolve()
        head = _commit(repo, head)
        result["head"] = head
        published = _common(repo) / "omp-structure" / f"{head}.json"
        parent, _ = _range(repo, parent, head, target_ref)
        result["parent"] = parent
        judgment = effective_judgment(_structure_config(), judgment_config(repo))
        result["thresholds"] = judgment.thresholds
        result["version_key"] = judgment.version_key
        # eval's judge API exposes answers, not a reported model: the judge role is checked instead.
        result["model_note"] = (
            f"judge role checked against the tuned model {judgment.model}; "
            "response model metadata unavailable"
        )
        require_direct_judge_route()
        require_judge_model(judgment)
        review = StructureJudgment(judgment, result, _github_repo(repo))
        # A question the repo config turns off runs neither its proposer nor the judge.
        if review.asks(KIND_QUESTION):
            await review.judge_kind_files(_kind_candidates(repo, parent, head))
        if review.asks(PAIR_QUESTION):
            await review.judge_pairs(_name_pairs(repo, parent, head))
        if review.asks(CONSUMER_QUESTION):
            await review.judge_consumers(_consumer_candidates(repo, parent, head))
    except INFRASTRUCTURE_ERRORS as exc:
        result["errors"].append(f"infrastructure: {exc}")
    except Exception as exc:
        # A bug still leaves an incomplete result and PR comment explaining it, then propagates.
        result["errors"].append(f"unexpected {type(exc).__name__}: {exc}")
        _finish_structure(repo, head, published, result)
        raise
    _finish_structure(repo, head, published, result)
    return result


def _finish_structure(
    repo: StrPath, head: str, published: Any, result: dict[str, Any]
) -> None:
    """Derive halt (enforced findings only) and status, then publish when the head resolved."""
    result["halt"] = any(item["mode"] == "enforced" for item in result["findings"])
    result["status"] = _structure_status(result)
    if published is not None:
        _publish_structure(Path(repo), head, published, result)


def _structure_status(result: dict[str, Any]) -> str:
    """blocked on any enforced finding, incomplete on any error, else pass."""
    if result["halt"]:
        return "blocked"
    return "incomplete" if result["errors"] else "pass"


class StructureJudgment:
    """Judge code-proposed files and pairs through the eval kernel; record scores and findings."""

    def __init__(self, judgment: Judgment, result: dict[str, Any], repo: str) -> None:
        self.judgment = judgment
        self.questions = judgment.questions
        self.result = result
        self.repo = repo

    def asks(self, ident: str) -> bool:
        """Whether the effective judgment asks `ident`: a question the repo config turns off is
        never asked and logs nothing."""
        return ident in self.judgment.decisions

    async def ask(
        self,
        states: dict[str, Any],
        selected: dict[str, Any],
        intent: str,
        read: Callable[[object], float],
    ) -> dict[str, float | str]:
        """One judge_batch(): each key's read answer, or the error that left it unanswered."""
        found: dict[str, float | str] = {}
        batch = _kernel("judge_batch")(states, selected, intent=intent)
        try:
            async for key, item in batch.drain_iter():
                try:
                    if not item.ok:
                        raise WorkflowError(str(item.error))
                    found[key] = read(item.answers)
                except INFRASTRUCTURE_ERRORS as exc:
                    found[key] = str(exc)
        finally:
            batch.close()
        return found

    def proposal_context(self, kind: str, item: str) -> Any:
        """The decision store context of one code-proposed pair or file at this PR head."""
        return _store().DecisionContext(
            "pr", repo=self.repo, commit=self.result["head"], item_kind=kind, item=item
        )

    def decide_proposal(
        self,
        ident: str,
        proposal: dict[str, Any],
        values: list[float | str | None],
        proposer: float | None = None,
    ) -> None:
        """Combine one proposal's answers (mean, times the proposer's score when it has one),
        log its decisions row and record its outcome and any finding."""
        kind, item_id = proposal["kind"], proposal["id"]
        call = JudgmentCall.start(
            self.judgment, proposal["state"], self.proposal_context(kind, item_id)
        )
        decision = self.judgment.decisions[ident]
        numbers = [
            value
            for value in values
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        ]
        verdict = None
        if len(numbers) < len(values):
            error = next(
                (value for value in values if isinstance(value, str)),
                f"{ident} was not answered in every order",
            )
            self.result["errors"].append(f"{item_id}: judgment: {error}")
            row = call.row(ident, None, ("error", error))
        else:
            mean = sum(numbers) / len(numbers)
            score = round(mean if proposer is None else proposer * mean, 6)
            row = call.row(ident, score, None)
            row |= {"probability": mean, "combined_score": score}
            if proposer is not None:
                row["proposer_score"] = proposer
            verdict = outcome(decision, score)
        try:
            call.log([row], [ident], {"answers": values})
        except WorkflowError as exc:
            self.result["errors"].append(f"{item_id}: {exc}")
        if verdict is not None:
            self.record_proposal(ident, proposal, row, verdict)

    def record_proposal(
        self, ident: str, proposal: dict[str, Any], row: dict[str, Any], verdict: str
    ) -> None:
        decision = self.judgment.decisions[ident]
        item_id, score = proposal["id"], row["combined_score"]
        self.result[proposal["record"]][item_id] = {
            "digest": _judgment_digest(proposal["state"]),
            "probability": row["probability"],
            "score": score,
            "outcome": verdict,
            **(
                {"proposer_score": row["proposer_score"]}
                if "proposer_score" in row
                else {}
            ),
        }
        if verdict == "escalate":
            self.result["errors"].append(
                f"{item_id}: {ident} escalated to {decision['fallback']}, "
                "which structure_review() does not run"
            )
        elif verdict == "yes":
            self.result["findings"].append({
                "item": item_id,
                "question": ident,
                "mode": decision["mode"],
                "score": score,
                "threshold": decision.get("threshold", decision.get("yes_at_least")),
            })

    async def judge_kind_files(self, candidates: list[dict[str, Any]]) -> None:
        """Each kind-folder file asks the technical_kind_grouping question once."""
        if not candidates or not self.asks(KIND_QUESTION):
            return
        asked = self.judgment.decisions[KIND_QUESTION].get("asked_as", KIND_QUESTION)
        found = await self.ask(
            {item["path"]: item["state"] for item in candidates},
            {asked: self.questions[KIND_QUESTION]},
            "Structure/naming kind-folder files",
            lambda answers: _probability(answers, asked, "bool"),
        )
        for item in candidates:
            self.decide_proposal(
                KIND_QUESTION,
                {
                    "kind": "file",
                    "id": item["path"],
                    "state": item["state"],
                    "record": "kind_files",
                },
                [found.get(item["path"])],
            )

    async def judge_pairs(self, pairs: list[dict[str, Any]]) -> None:
        """Each name pair asks same_thing in both orders; score = proposer_score * mean P(true)."""
        if not pairs or not self.asks(PAIR_QUESTION):
            return
        states: dict[str, dict[str, Any]] = {}
        for pair in pairs:
            first, second = pair["state"]["first"], pair["state"]["second"]
            states[pair["id"] + "##ab"] = {"first": first, "second": second}
            states[pair["id"] + "##ba"] = {"first": second, "second": first}
        found = await self.ask(
            states,
            {PAIR_QUESTION: self.questions[PAIR_QUESTION]},
            "Structure/naming name pairs",
            lambda answers: _probability(answers, PAIR_QUESTION, "bool"),
        )
        for pair in pairs:
            self.decide_proposal(
                PAIR_QUESTION,
                {
                    "kind": "pair",
                    "id": pair["id"],
                    "state": pair["state"],
                    "record": "pairs",
                },
                [found.get(pair["id"] + suffix) for suffix in ("##ab", "##ba")],
                pair["proposer_score"],
            )

    async def judge_consumers(self, candidates: list[dict[str, Any]]) -> None:
        """Each candidate in both option orders; score = mean P(option)."""
        if not candidates or not self.asks(CONSUMER_QUESTION):
            return
        decision = self.judgment.decisions[CONSUMER_QUESTION]
        question = self.questions[CONSUMER_QUESTION]
        asked = decision.get("asked_as", CONSUMER_QUESTION)
        criteria = question["criteria"]

        def read(answers: object) -> float:
            found: float = _probability(answers, asked, "choice", criteria)[
                decision["option"]
            ]
            return found

        answers: dict[str, float | str] = {}
        for suffix, ordered in (
            ("##o1", criteria),
            ("##o2", dict(reversed(list(criteria.items())))),
        ):
            answers |= await self.ask(
                {item["path"] + suffix: item["state"] for item in candidates},
                {asked: {**question, "criteria": ordered}},
                "Structure/naming consumer files",
                read,
            )
        for item in candidates:
            self.decide_proposal(
                CONSUMER_QUESTION,
                {
                    "kind": "file",
                    "id": item["path"],
                    "state": item["state"],
                    "record": "consumers",
                },
                [answers.get(item["path"] + suffix) for suffix in ("##o1", "##o2")],
            )


def _publish_structure(
    repo: StrPath, head: str, published: Path, result: dict[str, Any]
) -> None:
    """Write the result file, post the PR comment, then rewrite the file with the final status."""
    try:
        _atomic_json(published, result)
    except INFRASTRUCTURE_ERRORS as exc:
        result["errors"].append(f"writing structure result: {exc}")
    try:
        result["prComment"] = _post_structure_comment(repo, head, result)
    except INFRASTRUCTURE_ERRORS as exc:
        result["errors"].append(f"posting structure result: {exc}")
    result["status"] = _structure_status(result)
    try:
        _atomic_json(published, result)
    except INFRASTRUCTURE_ERRORS as exc:
        result["errors"].append(f"writing structure result: {exc}")
