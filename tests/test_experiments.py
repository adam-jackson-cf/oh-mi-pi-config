import importlib.util
import json
import sys
from concurrent.futures import Future
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

pytestmark = pytest.mark.fast

EXPERIMENTS = (
    Path(__file__).resolve().parents[1] / "agent/skills/omp-config-optimise/experiments"
)


def load(relative: str) -> ModuleType:
    path = EXPERIMENTS / relative
    sys.path.insert(0, str(path.parent))
    try:
        spec = importlib.util.spec_from_file_location(path.stem, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[path.stem] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(path.parent))


toolset = load("toolset.py")
aggregate = load("fixed-plan-effort/aggregate.py")
run_trials = load("fixed-plan-effort/run_trials.py")
cases = load("review-detection/cases.py")
analyze = load("review-detection/analyze.py")
run_reviews = load("review-detection/run_reviews.py")
run_vision = load("vision-reading/run_vision.py")

FIND = {"findAvailable": True, "configHash": "abcdef1234567890"}


def assistant(text: str, **usage: Any) -> str:
    return json.dumps({
        "type": "message_end",
        "message": {
            "role": "assistant",
            "provider": "openai-codex",
            "model": "gpt-6-sol",
            "stopReason": "stop",
            "usage": {"cost": {"total": 0.5}, **usage},
            "content": [
                {"type": "text", "text": text},
                {"type": "toolCall", "name": "read"},
            ],
        },
    })


def finished(value: dict[str, Any]) -> Future[dict[str, Any]]:
    future: Future[dict[str, Any]] = Future()
    future.set_result(value)
    return future


def failed() -> Future[dict[str, Any]]:
    future: Future[dict[str, Any]] = Future()
    future.set_exception(RuntimeError("workspace copy failed"))
    return future


def write_results(out: Path, records: list[dict[str, Any]]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in records) + "\n"
    )


class TestToolset:
    def test_find_is_available_only_with_a_judge_and_not_switched_off(self) -> None:
        assert toolset.find_available("auto", "judge-model")
        assert not toolset.find_available("off", "judge-model")
        assert not toolset.find_available("on", None)

    def test_config_hash_ignores_credentials_and_reads_resolved_values(self) -> None:
        base = {"find.enabled": {"value": "on"}, "theme": "dark"}
        rotated = {**base, "auth.token": {"value": "secret-two"}}
        assert toolset.config_hash(base) == toolset.config_hash(rotated)
        assert toolset.config_hash(base) != toolset.config_hash({
            **base,
            "theme": "light",
        })

    def test_label_reads_the_effective_config_from_a_neutral_directory(self) -> None:
        seen: list[list[str]] = []

        def omp(args: list[str], cwd: str) -> str:
            seen.append(args)
            assert Path(cwd).is_dir()
            return json.dumps({
                "find.enabled": {"value": "auto"},
                "modelRoles": {"value": {"judge": "openai-codex/gpt-6-luna"}},
            })

        label = toolset.toolset_label(omp)
        assert seen == [["config", "list", "--json"]]
        assert label["findAvailable"] is True
        assert label["judgeRole"] == "openai-codex/gpt-6-luna"
        assert len(label["configHash"]) == 64

    def test_labels_group_records_and_warn_when_mixed(self) -> None:
        assert toolset.label_of({}) == toolset.PRE_FIND
        assert toolset.label_of(FIND) == ("find", "abcdef123456")
        assert toolset.label_of({"findAvailable": False}) == ("no-find", "-")
        assert toolset.describe(toolset.PRE_FIND) == "pre-find"
        assert toolset.describe(("find", "abc")) == "find @ config abc"
        assert toolset.warn_if_mixed({("find", "abc")}) is None
        warning = toolset.warn_if_mixed({("find", "abc"), toolset.PRE_FIND})
        assert warning is not None
        assert "find @ config abc, pre-find" in warning


