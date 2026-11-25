"""Review batches: export unreviewed decisions for blind labelling, import the labels back.

An export holds every unreviewed flag of a judgment and a random sample of its unreviewed passes,
shuffled together, in the council labelling batch shape ({guide, items}); labellers see the stored
state, never the score or outcome. An import writes reviews rows, sets the counting label and verdict
on each decision, appends the case and label to the judgment's eval data (the permanent record), and
deletes payloads no unreviewed decision still needs.
"""

from __future__ import annotations

import json
import random
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from guardrails.commands.shadow_review.stores import ReviewError, Stores
from guardrails.foundation import decision_store

LABELS = ("0", "1", "abstain")
LABELLERS = ("council", "arbiter")
CASE_PREFIX = "SR-"


@dataclass(frozen=True)
class ExportRequest:
    judgment: str
    question: str | None
    sample_passes: int
    mode: str
    seed: int


def _unreviewed(
    stores: Stores, request: ExportRequest, outcome: str
) -> list[sqlite3.Row]:
    sql = (
        "SELECT * FROM decisions WHERE source = 'jev' AND judgment = ? AND mode = ? "
        "AND outcome = ? AND review_label IS NULL AND state_kept = 1"
    )
    params: tuple[Any, ...] = (request.judgment, request.mode, outcome)
    if request.question is not None:
        sql += " AND question = ?"
        params = (*params, request.question)
    return stores.query(sql + " ORDER BY ts, id", params)


def _item(stores: Stores, row: sqlite3.Row) -> dict[str, Any]:
    payload = stores.payload(row["call_id"])
    if payload is None:
        raise ReviewError(
            f"decision {row['id']} keeps no payload; it cannot be labelled"
        )
    return {
        "label_id": row["id"],
        "question": row["question"],
        "repo": row["repo"],
        "commit": row["commit_sha"],
        "item_kind": row["item_kind"],
        "item": row["item"],
        "machine": row["machine"],
        "state": payload["state"],
    }


def export_batch(stores: Stores, request: ExportRequest, guide: str) -> dict[str, Any]:
    """Every unreviewed flag plus up to `sample_passes` random unreviewed passes, shuffled."""
    rng = random.Random(request.seed)
    flags = _unreviewed(stores, request, "flag")
    passes = _unreviewed(stores, request, "pass")
    chosen = [*flags, *rng.sample(passes, min(request.sample_passes, len(passes)))]
    rng.shuffle(chosen)
    return {"guide": guide, "items": [_item(stores, row) for row in chosen]}


@dataclass(frozen=True)
class Label:
    decision_id: str
    label: str
    rule: str | None
    reason: str | None
    confirmed: str


def _text(row: dict[str, Any], key: str, index: int) -> str | None:
    value = row.get(key)
    if value is not None and not isinstance(value, str):
        raise ReviewError(f"labels row {index}: {key} must be text")
    return value


def parse_labels(document: object, labeller: str) -> list[Label]:
    """Council output rows ({label_id, council_label, rule, reason}) or arbiter rows ({label_id, label, ...})."""
    if not isinstance(document, list) or not document:
        raise ReviewError("labels must be a non-empty JSON array of label rows")
    key = "council_label" if labeller == "council" else "label"
    parsed = []
    for index, row in enumerate(document, 1):
        if not isinstance(row, dict) or not isinstance(row.get("label_id"), str):
            raise ReviewError(f"labels row {index}: needs a label_id")
        label = str(row.get(key))
        if label not in LABELS:
            raise ReviewError(f"labels row {index}: {key} must be one of {LABELS}")
        confirmed = _text(row, "confirmed", index) or "bulk"
        parsed.append(
            Label(
                row["label_id"],
                label,
                _text(row, "rule", index),
                _text(row, "reason", index),
                confirmed,
            )
        )
    return parsed


@dataclass(frozen=True)
class ImportRequest:
    labeller: str
    batch: str
    judgments: Path


