"""Consumer boundaries for frozen proofs (red at base, green at head), isolated executions and the prove() result."""

import asyncio
import copy
import csv
import hashlib
import importlib.util
import io
import itertools
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from typing import Any, Never
from unittest.mock import patch

import pytest
from test_reference_decision_store import (
    no_unclosed_connections as no_unclosed_connections,
)

# A sqlite3 connection left open fails the test where it is freed, and the guard fixture finds the rest.
pytestmark = [
    pytest.mark.fast,
    pytest.mark.filterwarnings("error::ResourceWarning"),
    pytest.mark.filterwarnings("error::pytest.PytestUnraisableExceptionWarning"),
]


@pytest.fixture(autouse=True)
def isolated_decision_store(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every test writes the decision store under a temporary agent dir, never the real one."""
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path_factory.mktemp("agent")))


MODULE = (
    Path(__file__).resolve().parents[1]
    / "agent/extensions/agent-rails/engine/workflows.py"
)

spec = importlib.util.spec_from_file_location("workflows", MODULE)

assert spec is not None

assert spec.loader is not None

workflow = importlib.util.module_from_spec(spec)

spec.loader.exec_module(workflow)

READ_JUDGE_MODEL = workflow._configured_judge_model
CONFIG = Path(__file__).resolve().parents[1] / "agent/config.yml"
CONFIGURED_JUDGE = next(
    line.split(":", 1)[1].strip()
    for line in CONFIG.read_text().splitlines()
    if line.startswith("  judge: ")
)


def isolated_agent(case: unittest.TestCase) -> Path:
    """A temporary omp agent dir for the decision store, so no test writes the real one."""
    scratch = tempfile.TemporaryDirectory()
    case.addCleanup(scratch.cleanup)
    patcher = patch.dict(os.environ, {"PI_CODING_AGENT_DIR": scratch.name})
    patcher.start()
    case.addCleanup(patcher.stop)
    return Path(scratch.name)


def stored(
    agent: Path, sql: str = "SELECT * FROM decisions ORDER BY rowid"
) -> list[dict[str, Any]]:
    """Rows of the decision store under `agent`; none before anything is logged."""
    if not (agent / "agent-rails/agent-rails.db").exists():
        return []
    with closing(sqlite3.connect(agent / "agent-rails/agent-rails.db")) as store:
        store.row_factory = sqlite3.Row
        return [dict(row) for row in store.execute(sql)]


class SchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.path = Path(scratch.name) / "manifest.json"
        self.data: dict[str, Any] = {
            "schemaVersion": 1,
            "slug": "valid",
            "base": "a" * 40,
            "proofs": [
                {
                    "id": "P",
                    "criterion": "works",
                    "cmd": [sys.executable, "proof.py"],
                    "files": ["proof.py"],
                    "sha256": {"proof.py": "b" * 64},
                }
            ],
        }

    def load(self, data: dict[str, Any]) -> Any:
        self.path.write_text(json.dumps(data))
        return workflow.load_manifest(self.path, workflow._sha(self.path))

    def test_manifest_invalid_shapes(self) -> None:
        mutations = [
            ("slug", []),
            ("schemaVersion", True),
            ("base", None),
            ("setup", "shell"),
            ("revisions", {}),
            ("revisions", [{"id": [], "reason": "why", "oldSha256": "a" * 64}]),
            ("proofs", None),
            ("proofs", {}),
        ]
        for key, value in mutations:
            with (
                self.subTest(key=key, value=value),
                pytest.raises(workflow.WorkflowError),
            ):
                self.load(dict(self.data, **{key: value}))
        for key, value in (
            ("files", [["unhashable"]]),
            ("files", ["a", "a"]),
            ("cmd", "shell"),
            ("criterion", " "),
            ("sha256", []),
            ("sha256", {"proof.py": 3}),
            ("parent", "HEAD"),
        ):
            data = copy.deepcopy(self.data)
            data["proofs"][0][key] = value
            with (
                self.subTest(key=key, value=value),
                pytest.raises(workflow.WorkflowError),
            ):
                self.load(data)
        self.path.write_text("{")
        with pytest.raises(workflow.WorkflowError):
            workflow.load_manifest(self.path, workflow._sha(self.path))

    def test_zero_proofs_are_valid(self) -> None:
        data = dict(self.data, proofs=[])
        assert self.load(data) == data

    def test_unknown_proof_fields_are_refused_by_name(self) -> None:
        for key, value in (("kind", "defect"), ("expectRed", None), ("note", "x")):
            data = copy.deepcopy(self.data)
            data["proofs"][0][key] = value
            with (
                self.subTest(key=key),
                pytest.raises(
                    workflow.WorkflowError, match=f"P: unknown proof field {key}"
                ),
            ):
                self.load(data)

    def test_unknown_manifest_fields_are_refused_by_name(self) -> None:
        data = dict(self.data, unknownField=2)
        with pytest.raises(
            workflow.WorkflowError, match="unknown manifest field unknownField"
        ):
            self.load(data)

    def test_noncanonical_paths_and_tmp_independence(self) -> None:
        for name in (
            "./proof.py",
            "../proof.py",
            "a//b",
            "a/./b",
            "a/../b",
            "/proof.py",
            "a/",
            "C:proof",
            "a\\b",
            "a/.git/b",
            "a\0b",
            "",
        ):
            data = copy.deepcopy(self.data)
            data["proofs"][0].update(files=[name], sha256={name: "b" * 64})
            with self.subTest(name=name), pytest.raises(workflow.WorkflowError):
                self.load(data)
        with patch.object(
            workflow.tempfile,
            "gettempdir",
            side_effect=AssertionError("lexical validation touched host"),
        ):
            assert self.load(self.data) == self.data

    def test_gate_policy_reads_the_validated_repo_config(self) -> None:
        # repo_config.validate owns the schema (tests/test_repo_config.py); gate_policy returns
        # the fields freeze() and prove() consume from a config it accepted.
        ci = {"enabled": True, "runner": "ci-gate", "requiredContext": "gate"}
        config = {"commands": {"commit": ["c"], "format": ["ruff", "format"]}, "ci": ci}
        assert workflow.gate_policy(config) == {"format": ["ruff", "format"], "ci": ci}
        off = {"commands": {"commit": ["c"]}, "ci": {"enabled": False}}
        assert workflow.gate_policy(off) == {"format": None, "ci": {"enabled": False}}

    def test_journey_flag_must_be_boolean(self) -> None:
        for value in (True, False):
            data = copy.deepcopy(self.data)
            data["proofs"][0]["journey"] = value
            assert self.load(data) == data
        data = copy.deepcopy(self.data)
        data["proofs"][0]["journey"] = "yes"
        with pytest.raises(workflow.WorkflowError, match="journey must be a boolean"):
            self.load(data)

    def test_harness_root_survives_a_later_kernel_load(self) -> None:
        loaded: dict[str, Any] = {"__name__": "kernel"}
        exec(compile(MODULE.read_text(), "<cell>", "exec"), loaded)
        with pytest.raises(loaded["WorkflowError"], match="without __file__"):
            loaded["_harness_root"]()
        # The eval kernel's %load sets __file__ in the shared namespace; a later %load replaces it.
        kernel: dict[str, Any] = {"__name__": "kernel", "__file__": str(MODULE)}
        exec(compile(MODULE.read_text(), str(MODULE), "exec"), kernel)
        kernel["__file__"] = "/elsewhere/other.py"
        assert kernel["_harness_root"]() == Path(__file__).resolve().parents[1]

    def test_workflow_code_holds_no_model_ids_or_hosts(self) -> None:
        text = MODULE.read_text()
        for pattern in (
            r"gpt-\d",
            r"claude-",
            r"sonnet",
            r"opus",
            r"openai-codex",
            r"\bnuc\b",
            r"omarchy",
            r"/Users/",
        ):
            assert re.search(pattern, text, re.IGNORECASE) is None, pattern

    def test_load_safe_no_bang_lines(self) -> None:
        assert re.search(r"^\s*!", MODULE.read_text(), re.MULTILINE) is None


class LabelTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.path = Path(scratch.name) / "batch.json"
        self.path.write_text(json.dumps({"cases": [{"id": "a"}, {"id": "b"}]}))
        self.spawn = patch.object(workflow, "agent", create=True, return_value="job")
        self.agent = self.spawn.start()
        self.addCleanup(self.spawn.stop)
        self.wait_patch = patch.object(workflow, "wait", create=True)
        self.wait = self.wait_patch.start()
        self.addCleanup(self.wait_patch.stop)

    def answer(self, ident: str, label: str = "yes") -> dict[str, str]:
        return {"id": ident, "label": label, "evidence": "quote", "rationale": "reason"}

    def label(self, results: list[Any]) -> Any:
        self.wait.return_value = results
        result = workflow.blind_label([self.path], "rubric", ["yes", "no"])
        json.dumps(result)
        assert sorted(r["id"] for r in result["agreed"] + result["disputed"]) == [
            "a",
            "b",
        ]
        return result

    def test_readonly_selectors_and_json_lists(self) -> None:
        answer = {"cases": [self.answer("a"), self.answer("b")]}
        result = self.label([answer, answer])
        assert len(result["agreed"]) == 2
        assert result["failed"] == []
        assert {c.kwargs["agent"] for c in self.agent.call_args_list} == {
            "scout",
            "reviewer",
        }
        assert "Read only those two files" in self.agent.call_args.args[0]

    def test_disputes_missing_duplicates_and_unknown(self) -> None:
        first = {"cases": [self.answer("a"), self.answer("b")]}
        for second in (
            {"cases": [self.answer("a", "no")]},
            {"cases": [self.answer("a"), self.answer("a"), self.answer("b")]},
            {"cases": [self.answer("unknown")]},
            None,
            RuntimeError("failed"),
            {"cases": [self.answer("a", "invalid")]},
        ):
            result = self.label([first, second])
            assert result["disputed"]
            if (
                not isinstance(second, dict)
                or second.get("cases", [{}])[0].get("id") == "unknown"
            ):
                assert result["failed"]

    def test_inputs_validated_before_spawn(self) -> None:
        for cases in (None, [{"id": ""}], [{"id": "a"}, {"id": "a"}], [{"id": 1}]):
            self.path.write_text(json.dumps(cases))
            with pytest.raises(workflow.WorkflowError):
                workflow.blind_label([self.path], "rubric", ["yes"])
        for selectors in (
            ("scout", "task"),
            ("scout", "scout"),
            ("designer", "reviewer"),
        ):
            with pytest.raises(workflow.WorkflowError):
                workflow.blind_label([], "rubric", ["yes"], selectors)
        with pytest.raises(workflow.WorkflowError):
            workflow.blind_label([self.path] * 9, "rubric", ["yes"])
        self.agent.assert_not_called()


CATALOGUE = Path(__file__).resolve().parents[1] / (
    "agent/extensions/agent-rails/engine/guardrails/data/guardrail-rules.json"
)

ADVISORY = (
    "SECURITY.dependency-audit lock=uv.lock ecosystem=PyPI package=PyYAML version=5.3.1 "
    "advisory=GHSA-8q59-q68h-6hv4 aliases=CVE-2020-14343,PYSEC-2021-142 fix=5.4"
)

NO_FIX = (
    "SECURITY.dependency-audit lock=web/package-lock.json ecosystem=npm package=node-serialize version=0.0.4 "
    "advisory=GHSA-q4v7-4rhw-9hqm aliases= fix="
)

BLOCKED_FIX = {
    "blocked_by": "scanner@4.0",
    "constraint": "scanner==4.0 requires PyYAML<5.4",
    "constraint_source": "https://example.org/scanner/4.0/metadata",
}


def evidence(
    scope: str = "runtime",
    hits: bool = True,
    fix_options: str = "",
    blocked_fix: object = None,
) -> dict[str, Any]:
    return {
        "advisory_text": "arbitrary code execution through full_load",
        "advisory_source": "https://osv.dev/x",
        "scope": scope,
        "scope_source": "pyproject.toml:12",
        "fix_options": fix_options,
        "blocked_fix": blocked_fix,
        "code_search": {
            "queries": ["yaml.full_load"],
            "hits": [{"location": "api/imports.py:88", "line": "yaml.full_load(body)"}]
            if hits
            else [],
        },
    }


# Judge answers for local_only, needs_privileges, needs_interaction and reached unless a case overrides one.
DEFAULT_PRECONDITIONS = {
    "local": 0.02,
    "privileges": 0.02,
    "interaction": 0.2,
    "reached": 0.95,
}


def answers(
    impact: str = "rce", spread: dict[str, float] | None = None, **overrides: float
) -> dict[str, Any]:
    unknown = set(overrides) - set(DEFAULT_PRECONDITIONS)
    if unknown:
        message = f"unknown answer overrides: {sorted(unknown)}"
        raise TypeError(message)
    values = {**DEFAULT_PRECONDITIONS, **overrides}
    probabilities = dict.fromkeys(workflow.IMPACT_LEVEL, 0.0)
    probabilities.update(spread or {impact: 1.0})
    return {
        "impact": {
            "type": "choice",
            "choice": impact,
            "probabilities": probabilities,
            "confidence": 0.9,
        },
        "local_only": {"type": "bool", "bool": values["local"]},
        "needs_privileges": {"type": "bool", "bool": values["privileges"]},
        "needs_interaction": {"type": "bool", "bool": values["interaction"]},
        "reached": {"type": "bool", "bool": values["reached"]},
    }


class DependencyAuditParsingTests(unittest.TestCase):
    def test_advisory_and_failure_lines_parse_by_the_catalogue_contract(self) -> None:
        output = "\n".join([
            "make: entering gate",
            ADVISORY,
            NO_FIX,
            ADVISORY,
            "SECURITY.dependency-audit failure=manifest-without-lock path=tools/package.json reason=dependencies but no lock",
            "test_security.py::test_dependency PASSED mentions SECURITY.dependency-audit mid-line",
        ])
        found = workflow.parse_dependency_audit(output)
        assert found["advisories"] == [
            {
                "lock": "uv.lock",
                "ecosystem": "PyPI",
                "package": "PyYAML",
                "version": "5.3.1",
                "advisory": "GHSA-8q59-q68h-6hv4",
                "aliases": ["CVE-2020-14343", "PYSEC-2021-142"],
                "fix": ["5.4"],
            },
            {
                "lock": "web/package-lock.json",
                "ecosystem": "npm",
                "package": "node-serialize",
                "version": "0.0.4",
                "advisory": "GHSA-q4v7-4rhw-9hqm",
                "aliases": [],
                "fix": [],
            },
        ]
        assert found["failures"] == [
            {
                "kind": "manifest-without-lock",
                "path": "tools/package.json",
                "reason": "dependencies but no lock",
            }
        ]
        assert (
            workflow.advisory_key(found["advisories"][0])
            == "uv.lock:PyYAML@5.3.1:GHSA-8q59-q68h-6hv4"
        )

    def test_off_contract_rule_lines_and_truncated_output_fail_closed(self) -> None:
        reordered = ADVISORY.replace(
            "lock=uv.lock ecosystem=PyPI", "ecosystem=PyPI lock=uv.lock"
        )
        for line in (
            reordered,
            ADVISORY.replace(" fix=5.4", ""),
            ADVISORY + " extra=1",
            ADVISORY.replace(
                "aliases=CVE-2020-14343,PYSEC-2021-142", "aliases=CVE-2020-14343,,X"
            ),
            ADVISORY.replace("version=5.3.1", "version="),
            "SECURITY.dependency-audit",
            "SECURITY.dependency-audit PyYAML 5.3.1 has GHSA-8q59-q68h-6hv4",
            "SECURITY.dependency-audit failure=other path=a reason=b",
            "SECURITY.dependency-audit failure=auditor path=a reason=",
        ):
            with (
                self.subTest(line=line),
                pytest.raises(
                    workflow.WorkflowError, match="SECURITY.dependency-audit"
                ),
            ):
                workflow.parse_dependency_audit(line)
        with pytest.raises(workflow.WorkflowError, match="truncated"):
            workflow.parse_dependency_audit("…" + ADVISORY)

    def test_the_parser_matches_the_catalogue_contract(self) -> None:
        rows = json.loads(CATALOGUE.read_text())["rules"]
        contract = next(
            rule for rule in rows if rule["id"] == workflow.DEPENDENCY_AUDIT
        )["contract"]
        assert list(workflow.ADVISORY_FIELDS) == contract["advisory_fields_in_order"]
        assert list(workflow.AUDIT_FAILURE_KINDS) == contract["failure_kinds"]
        example = re.sub(r"<[^>]+>", "x", contract["advisory_line"])
        assert len(workflow.parse_dependency_audit(example)["advisories"]) == 1


class HarnessCheckLogTests(unittest.TestCase):
    def test_prove_checkers_log_their_results_and_a_store_error_never_blocks(
        self,
    ) -> None:
        outputs = iter([
            (True, "rename-residue: ok\n"),
            (
                False,
                "src/a.py: GATE.path-check: bad name; rename it\ndecision store: locked\n",
            ),
            (True, "structure: ok\ndecision store: cannot open the decision store\n"),
            (True, "responsibility-overlap: ok\n"),
            (True, "prefix-folder: ok\n"),
        ])
        with patch.object(
            workflow, "run_check", side_effect=lambda *a, **k: next(outputs)
        ) as run:
            found, errors = workflow._harness_check_findings(
                Path("/repo"), "p" * 40, "h" * 40
            )
        assert found == ["src/a.py: GATE.path-check: bad name; rename it"]
        assert errors == [
            "path-check: decision store: locked",
            "structure: decision store: cannot open the decision store",
        ]
        checkers = []
        for call in run.call_args_list:
            argv = call.args[0]
            assert argv[argv.index("--log-decisions") + 1] == "prove"
            checkers.append(argv[argv.index("--repo") - 1])
        assert checkers == [
            "rename-residue",
            "path-check",
            "structure",
            "responsibility-overlap",
            "prefix-folder",
        ]


class SeverityRubricTests(unittest.TestCase):
    def setUp(self) -> None:
        isolated_agent(self)
        self.decisions = workflow.load_judgment("dependency-severity").decisions
        self.advisory = workflow.parse_dependency_audit(ADVISORY)["advisories"][0]
        self.no_fix = workflow.parse_dependency_audit(NO_FIX)["advisories"][0]

    def rate(self, answer: Any, advisory: Any = None, **kwargs: Any) -> Any:
        return workflow.rate_advisory(
            advisory or self.advisory, evidence(**kwargs), answer, self.decisions
        )

    def test_thresholds_belong_to_the_current_questions_scores_and_judge_role(
        self,
    ) -> None:
        judgment = workflow.load_judgment("dependency-severity")
        assert set(self.decisions) == {"impact", *workflow.PRECONDITIONS, "reached"}
        assert judgment.model == CONFIGURED_JUDGE
        directory = workflow._harness_root() / workflow.JUDGMENTS / judgment.name
        scores = (directory / "scores.csv").read_bytes()
        assert (
            hashlib.sha256(scores).hexdigest() == judgment.thresholds["scores_sha256"]
        )
        rows = list(csv.DictReader(io.StringIO(scores.decode())))
        assert {row["model"] for row in rows} == {judgment.model}
        assert {row["question_version"] for row in rows} == {
            f"{ident}@{self.decisions[ident]['version_key']}"
            for ident in workflow.PRECONDITIONS + ("reached",)
        }

    def test_the_rubric_spans_every_level_and_both_exposures(self) -> None:
        cases = [
            (answers(), {}, "critical", "owner"),
            (answers(reached=0.1), {}, "high", "owner"),
            (answers(), {"scope": "dev"}, "high", "owner"),
            (answers(local=0.9, reached=0.1), {}, "medium", "fix"),
            (answers(impact="dos"), {}, "medium", "fix"),
            (answers(impact="dos", reached=0.1), {"scope": "dev"}, "low", "fix"),
            (
                answers(impact="info_leak", interaction=0.95, reached=0.05),
                {"scope": "dev"},
                "low",
                "fix",
            ),
        ]
        for answer, kwargs, severity, route in cases:
            for blocker in (None, BLOCKED_FIX):
                with self.subTest(severity=severity, kwargs=kwargs, blocker=blocker):
                    rated = self.rate(answer, **kwargs, blocked_fix=blocker)
                    expected_route = "owner" if blocker is not None else route
                    assert (rated["severity"], rated["route"]) == (
                        severity,
                        expected_route,
                    )
                    if blocker is not None:
                        assert "dated exception" in rated["suggested_fix"]
                        assert rated["dimensions"]["fix"]["blocked_fix"] == blocker

    def test_an_off_precondition_or_reach_never_lowers_the_severity(self) -> None:
        mild = answers(local=0.9, reached=0.1)
        assert self.rate(mild)["severity"] == "medium"
        config = {
            "judgments": {
                f"dependency-severity/{ident}": {"enabled": False}
                for ident in (*workflow.PRECONDITIONS, "reached")
            }
        }
        decisions = workflow.effective_judgment(
            workflow.load_judgment("dependency-severity"), config
        ).decisions
        assert set(decisions) == {"impact"}
        unasked = {"impact": mild["impact"]}
        rated = workflow.rate_advisory(self.advisory, evidence(), unasked, decisions)
        assert rated["severity"] == "critical"
        assert rated["dimensions"]["preconditions"] == {
            "probabilities": {},
            "cleared": [],
            "source": "https://osv.dev/x",
        }
        assert (
            rated["dimensions"]["exposure"]["reached"],
            rated["dimensions"]["exposure"]["unreached"],
        ) == (None, False)

    def test_unclear_evidence_rounds_up(self) -> None:
        assert self.rate(answers(impact="unclear"))["severity"] == "critical"
        spread = self.rate(answers(spread={"dos": 0.6, "rce": 0.35, "info_leak": 0.05}))
        assert (spread["dimensions"]["impact"]["class"], spread["severity"]) == (
            "rce",
            "critical",
        )
        doubtful = self.rate(answers(local=0.6, reached=0.45))
        assert (
            doubtful["dimensions"]["preconditions"]["cleared"],
            doubtful["severity"],
        ) == ([], "critical")

    def test_an_advisory_with_no_fix_always_goes_to_the_owner(self) -> None:
        rated = self.rate(
            answers(impact="dos", reached=0.1),
            advisory=self.no_fix,
            scope="dev",
            fix_options="replace node-serialize with JSON.parse",
        )
        assert (rated["severity"], rated["route"]) == ("low", "owner")
        assert "dated exception" in rated["reasons"][0]
        assert rated["suggested_fix"] == "replace node-serialize with JSON.parse"
        with pytest.raises(workflow.WorkflowError, match="fix_options"):
            self.rate(answers(), advisory=self.no_fix)

    def test_invalid_judgment_answers_or_evidence_raise(self) -> None:
        broken = answers()
        broken["reached"] = {"type": "bool", "bool": "yes"}
        for answer in (
            broken,
            {},
            None,
            {**answers(), "impact": {"probabilities": {"rce": 1.0}}},
        ):
            with self.subTest(answer=answer), pytest.raises(workflow.WorkflowError):
                self.rate(answer)
        bad = evidence()
        bad["scope"] = "test"
        with pytest.raises(workflow.WorkflowError, match="scope"):
            workflow.rate_advisory(self.advisory, bad, answers(), self.decisions)
        bad = {**evidence(), "extra": 1}
        with pytest.raises(workflow.WorkflowError, match="evidence needs"):
            workflow.rate_advisory(self.advisory, bad, answers(), self.decisions)
        for invalid in (
            {},
            {**BLOCKED_FIX, "constraint": ""},
            {**BLOCKED_FIX, "blocked_by": "scanner"},
            {**BLOCKED_FIX, "constraint_source": "http://example.org"},
            {**BLOCKED_FIX, "unexpected": "value"},
        ):
            with self.subTest(blocker=invalid), pytest.raises(workflow.WorkflowError):
                self.rate(answers(), blocked_fix=invalid)
        with pytest.raises(workflow.WorkflowError):
            self.rate(
                answers(),
                advisory=self.no_fix,
                blocked_fix=BLOCKED_FIX,
                fix_options="replace it",
            )


# The decisions columns a Jev answer sets besides its ids, timestamp and payload flag.
DECISION_FIELDS = (
    "source",
    "judgment",
    "mode",
    "status",
    "probability",
    "threshold",
    "model",
    "question_version",
    "judgment_version",
    "state_sha256",
    "trigger",
    "item_kind",
    "item",
    "machine",
)
# A decision's jev-actions taxonomy and mode, as every thresholds.json decision carries them.
TAXONOMY: dict[str, Any] = {
    "approach": "Gate",
    "overlays": ["Face-value"],
    "primitive": "noul",
    "evidence_unit": "item",
    "combination": "jev_only",
    "action": "block",
    "policy_area": "secrets",
    "mode": "enforced",
}


def write_judgment(
    root: Path,
    name: str = "demo",
    model: str = "typesafe/jev-1.14.0",
    flag: dict[str, Any] | None = None,
) -> Path:
    """A judgments/<name>/ folder under `root` with current version keys."""
    directory: Path = root / workflow.JUDGMENTS / name
    directory.mkdir(parents=True)
    state = {"text": "the text judged"}
    questions = {
        "flag": {"type": "bool", "instructions": "Does `text` hold a secret?"},
        "kind": {
            "type": "choice",
            "instructions": "What kind is `text`?",
            "criteria": {"note": "a note", "log": "a log"},
        },
    }
    (directory / "questions.json").write_text(
        json.dumps({"state": state, "questions": questions})
    )
    thresholds = {
        "model": model,
        "score_space": "raw",
        "version_key": workflow._judgment_digest({
            "questions": questions,
            "state": state,
            "model": model,
        }),
        "decisions": {
            "flag": {**TAXONOMY, **(flag or {"threshold": 0.7})},
            "kind": {
                **TAXONOMY,
                "approach": "Route",
                "primitive": "choice",
                "action": "route",
                "round_up_floor": 0.3,
            },
        },
    }
    for ident, question in questions.items():
        thresholds["decisions"][ident]["version_key"] = workflow._judgment_digest({
            "question": question,
            "state": state,
            "model": model,
        })
    (directory / "thresholds.json").write_text(json.dumps(thresholds))
    return directory


class JudgmentRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        # Load the store from the real harness before _harness_root moves to the temp copy: a worker
        # that already imported guardrails from the repository would otherwise refuse the copy.
        workflow._store()
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name) / "harness"
        self.agent = Path(scratch.name) / "agent"
        self.calls: list[Any] = []
        # The repo config at the kernel's or rail's cwd; None is an unconfigured repo. Resolving a
        # real cwd runs git, so the seam tests cover it (test_workflows_seams.JudgmentConfigTests).
        self.repo_config: dict[str, Any] | None = None
        for patcher in (
            patch.object(workflow, "_harness_root", return_value=self.root),
            patch.dict(os.environ, {"PI_CODING_AGENT_DIR": str(self.agent)}),
            patch.object(
                workflow, "_configured_judge_model", return_value="typesafe/jev-1.14.0"
            ),
            patch.object(
                workflow, "judgment_config", side_effect=lambda cwd: self.repo_config
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def decide(self, flag: Any, name: str = "demo") -> Any:
        async def judge(state: Any, questions: Any) -> Any:
            self.calls.append((state, questions))
            if isinstance(flag, Exception):
                raise flag
            return {
                "flag": {"type": "bool", "bool": flag},
                "kind": {"type": "choice", "probabilities": {"note": 0.8, "log": 0.2}},
            }

        context = workflow._store().DecisionContext(
            "manual", item_kind="text", item="a note"
        )
        with patch.object(workflow, "judge", judge, create=True):
            return asyncio.run(workflow.decide(name, {"text": "a note"}, context))

    def logged(self, sql: str = "SELECT * FROM decisions ORDER BY rowid") -> list[Any]:
        return stored(self.agent, sql)

    def test_a_single_threshold_decides_yes_or_no_and_every_call_is_logged(
        self,
    ) -> None:
        write_judgment(self.root)
        for score, expected in (
            (0.7, "yes"),
            (0.95, "yes"),
            (0.69, "no"),
            (0.0, "no"),
        ):
            with self.subTest(score=score):
                decided = self.decide(score)
                assert decided["outcomes"] == {"flag": expected}
                assert decided["probabilities"]["kind"] == {"note": 0.8, "log": 0.2}
        rows = self.logged()
        flags = [row for row in rows if row["question"] == "flag"]
        assert [row["outcome"] for row in flags] == ["flag", "flag", "pass", "pass"]
        assert len({row["call_id"] for row in rows}) == 4
        judgment = workflow.load_judgment("demo")
        assert {key: flags[0][key] for key in DECISION_FIELDS} == {
            "source": "jev",
            "judgment": "demo",
            "mode": "enforced",
            "status": "ok",
            "probability": 0.7,
            "threshold": 0.7,
            "model": "typesafe/jev-1.14.0",
            "question_version": judgment.decisions["flag"]["version_key"],
            "judgment_version": judgment.version_key,
            "state_sha256": workflow._judgment_digest({"text": "a note"}),
            "trigger": "manual",
            "item_kind": "text",
            "item": "a note",
            "machine": socket.gethostname(),
        }
        kind = next(row for row in rows if row["question"] == "kind")
        assert (kind["status"], kind["outcome"], kind["threshold"]) == ("ok", None, 0.3)
        assert json.loads(kind["choice_probabilities"]) == {"note": 0.8, "log": 0.2}
        assert self.calls[0][1] == judgment.questions
        assert (
            workflow._store().store_path() == self.agent / "agent-rails/agent-rails.db"
        )

    def test_a_band_escalates_only_with_a_registered_fallback_judge(self) -> None:
        write_judgment(self.root, "backup")
        band = {"no_at_most": 0.3, "yes_at_least": 0.7, "fallback": "backup"}
        write_judgment(self.root, flag=band)
        for score, expected in ((0.7, "yes"), (0.5, "escalate"), (0.3, "no")):
            with self.subTest(score=score):
                assert self.decide(score)["outcomes"] == {"flag": expected}

    def test_stale_or_malformed_thresholds_are_refused(self) -> None:
        write_judgment(self.root, "backup")
        directory = write_judgment(self.root)
        thresholds = json.loads((directory / "thresholds.json").read_text())
        questions = json.loads((directory / "questions.json").read_text())

        def broken(change: Any) -> dict[str, Any]:
            copied: dict[str, Any] = copy.deepcopy(thresholds)
            change(copied)
            return copied

        def band(document: dict[str, Any], low: float, fallback: str | None) -> None:
            decision = document["decisions"]["flag"]
            del decision["threshold"]
            decision.update(no_at_most=low, yes_at_least=0.7)
            if fallback is not None:
                decision["fallback"] = fallback

        def reword(document: dict[str, Any]) -> None:
            document["questions"]["flag"]["instructions"] += " Really?"

        cases = {
            "stale": broken(lambda t: t.update(model="typesafe/jev-1.13.0")),
            "stale for the current": broken(lambda t: t.update(version_key="0" * 64)),
            "decision flag is stale": broken(
                lambda t: t["decisions"]["flag"].update(version_key="0" * 64)
            ),
            "one decision per question": broken(lambda t: t["decisions"].pop("kind")),
            "needs a threshold in": broken(
                lambda t: t["decisions"]["flag"].update(threshold=1.5)
            ),
            "both a threshold and a band": broken(
                lambda t: t["decisions"]["flag"].update(no_at_most=0.3)
            ),
            "no_at_most < yes_at_least": broken(lambda t: band(t, 0.8, "backup")),
            "band without a fallback judge": broken(lambda t: band(t, 0.3, None)),
            "fallback judge; use": broken(lambda t: band(t, 0.3, "absent")),
            "fallback judge; use a": broken(lambda t: band(t, 0.3, "demo")),
            "without a status": broken(
                lambda t: t["decisions"]["flag"].update(status="ready")
            ),
            "thresholds hold a status": broken(lambda t: t.update(status="ready")),
            "round_up_floor": broken(
                lambda t: t["decisions"]["kind"].update(round_up_floor=True)
            ),
            "raw score space": broken(lambda t: t.update(score_space="platt")),
        }
        for message, document in cases.items():
            with self.subTest(message=message):
                (directory / "thresholds.json").write_text(json.dumps(document))
                with pytest.raises(workflow.WorkflowError, match=message):
                    workflow.load_judgment("demo")
        (directory / "thresholds.json").write_text(json.dumps(thresholds))
        reworded = copy.deepcopy(questions)
        reword(reworded)
        (directory / "questions.json").write_text(json.dumps(reworded))
        with pytest.raises(workflow.WorkflowError, match="stale"):
            workflow.load_judgment("demo")
        for name in ("../demo", "Demo", "", "demo/x"):
            with (
                self.subTest(name=name),
                pytest.raises(workflow.WorkflowError, match="name"),
            ):
                workflow.load_judgment(name)
        with pytest.raises(workflow.WorkflowError, match="cannot read"):
            workflow.load_judgment("absent")

    def test_another_judge_model_failed_or_invalid_answers_never_decide(self) -> None:
        write_judgment(self.root)
        with (
            patch.object(
                workflow,
                "_configured_judge_model",
                return_value="openrouter/typesafe/jev-1.14",
            ),
            pytest.raises(workflow.WorkflowError, match="judge role is openrouter"),
        ):
            self.decide(0.9)
        assert self.calls == []
        with pytest.raises(workflow.WorkflowError, match="judgment failed: offline"):
            self.decide(RuntimeError("offline"))
        for invalid in (1.5, "yes", True, float("nan")):
            with self.subTest(invalid=invalid), pytest.raises(workflow.WorkflowError):
                self.decide(invalid)
        rows = self.logged()
        assert all(row["outcome"] is None for row in rows), "no failed call decides"
        offline = [row for row in rows if row["call_id"] == rows[0]["call_id"]]
        assert {(row["question"], row["status"]) for row in offline} == {
            ("flag", "error"),
            ("kind", "error"),
        }
        assert offline[0]["error"] == "judgment failed: offline"
        invalid_calls = rows[2:]
        assert {(row["question"], row["status"]) for row in invalid_calls} == {
            ("flag", "error"),
            ("kind", "incomplete"),
        }
        assert all(row["state_kept"] == 1 for row in rows), (
            "every error keeps its payload"
        )
        with pytest.raises(workflow.WorkflowError, match="non-empty object"):
            asyncio.run(workflow.decide("demo", {}, None))

    def bridge(self, *argv: str) -> tuple[int, Any]:
        out = io.StringIO()
        with patch("sys.stdout", out):
            code = workflow.rail_bridge(list(argv), self.root)
        return code, json.loads(out.getvalue())

    def test_rail_bridge_asks_then_records_the_host_answers(self) -> None:
        write_judgment(self.root)
        judgment = workflow.load_judgment("demo")
        assert self.bridge("questions", "demo") == (
            0,
            {
                "judgment": "demo",
                "model": judgment.model,
                "questions": judgment.questions,
            },
        )
        request = self.agent.parent / "answers.json"
        answers = {
            "flag": {"type": "bool", "bool": 0.9},
            "kind": {"type": "choice", "probabilities": {"note": 0.6, "log": 0.4}},
        }

        def send(document: object) -> tuple[int, Any]:
            request.write_text(json.dumps(document))
            return self.bridge("record", "demo", str(request))

        context = {"trigger": "commit", "repo": "owner/name", "item": "tree"}
        code, record = send({
            "state": {"text": "a note"},
            "answers": answers,
            "model": "typesafe/jev-1.14.0",
            "context": context,
        })
        assert code == 0
        assert record["outcomes"] == {"flag": "yes"}
        assert record["modes"] == {"flag": "enforced", "kind": "enforced"}
        rows = self.logged()
        assert {row["call_id"] for row in rows} == {record["call_id"]}
        assert {(row["trigger"], row["repo"], row["item"]) for row in rows} == {
            ("commit", "owner/name", "tree")
        }
        refusals = {
            "but 'openrouter/typesafe/jev-1.14' answered": {
                "state": {"text": "a note"},
                "answers": answers,
                "model": "openrouter/typesafe/jev-1.14",
                "context": context,
            },
            "exactly state, answers, model and context": {"state": {"text": "a note"}},
            "non-empty object": {
                "state": {},
                "answers": answers,
                "model": "typesafe/jev-1.14.0",
                "context": context,
            },
            "invalid decision context": {
                "state": {"text": "a note"},
                "answers": answers,
                "model": "typesafe/jev-1.14.0",
                "context": {"trigger": "weekly"},
            },
        }
        for message, document in refusals.items():
            with self.subTest(message=message):
                code, refused = send(document)
                assert code == 1
                assert message in refused["error"]
        assert self.bridge("record", "demo", str(request.parent / "absent.json"))[1][
            "error"
        ].startswith("cannot read rail answers")
        assert self.bridge("decide", "demo")[0] == 1
        request.write_text(
            json.dumps({
                "state": {"text": "a note"},
                "error": "transport closed",
                "context": context,
            })
        )
        code, failed = self.bridge("error", "demo", str(request))
        assert code == 0
        errors = self.logged(
            f"SELECT * FROM decisions WHERE call_id = '{failed['call_id']}'"
        )
        assert {(row["status"], row["error"]) for row in errors} == {
            ("error", "transport closed")
        }
        with patch.dict(os.environ, {"TYPESAFE_BASE_URL": "http://127.0.0.1:8787"}):
            code, refused = self.bridge("questions", "demo")
        assert code == 1
        assert "direct TypeSafe endpoint" in refused["error"]
        assert len({row["call_id"] for row in self.logged()}) == 2

    def test_configured_judge_model_reads_omp_config(self) -> None:
        def completed(stdout: str, code: int = 0) -> Any:
            return subprocess.CompletedProcess([], code, stdout, "")

        good = json.dumps({
            "key": "modelRoles",
            "value": {"judge": "typesafe/jev-1.14.0"},
        })
        with patch.object(
            workflow.subprocess, "run", return_value=completed(good)
        ) as run:
            assert READ_JUDGE_MODEL() == "typesafe/jev-1.14.0"
        assert run.call_args.args[0] == ["omp", "config", "get", "modelRoles", "--json"]
        for stdout, code in (
            ("not json", 0),
            (json.dumps({"value": {}}), 0),
            (json.dumps({"value": {"judge": ""}}), 0),
            (good, 1),
        ):
            with (
                self.subTest(stdout=stdout, code=code),
                patch.object(
                    workflow.subprocess, "run", return_value=completed(stdout, code)
                ),
                pytest.raises(workflow.WorkflowError, match="judge model"),
            ):
                READ_JUDGE_MODEL()
        with (
            patch.object(
                workflow.subprocess, "run", side_effect=FileNotFoundError("omp")
            ),
            pytest.raises(workflow.WorkflowError, match="judge model"),
        ):
            READ_JUDGE_MODEL()

    def test_agent_dir_defaults_to_the_omp_home(self) -> None:
        with patch.dict(os.environ, {"PI_CODING_AGENT_DIR": ""}):
            assert workflow._store().store_path() == (
                Path.home() / ".omp/agent/agent-rails/agent-rails.db"
            )

    def test_every_decision_carries_its_taxonomy_and_mode(self) -> None:
        directory = write_judgment(self.root)
        thresholds = json.loads((directory / "thresholds.json").read_text())

        def changed(ident: str, **change: Any) -> dict[str, Any]:
            document: dict[str, Any] = copy.deepcopy(thresholds)
            decision = document["decisions"][ident]
            for key, value in change.items():
                if value is None:
                    decision.pop(key)
                else:
                    decision[key] = value
            return document

        cases = {
            "needs approach in": changed("flag", approach=None),
            "needs evidence_unit in": changed("flag", evidence_unit="folder"),
            "needs combination in": changed("flag", combination="all"),
            "needs action in": changed("flag", action="warn"),
            "needs mode in": changed("flag", mode="advisory"),
            "needs overlays": changed("flag", overlays=["Face-value", "Face-value"]),
            "needs overlays, a list": changed("flag", overlays="Place"),
            "so its primitive is noul": changed("flag", primitive="choice"),
            "so its primitive is choice": changed("kind", primitive="score"),
            "kebab-case policy_area": changed("flag", policy_area="Naming Rules"),
            "remove its shadow_exit": changed(
                "flag", shadow_exit={"labelled_flags": 5}
            ),
            "needs shadow_exit": changed("flag", mode="shadow"),
            'shadow_exit {"labelled_flags": n}': changed(
                "flag", mode="shadow", shadow_exit={"labelled_flags": 0}
            ),
            "or a choice option threshold has a non-blocking outcome": changed(
                "kind", mode="shadow", shadow_exit={"labelled_flags": 5}
            ),
        }
        for message, document in cases.items():
            with self.subTest(message=message):
                (directory / "thresholds.json").write_text(json.dumps(document))
                with pytest.raises(workflow.WorkflowError, match=re.escape(message)):
                    workflow.load_judgment("demo")

    def test_loading_registers_each_question_version_with_its_taxonomy(self) -> None:
        write_judgment(self.root, flag={"threshold": 0.7, "override": {"by": "owner"}})
        judgment = workflow.load_judgment("demo")
        rows = {row["question"]: row for row in self.logged("SELECT * FROM questions")}
        assert set(rows) == {"flag", "kind"}
        flag = rows["flag"]
        assert {key: flag[key] for key in TAXONOMY if key != "overlays"} == {
            key: value for key, value in TAXONOMY.items() if key != "overlays"
        }
        assert json.loads(flag["overlays"]) == ["Face-value"]
        assert flag["question_version"] == judgment.decisions["flag"]["version_key"]
        assert json.loads(flag["decision"]) == {"threshold": 0.7}
        assert json.loads(flag["override"]) == {"by": "owner"}
        assert flag["shadow_exit"] is None
        assert json.loads(rows["kind"]["decision"]) == {"round_up_floor": 0.3}
        workflow.load_judgment("demo")
        assert len(self.logged("SELECT * FROM questions")) == 2, (
            "a reload replaces its rows"
        )

    def test_payloads_keep_every_flag_and_error_and_a_stable_sample_of_passes(
        self,
    ) -> None:
        write_judgment(self.root)
        store = workflow._store()
        for score in (0.9, 0.1):
            self.decide(score)
        with pytest.raises(workflow.WorkflowError):
            self.decide(RuntimeError("offline"))
        rows = self.logged()
        kept = {row["call_id"] for row in self.logged("SELECT * FROM payloads")}
        for row in rows:
            expected = (
                row["outcome"] == "flag"
                or row["status"] != "ok"
                or (
                    row["outcome"] == "pass"
                    and store.sampled_pass(row["state_sha256"], "demo", row["question"])
                )
            )
            assert row["state_kept"] == int(expected), row
        assert kept == {row["call_id"] for row in rows if row["state_kept"]}
        body = self.logged("SELECT body FROM payloads")[0]["body"]
        assert set(store.decompress(body)) == {"state", "questions", "response"}
        samples = [store.sampled_pass(f"{n:064x}", "demo", "flag") for n in range(2000)]
        assert 150 < sum(samples) < 250, "about one pass in ten keeps its payload"
        assert samples == [
            store.sampled_pass(f"{n:064x}", "demo", "flag") for n in range(2000)
        ]

    def test_a_shadow_decision_is_asked_and_logged_like_an_enforced_one(self) -> None:
        write_judgment(
            self.root,
            flag={
                "threshold": 0.7,
                "mode": "shadow",
                "shadow_exit": {"labelled_flags": 10},
            },
        )
        decided = self.decide(0.9)
        assert decided["outcomes"] == {"flag": "yes"}
        assert decided["modes"] == {"flag": "shadow", "kind": "enforced"}
        flag = next(row for row in self.logged() if row["question"] == "flag")
        assert (flag["mode"], flag["outcome"], flag["state_kept"]) == (
            "shadow",
            "flag",
            1,
        )
        progress = self.logged("SELECT * FROM shadow_progress")
        assert [
            (row["question"], row["decisions"], row["flags"], row["exit_met"])
            for row in progress
        ] == [("flag", 1, 1, 0)]

    def overrides(self, **settings: dict[str, Any]) -> dict[str, Any]:
        """A repo config holding only `judgments`: demo/<question> settings."""
        return {
            "judgments": {f"demo/{ident}": value for ident, value in settings.items()}
        }

    def test_the_effective_mode_is_the_softer_of_global_and_repo(self) -> None:
        shadow_flag = {
            "threshold": 0.7,
            "mode": "shadow",
            "shadow_exit": {"labelled_flags": 10},
        }
        off = {"enabled": False}
        cases = (
            ("enforced", None, "enforced"),
            ("enforced", {"enabled": True, "mode": "enforced"}, "enforced"),
            ("enforced", {"enabled": True, "mode": "shadow"}, "shadow"),
            ("enforced", off, "off"),
            ("shadow", None, "shadow"),
            ("shadow", {"enabled": True, "mode": "shadow"}, "shadow"),
            ("shadow", off, "off"),
        )
        for global_mode, setting, expected in cases:
            with self.subTest(global_mode=global_mode, setting=setting):
                shutil.rmtree(self.root, ignore_errors=True)
                write_judgment(
                    self.root, flag=shadow_flag if global_mode == "shadow" else None
                )
                judgment = workflow.load_judgment("demo")
                config = (
                    self.overrides()
                    if setting is None
                    else self.overrides(flag=setting)
                )
                effective = workflow.effective_judgment(judgment, config)
                if expected == "off":
                    assert "flag" not in effective.decisions
                    assert "flag" not in effective.questions
                else:
                    assert effective.decisions["flag"]["mode"] == expected
                    assert effective.questions["flag"] == judgment.questions["flag"]
                assert effective.decisions["kind"] == judgment.decisions["kind"]
                assert judgment.decisions["flag"]["mode"] == global_mode, (
                    "the global judgment is never changed"
                )
        assert workflow.effective_judgment(judgment, None) is judgment, (
            "no repo config keeps the global modes"
        )
        with pytest.raises(workflow.WorkflowError, match="only measurement promotes"):
            workflow.effective_judgment(
                judgment, self.overrides(flag={"enabled": True, "mode": "enforced"})
            )

    def test_an_off_question_is_never_asked_and_writes_no_row(self) -> None:
        write_judgment(self.root)
        self.repo_config = self.overrides(flag={"enabled": False})
        decided = self.decide(0.9)
        assert decided["outcomes"] == {}
        assert set(decided["modes"]) == {"kind"}
        assert list(self.calls[0][1]) == ["kind"], "the judge is asked the rest only"
        assert {row["question"] for row in self.logged()} == {"kind"}
        self.repo_config = self.overrides(
            flag={"enabled": False}, kind={"enabled": False}
        )
        calls = len(self.calls)
        assert self.decide(0.9) == {"judgment": "demo", "off": True}
        assert len(self.calls) == calls, "an all-off judgment costs no judge call"
        assert len(self.logged()) == 1, "and writes no decisions row"
        assert self.bridge("questions", "demo") == (
            0,
            {"judgment": "demo", "off": True},
        )
        request = self.agent.parent / "off.json"
        request.write_text(
            json.dumps({
                "state": {"text": "a note"},
                "error": "transport closed",
                "context": {"trigger": "commit"},
            })
        )
        code, refused = self.bridge("error", "demo", str(request))
        assert code == 1
        assert "every question is off" in refused["error"]
        assert len(self.logged()) == 1

    def test_the_questions_table_keeps_the_global_mode_under_a_repo_shadow(
        self,
    ) -> None:
        write_judgment(self.root)
        self.repo_config = self.overrides(flag={"enabled": True, "mode": "shadow"})
        decided = self.decide(0.9)
        assert decided["outcomes"] == {"flag": "yes"}
        assert decided["modes"] == {"flag": "shadow", "kind": "enforced"}
        flag = next(row for row in self.logged() if row["question"] == "flag")
        assert (flag["mode"], flag["outcome"]) == ("shadow", "flag")
        questions = {
            row["question"]: row["mode"]
            for row in self.logged("SELECT * FROM questions")
        }
        assert questions == {"flag": "enforced", "kind": "enforced"}
        assert self.bridge("questions", "demo")[1]["questions"] == (
            workflow.load_judgment("demo").questions
        ), "a shadow question is still asked"


class ShippedJudgmentTests(unittest.TestCase):
    def setUp(self) -> None:
        isolated_agent(self)

    def test_the_inspector_route_is_opt_in_and_loopback_only(self) -> None:
        root = Path(__file__).resolve().parents[1]
        overlay = root / "agent/extensions/agent-rails/inspector.env"
        settings = [
            line
            for line in overlay.read_text().splitlines()
            if line.strip() and not line.startswith("#")
        ]
        assert settings == ["TYPESAFE_BASE_URL=http://127.0.0.1:8787"]
        for path in (
            root / "agent/config.yml",
            *sorted((root / "scripts").glob("*.py")),
            *sorted((root / "scripts").glob("*.json")),
            *sorted((root / "scripts").glob("*.sh")),
        ):
            text = path.read_text()
            assert "inspector.env" not in text, path
            assert "TYPESAFE_BASE_URL" not in text, path

    def test_shipped_judgments_load_on_the_configured_judge(self) -> None:
        for name in ("dependency-severity", "structure-naming"):
            with self.subTest(name=name):
                judgment = workflow.load_judgment(name)
                assert judgment.model == CONFIGURED_JUDGE
                assert "status" not in judgment.thresholds
                for decision in judgment.decisions.values():
                    assert "status" not in decision
                directory = workflow._harness_root() / workflow.JUDGMENTS / name
                scores = (directory / "scores.csv").read_bytes()
                assert (
                    hashlib.sha256(scores).hexdigest()
                    == judgment.thresholds["scores_sha256"]
                )
                for row in csv.DictReader(io.StringIO(scores.decode())):
                    ident = row["question_id"]
                    assert row["model"] == judgment.model
                    assert row["question_version"] == (
                        f"{ident}@{judgment.decisions[ident]['version_key']}"
                    )
                    assert row["label"] in ("0", "1")
                    assert 0 <= float(row["score"]) <= 1

    def test_extension_impact_loads_on_the_rekeyed_config_state(self) -> None:
        judgment = workflow.load_judgment("extension-impact")
        assert judgment.model == CONFIGURED_JUDGE
        assert set(judgment.state) == {"config", "change"}
        decision = judgment.decisions["impact"]
        assert decision["mode"] == "enforced"
        directory = workflow._harness_root() / workflow.JUDGMENTS / judgment.name
        scores = (directory / "scores.csv").read_text()
        measured = decision["measured_on"]["question_version"]
        assert measured != f"impact@{decision['version_key']}"
        assert {
            row["question_version"] for row in csv.DictReader(io.StringIO(scores))
        } == {measured}


class StructureProposalTests(unittest.TestCase):
    """structure_review()'s code-proposed pairs and files: scores, modes and decision store rows."""

    def setUp(self) -> None:
        agent = tempfile.TemporaryDirectory(prefix="omp-proposal-agent-")
        self.addCleanup(agent.cleanup)
        environment = patch.dict(os.environ, {"PI_CODING_AGENT_DIR": agent.name})
        environment.start()
        self.addCleanup(environment.stop)
        self.store = Path(agent.name) / "agent-rails/agent-rails.db"
        self.judgment = workflow.load_judgment("structure-naming")
        self.result: dict[str, Any] = {
            "head": "h" * 40,
            "pairs": {},
            "kind_files": {},
            "consumers": {},
            "findings": [],
            "errors": [],
        }
        self.asked: list[Any] = []

    def batches(self, answer: Any) -> Any:
        from types import SimpleNamespace

        asked = self.asked

        def judge_batch(states: Any, questions: Any, **kwargs: Any) -> Any:
            asked.append((states, questions))

            class Batch:
                async def drain_iter(self) -> Any:
                    for key in states:
                        value = answer(key, questions)
                        yield (
                            key,
                            SimpleNamespace(
                                ok=value is not None, error="offline", answers=value
                            ),
                        )

                def close(self) -> None:
                    asked.append("closed")

            return Batch()

        return patch.object(workflow, "judge_batch", judge_batch, create=True)

    def review(self) -> Any:
        return workflow.StructureJudgment(self.judgment, self.result, "owner/repo")

    def rows(self) -> list[dict[str, Any]]:
        import sqlite3

        with closing(sqlite3.connect(self.store)) as connection:
            connection.row_factory = sqlite3.Row
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM decisions WHERE source = 'jev' ORDER BY item"
                )
            ]

    def pair(self, ab: float | None, ba: float | None) -> None:
        values = {"x##ab": ab, "x##ba": ba}

        def answer(key: str, questions: Any) -> Any:
            value = values[key.replace("a.py::x<>b.py::y", "x")]
            return None if value is None else {"same_thing": {"bool": value}}

        pairs = [
            {
                "id": "a.py::x<>b.py::y",
                "det_score": 0.2,
                "state": {"first": {"name": "x"}, "second": {"name": "y"}},
            }
        ]
        with self.batches(answer):
            asyncio.run(self.review().judge_pairs(pairs))

    def test_an_off_question_is_never_asked_and_logs_nothing(self) -> None:
        self.judgment = workflow.effective_judgment(
            self.judgment,
            {"judgments": {"structure-naming/consumer_placement": {"enabled": False}}},
        )
        review = self.review()
        assert not review.asks(workflow.CONSUMER_QUESTION)
        assert review.asks(workflow.PAIR_QUESTION)
        candidates = [
            {"path": "src/orders/dash.py", "state": {"file": {}, "package": {}}}
        ]
        with self.batches(lambda key, questions: None):
            asyncio.run(review.judge_consumers(candidates))
        assert self.asked == []
        assert self.rows() == []
        assert self.result["consumers"] == {}

    def test_a_pair_is_asked_in_both_orders_and_logged_with_its_combined_score(
        self,
    ) -> None:
        self.pair(0.4, 0.2)
        states, questions = self.asked[0]
        assert states["a.py::x<>b.py::y##ba"] == {
            "first": {"name": "y"},
            "second": {"name": "x"},
        }
        assert list(questions) == [workflow.PAIR_QUESTION]
        assert self.result["findings"] == [
            {
                "item": "a.py::x<>b.py::y",
                "question": workflow.PAIR_QUESTION,
                "mode": "enforced",
                "score": 0.06,
                "threshold": 0.051143,
            }
        ]
        (row,) = self.rows()
        assert (row["outcome"], row["item_kind"], row["trigger"]) == (
            "flag",
            "pair",
            "pr",
        )
        assert (row["proposer_score"], row["combined_score"]) == (0.2, 0.06)
        assert row["probability"] == pytest.approx(0.3)

    def test_a_pair_answered_in_one_order_only_is_logged_as_an_error_not_an_outcome(
        self,
    ) -> None:
        self.pair(0.9, None)
        assert self.result["findings"] == []
        assert self.result["errors"] == ["a.py::x<>b.py::y: judgment: offline"]
        (row,) = self.rows()
        assert (row["status"], row["error"], row["outcome"]) == (
            "error",
            "offline",
            None,
        )

    def consumer(self, o1: float | None, o2: float | None) -> None:
        def answer(key: str, questions: Any) -> Any:
            value = o1 if key.endswith("##o1") else o2
            if value is None:
                return None
            options = list(questions["kind"]["criteria"])
            rest = (1 - value) / (len(options) - 1)
            return {
                "kind": {
                    "probabilities": {
                        option: value if option == "finished_tool" else rest
                        for option in options
                    }
                }
            }

        candidates = [{"path": "pkg/dash.py", "state": {"file": {}, "package": {}}}]
        with self.batches(answer):
            asyncio.run(self.review().judge_consumers(candidates))

    def test_a_file_is_asked_in_both_option_orders_and_scored_on_the_mean(self) -> None:
        self.consumer(0.9, 0.7)
        first, second = self.asked[0], self.asked[2]
        criteria = list(self.judgment.questions[workflow.CONSUMER_QUESTION]["criteria"])
        assert list(first[1]["kind"]["criteria"]) == criteria
        assert list(second[1]["kind"]["criteria"]) == list(reversed(criteria))
        assert self.result["findings"][0]["score"] == 0.8
        assert self.result["findings"][0]["mode"] == "shadow"
        (row,) = self.rows()
        assert (row["outcome"], row["item"]) == ("flag", "pkg/dash.py")

    def test_a_file_below_the_threshold_or_judged_once_is_no_finding(self) -> None:
        self.consumer(0.9, 0.6)
        assert self.result["findings"] == []
        assert self.result["consumers"]["pkg/dash.py"]["outcome"] == "no"
        self.consumer(0.95, None)
        assert self.result["errors"] == ["pkg/dash.py: judgment: offline"]

    def test_a_kind_folder_file_is_a_shadow_finding_that_never_halts(self) -> None:
        def answer(key: str, questions: Any) -> Any:
            return {"own_capability": {"bool": 0.85}}

        candidates = [
            {
                "path": "app/models/invoice.py",
                "state": {"file": {}, "folder": "app/models", "importers": []},
            }
        ]
        with self.batches(answer):
            asyncio.run(self.review().judge_kind_files(candidates))
        assert list(self.asked[0][1]) == ["own_capability"]
        (finding,) = self.result["findings"]
        assert (finding["question"], finding["mode"]) == (
            workflow.KIND_QUESTION,
            "shadow",
        )
        workflow._finish_structure(Path("."), "h" * 40, None, self.result)
        assert not self.result["halt"]
        (row,) = self.rows()
        assert (row["mode"], row["outcome"]) == ("shadow", "flag")

    def proposals(self, output: str, ok: bool = True) -> tuple[Any, list[Any]]:
        commands: list[Any] = []

        def run_check(cmd: Any, *args: Any, **kwargs: Any) -> tuple[bool, str]:
            commands.append(cmd)
            return ok, output

        return patch.multiple(workflow, run_check=run_check), commands

    def test_a_proposer_passes_the_repo_only_and_returns_its_json(self) -> None:
        found = {
            "pairs": [
                {"id": "p", "det_score": 0.5, "state": {"first": {}, "second": {}}}
            ]
        }
        patched, commands = self.proposals(json.dumps(found))
        with patched:
            assert workflow._name_pairs(Path("."), "b" * 40, "h" * 40) == found["pairs"]
        (command,) = commands
        assert "--record" not in command
        assert command[command.index("name-pairs") + 1 : command.index("--base")] == [
            "--repo",
            ".",
        ]

    def test_a_failed_proposer_is_a_workflow_error(self) -> None:
        patched, _ = self.proposals("boom", ok=False)
        with (
            patched,
            pytest.raises(workflow.WorkflowError, match="kind-candidates failed: boom"),
        ):
            workflow._kind_candidates(Path("."), "b" * 40, "h" * 40)

    def test_a_proposal_of_the_wrong_shape_is_a_workflow_error(self) -> None:
        readers = {
            "name-pairs": (workflow._name_pairs, "pairs"),
            "consumer-candidates": (workflow._consumer_candidates, "candidates"),
            "kind-candidates": (workflow._kind_candidates, "candidates"),
        }
        shapes = [None, [1], [{"path": "a.py", "id": "a", "det_score": 2, "state": {}}]]
        for subcommand, (reader, key) in readers.items():
            for shape in shapes:
                with self.subTest(subcommand=subcommand, shape=shape):
                    patched, _ = self.proposals(json.dumps({key: shape}))
                    with (
                        patched,
                        pytest.raises(
                            workflow.WorkflowError, match=f"{subcommand} must emit"
                        ),
                    ):
                        reader(Path("."), "b" * 40, "h" * 40)

    def test_a_choice_decision_names_one_of_its_options(self) -> None:
        question = self.judgment.questions[workflow.CONSUMER_QUESTION]
        decision = self.judgment.decisions[workflow.CONSUMER_QUESTION]
        workflow._check_decision("j", "q", question, decision)
        with pytest.raises(workflow.WorkflowError, match="option of the question"):
            workflow._check_decision(
                "j", "q", question, {**decision, "option": "dashboard"}
            )


