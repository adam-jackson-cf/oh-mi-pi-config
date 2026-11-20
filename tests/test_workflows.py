"""Consumer boundaries for frozen proofs (red at base, green at head), isolated executions and the prove() result."""

import copy
import hashlib
import importlib.util
import itertools
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Never
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.fast

MODULE = (
    Path(__file__).resolve().parents[1]
    / "agent/extensions/dynaflows/engine/workflows.py"
)

spec = importlib.util.spec_from_file_location("workflows", MODULE)

assert spec is not None

assert spec.loader is not None

workflow = importlib.util.module_from_spec(spec)

spec.loader.exec_module(workflow)


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

    def test_gate_policy_uses_the_harness_gate_validation(self) -> None:
        # repo_record.gate_reason owns the schema (tests/test_repo_record.py); gate_policy accepts
        # exactly what it accepts and returns the fields freeze() and prove() consume.
        gate = {
            "schemaVersion": 2,
            "checks": {"commit": ["c"], "format": ["ruff", "format"]},
            "ci": {"requiredContext": "gate", "runner": "ci-gate"},
            "dynaflows": {"mode": "minimal"},
        }
        assert workflow.gate_policy(gate) == {
            "format": ["ruff", "format"],
            "ci": {"requiredContext": "gate", "runner": "ci-gate"},
        }
        for invalid in (
            {k: v for k, v in gate.items() if k != "checks"},
            {**gate, "checks": {"ci": ["c"]}},
            {**gate, "unknownField": 3},
            {**gate, "ownerOnlyPaths": []},
        ):
            with self.subTest(gate=invalid):
                assert workflow.gate_policy(invalid) is None

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
    "agent/extensions/dynaflows/engine/guardrails/data/guardrail-rules.json"
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


class SeverityRubricTests(unittest.TestCase):
    def setUp(self) -> None:
        self.decisions = workflow.severity_thresholds()
        self.advisory = workflow.parse_dependency_audit(ADVISORY)["advisories"][0]
        self.no_fix = workflow.parse_dependency_audit(NO_FIX)["advisories"][0]

    def rate(self, answer: Any, advisory: Any = None, **kwargs: Any) -> Any:
        return workflow.rate_advisory(
            advisory or self.advisory, evidence(**kwargs), answer, self.decisions
        )

    def test_thresholds_belong_to_the_current_questions(self) -> None:
        assert json.loads((workflow._severity_dir() / "thresholds.json").read_text())[
            "version_key"
        ] == workflow.severity_version_key("typesafe/jev-1.14")
        assert set(self.decisions) == {"impact", *workflow.PRECONDITIONS, "reached"}
        thresholds = json.loads(
            (workflow._severity_dir() / "thresholds.json").read_text()
        )
        provenance = thresholds["score_provenance"]
        questions, state = workflow.severity_questions()
        source_questions = copy.deepcopy(questions)
        for ident in (*workflow.PRECONDITIONS, "reached"):
            source_questions[ident]["type"] = "noul"
        source_payload = json.dumps(
            {
                "questions": source_questions,
                "state": state,
                "model": thresholds["model"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        assert (
            hashlib.sha256(source_payload.encode()).hexdigest()
            == provenance["source_version_key"]
        )
        assert (
            hashlib.sha256(
                (workflow._severity_dir() / "scores.csv").read_bytes()
            ).hexdigest()
            == provenance["scores_sha256"]
        )

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


MALICIOUS = (
    "SECURITY.dependency-audit lock=package-lock.json ecosystem=npm package=eslint-scope version=3.7.2 "
    "advisory=GHSA-hxxf-q3w9-4xgw aliases=MAL-GHSA-hxxf-q3w9-4xgw fix=3.7.3"
)


class MaliciousPackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.decisions = workflow.severity_thresholds()

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

        import asyncio

        found = workflow.parse_dependency_audit(MALICIOUS)
        key = workflow.advisory_key(found["advisories"][0])
        with patch.object(workflow, "judge", judge, create=True):
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


class TriageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.found = workflow.parse_dependency_audit(
            "\n".join([
                ADVISORY,
                ADVISORY.replace(
                    "PyYAML version=5.3.1", "black version=23.12.1"
                ).replace("GHSA-8q59-q68h-6hv4", "PYSEC-B"),
                "SECURITY.dependency-audit failure=auditor path=uv.lock reason=auditor exited 2",
            ])
        )
        self.keys = [workflow.advisory_key(a) for a in self.found["advisories"]]
        self.calls: list[Any] = []

    def run_triage(self, replies: dict[str, Any], evidence_map: dict[str, Any]) -> Any:
        async def judge(state: Any, questions: dict[str, Any]) -> Any:
            # Exercise the real frozen questions at the judge() boundary, not just the rubric.
            # Contract: xd://eval/judge; TypeSafe's wire name 'noul' is not accepted here.
            frozen = json.loads(
                (workflow._severity_dir() / "questions.json").read_text()
            )["questions"]
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

        import asyncio

        with patch.object(workflow, "judge", judge, create=True):
            return asyncio.run(workflow.triage_advisories(self.found, evidence_map))

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
        assert state["advisory"]["text"] == "arbitrary code execution through full_load"

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
