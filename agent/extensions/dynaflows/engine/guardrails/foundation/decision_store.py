"""The dynaflows decision store: one SQLite database under the omp agent dir.

Every Jev judgment answer, deterministic check result and rail decision lands here for analysis
queries and shadow review. data/decision-store.sql is the only schema; lib/decision-store.ts applies
the same file through bun:sqlite. A database at any schema version other than that file's is refused.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import socket
import sqlite3
import uuid
import zlib
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any

STORE_FILE = "dynaflows/dynaflows.db"
SCHEMA_FILE = "decision-store.sql"
BUSY_TIMEOUT_SECONDS = 5.0
# One pass in PASS_SAMPLE keeps its payload, chosen by hash so the choice is stable.
PASS_SAMPLE = 10
TRIGGERS = ("commit", "prove", "pr", "manual")
MODES = ("enforced", "shadow")
OUTCOME_NAMES = {"yes": "flag", "no": "pass", "escalate": "escalate"}
DECISION_COLUMNS = (
    "id",
    "call_id",
    "ts",
    "source",
    "judgment",
    "question",
    "mode",
    "status",
    "error",
    "outcome",
    "probability",
    "choice_probabilities",
    "threshold",
    "proposer_score",
    "combined_score",
    "model",
    "question_version",
    "judgment_version",
    "repo",
    "commit_sha",
    "pr",
    "trigger",
    "item_kind",
    "item",
    "machine",
    "state_sha256",
    "state_kept",
)
QUESTION_COLUMNS = (
    "judgment",
    "question",
    "question_version",
    "approach",
    "overlays",
    "primitive",
    "evidence_unit",
    "combination",
    "action",
    "policy_area",
    "mode",
    "decision",
    "precision_target",
    "override",
    "shadow_exit",
    "model",
    "loaded_at",
)


class StoreError(RuntimeError):
    """The decision store cannot be opened, read or written."""


@dataclass(frozen=True)
class DecisionContext:
    """Where a decision was made: the event that asked and the subject it judged."""

    trigger: str
    repo: str | None = None
    commit: str | None = None
    pr: int | None = None
    item_kind: str | None = None
    item: str | None = None

    def __post_init__(self) -> None:
        if self.trigger not in TRIGGERS:
            raise StoreError(
                f"decision trigger must be one of {TRIGGERS}, not {self.trigger!r}"
            )

    def columns(self) -> dict[str, object]:
        return {
            "trigger": self.trigger,
            "repo": self.repo,
            "commit_sha": self.commit,
            "pr": self.pr,
            "item_kind": self.item_kind,
            "item": self.item,
        }


@cache
def schema_text() -> str:
    return (
        resources
        .files("guardrails.data")
        .joinpath(SCHEMA_FILE)
        .read_text(encoding="utf-8")
    )


def schema_version() -> int:
    """The user_version decision-store.sql sets."""
    match = re.search(r"^PRAGMA user_version = (\d+);$", schema_text(), re.MULTILINE)
    if match is None:
        raise StoreError(f"{SCHEMA_FILE} sets no user_version")
    return int(match.group(1))


def agent_dir() -> Path:
    """The omp agent state directory: PI_CODING_AGENT_DIR, else ~/.omp/agent (omp's default)."""
    configured = os.environ.get("PI_CODING_AGENT_DIR", "").strip()
    return Path(configured).expanduser() if configured else Path.home() / ".omp/agent"


def store_path() -> Path:
    return agent_dir() / STORE_FILE


def machine_name() -> str:
    return socket.gethostname()


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex


def _version(connection: sqlite3.Connection) -> int:
    row = connection.execute("PRAGMA user_version").fetchone()
    return int(row[0])


def _check_version(connection: sqlite3.Connection, path: Path) -> None:
    version = _version(connection)
    if version != schema_version():
        raise StoreError(
            f"the decision store {path} is at schema version {version}; "
            f"this harness reads version {schema_version()} only"
        )


@contextmanager
def closing_on_error(
    connection: sqlite3.Connection,
) -> Generator[sqlite3.Connection, None, None]:
    """Close `connection` when the block raises, so no failed open leaves a handle behind."""
    try:
        yield connection
    except BaseException:
        connection.close()
        raise


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Open the store, creating it on first use. Statements autocommit unless wrapped in `transaction`.

    The caller closes the connection (`opened` does); a failed open closes it before raising.
    """
    path = store_path() if path is None else path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            path, timeout=BUSY_TIMEOUT_SECONDS, isolation_level=None
        )
        with closing_on_error(connection):
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode = WAL")
            if _version(connection) == 0:
                connection.executescript(schema_text())
    except (OSError, sqlite3.Error) as exc:
        raise StoreError(f"cannot open the decision store {path}: {exc}") from exc
    with closing_on_error(connection):
        _check_version(connection, path)
    return connection


def connect_read_only(path: Path) -> sqlite3.Connection:
    """Open a static store copy (another machine's) without writing to it or beside it.

    The caller closes the connection; a failed open closes it before raising.
    """
    try:
        connection = sqlite3.connect(f"{path.as_uri()}?mode=ro&immutable=1", uri=True)
        with closing_on_error(connection):
            connection.row_factory = sqlite3.Row
            _version(connection)
    except sqlite3.Error as exc:
        raise StoreError(f"cannot read the decision store {path}: {exc}") from exc
    with closing_on_error(connection):
        _check_version(connection, path)
    return connection


@contextmanager
def opened(path: Path | None = None) -> Generator[sqlite3.Connection, None, None]:
    connection = connect(path)
    try:
        yield connection
    finally:
        connection.close()


@contextmanager
def transaction(
    connection: sqlite3.Connection,
) -> Generator[sqlite3.Connection, None, None]:
    """BEGIN IMMEDIATE ... COMMIT, rolled back on any error."""
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    connection.execute("COMMIT")


def _jsonable(value: object) -> object:
    """A raw judge response as strict JSON: a non-finite number or a non-JSON value becomes its text."""
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if value is None or isinstance(value, (str, int, float)):
        return value
    return repr(value)


def compress(value: object) -> bytes:
    return zlib.compress(
        json.dumps(_jsonable(value), sort_keys=True, allow_nan=False).encode()
    )


def decompress(blob: bytes) -> Any:
    return json.loads(zlib.decompress(blob))


def sampled_pass(state_sha256: str, judgment: str, question: str) -> bool:
    """The stable 1-in-PASS_SAMPLE choice of passes whose payload is kept."""
    digest = hashlib.sha256(f"{state_sha256}:{judgment}:{question}".encode()).digest()
    return int.from_bytes(digest[:4], "big") % PASS_SAMPLE == 0


def keeps_payload(row: Mapping[str, object]) -> bool:
    """Every flag, every error and a stable sample of passes keep their call's payload."""
    outcome = row.get("outcome")
    if row["status"] != "ok" or outcome in ("flag", "escalate"):
        return True
    if outcome != "pass":
        return False
    return sampled_pass(
        str(row["state_sha256"]), str(row["judgment"]), str(row["question"])
    )


def _insert(
    connection: sqlite3.Connection,
    table: str,
    columns: Sequence[str],
    rows: Sequence[Mapping[str, object]],
    verb: str = "INSERT",
) -> None:
    names = ", ".join(columns)
    marks = ", ".join(f":{name}" for name in columns)
    connection.executemany(
        f"{verb} INTO {table} ({names}) VALUES ({marks})",
        [{name: row.get(name) for name in columns} for row in rows],
    )


def record_decisions(
    rows: Sequence[Mapping[str, object]],
    payload: Mapping[str, object] | None = None,
    path: Path | None = None,
) -> None:
    """Insert decision rows in one transaction, with the call's payload when any row keeps it."""
    try:
        with opened(path) as connection, transaction(connection):
            _insert(connection, "decisions", DECISION_COLUMNS, rows)
            kept = [row for row in rows if row.get("state_kept")]
            if payload is not None and kept:
                first = kept[0]
                connection.execute(
                    "INSERT OR REPLACE INTO payloads (call_id, state_sha256, body, created_at) VALUES (?, ?, ?, ?)",
                    (
                        first["call_id"],
                        first["state_sha256"],
                        compress(payload),
                        first["ts"],
                    ),
                )
    except sqlite3.Error as exc:
        raise StoreError(f"cannot write the decision store: {exc}") from exc


def record_questions(
    rows: Sequence[Mapping[str, object]], path: Path | None = None
) -> None:
    """Upsert the question versions a judgment load saw."""
    try:
        with opened(path) as connection, transaction(connection):
            _insert(
                connection, "questions", QUESTION_COLUMNS, rows, "INSERT OR REPLACE"
            )
    except sqlite3.Error as exc:
        raise StoreError(f"cannot write the decision store: {exc}") from exc


def shadow_capable(kind: object, decision: Mapping[str, object]) -> bool:
    return kind == "bool" or "threshold" in decision


def outcome_name(outcome: str) -> str:
    """A registry outcome (yes, no, escalate) as the store names it (flag, pass, escalate)."""
    return OUTCOME_NAMES[outcome]


def verdict(outcome: str | None, label: str | None) -> str | None:
    """true_flag, false_flag, missed or true_pass for a labelled flag or pass; else None."""
    table = {
        ("flag", "1"): "true_flag",
        ("flag", "0"): "false_flag",
        ("pass", "1"): "missed",
        ("pass", "0"): "true_pass",
    }
    return table.get((outcome or "", label or ""))