MALICIOUS = (
    "SECURITY.dependency-audit lock=package-lock.json ecosystem=npm package=eslint-scope version=3.7.2 "
    "advisory=GHSA-hxxf-q3w9-4xgw aliases=MAL-GHSA-hxxf-q3w9-4xgw fix=3.7.3"
)


class MaliciousPackageTests(unittest.TestCase):
    def setUp(self) -> None:
        isolated_agent(self)
        self.decisions = workflow.load_judgment("dependency-severity").decisions

    def advisory(self, line: str) -> Any:
        return workflow.parse_dependency_audit(line)["advisories"][0]

    def test_a_mal_id_or_alias_marks_a_malicious_package(self) -> None:
        assert workflow.malicious_package(self.advisory(MALICIOUS))
        own = MALICIOUS.replace(
            "advisory=GHSA-hxxf-q3w9-4xgw aliases=MAL-GHSA-hxxf-q3w9-4xgw",
            "advisory=MAL-2025-1 aliases=",
        )
        assert workflow.malicious_package(self.advisory(own))
        assert not workflow.malicious_package(self.advisory(ADVISORY))
        assert not workflow.malicious_package(
            self.advisory(MALICIOUS.replace("MAL-GHSA", "GHSA-MAL"))
        )

    def test_malicious_is_critical_for_the_owner_whatever_scope_reach_or_fix(
        self,
    ) -> None:
        mild = answers(impact="info_leak", local=0.9, reached=0.01)
        for line, kwargs in (
            (MALICIOUS, {"scope": "dev", "hits": False}),
            (
                MALICIOUS.replace(" fix=3.7.3", " fix="),
                {"scope": "dev", "hits": False, "fix_options": "drop it"},
            ),
            (MALICIOUS, {"scope": "runtime"}),
        ):
            with self.subTest(line=line, kwargs=kwargs):
                rated = workflow.rate_advisory(
                    self.advisory(line), evidence(**kwargs), mild, self.decisions
                )
                assert (rated["severity"], rated["route"]) == ("critical", "owner")
                assert "malicious package" in rated["reasons"][0]
                assert rated["suggested_fix"].startswith(
                    "remove eslint-scope or replace it"
                )
                assert "rotate every credential" in rated["suggested_fix"]

    def test_the_judgment_is_never_asked_for_a_malicious_package(self) -> None:
        async def judge(state: object, questions: object) -> Never:
            raise AssertionError("judge() must not run for a malicious package")

        found = {**workflow.parse_dependency_audit(MALICIOUS), "repo": "/repo"}
        key = workflow.advisory_key(found["advisories"][0])
        with (
            patch.object(workflow, "judge", judge, create=True),
            patch.object(workflow, "judgment_config", return_value=None),
        ):
            result = asyncio.run(
                workflow.triage_advisories(
                    found, {key: evidence(scope="dev", hits=False)}
                )
            )
        assert result["halt"]
        assert [item["severity"] for item in result["owner"]] == ["critical"]
        for text in (
            "Severity: critical",
            "malicious package",
            "rotate every credential",
            "not judged",
        ):
            assert text in result["report"]

    def test_the_contract_states_the_malicious_mark(self) -> None:
        rows = json.loads(CATALOGUE.read_text())["rules"]
        contract = next(
            rule for rule in rows if rule["id"] == workflow.DEPENDENCY_AUDIT
        )["contract"]
        assert workflow.MALICIOUS_PREFIX in contract["malicious_package_mark"]


