"""Jev Inspector session files rebuilt from the decision store.

The format is the inspector's own (jev-inspector crates/jev-inspector/src/session.rs and
crates/jev-protocol/src/lib.rs): one exchange per judge call, its request JSON rebuilt from the
stored state and questions, its response JSON from the stored raw answers. A file holds at most
MAX_EXCHANGES exchanges and MAX_FILE_BYTES bytes; a larger export is split across several files. A
call whose payload was pruned after review is listed as a summary only, under `missing`.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from guardrails.commands.shadow_review.stores import Stores

SESSION_FORMAT = "jev-inspector-session"
SESSION_VERSION = 1
PROTOCOL_VERSION = 1
MAX_EXCHANGES = 200
MAX_FILE_BYTES = 64 * 1024 * 1024
# The inspector refuses a capture whose JSON exceeds 512 KB; keep a margin for the capture fields.
MAX_CAPTURE_BYTES = 512 * 1024 - 4096
NOTE_LIMIT = 500
JEV_PATH = "/v1/systemone"


@dataclass(frozen=True)
class InspectorFilter:
    judgment: str
    question: str | None = None
    verdict: str | None = None
    outcome: str | None = None
    mode: str | None = None


def _decisions(stores: Stores, request: InspectorFilter) -> list[sqlite3.Row]:
    sql = "SELECT * FROM decisions WHERE source = 'jev' AND judgment = ?"
    params: list[Any] = [request.judgment]
    for column in ("question", "verdict", "outcome", "mode"):
        value = getattr(request, column)
        if value is not None:
            sql += f" AND {column} = ?"
            params.append(value)
    return stores.query(sql + " ORDER BY ts, call_id, question", tuple(params))


def _api_question(question: dict[str, Any]) -> dict[str, Any]:
    """A registry question as the Jev API takes it: `bool` is the API's `noul`."""
    kind = "noul" if question["type"] == "bool" else question["type"]
    return {**question, "type": kind}


def _api_answer(answer: Any) -> Any:
    """A stored raw answer as the Jev API returns it."""
    if not isinstance(answer, dict):
        return answer
    if "bool" in answer:
        return {"type": "noul", "noul": answer["bool"]}
    probabilities = answer.get("probabilities")
    if isinstance(probabilities, dict) and probabilities:
        return {
            "type": "choice",
            "choice": max(probabilities, key=probabilities.__getitem__),
            "probabilities": probabilities,
        }
    return answer