class TestAggregate:
    def test_tables_per_toolset_with_harness_errors(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        trial = {
            "fixture": "py-score-summary",
            "arm": "A-low",
            "checkPassed": True,
            "scopeViolations": [],
            "testsPassed": 7,
            "testsTotal": 7,
            "usage": {"output": 1200, "cost": 0.25},
            "wallSeconds": 30.0,
            "assistantMessages": 4,
        }
        records = [
            {**trial, **FIND},
            {
                **trial,
                "arm": "B-medium",
                "checkPassed": False,
                "scopeViolations": ["x.py"],
                "testsPassed": 3,
                **FIND,
            },
            {
                "fixture": "ts-rate-limiter",
                "arm": "A-low",
                "rep": 2,
                "harnessError": "boom",
                **FIND,
            },
            {**trial, "testsTotal": 0},
        ]
        write_results(tmp_path, records)
        monkeypatch.setattr(sys, "argv", ["aggregate.py", str(tmp_path)])
        aggregate.main()
        out = capsys.readouterr().out
        assert out.startswith("WARNING: results span different toolsets")
        assert "## Toolset: find @ config abcdef123456" in out
        assert "## Toolset: pre-find" in out
        assert (
            "| A-low | 1 | 1/1 (100%) | 1.000 | 0 | 1200 | $0.2500 | 30s | 4 |" in out
        )
        assert "| B-medium | 1 | 0/1 (0%) | 0.429 | 1 |" in out
        assert "| A-low / implementation |" in out
        assert "| ts-rate-limiter / A-low | 0 | – |" in out
        assert "- ts-rate-limiter A-low rep2: boom" in out
        assert "| A-low | 1 | 1/1 (100%) | 0.000 |" in out


class TestRunTrials:
    def test_arm_specs_parse_effort_and_optional_tier(self) -> None:
        assert run_trials.parse_arm("F-fast=low@priority") == run_trials.Arm(
            "F-fast", "low", "priority"
        )
        assert run_trials.parse_arm("A=medium") == run_trials.Arm("A", "medium", None)
        with pytest.raises(Exception, match="NAME=EFFORT"):
            run_trials.parse_arm("=low")

    def test_event_summary_sums_assistant_usage_and_keeps_the_last_text(self) -> None:
        stdout = "\n".join([
            "not json",
            json.dumps({"type": "message_start"}),
            json.dumps({"type": "message_end", "message": {"role": "user"}}),
            assistant("first", input=10, output=5, cacheRead=2),
            assistant("final answer", input=1, output=1),
        ])
        summary = run_trials.summarize_events(stdout)
        assert summary["usage"] == {
            "input": 11,
            "output": 6,
            "cacheRead": 2,
            "cost": 1.0,
        }
        assert summary["assistantMessages"] == 2
        assert summary["toolCalls"] == 2
        assert summary["stopReasons"] == ["stop"]
        assert summary["finalText"] == "final answer"

    def test_check_score_reads_the_last_json_line(self) -> None:
        assert run_trials.check_score(
            '{"passed": 1, "total": 2}\nnoise\n{"passed": 3, "total": 4}\nok'
        ) == {"passed": 3, "total": 4}
        assert run_trials.check_score("no json at all") == {"passed": 0, "total": 0}

    def test_a_failed_job_is_recorded_not_dropped(self) -> None:
        job = {"fixture": "f", "arm": "A", "rep": 0}
        assert run_trials.outcome(finished({"ok": True}), job) == {"ok": True}
        assert run_trials.outcome(failed(), job) == {
            **job,
            "harnessError": "RuntimeError('workspace copy failed')",
        }


class TestCases:
    def test_every_case_mutation_names_the_plan_steps_it_violates(self) -> None:
        mutation = cases.Mutation("M", "f.py", "a", "b", "3/4: something")
        assert mutation.steps == frozenset({3, 4})
        assert all(m.steps for case in cases.CASES for m in case.mutations)
        assert len({case.id for case in cases.CASES}) == len(cases.CASES)

    def test_a_mutation_returns_its_line_span(self, tmp_path: Path) -> None:
        (tmp_path / "f.py").write_text("one\ntwo\nthree\nfour\n")
        mutation = cases.Mutation("M", "f.py", "two\nthree", "TWO\nTHREE", "1: x")
        assert cases.apply_mutation(tmp_path, mutation) == (2, 3)
        assert (tmp_path / "f.py").read_text() == "one\nTWO\nTHREE\nfour\n"

    @pytest.mark.parametrize(
        ("text", "replace", "message"),
        [
            ("same\nsame\n", "other", "anchor must match exactly once"),
            ("same\n", "two\nlines", "replacement must keep the line count"),
        ],
    )
    def test_a_mutation_refuses_ambiguous_anchors_and_shifted_lines(
        self, tmp_path: Path, text: str, replace: str, message: str
    ) -> None:
        (tmp_path / "f.py").write_text(text)
        with pytest.raises(RuntimeError, match=message):
            cases.apply_mutation(
                tmp_path, cases.Mutation("M", "f.py", "same", replace, "1: x")
            )


def review(case: str, arm: str, rep: int, **fields: Any) -> dict[str, Any]:
    return {
        "runId": f"{case}-{arm}-{rep}",
        "case": case,
        "arm": arm,
        "rep": rep,
        "model": "openai-codex/gpt-6-sol",
        "thinking": "high",
        "defects": {},
        "findings": [],
        "usage": {
            "input": 1000,
            "cacheRead": 0,
            "output": 100,
            "cost": 0.01,
            "reasoning": 50,
        },
        "effectiveModels": ["openai-codex/gpt-6-sol"],
        "wallSeconds": 10.0,
        **FIND,
        **fields,
    }


SPAN = {
    "file": "src/sender.ts",
    "start": 10,
    "end": 10,
    "steps": [4],
    "plan_step": "4: x",
}


class TestAnalyze:
    def test_detection_matches_by_line_window_or_plan_step(self) -> None:
        record = review(
            "webhook-m1",
            "G-high",
            0,
            defects={
                "W-a": SPAN,
                "W-b": {**SPAN, "start": 40, "end": 40, "steps": [6]},
            },
            findings=[
                {"file": "./src/sender.ts", "line": 12, "severity": "blocking"},
                {
                    "file": "src/sender.ts",
                    "line": 90,
                    "step": 6,
                    "severity": "blocking",
                },
                {"file": "src/sender.ts", "line": 11, "severity": "minor"},
                {"file": "src/other.ts", "line": 10, "severity": "blocking"},
                "not a finding",
            ],
        )
        scored = analyze.score(record)
        assert scored["detected"] == {"W-a": "line", "W-b": "step"}
        assert scored["fileLevel"] == {"W-a", "W-b"}
        assert scored["unmatched"] == [
            {"file": "src/other.ts", "line": 10, "severity": "blocking"}
        ]
        assert analyze.same_file("lib/src/sender.ts", "src/sender.ts")
        assert not analyze.same_file("", "src/sender.ts")

    def test_credits_follow_the_model_rate_card(self) -> None:
        assert analyze.credits_of(review("c", "A", 0)) == pytest.approx(0.075)
        assert analyze.credits_of(review("c", "A", 0, model="unknown/model")) is None

    def test_report_withholds_the_decision_until_every_trial_is_scored(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        clean_finding = {"file": "src/a.ts", "line": 5, "severity": "blocking"}
        records = [
            review(
                "webhook-m1",
                "G-high",
                0,
                defects={"W-a": SPAN},
                findings=[
                    {
                        "file": "src/sender.ts",
                        "line": 10,
                        "step": 4,
                        "severity": "blocking",
                    }
                ],
            ),
            review("webhook-m1", "P-low", 0, defects={"W-a": SPAN}, findings=None),
            review("webhook-clean", "G-high", 0, findings=[clean_finding]),
            review("webhook-clean", "P-low", 0, failure="exit 1"),
            review(
                "webhook-clean",
                "P-low",
                0,
                findings=[
                    clean_finding,
                    {"file": "src/b.ts", "line": 1, "severity": "blocking"},
                ],
            ),
            review(
                "webhook-m1", "G-high", 1, defects={"W-a": SPAN}, harnessError="boom"
            ),
            review("webhook-clean", "G-high", 1),
            review("webhook-m1", "P-low", 1, defects={"W-a": SPAN}),
            review("webhook-clean", "P-low", 1),
        ]
        write_results(tmp_path, [*records, {**records[0], "findAvailable": False}])
        monkeypatch.setattr(sys, "argv", ["analyze.py", str(tmp_path)])
        analyze.main()
        out = capsys.readouterr().out
        assert "WARNING: results span different toolsets" in out
        assert "| G-high | gpt-6-sol:high | 3/4 | 1 | 1/1 | 0 / 0 / 1 |" in out
        assert "Shared clean-case clusters excluded from FP: 2" in out
        assert "Complete (every arm 4/4 scored): False" in out
        assert "INCOMPLETE" in out
        assert "- P-low: +0 / −1 → -1" in out
        summary = json.loads((tmp_path / "summary.json").read_text())
        assert summary["find @ config abcdef123456"]["G-high"]["detected"] == 1
        assert (tmp_path / "analysis.md").read_text().startswith("WARNING")

    def test_a_trial_whose_events_mention_the_fixture_is_rerun(
        self, tmp_path: Path
    ) -> None:
        record = review("c", "A", 0)
        events = tmp_path / "runs" / record["runId"] / "events.jsonl"
        events.parent.mkdir(parents=True)
        events.write_text("read fixed-plan-effort/fixtures/repo")
        assert analyze.needs_rerun(record, tmp_path)
        assert not analyze.needs_rerun(review("c", "A", 1), tmp_path)


class TestRunReviews:
    def test_arm_specs_split_model_and_effort(self) -> None:
        assert run_reviews.parse_arm(
            "G-high=openai-codex/gpt-6-sol:high"
        ) == run_reviews.Arm("G-high", "openai-codex/gpt-6-sol", "high")
        with pytest.raises(Exception, match="NAME=provider/model:effort"):
            run_reviews.parse_arm("G-high=gpt")

    def test_findings_come_from_the_last_parsable_block(self) -> None:
        texts = [
            '{"findings": [{"line": 1}]}',
            'Review done.\n```json\n{"findings": [{"line": 2}]}\n```',
            '{"findings": "not a list"} and {"findings": broken',
        ]
        assert run_reviews.parse_findings(texts) == [{"line": 2}]
        assert run_reviews.parse_findings(["no json"]) is None

    def test_event_summary_records_usage_texts_and_models(self) -> None:
        stdout = "\n".join([
            "garbage",
            assistant("one", input=3, reasoningTokens=7),
            assistant("", output=2),
        ])
        summary = run_reviews.summarize_events(stdout)
        assert summary["usage"]["reasoning"] == 7
        assert summary["usage"]["input"] == 3
        assert summary["texts"] == ["one"]
        assert summary["effectiveModels"] == ["openai-codex/gpt-6-sol"]

    def test_failures_are_named(self) -> None:
        def invocation(code: int = 0, timed_out: bool = False, stderr: str = "") -> Any:
            return run_reviews.Invocation("", stderr, code, timed_out, 1.0)

        assert run_reviews.failure_of(invocation(timed_out=True), ["x"]) == "timeout"
        assert run_reviews.failure_of(invocation(code=2), ["x"]) == "exit 2"
        assert run_reviews.failure_of(invocation(), []) == "empty reply"
        assert (
            run_reviews.failure_of(invocation(stderr="HTTP 429 Too Many"), ["x"])
            == "rate limit"
        )
        assert run_reviews.failure_of(invocation(), ["x"]) is None
        job = {"case": "c", "arm": "A", "rep": 0}
        assert run_reviews.outcome(failed(), job)["harnessError"].startswith(
            "RuntimeError"
        )


class TestRunVision:
    def test_answers_compare_after_normalisation(self) -> None:
        assert run_vision.normalise(" R1, C2. ") == "r1,c2"
        assert run_vision.normalise("Invoice #AB-123!") == "invoice#ab-123"
        assert run_vision.parse_arm("S=anthropic/claude:low").thinking == "low"
        with pytest.raises(Exception, match="NAME=provider/model:effort"):
            run_vision.parse_arm("S")

    def test_reply_summary_keeps_the_last_text(self) -> None:
        usage, text = run_vision.summarize_reply(
            "\n".join(["x", assistant("a", input=2), assistant("ANSWER: 7", output=1)])
        )
        assert usage == {"input": 2, "output": 1, "cacheRead": 0, "cost": 1.0}
        assert text == "ANSWER: 7"

    def test_analysis_tables_accuracy_cost_and_items(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        base = {
            "model": "openai-codex/gpt-6-sol",
            "thinking": "low",
            "timedOut": False,
            "wallSeconds": 4.0,
            "usage": {"input": 1000, "cacheRead": 0, "output": 100, "cost": 0.02},
        }
        records = [
            {**base, "arm": "G", "item": "bar-01", "correct": True},
            {**base, "arm": "G", "item": "bar-01", "correct": False, "timedOut": True},
            {"arm": "G", "item": "bar-01", "harnessError": "boom"},
            {
                **base,
                "arm": "U",
                "model": "other/model",
                "item": "pie-02",
                "correct": True,
            },
        ]
        (tmp_path / "results.jsonl").write_text(
            "\n".join(json.dumps(r) for r in records)
        )
        run_vision.analyze(tmp_path)
        out = capsys.readouterr().out
        assert "| G | gpt-6-sol:low | 3 | 1/3 | 2 | $0.0200 | 0.075 | 4s |" in out
        assert "| U | model:low | 1 | 1/1 | 0 | $0.0200 | — | 4s |" in out
        assert "| bar-01 | 1 | 0 |" in out
        assert (tmp_path / "analysis.md").exists()
        assert run_vision.outcome(failed(), {"item": "i"})["harnessError"].startswith(
            "RuntimeError"
        )