def calls(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Decision rows grouped by call, oldest first: judgment, state digest, context and outcomes."""
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        call = grouped.setdefault(
            row["call_id"],
            {
                "judgment": row["judgment"],
                "state_sha256": row["state_sha256"],
                "context": (row["trigger"], row["item_kind"], row["item"]),
                "outcomes": {},
            },
        )
        call["outcomes"][row["question"]] = row["outcome"]
    return list(grouped.values())


class TriageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.found = {
            **workflow.parse_dependency_audit(
                "\n".join([
                    ADVISORY,
                    ADVISORY.replace(
                        "PyYAML version=5.3.1", "black version=23.12.1"
                    ).replace("GHSA-8q59-q68h-6hv4", "PYSEC-B"),
                    "SECURITY.dependency-audit failure=auditor path=uv.lock reason=auditor exited 2",
                ])
            ),
            "repo": "/work/repo",
        }
        self.keys = [workflow.advisory_key(a) for a in self.found["advisories"]]
        self.calls: list[Any] = []
        # The config of found["repo"]; None is an unconfigured repo (resolving it runs git).
        self.repo_config: dict[str, Any] | None = None
        self.resolved: list[Any] = []

    def run_triage(self, replies: dict[str, Any], evidence_map: dict[str, Any]) -> Any:
        async def judge(state: Any, questions: dict[str, Any]) -> Any:
            # Exercise the real frozen questions at the judge() boundary, not just the rubric.
            # Contract: xd://eval/judge; TypeSafe's wire name 'noul' is not accepted here.
            frozen = workflow.effective_judgment(
                workflow.load_judgment("dependency-severity"), self.repo_config
            ).questions
            assert questions == frozen
            for ident, question in questions.items():
                assert question["type"] in ("choice", "bool", "score"), ident
                assert isinstance(question["instructions"], str), ident
                assert question["instructions"].strip(), ident
                if question["type"] == "choice":
                    assert isinstance(question["criteria"], dict), ident
                    assert len(question["criteria"]) >= 2, ident
                elif question["type"] == "score":
                    assert isinstance(question["criteria"], list), ident
                    assert len(question["criteria"]) >= 2, ident
                elif "criteria" in question:
                    assert isinstance(question["criteria"], dict), ident
                    assert set(question["criteria"]) <= {"true", "false"}, ident
            self.calls.append((state, questions))
            reply = replies[state["advisory"]["package"]]
            if isinstance(reply, Exception):
                raise reply
            return reply

        with (
            tempfile.TemporaryDirectory() as agent,
            patch.dict(os.environ, {"PI_CODING_AGENT_DIR": agent}),
            patch.object(
                workflow, "_configured_judge_model", return_value=CONFIGURED_JUDGE
            ),
            patch.object(workflow, "judge", judge, create=True),
            patch.object(workflow, "judgment_config", side_effect=self.resolve),
        ):
            try:
                return asyncio.run(workflow.triage_advisories(self.found, evidence_map))
            finally:
                self.logged = calls(stored(Path(agent)))

    def resolve(self, cwd: object) -> dict[str, Any] | None:
        self.resolved.append(cwd)
        return self.repo_config

    def test_the_repo_config_of_found_sets_the_severity_modes(self) -> None:
        self.repo_config = {
            "judgments": {"dependency-severity/local_only": {"enabled": False}}
        }
        result = self.run_triage(
            {"PyYAML": answers(local=0.9), "black": answers(local=0.9)},
            {key: evidence() for key in self.keys},
        )
        assert self.resolved == ["/work/repo"], "one config read, from found's repo"
        assert all("local_only" not in questions for _, questions in self.calls)
        assert all("local_only" not in call["outcomes"] for call in self.logged)
        assert {item["severity"] for item in result["owner"]} == {"critical"}, (
            "an off precondition never lowers the severity"
        )
        with pytest.raises(workflow.WorkflowError, match="names its repo"):
            asyncio.run(
                workflow.triage_advisories(
                    {key: value for key, value in self.found.items() if key != "repo"},
                    {},
                )
            )

    def test_high_severity_halts_with_an_owner_report_and_low_is_fixed_in_flow(
        self,
    ) -> None:
        result = self.run_triage(
            {"PyYAML": answers(), "black": answers(impact="dos", reached=0.02)},
            {self.keys[0]: evidence(), self.keys[1]: evidence(scope="dev", hits=False)},
        )
        assert result["halt"]
        assert [item["key"] for item in result["owner"]] == [self.keys[0]]
        assert [(item["key"], item["severity"]) for item in result["fix"]] == [
            (self.keys[1], "low")
        ]
        assert result["failures"] == [
            {"kind": "auditor", "path": "uv.lock", "reason": "auditor exited 2"}
        ]
        for text in (
            "GHSA-8q59-q68h-6hv4",
            "CVE-2020-14343",
            "Severity: critical",
            "api/imports.py:88",
            "Suggested fix: upgrade PyYAML from 5.3.1 to a fixed version (5.4)",
            "pyproject.toml:12",
        ):
            assert text in result["report"]
        state, questions = self.calls[0]
        assert set(questions) == {"impact", *workflow.PRECONDITIONS, "reached"}
        assert [record["judgment"] for record in self.logged] == [
            "dependency-severity"
        ] * 2
        assert self.logged[0]["state_sha256"] == workflow._judgment_digest(state)
        assert self.logged[1]["outcomes"]["reached"] == "pass"
        assert [record["context"] for record in self.logged] == [
            ("commit", "advisory", key) for key in self.keys
        ]
        assert state["advisory"]["text"] == "arbitrary code execution through full_load"

    def test_the_inspector_route_never_serves_advisory_triage(self) -> None:
        with (
            patch.dict(os.environ, {"TYPESAFE_BASE_URL": "http://127.0.0.1:8787"}),
            pytest.raises(workflow.WorkflowError, match="TYPESAFE_BASE_URL"),
        ):
            self.run_triage(
                {"PyYAML": answers(), "black": answers()},
                {self.keys[0]: evidence(), self.keys[1]: evidence()},
            )
        assert self.calls == []

    def test_low_or_medium_routes_by_fix_availability(self) -> None:
        ratings = (
            (answers(impact="dos", reached=0.02), "low"),
            (answers(impact="dos"), "medium"),
        )
        for (answer, severity), blocker in itertools.product(
            ratings, (None, BLOCKED_FIX)
        ):
            with self.subTest(severity=severity, blocker=blocker):
                result = self.run_triage(
                    {"PyYAML": answer, "black": answer},
                    {
                        self.keys[0]: evidence(blocked_fix=blocker),
                        self.keys[1]: evidence(),
                    },
                )
                assert result["halt"] == (blocker is not None)
                assert [item["key"] for item in result["owner"]] == (
                    [self.keys[0]] if blocker is not None else []
                )
                assert [item["key"] for item in result["fix"]] == (
                    [self.keys[1]] if blocker is not None else self.keys
                )
                assert all(
                    item["severity"] == severity
                    for item in result["owner"] + result["fix"]
                )
                if blocker is None:
                    assert result["report"] == ""
                else:
                    for text in (
                        *blocker.values(),
                        "dated exception",
                        "Pinned dependent",
                    ):
                        assert text in result["report"]

    def test_exception_file_failures_halt_for_the_owner(self) -> None:
        self.found["failures"].append({
            "kind": "exception",
            "path": "scripts/checks/exceptions.json",
            "reason": "expired",
        })
        low = answers(impact="dos", reached=0.02)
        result = self.run_triage(
            {"PyYAML": low, "black": low}, {key: evidence() for key in self.keys}
        )
        assert result["halt"]
        assert (
            "Exception file failure at scripts/checks/exceptions.json: expired (the owner changes the exception file)"
            in result["report"]
        )
        assert result["owner"] == [], "the low advisories themselves stay in-flow fixes"

    def test_missing_evidence_or_a_failed_judgment_never_passes(self) -> None:
        with pytest.raises(workflow.WorkflowError, match="missing"):
            self.run_triage({}, {self.keys[0]: evidence()})
        with pytest.raises(workflow.WorkflowError, match="severity judgment failed"):
            self.run_triage(
                {"PyYAML": RuntimeError("judge offline"), "black": answers()},
                {key: evidence() for key in self.keys},
            )


if __name__ == "__main__":
    unittest.main()
