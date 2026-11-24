"""The decision store, deterministic check logging and guardrails shadow-review."""

import json
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from guardrails.commands import cli
from guardrails.commands.shadow_review import command, inspector, stores
from guardrails.foundation import decision_store
from guardrails.foundation.findings import Finding

pytestmark = pytest.mark.fast

# The Jev Inspector session shape, vendored as key sets from jev-inspector's
# crates/jev-inspector/src/session.rs (Session) and crates/jev-protocol/src/lib.rs
# (Snapshot, Summary, Exchange, Capture, Outcome); Summary.usage is optional and never written here.
SESSION_KEYS = {
    "format",
    "version",
    "snapshot",
    "exchanges",
    "starred",
    "missing",
    "last_received",
}
SNAPSHOT_KEYS = {
    "version",
    "instance_id",
    "revision",
    "relay_url",
    "upstream",
    "capture_bodies",
    "history_limit",
    "total_requests",
    "evicted",
    "capture_dropped",
    "requests",
}
SUMMARY_KEYS = {
    "id",
    "started_at_ms",
    "method",
    "path",
    "status",
    "outcome",
    "duration_ms",
    "upstream_headers_ms",
    "request_id",
    "model",
    "request_bytes",
    "response_bytes",
    "note",
}
EXCHANGE_KEYS = {"summary", "request", "response"}
CAPTURE_KEYS = {
    "content_type",
    "encoding",
    "wire_bytes",
    "captured_bytes",
    "complete",
    "json",
    "notes",
}
SUMMARY_OUTCOMES = {"pending", "complete", "disconnected", "upstream_error", "rejected"}
QUESTIONS = {
    "secret": {"type": "bool", "instructions": "Does `text` hold a secret?"},
}


def rows(path: Path, sql: str) -> list[dict[str, Any]]:
    with closing(sqlite3.connect(path)) as store:
        store.row_factory = sqlite3.Row
        return [dict(row) for row in store.execute(sql)]


class Seed:
    """Judge calls written straight to a store, as the registry writes them."""

    def __init__(self, path: Path, machine: str = "mac") -> None:
        self.path = path
        self.machine = machine
        self.count = 0

    def call(
        self,
        outcome: str | None,
        mode: str = "shadow",
        kept: bool = True,
        status: str = "ok",
    ) -> str:
        self.count += 1
        call_id = decision_store.new_id()
        score = {"flag": 0.9, "pass": 0.1, None: None}[outcome]
        row = {
            "id": f"{self.machine}-{self.count:03d}",
            "call_id": call_id,
            "ts": f"2026-11-24T10:{self.count // 60:02d}:{self.count % 60:02d}+00:00",
            "source": "jev",
            "judgment": "demo",
            "question": "secret",
            "mode": mode,
            "status": status,
            "error": None if status == "ok" else "judge offline",
            "outcome": outcome,
            "probability": score,
            "threshold": 0.7,
            "model": "typesafe/jev-1.14.0",
            "question_version": "v1",
            "judgment_version": "j1",
            "repo": "owner/repo",
            "commit_sha": "c" * 40,
            "trigger": "pr",
            "item_kind": "unit",
            "item": f"src/unit{self.count}",
            "machine": self.machine,
            "state_sha256": f"{self.count:064x}",
            "state_kept": int(kept),
        }
        response = (
            {"error": "judge offline"}
            if status != "ok"
            else {"secret": {"type": "bool", "bool": score}}
        )
        payload = {
            "state": {"text": f"unit {self.count}"},
            "questions": QUESTIONS,
            "response": response,
        }
        decision_store.record_decisions([row], payload, self.path)
        return str(row["id"])

    def question(self, labelled_flags: int = 2) -> None:
        decision_store.record_questions(
            [
                {
                    "judgment": "demo",
                    "question": "secret",
                    "question_version": "v1",
                    "approach": "Eval judge",
                    "overlays": "[]",
                    "primitive": "noul",
                    "evidence_unit": "item",
                    "combination": "jev_only",
                    "action": "block",
                    "policy_area": "secrets",
                    "mode": "shadow",
                    "decision": '{"threshold": 0.7}',
                    "precision_target": 0.75,
                    "shadow_exit": json.dumps({"labelled_flags": labelled_flags}),
                    "model": "typesafe/jev-1.14.0",
                    "loaded_at": "2026-11-24T10:00:00+00:00",
                }
            ],
            self.path,
        )


