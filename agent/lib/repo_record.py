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


def find_record(repo: str | Path, root: str | Path | None = None) -> Path | None:
    """Record directory for the clone at ``repo`` when its ``gate.json`` exists, else None."""
    slug = repo_slug(repo)
    if slug is None:
        return None
    path = record_dir(slug, root)
    return path if (path / GATE_FILE).is_file() else None


def load_gate(record: str | Path) -> dict:
    """The record's ``gate.json`` object; schema checks stay with the consumer."""
    path = Path(record) / GATE_FILE
    try:
        gate = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise RecordError(f"{path}: {exc}") from exc
    if not isinstance(gate, dict):
        raise RecordError(f"{path}: must hold one JSON object")
    return gate


def dynaflows_enabled(repo: str | Path, root: str | Path | None = None) -> bool:
    """True only when the clone has a record whose ``dynaflows.enabled`` is true."""
    record = find_record(repo, root)
    if record is None:
        return False
    dynaflows = load_gate(record).get("dynaflows")
    if not isinstance(dynaflows, dict) or type(dynaflows.get("enabled")) is not bool:
        raise RecordError(f"{record / GATE_FILE}: dynaflows must be {{\"enabled\": true|false}}")
    return dynaflows["enabled"]