def _localise(stores: Stores, decision_id: str) -> sqlite3.Row:
    """The decision in this machine's store, copied there (with its payload) from another machine's."""
    local = stores.local
    source = stores.source(decision_id)
    if source is None:
        raise ReviewError(f"no decision {decision_id} in the stores read")
    row: sqlite3.Row = source.execute(
        "SELECT * FROM decisions WHERE id = ?", (decision_id,)
    ).fetchone()
    if source is not local:
        columns = row.keys()
        local.execute(
            f"INSERT OR IGNORE INTO decisions ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
            tuple(row),
        )
        payload = source.execute(
            "SELECT * FROM payloads WHERE call_id = ?", (row["call_id"],)
        ).fetchone()
        if payload is not None:
            local.execute(
                "INSERT OR IGNORE INTO payloads (call_id, state_sha256, body, created_at) VALUES (?, ?, ?, ?)",
                tuple(payload),
            )
        row = local.execute(
            "SELECT * FROM decisions WHERE id = ?", (decision_id,)
        ).fetchone()
    return row


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _append(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    lines = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
    if lines:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(lines)


class EvalData:
    """A judgment's cases.jsonl and labels.jsonl, appended without duplicating a case or label."""

    def __init__(self, directory: Path) -> None:
        if not (directory / "thresholds.json").is_file():
            raise ReviewError(f"{directory} is not a judgment directory")
        self.directory = directory
        self.cases = {row["case_id"] for row in _jsonl(directory / "cases.jsonl")}
        self.labels = {
            (row["case_id"], row.get("question_id"), row.get("by"), row.get("batch"))
            for row in _jsonl(directory / "labels.jsonl")
        }
        self.new_cases: list[dict[str, Any]] = []
        self.new_labels: list[dict[str, Any]] = []

    def add(
        self, decision: sqlite3.Row, payload: Any, label: Label, request: ImportRequest
    ) -> None:
        """Append the decision's case (once) and this label; a new case needs the stored state."""
        case_id = CASE_PREFIX + decision["id"]
        if case_id not in self.cases:
            if payload is None:
                raise ReviewError(
                    f"decision {decision['id']} keeps no payload; its case cannot be recorded"
                )
            state = payload["state"]
            self.cases.add(case_id)
            self.new_cases.append(_case(case_id, decision, state))
        key = (case_id, decision["question"], request.labeller, request.batch)
        if key not in self.labels:
            self.labels.add(key)
            self.new_labels.append(_label_row(case_id, decision, label, request))

    def write(self) -> None:
        _append(self.directory / "cases.jsonl", self.new_cases)
        _append(self.directory / "labels.jsonl", self.new_labels)


def _case(case_id: str, decision: sqlite3.Row, state: object) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "unit": decision["item"] or decision["repo"] or decision["call_id"],
        "state": state,
        "stratum": "shadow-review",
        "split": "test",
        "source": "replay",
        "builder": f"decision store {decision['question_version']}",
        "provenance": {
            key: decision[key]
            for key in (
                "id",
                "call_id",
                "ts",
                "machine",
                "repo",
                "commit_sha",
                "pr",
                "trigger",
                "item_kind",
                "item",
                "mode",
            )
        },
    }


def _label_row(
    case_id: str, decision: sqlite3.Row, label: Label, request: ImportRequest
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "case_id": case_id,
        "question_id": decision["question"],
        "question_version": decision["question_version"],
        "label": label.label if label.label == "abstain" else int(label.label),
        "by": request.labeller,
        "provenance": "arbiter" if request.labeller == "arbiter" else "agent_reviewed",
        "rule": label.rule,
        "note": label.reason,
        "timestamp": decision_store.now(),
        "blind": True,
        "batch": request.batch,
    }
    if request.labeller == "arbiter":
        row["confirmed"] = label.confirmed
    return row


def _counting(local: sqlite3.Connection, decision_id: str) -> sqlite3.Row:
    """The counting review: the latest arbiter label, else the latest council label."""
    row: sqlite3.Row = local.execute(
        "SELECT * FROM reviews WHERE decision_id = ? "
        "ORDER BY labeller = 'arbiter' DESC, ts DESC, id DESC LIMIT 1",
        (decision_id,),
    ).fetchone()
    return row


def _apply(
    local: sqlite3.Connection,
    decision: sqlite3.Row,
    label: Label,
    request: ImportRequest,
) -> None:
    local.execute(
        "INSERT INTO reviews (decision_id, label, rule, reason, labeller, batch, ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            decision["id"],
            label.label,
            label.rule,
            label.reason,
            request.labeller,
            request.batch,
            decision_store.now(),
        ),
    )
    counting = _counting(local, decision["id"])
    local.execute(
        "UPDATE decisions SET review_label = ?, review_rule = ?, reviewed_at = ?, review_batch = ?, "
        "verdict = ?, state_kept = 0 WHERE id = ?",
        (
            counting["label"],
            counting["rule"],
            counting["ts"],
            counting["batch"],
            decision_store.verdict(decision["outcome"], counting["label"]),
            decision["id"],
        ),
    )


def _prune(local: sqlite3.Connection, calls: set[str]) -> int:
    """Delete the payloads of these calls that no unreviewed kept decision still needs."""
    removed = 0
    for call_id in sorted(calls):
        cursor = local.execute(
            "DELETE FROM payloads WHERE call_id = ? AND NOT EXISTS "
            "(SELECT 1 FROM decisions WHERE call_id = ? AND state_kept = 1)",
            (call_id, call_id),
        )
        removed += cursor.rowcount
    return removed


def import_labels(
    stores: Stores, labels: list[Label], request: ImportRequest
) -> dict[str, Any]:
    """Apply labels: reviews rows, counting label and verdict, eval data, then payload pruning."""
    local = stores.local
    with decision_store.transaction(local):
        decisions = [_localise(stores, label.decision_id) for label in labels]
        eval_data: dict[str, EvalData] = {}
        for decision, label in zip(decisions, labels, strict=True):
            name = decision["judgment"]
            data = eval_data.setdefault(name, EvalData(request.judgments / name))
            data.add(decision, stores.payload(decision["call_id"]), label, request)
            _apply(local, decision, label, request)
        for data in eval_data.values():
            data.write()
        pruned = _prune(local, {decision["call_id"] for decision in decisions})
    return {
        "imported": len(labels),
        "batch": request.batch,
        "labeller": request.labeller,
        "cases_added": sum(len(data.new_cases) for data in eval_data.values()),
        "labels_added": sum(len(data.new_labels) for data in eval_data.values()),
        "payloads_pruned": pruned,
    }