def _bodies(
    model: str | None, payload: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    request = {
        "model": model,
        "state": payload["state"],
        "questions": {
            ident: _api_question(q) for ident, q in payload["questions"].items()
        },
    }
    response = payload["response"]
    if isinstance(response, dict) and set(response) == {"error"}:
        return request, {"ok": False, "error": response["error"]}
    answers = response if isinstance(response, dict) else {}
    return request, {
        "ok": True,
        "answers": {ident: _api_answer(a) for ident, a in answers.items()},
    }


def capture(body: dict[str, Any] | None) -> dict[str, Any]:
    """One request or response capture; a body over the inspector's capture limit is left out."""
    if body is None:
        return {
            "content_type": "application/json",
            "encoding": "",
            "wire_bytes": 0,
            "captured_bytes": 0,
            "complete": False,
            "json": None,
            "notes": ["payload pruned after review"],
        }
    size = len(json.dumps(body, separators=(",", ":")).encode())
    if size > MAX_CAPTURE_BYTES:
        return {
            "content_type": "application/json",
            "encoding": "",
            "wire_bytes": size,
            "captured_bytes": 0,
            "complete": False,
            "json": None,
            "notes": [
                "body exceeds the inspector's 512 KB capture limit; read it from the decision store"
            ],
        }
    return {
        "content_type": "application/json",
        "encoding": "",
        "wire_bytes": size,
        "captured_bytes": size,
        "complete": True,
        "json": body,
        "notes": [],
    }


def _note(rows: list[sqlite3.Row]) -> str:
    first = rows[0]
    subject = " ".join(str(part) for part in (first["repo"], first["item"]) if part)
    parts = []
    for row in rows:
        score = "" if row["probability"] is None else f" p={row['probability']:.3f}"
        result = row["outcome"] or row["status"]
        verdict = f" {row['verdict']}" if row["verdict"] else ""
        parts.append(f"{row['question']} {result}{score}{verdict} ({row['mode']})")
    text = f"{first['judgment']} {subject}: {'; '.join(parts)}"
    return text if len(text) <= NOTE_LIMIT else text[: NOTE_LIMIT - 1] + "…"


@dataclass
class Call:
    """One judge call: its decisions rows and stored payload, if still kept."""

    call_id: str
    rows: list[sqlite3.Row]
    payload: dict[str, Any] | None

    def exchange(self, ident: int) -> dict[str, Any]:
        first = self.rows[0]
        failed = any(row["status"] != "ok" for row in self.rows)
        request, response = (
            _bodies(first["model"], self.payload) if self.payload else (None, None)
        )
        request_capture, response_capture = capture(request), capture(response)
        summary = {
            "id": ident,
            "started_at_ms": int(
                datetime.fromisoformat(first["ts"]).timestamp() * 1000
            ),
            "method": "POST",
            "path": JEV_PATH,
            "status": None if failed else 200,
            "outcome": "upstream_error" if failed else "complete",
            "duration_ms": None,
            "upstream_headers_ms": None,
            "request_id": self.call_id,
            "model": first["model"],
            "request_bytes": request_capture["wire_bytes"],
            "response_bytes": response_capture["wire_bytes"],
            "note": _note(self.rows),
        }
        return {
            "summary": summary,
            "request": request_capture,
            "response": response_capture,
        }

    @property
    def starred(self) -> bool:
        """Wrong decisions (false flags and misses) are starred for review."""
        return any(row["verdict"] in ("false_flag", "missed") for row in self.rows)


def calls(stores: Stores, request: InspectorFilter) -> list[Call]:
    """The calls behind the matching decisions, each with all of its own decision rows."""
    matched = list(dict.fromkeys(row["call_id"] for row in _decisions(stores, request)))
    found = []
    for call_id in matched:
        rows = stores.query(
            "SELECT * FROM decisions WHERE call_id = ? ORDER BY question", (call_id,)
        )
        found.append(Call(call_id, rows, stores.payload(call_id)))
    return found


@dataclass
class SessionFile:
    """One session file's exchanges, numbered from 1, newest summary first as the inspector lists them."""

    judgment: str
    exchanges: dict[int, dict[str, Any]] = field(default_factory=dict)
    starred: list[int] = field(default_factory=list)
    missing: list[int] = field(default_factory=list)
    size: int = 0

    def add(self, call: Call) -> None:
        ident = len(self.exchanges) + 1
        exchange = call.exchange(ident)
        self.exchanges[ident] = exchange
        self.size += len(json.dumps(exchange, separators=(",", ":")).encode())
        if call.payload is None:
            self.missing.append(ident)
        if call.starred:
            self.starred.append(ident)

    def fits(self, call: Call) -> bool:
        incoming = len(json.dumps(call.exchange(1), separators=(",", ":")).encode())
        # Each exchange's summary is written twice: in the snapshot and in the exchange.
        return (
            len(self.exchanges) < MAX_EXCHANGES
            and 2 * (self.size + incoming) < MAX_FILE_BYTES
        )

    def session(self) -> dict[str, Any]:
        requests = [
            self.exchanges[ident]["summary"]
            for ident in sorted(self.exchanges, reverse=True)
        ]
        return {
            "format": SESSION_FORMAT,
            "version": SESSION_VERSION,
            "snapshot": {
                "version": PROTOCOL_VERSION,
                "instance_id": f"dynaflows-decision-store-{self.judgment}",
                "revision": 1,
                "relay_url": "Offline export from the dynaflows decision store",
                "upstream": "TypeSafe Jev, replayed from stored payloads",
                "capture_bodies": True,
                "history_limit": len(requests),
                "total_requests": len(requests),
                "evicted": 0,
                "capture_dropped": 0,
                "requests": requests,
            },
            "exchanges": {
                str(ident): exchange
                for ident, exchange in sorted(self.exchanges.items())
            },
            "starred": self.starred,
            "missing": [ident for ident in self.missing if ident in self.exchanges],
            "last_received": False,
        }


def split(judgment: str, found: list[Call]) -> Iterator[SessionFile]:
    current = SessionFile(judgment)
    for call in found:
        if current.exchanges and not current.fits(call):
            yield current
            current = SessionFile(judgment)
        current.add(call)
    yield current


def part_path(path: Path, index: int) -> Path:
    """The first part keeps the name asked for; later parts insert -<n> before .jev.json."""
    if index == 1:
        return path
    name = path.name
    stem, suffix = (
        (name[: -len(".jev.json")], ".jev.json")
        if name.endswith(".jev.json")
        else (path.stem, path.suffix)
    )
    return path.with_name(f"{stem}-{index}{suffix}")


def write_sessions(
    stores: Stores, request: InspectorFilter, path: Path
) -> list[dict[str, Any]]:
    """Write the session file(s); returns each file with its exchange count."""
    written = []
    for index, session_file in enumerate(
        split(request.judgment, calls(stores, request)), 1
    ):
        target = part_path(path, index)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(session_file.session(), separators=(",", ":")))
        written.append({"path": str(target), "exchanges": len(session_file.exchanges)})
    return written