class StoreCase(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.dir = Path(scratch.name)
        self.store = self.dir / "agent/dynaflows/dynaflows.db"
        self.judgments = self.dir / "judgments"
        (self.judgments / "demo").mkdir(parents=True)
        (self.judgments / "demo/thresholds.json").write_text("{}")
        (self.judgments / "demo/label-guide.md").write_text("# Demo guide\n")
        self.seed = Seed(self.store)

    def review(self, *argv: str) -> tuple[int, Any]:
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = command.main([
                "--store",
                str(self.store),
                "--judgments",
                str(self.judgments),
                *argv,
            ])
        return code, json.loads(out.getvalue()) if code == 0 else err.getvalue()

    def labels(self, name: str, document: object) -> Path:
        path = self.dir / name
        path.write_text(json.dumps(document))
        return path


class DecisionStoreTests(StoreCase):
    def test_the_store_is_created_on_first_use_in_wal_mode_and_refuses_unknown_versions(
        self,
    ) -> None:
        with decision_store.opened(self.store) as connection:
            assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
            assert (
                connection.execute("PRAGMA user_version").fetchone()[0]
                == decision_store.schema_version()
                == 1
            )
        with decision_store.opened(self.store) as connection:
            connection.execute("PRAGMA user_version = 2")
        for opener in (decision_store.connect, decision_store.connect_read_only):
            with (
                self.subTest(opener=opener.__name__),
                pytest.raises(decision_store.StoreError, match="schema version 2"),
            ):
                opener(self.store)

    def test_a_store_that_cannot_be_written_raises_a_store_error(self) -> None:
        blocked = self.dir / "file"
        blocked.write_text("not a directory")
        with pytest.raises(
            decision_store.StoreError, match="cannot open the decision store"
        ):
            decision_store.record_decisions([], None, blocked / "dynaflows.db")

    def test_a_decision_context_takes_only_the_known_triggers(self) -> None:
        assert (
            decision_store.DecisionContext("pr", repo="o/r", commit="c").columns()[
                "commit_sha"
            ]
            == "c"
        )
        with pytest.raises(decision_store.StoreError, match="trigger"):
            decision_store.DecisionContext("nightly")

    def test_verdicts_follow_outcome_and_label(self) -> None:
        assert [
            decision_store.verdict(o, label)
            for o, label in (
                ("flag", "1"),
                ("flag", "0"),
                ("pass", "1"),
                ("pass", "0"),
                ("flag", "abstain"),
                ("escalate", "1"),
            )
        ] == [
            "true_flag",
            "false_flag",
            "missed",
            "true_pass",
            None,
            None,
        ]


class CheckLogTests(StoreCase):
    def log(self, findings: list[Finding]) -> str:
        target = cli.Target(self.dir / "repo", self.dir / "config/repos/owner/repo")
        err = StringIO()
        with (
            patch.dict("os.environ", {"PI_CODING_AGENT_DIR": str(self.dir / "agent")}),
            redirect_stderr(err),
        ):
            cli.log_decisions(
                "structure",
                target,
                ["--base", "b" * 40, "--head", "h" * 40],
                findings,
                "prove",
            )
        return err.getvalue()

    def test_a_checker_run_logs_one_flag_per_finding_and_one_pass_per_clean_rule(
        self,
    ) -> None:
        findings = [
            Finding("STRUCT.folder-placement", "src/a.py", "misplaced", "move it"),
            Finding(
                "STRUCT.folder-placement", "src/a.py", "misplaced again", "move it"
            ),
            Finding("STRUCT.folder-placement", "src/b.py", "misplaced", "move it"),
        ]
        assert self.log(findings) == ""
        logged = rows(self.store, "SELECT * FROM decisions ORDER BY judgment, item")
        flags = [
            (row["judgment"], row["item"]) for row in logged if row["outcome"] == "flag"
        ]
        assert flags == [
            ("STRUCT.folder-placement", "src/a.py"),
            ("STRUCT.folder-placement", "src/b.py"),
        ]
        rules = {
            rule for rule, _ in cli.module_rules("guardrails.commands.structure.layout")
        }
        passes = {row["judgment"] for row in logged if row["outcome"] == "pass"}
        assert passes == rules - {"STRUCT.folder-placement"}
        assert {
            (
                row["source"],
                row["question"],
                row["trigger"],
                row["repo"],
                row["commit_sha"],
                row["mode"],
            )
            for row in logged
        } == {("check", "structure", "prove", "owner/repo", "h" * 40, "enforced")}
        assert len({row["call_id"] for row in logged}) == 1

    def test_a_store_failure_is_printed_and_never_raised(self) -> None:
        (self.dir / "agent").write_text("not a directory")
        printed = self.log([])
        assert printed.startswith(f"{cli.STORE_ERROR}cannot open the decision store")

    def test_the_cli_takes_a_leading_log_decisions_trigger(self) -> None:
        assert cli.split_decision_log(["--log-decisions", "prove", "--base", "b"]) == (
            "prove",
            ["--base", "b"],
        )
        assert cli.split_decision_log(["--base", "b"]) == (None, ["--base", "b"])
        for argv in (["--log-decisions"], ["--log-decisions", "weekly"]):
            with (
                self.subTest(argv=argv),
                pytest.raises(cli.UsageError, match="needs a trigger"),
            ):
                cli.split_decision_log(argv)
        assert cli.option_value(["--base", "b", "--head", "h"], "--head") == "h"
        assert cli.option_value(["--head=h"], "--head") == "h"
        assert cli.option_value(["--base", "b"], "--head") is None


class ExportImportTests(StoreCase):
    def test_export_holds_every_unreviewed_flag_and_a_sample_of_passes_without_scores(
        self,
    ) -> None:
        flags = [self.seed.call("flag") for _ in range(3)]
        passes = [self.seed.call("pass") for _ in range(5)]
        self.seed.call("pass", kept=False)
        self.seed.call("flag", mode="enforced")
        self.seed.call(None, status="error")
        code, batch = self.review(
            "export", "demo", "--sample-passes", "2", "--seed", "7"
        )
        assert code == 0, batch
        assert batch["guide"] == "# Demo guide\n"
        ids = [item["label_id"] for item in batch["items"]]
        assert set(flags) <= set(ids)
        assert len(set(ids) & set(passes)) == 2
        assert len(ids) == 5
        item = batch["items"][0]
        assert set(item) == {
            "label_id",
            "question",
            "repo",
            "commit",
            "item_kind",
            "item",
            "machine",
            "state",
        }
        assert item["state"]["text"].startswith("unit ")
        assert (
            self.review("export", "demo", "--sample-passes", "2", "--seed", "7")[1]
            == batch
        ), "a seed repeats the sample"
        enforced = self.review(
            "export", "demo", "--mode", "enforced", "--sample-passes", "0"
        )[1]
        assert len(enforced["items"]) == 1

    def test_import_records_labels_verdicts_eval_data_and_prunes_payloads(self) -> None:
        flag, wrong, passed = (
            self.seed.call("flag"),
            self.seed.call("flag"),
            self.seed.call("pass"),
        )
        council = self.labels(
            "council-batch-1.json",
            [
                {
                    "label_id": flag,
                    "council_label": "1",
                    "rule": "S1",
                    "reason": "a key",
                    "split": False,
                    "votes": {},
                },
                {
                    "label_id": wrong,
                    "council_label": "1",
                    "rule": "S1",
                    "reason": "looked like a key",
                },
                {
                    "label_id": passed,
                    "council_label": "0",
                    "rule": "N1",
                    "reason": "plain",
                },
            ],
        )
        code, summary = self.review("import", str(council))
        assert code == 0, summary
        assert summary == {
            "imported": 3,
            "batch": "council-batch-1",
            "labeller": "council",
            "cases_added": 3,
            "labels_added": 3,
            "payloads_pruned": 3,
        }
        arbiter = self.labels(
            "arbiter-1.json",
            [
                {
                    "label_id": wrong,
                    "label": "0",
                    "rule": "N2",
                    "reason": "a test fixture",
                    "confirmed": "individual",
                }
            ],
        )
        assert self.review("import", str(arbiter), "--labeller", "arbiter")[0] == 0
        decided = {
            row["id"]: row for row in rows(self.store, "SELECT * FROM decisions")
        }
        assert [decided[ident]["verdict"] for ident in (flag, wrong, passed)] == [
            "true_flag",
            "false_flag",
            "true_pass",
        ]
        assert (
            decided[wrong]["review_label"],
            decided[wrong]["review_rule"],
            decided[wrong]["review_batch"],
        ) == ("0", "N2", "arbiter-1")
        assert all(row["state_kept"] == 0 for row in decided.values())
        assert rows(self.store, "SELECT * FROM payloads") == []
        assert [
            (row["labeller"], row["label"])
            for row in rows(
                self.store,
                f"SELECT * FROM reviews WHERE decision_id = '{wrong}' ORDER BY id",
            )
        ] == [
            ("council", "1"),
            ("arbiter", "0"),
        ]
        cases = [
            json.loads(line)
            for line in (self.judgments / "demo/cases.jsonl").read_text().splitlines()
        ]
        labels = [
            json.loads(line)
            for line in (self.judgments / "demo/labels.jsonl").read_text().splitlines()
        ]
        assert [case["case_id"] for case in cases] == [
            f"SR-{flag}",
            f"SR-{wrong}",
            f"SR-{passed}",
        ]
        assert cases[0]["state"] == {"text": "unit 1"}
        assert (
            cases[0]["split"],
            cases[0]["source"],
            cases[0]["provenance"]["machine"],
        ) == ("test", "replay", "mac")
        assert [
            (row["case_id"], row["label"], row["provenance"], row.get("confirmed"))
            for row in labels[-2:]
        ] == [
            (f"SR-{passed}", 0, "agent_reviewed", None),
            (f"SR-{wrong}", 0, "arbiter", "individual"),
        ]
        assert all(row["blind"] for row in labels)
        assert self.review("import", str(arbiter), "--labeller", "arbiter")[0] == 0
        assert (
            len((self.judgments / "demo/labels.jsonl").read_text().splitlines()) == 4
        ), "a re-import adds no duplicate label"

    def test_an_unknown_label_or_decision_imports_nothing(self) -> None:
        flag = self.seed.call("flag")
        for document, message in (
            ([{"label_id": "nope", "council_label": "1"}], "no decision nope"),
            ([{"label_id": flag, "council_label": "maybe"}], "must be one of"),
            ([], "non-empty JSON array"),
        ):
            with self.subTest(message=message):
                code, error = self.review(
                    "import", str(self.labels("bad.json", document))
                )
                assert code == 1
                assert message in error
        assert rows(self.store, "SELECT * FROM reviews") == []
        assert not (self.judgments / "demo/cases.jsonl").exists()

    def test_report_shows_shadow_progress_and_precision_at_the_cutoff(self) -> None:
        self.seed.question(labelled_flags=2)
        right, wrong = self.seed.call("flag"), self.seed.call("flag")
        self.seed.call("flag")
        self.seed.call("pass")
        self.seed.call("flag", mode="enforced")
        council = self.labels(
            "batch.json",
            [
                {"label_id": right, "council_label": "1"},
                {"label_id": wrong, "council_label": "0"},
            ],
        )
        assert self.review("import", str(council))[0] == 0
        code, report = self.review("report", "demo")
        assert code == 0
        assert report["shadow_progress"] == [
            {
                "judgment": "demo",
                "question": "secret",
                "question_version": "v1",
                "shadow_exit": {"labelled_flags": 2},
                "decisions": 4,
                "flags": 3,
                "labelled_flags": 2,
                "true_flags": 1,
                "precision": 0.5,
                "exit_met": True,
            }
        ]
        by_mode = {row["mode"]: row for row in report["questions"]}
        assert (
            by_mode["shadow"]["flags"],
            by_mode["shadow"]["false_flags"],
            by_mode["shadow"]["precision"],
        ) == (3, 1, 0.5)
        assert (
            by_mode["enforced"]["flags"],
            by_mode["enforced"]["precision"],
            by_mode["enforced"]["threshold"],
        ) == (1, None, 0.7)


class InspectorTests(StoreCase):
    def session_files(self, *argv: str) -> list[dict[str, Any]]:
        out = self.dir / "out/demo.jev.json"
        code, written = self.review("inspector", "demo", str(out), *argv)
        assert code == 0, written
        return [
            json.loads(Path(item["path"]).read_text()) for item in written["sessions"]
        ]

    def assert_session_shape(self, session: dict[str, Any]) -> None:
        assert set(session) == SESSION_KEYS
        assert (session["format"], session["version"]) == ("jev-inspector-session", 1)
        assert set(session["snapshot"]) == SNAPSHOT_KEYS
        assert session["snapshot"]["version"] == 1
        requests = session["snapshot"]["requests"]
        ids = {summary["id"] for summary in requests}
        assert len(ids) == len(requests) <= 200
        assert 0 not in ids
        assert {int(key) for key in session["exchanges"]} <= ids
        assert set(session["starred"]) | set(session["missing"]) <= ids
        for summary in requests:
            assert set(summary) == SUMMARY_KEYS
            assert summary["outcome"] in SUMMARY_OUTCOMES
        for key, exchange in session["exchanges"].items():
            assert set(exchange) == EXCHANGE_KEYS
            assert exchange["summary"]["id"] == int(key)
            for capture in (exchange["request"], exchange["response"]):
                assert set(capture) == CAPTURE_KEYS
                assert len(json.dumps(capture)) <= 512 * 1024

    def test_a_session_rebuilds_each_call_s_request_and_response_in_the_inspector_shape(
        self,
    ) -> None:
        flag = self.seed.call("flag")
        self.seed.call("pass", kept=False)
        self.seed.call(None, status="error")
        wrong = self.labels("batch.json", [{"label_id": flag, "council_label": "0"}])
        self.seed.call("flag")
        assert self.review("import", str(wrong))[0] == 0
        [session] = self.session_files()
        self.assert_session_shape(session)
        exchanges = [
            session["exchanges"][str(ident)]
            for ident in sorted(int(key) for key in session["exchanges"])
        ]
        assert len(exchanges) == 4
        assert session["missing"] == [1, 2], (
            "pruned and never-kept payloads are summaries only"
        )
        assert session["starred"] == [1], "a false flag is starred"
        error = exchanges[2]
        assert (error["summary"]["outcome"], error["summary"]["status"]) == (
            "upstream_error",
            None,
        )
        assert error["response"]["json"] == {"ok": False, "error": "judge offline"}
        kept = exchanges[3]
        assert kept["request"]["json"] == {
            "model": "typesafe/jev-1.14.0",
            "state": {"text": "unit 4"},
            "questions": {
                "secret": {"type": "noul", "instructions": "Does `text` hold a secret?"}
            },
        }
        assert kept["response"]["json"] == {
            "ok": True,
            "answers": {"secret": {"type": "noul", "noul": 0.9}},
        }
        assert "secret flag p=0.900 (shadow)" in kept["summary"]["note"]
        [false_flags] = self.session_files("--verdict", "false_flag")
        assert len(false_flags["exchanges"]) == 1

    def test_more_than_200_calls_are_split_across_files(self) -> None:
        for _ in range(201):
            self.seed.call("flag")
        sessions = self.session_files()
        assert [len(session["exchanges"]) for session in sessions] == [200, 1]
        for session in sessions:
            self.assert_session_shape(session)
        assert (self.dir / "out/demo-2.jev.json").is_file()
        assert inspector.part_path(Path("x/out.json"), 3) == Path("x/out-3.json")


class MachineTests(StoreCase):
    def test_the_nuc_store_is_read_alongside_and_its_labelled_decisions_come_home(
        self,
    ) -> None:
        remote = self.dir / "nuc.db"
        nuc = Seed(remote, machine="nuc")
        nuc_flag = nuc.call("flag")
        local_flag = self.seed.call("flag")
        copied: list[tuple[str, Path]] = []

        def fetch(machine: str, destination: Path) -> None:
            copied.append((machine, destination))
            shutil.copy(remote, destination)

        with patch.object(stores, "fetch", fetch):
            code, batch = self.review(
                "--machine", "nuc", "export", "demo", "--sample-passes", "0"
            )
            assert code == 0, batch
            assert {(item["label_id"], item["machine"]) for item in batch["items"]} == {
                (nuc_flag, "nuc"),
                (local_flag, "mac"),
            }
            labels = self.labels(
                "batch.json", [{"label_id": nuc_flag, "council_label": "1"}]
            )
            assert self.review("--machine", "nuc", "import", str(labels))[0] == 0
        assert [machine for machine, _ in copied] == ["nuc", "nuc"]
        home = rows(self.store, f"SELECT * FROM decisions WHERE id = '{nuc_flag}'")
        assert [(row["machine"], row["verdict"]) for row in home] == [
            ("nuc", "true_flag")
        ]
        assert rows(
            remote, f"SELECT review_label FROM decisions WHERE id = '{nuc_flag}'"
        ) == [{"review_label": None}], "the NUC store is only read"

    def test_a_failed_copy_stops_the_review(self) -> None:
        def fetch(machine: str, destination: Path) -> None:
            raise stores.ReviewError(
                f"cannot copy the {machine} decision store: Permission denied"
            )

        with patch.object(stores, "fetch", fetch):
            code, error = self.review("--machine", "nuc", "report")
        assert code == 1
        assert "Permission denied" in error

    def test_the_copy_runs_a_read_only_serialize_over_batch_mode_ssh(self) -> None:
        completed = type(
            "Completed",
            (),
            {"returncode": 0, "stdout": b"SQLite format 3\x00", "stderr": b""},
        )
        destination = self.dir / "copy.db"
        with patch.object(subprocess, "run", return_value=completed) as run:
            stores.fetch("nuc", destination)
        argv = run.call_args.args[0]
        assert argv[:4] == ["ssh", "-o", "BatchMode=yes", "codex-omarchy-nuc"]
        assert "mode=ro" in argv[4]
        assert "serialize()" in argv[4]
        assert "~/.omp/agent/dynaflows/dynaflows.db" in argv[4]
        assert destination.read_bytes().startswith(b"SQLite format 3")


if __name__ == "__main__":
    unittest.main()
