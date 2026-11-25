"""The decision stores a review reads: this machine's, plus read-only copies of other machines'.

Rows carry the machine that wrote them. Reads go through one in-memory merge built from the same
schema, so the shadow_progress view and every query see all machines at once; a decision present in
several stores is read from the first (this machine's store comes first).
"""

from __future__ import annotations

import shlex
import sqlite3
import subprocess
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from guardrails.foundation import decision_store

# Machines whose store a review can read, by the name --machine takes: the ssh host.
MACHINES = {"nuc": "codex-omarchy-nuc"}
# The same agent-dir path on the remote machine.
REMOTE_STORE = "~/.omp/agent/agent-rails/agent-rails.db"
COPY_TIMEOUT_SECONDS = 120
# Run on the remote machine: open its store read-only and write the whole database to stdout.
SERIALIZE = (
    "import pathlib, sqlite3, sys; "
    "path = pathlib.Path(sys.argv[1]).expanduser(); "
    "store = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True); "
    "sys.stdout.buffer.write(store.serialize())"
)
MERGED_TABLES = ("questions", "decisions")


class ReviewError(RuntimeError):
    """A review command cannot run as asked."""


def fetch(machine: str, destination: Path) -> None:
    """Copy `machine`'s decision store to `destination` over ssh (BatchMode), reading it only."""
    host = MACHINES[machine]
    remote = shlex.join(["python3", "-c", SERIALIZE, REMOTE_STORE])
    try:
        result = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", host, remote],
            capture_output=True,
            timeout=COPY_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReviewError(f"cannot copy the {machine} decision store: {exc}") from exc
    if result.returncode != 0 or not result.stdout:
        reason = result.stderr.decode(errors="replace").strip() or "no output"
        raise ReviewError(
            f"cannot copy the {machine} decision store from {host}: {reason}"
        )
    destination.write_bytes(result.stdout)


class Stores:
    """This machine's store (read-write) and copies of other machines' stores (read-only)."""

    def __init__(
        self, local: sqlite3.Connection, remotes: list[sqlite3.Connection]
    ) -> None:
        self.local = local
        self.remotes = remotes
        self.merged = self._merge()

    def all(self) -> list[sqlite3.Connection]:
        return [self.local, *self.remotes]

    def _merge(self) -> sqlite3.Connection:
        merged = sqlite3.connect(":memory:", isolation_level=None)
        with decision_store.closing_on_error(merged):
            merged.row_factory = sqlite3.Row
            merged.executescript(decision_store.schema_text())
            for connection in self.all():
                for table in MERGED_TABLES:
                    self._copy(connection, merged, table)
        return merged

    @staticmethod
    def _copy(
        source: sqlite3.Connection, merged: sqlite3.Connection, table: str
    ) -> None:
        rows = source.execute(f"SELECT * FROM {table}").fetchall()
        if not rows:
            return
        columns = rows[0].keys()
        marks = ", ".join("?" for _ in columns)
        merged.executemany(
            f"INSERT OR IGNORE INTO {table} ({', '.join(columns)}) VALUES ({marks})",
            [tuple(row) for row in rows],
        )

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        """Rows from the merged view of every store."""
        return self.merged.execute(sql, params).fetchall()

    def payload(self, call_id: str) -> Any:
        """The call's stored state, questions and response, from the first store that keeps it."""
        for connection in self.all():
            row = connection.execute(
                "SELECT body FROM payloads WHERE call_id = ?", (call_id,)
            ).fetchone()
            if row is not None:
                return decompress(row["body"])
        return None

    def source(self, decision_id: str) -> sqlite3.Connection | None:
        """The first store holding the decision."""
        for connection in self.all():
            found = connection.execute(
                "SELECT 1 FROM decisions WHERE id = ?", (decision_id,)
            ).fetchone()
            if found is not None:
                return connection
        return None


def decompress(blob: bytes) -> Any:
    return decision_store.decompress(blob)


@contextmanager
def opened_stores(local: Path, machines: list[str]) -> Generator[Stores, None, None]:
    """This machine's store and a fresh read-only copy of each named machine's store."""
    with TemporaryDirectory(prefix="agent-rails-review-") as scratch:
        connections = [decision_store.connect(local)]
        try:
            for machine in dict.fromkeys(machines):
                copy = Path(scratch) / f"{machine}.db"
                fetch(machine, copy)
                connections.append(decision_store.connect_read_only(copy))
            stores = Stores(connections[0], connections[1:])
            try:
                yield stores
            finally:
                stores.merged.close()
        finally:
            for connection in connections:
                connection.close()
