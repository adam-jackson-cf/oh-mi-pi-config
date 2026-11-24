#!/usr/bin/env python3
"""One-off: import <agent dir>/dynaflows/judgment-calls.jsonl into the decision store, then delete it.

Run once by hand on each machine after the decision store merges:

    uv run python scripts/migrate_judgment_calls.py [--log PATH]

Every line becomes one decisions row per question it answered (source jev, mode enforced: every
judgment was enforced when the log was written). The log held no state, so no payload is kept. The
trigger is the event each judgment runs at today; the per-question version key and threshold are
filled only when the line's judgment version still matches thresholds.json. A malformed line stops
the import before anything is written, and the log is kept.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from guardrails.foundation import decision_store

LOG = "dynaflows/judgment-calls.jsonl"
JUDGMENTS = Path(__file__).resolve().parents[1] / "agent/extensions/dynaflows/judgments"
# The event each judgment runs at, as the runtime now records it.
TRIGGERS = {
    "completion-evidence": "pr",
    "dependency-severity": "commit",
    "extension-impact": "manual",
    "implementation-integrity": "commit",
    "in-scope": "manual",
    "structure-naming": "pr",
}
FIELDS = frozenset({
    "judgment",
    "state_sha256",
    "model",
    "question_version",
    "probabilities",
    "outcomes",
    "timestamp",
})


class MigrationError(RuntimeError):
    pass


def current_decisions(judgment: str, version: str) -> dict[str, Any]:
    """The judgment's decisions when `version` is still its version key, else none."""
    path = JUDGMENTS / judgment / "thresholds.json"
    if not path.is_file():
        return {}
    thresholds = json.loads(path.read_text())
    decisions: dict[str, Any] = thresholds["decisions"]
    return decisions if thresholds.get("version_key") == version else {}


def rows_for(record: dict[str, Any], machine: str) -> list[dict[str, Any]]:
    judgment = record["judgment"]
    if judgment not in TRIGGERS:
        raise MigrationError(f"unknown judgment {judgment!r}")
    decisions = current_decisions(judgment, record["question_version"])
    call_id = decision_store.new_id()
    rows = []
    for question, probability in record["probabilities"].items():
        decision = decisions.get(question, {})
        row: dict[str, Any] = {
            "id": decision_store.new_id(),
            "call_id": call_id,
            "ts": record["timestamp"],
            "source": "jev",
            "judgment": judgment,
            "question": question,
            "mode": "enforced",
            "status": "ok",
            "model": record["model"],
            "question_version": decision.get("version_key"),
            "judgment_version": record["question_version"],
            "threshold": decision.get("threshold", decision.get("round_up_floor")),
            "trigger": TRIGGERS[judgment],
            "machine": machine,
            "state_sha256": record["state_sha256"],
            "state_kept": 0,
        }
        if isinstance(probability, dict):
            row["choice_probabilities"] = json.dumps(probability, sort_keys=True)
        else:
            row["probability"] = probability
            row["outcome"] = decision_store.outcome_name(record["outcomes"][question])
        rows.append(row)
    return rows


def parse(path: Path, machine: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            if not isinstance(record, dict) or set(record) != FIELDS:
                raise MigrationError(f"fields must be exactly {sorted(FIELDS)}")
            rows.extend(rows_for(record, machine))
        except (ValueError, KeyError, TypeError, MigrationError) as exc:
            raise MigrationError(f"{path} line {number}: {exc}") from exc
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--log", type=Path, default=None)
    args = parser.parse_args(argv)
    log: Path = args.log or decision_store.agent_dir() / LOG
    if not log.is_file():
        print(f"no judgment call log at {log}; nothing to import")
        return 0
    try:
        rows = parse(log, decision_store.machine_name())
        decision_store.record_decisions(rows)
    except (MigrationError, decision_store.StoreError) as exc:
        print(f"migrate_judgment_calls: {exc}; {log} is kept", file=sys.stderr)
        return 1
    log.unlink()
    calls = len({row["call_id"] for row in rows})
    print(
        f"imported {calls} calls ({len(rows)} decisions) into {decision_store.store_path()}; deleted {log}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
