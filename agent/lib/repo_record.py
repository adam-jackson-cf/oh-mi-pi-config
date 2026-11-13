"""Resolve a clone to its personal per-repo record under the oh-mi-pi-config root.

A record lives at ``<config root>/repos/<owner>/<name>/`` (lowercase) and holds ``gate.json``
and ``profile.md``. ``/repos/`` is gitignored in oh-mi-pi-config: records are personal and are
never committed. The config root is the checkout this file belongs to, so the code finds the
records that sit beside it on every device, with no home-directory path in code.

The clone's ``origin`` remote names the record. A worktree shares its main checkout's remotes, so
it resolves to the same record. No origin remote, a non-GitHub origin or no record on disk means
the repo has no record, and dynaflows is off for it.

The TypeScript twin is ``repo-record.ts`` in this directory; keep both in step.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess

GATE_FILE = "gate.json"
PROFILE_FILE = "profile.md"
RECORDS_DIR = "repos"

# The three GitHub origin forms. "." and ".." are refused so a slug never leaves repos/.
_ORIGIN = re.compile(
    r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
    r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?")


class RecordError(RuntimeError):
    """A record exists but cannot be read; callers report it instead of treating dynaflows as off."""


def config_root() -> Path:
    """The oh-mi-pi-config checkout this module belongs to (``<root>/agent/lib/repo_record.py``)."""
    return Path(__file__).resolve().parents[2]


def parse_origin(url: str) -> str | None:
    """``owner/name`` in lowercase for a GitHub origin URL, else None."""
    match = _ORIGIN.fullmatch(url.strip())
    if not match or {match[1], match[2]} & {".", ".."}:
        return None
    return f"{match[1]}/{match[2]}".lower()


def repo_slug(repo: str | Path) -> str | None:
    """``owner/name`` of the clone or worktree at ``repo`` from its origin remote, else None."""
    try:
        process = subprocess.run(["git", "-C", str(repo), "remote", "get-url", "origin"],
                                 capture_output=True, text=True, errors="replace")
    except OSError:
        return None
    if process.returncode:
        return None
    return parse_origin(process.stdout)


def record_dir(slug: str, root: str | Path | None = None) -> Path:
    """Directory of the record for ``owner/name``; it may not exist."""
    owner, name = slug.lower().split("/")
    return Path(root if root is not None else config_root()) / RECORDS_DIR / owner / name


def off_text(slug: str | None, reason: str) -> str:
    """Why dynaflows is off, in the words rails and workflows.py both use: names the slug or "none"."""
    return f"dynaflows off for {slug or 'none'}: {reason}"


def resolve_record(repo: str | Path, root: str | Path | None = None) -> tuple[str | None, Path | None, str | None]:
    """``(slug, record dir, None)``, or the reason the clone has no record: no origin remote, an
    origin that is not a GitHub owner/name URL, or no ``gate.json`` at the record directory."""
    try:
        process = subprocess.run(["git", "-C", str(repo), "remote", "get-url", "origin"],
                                 capture_output=True, text=True, errors="replace")
    except OSError:
        return None, None, "no origin remote"
    if process.returncode:
        return None, None, "no origin remote"
    slug = parse_origin(process.stdout)
    if slug is None:
        return None, None, f"origin {process.stdout.strip()} is not a GitHub owner/name URL"
    path = record_dir(slug, root)
    if not (path / GATE_FILE).is_file():
        return slug, None, f"no per-repo record at {path / GATE_FILE}"
    return slug, path, None


def find_record(repo: str | Path, root: str | Path | None = None) -> Path | None:
    """Record directory for the clone at ``repo`` when its ``gate.json`` exists, else None."""
    slug = repo_slug(repo)
    if slug is None:
        return None
    path = record_dir(slug, root)
    return path if (path / GATE_FILE).is_file() else None


# Every current gate.json field; any other field, at the top level or in ``checks``, is rejected.
GATE_FIELDS = frozenset({"schemaVersion", "checks", "pathCheck", "agentMerge", "ci", "dynaflows"})
CHECK_FIELDS = frozenset({"commit", "ci", "format", "profile"})


def _argv(value) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(arg, str) and arg.strip() for arg in value)


def gate_reason(gate) -> str | None:
    """Why rails would reject this gate.json object, or None when it is accepted.

    The Python twin of ``gateSchema`` in repo-record.ts. ``pathCheck`` and ``checks.commit`` are
    required; an unknown field is rejected by name.
    """
    if not isinstance(gate, dict) or type(gate.get("schemaVersion")) is not int or gate["schemaVersion"] != 2:
        return "unsupported gate schemaVersion"
    unknown = sorted(set(gate) - GATE_FIELDS)
    if unknown:
        return f"unknown field {', '.join(unknown)} must be removed"
    checks = gate.get("checks")
    if not isinstance(checks, dict):
        return "checks must be an object with checks.commit"
    unknown = sorted(set(checks) - CHECK_FIELDS)
    if unknown:
        return f"unknown field {', '.join(f'checks.{key}' for key in unknown)} must be removed"
    if not _argv(checks.get("commit")):
        return "checks.commit must be argv"
    for key in ("ci", "format", "profile"):
        if key in checks and not _argv(checks[key]):
            return f"checks.{key} must be argv"
    if not _argv(gate.get("pathCheck")):
        return "pathCheck must be argv"
    ci = gate.get("ci")
    if "ci" in gate:
        if not isinstance(ci, dict) or any(not isinstance(ci.get(k), str) or not ci[k].strip() for k in ("requiredContext", "runner")):
            return "ci must declare nonblank requiredContext and runner"
    if gate.get("agentMerge", "allowed") not in ("allowed", "disabled"):
        return "agentMerge must be allowed or disabled"
    dynaflows = gate.get("dynaflows")
    if not isinstance(dynaflows, dict) or type(dynaflows.get("enabled")) is not bool:
        return 'dynaflows must be {"enabled": true|false}'
    return None


def load_gate(record: str | Path) -> dict:
    """The record's ``gate.json`` object; an unreadable gate or one ``gate_reason`` rejects raises."""
    path = Path(record) / GATE_FILE
    try:
        gate = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise RecordError(f"{path}: {exc}") from exc
    reason = gate_reason(gate)
    if reason is not None:
        raise RecordError(f"{path}: {reason}")
    return gate


def dynaflows_enabled(repo: str | Path, root: str | Path | None = None) -> bool:
    """True only when the clone has a record whose ``dynaflows.enabled`` is true."""
    record = find_record(repo, root)
    if record is None:
        return False
    return load_gate(record)["dynaflows"]["enabled"]
