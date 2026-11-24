"""scripts/migrate_judgment_calls.py: the one-off import of the old JSONL call log."""

import importlib.util
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.fast

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "migrate_judgment_calls", ROOT / "scripts/migrate_judgment_calls.py"
)
assert spec is not None
assert spec.loader is not None
migrate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migrate)
SEVERITY = json.loads(
    (
        ROOT
        / "agent/extensions/dynaflows/judgments/dependency-severity/thresholds.json"
    ).read_text()
)


def line(**change: Any) -> str:
    record = {
        "judgment": "dependency-severity",
        "state_sha256": "a" * 64,
        "model": "typesafe/jev-1.14.0",
        "question_version": SEVERITY["version_key"],
        "probabilities": {"reached": 0.2, "impact": {"rce": 0.9, "dos": 0.1}},
        "outcomes": {"reached": "no"},
        "timestamp": "2026-11-21T12:00:00+00:00",
        **change,
    }
    return json.dumps(record)


class MigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.agent = Path(scratch.name)
        patcher = patch.dict("os.environ", {"PI_CODING_AGENT_DIR": str(self.agent)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.log = self.agent / "dynaflows/judgment-calls.jsonl"
        self.log.parent.mkdir(parents=True)

    def run_main(self) -> tuple[int, str]:
        out = StringIO()
        with redirect_stdout(out), redirect_stderr(out):
            code = migrate.main([])
        return code, out.getvalue()

    def rows(self) -> list[dict[str, Any]]:
        with closing(sqlite3.connect(self.agent / "dynaflows/dynaflows.db")) as store:
            store.row_factory = sqlite3.Row
            return [
                dict(row)
                for row in store.execute("SELECT * FROM decisions ORDER BY rowid")
            ]

    def test_every_logged_answer_becomes_a_decision_and_the_log_is_deleted(
        self,
    ) -> None:
        stale = line(question_version="b" * 64, timestamp="2026-11-22T09:00:00+00:00")
        self.log.write_text(f"{line()}\n\n{stale}\n")
        code, out = self.run_main()
        assert code == 0, out
        assert "imported 2 calls (4 decisions)" in out
        assert not self.log.exists()
        rows = self.rows()
        reached = rows[0]
        assert {
            key: reached[key]
            for key in (
                "source",
                "judgment",
                "question",
                "mode",
                "status",
                "outcome",
                "probability",
                "trigger",
                "state_kept",
            )
        } == {
            "source": "jev",
            "judgment": "dependency-severity",
            "question": "reached",
            "mode": "enforced",
            "status": "ok",
            "outcome": "pass",
            "probability": 0.2,
            "trigger": "commit",
            "state_kept": 0,
        }
        assert (
            reached["question_version"]
            == SEVERITY["decisions"]["reached"]["version_key"]
        )
        assert reached["threshold"] == SEVERITY["decisions"]["reached"]["threshold"]
        assert json.loads(rows[1]["choice_probabilities"]) == {"rce": 0.9, "dos": 0.1}
        assert rows[0]["call_id"] == rows[1]["call_id"] != rows[2]["call_id"]
        assert (rows[2]["question_version"], rows[2]["threshold"]) == (None, None), (
            "a stale version key fills no per-question version or threshold"
        )
        assert self.run_main() == (
            0,
            f"no judgment call log at {self.log}; nothing to import\n",
        )

    def test_a_malformed_line_imports_nothing_and_keeps_the_log(self) -> None:
        for bad in (
            "not json",
            line(judgment="unknown-judgment"),
            json.dumps({"judgment": "in-scope"}),
        ):
            with self.subTest(bad=bad):
                self.log.write_text(f"{line()}\n{bad}\n")
                code, out = self.run_main()
                assert code == 1
                assert "line 2" in out
                assert "is kept" in out
                assert self.log.exists()
                assert (
                    not (self.agent / "dynaflows/dynaflows.db").exists()
                    or self.rows() == []
                )


if __name__ == "__main__":
    unittest.main()
