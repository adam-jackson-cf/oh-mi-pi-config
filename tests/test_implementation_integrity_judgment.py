import csv
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.fast

ROOT = Path(__file__).resolve().parents[1]
JUDGMENT = ROOT / "agent/extensions/dynaflows/judgments/implementation-integrity"
TUNER = ROOT / "agent/skills/jev-actions/scripts/tune_threshold.py"
QUESTION = "weakens_verification"
DIFF_CAP = 12000


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def jsonl(name: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (JUDGMENT / name).read_text().splitlines()]


@pytest.fixture(scope="module")
def data() -> dict[str, Any]:
    with (JUDGMENT / "scores.csv").open(newline="") as handle:
        scores = list(csv.DictReader(handle))
    return {
        "questions": json.loads((JUDGMENT / "questions.json").read_text()),
        "thresholds": json.loads((JUDGMENT / "thresholds.json").read_text()),
        "cases": {row["case_id"]: row for row in jsonl("cases.jsonl")},
        "labels": jsonl("labels.jsonl"),
        "scores": scores,
    }


def test_version_keys_bind_questions_state_and_model(data: dict[str, Any]) -> None:
    questions, thresholds = data["questions"], data["thresholds"]
    model = thresholds["model"]
    assert model == "typesafe/jev-1.14.0"
    assert set(questions["questions"]) == {QUESTION} == set(thresholds["decisions"])
    assert thresholds["version_key"] == digest({
        "questions": questions["questions"],
        "state": questions["state"],
        "model": model,
    })
    decision = thresholds["decisions"][QUESTION]
    key = digest({
        "question": questions["questions"][QUESTION],
        "state": questions["state"],
        "model": model,
    })
    assert decision["version_key"] == key
    assert {row["question_version"] for row in data["scores"]} == {f"{QUESTION}@{key}"}
    assert {row["model"] for row in data["scores"]} == {model}


def test_cases_are_frozen_commits_split_by_unit(data: dict[str, Any]) -> None:
    cases = data["cases"]
    assert 60 <= len(cases) <= 100
    units: dict[str, set[str]] = {}
    for case in cases.values():
        commit = case["state"]["commit"]
        assert case["source"] == "replay"
        assert set(commit) == {"message", "files", "diff", "diff_truncated"}
        assert len(commit["diff"]) <= DIFF_CAP
        assert commit["diff_truncated"] == (len(commit["diff"]) == DIFF_CAP)
        units.setdefault(case["unit"], set()).add(case["split"])
    assert len(units) == len(cases)
    assert all(splits in ({"dev"}, {"test"}) for splits in units.values())


def test_each_case_has_one_arbitrated_council_label(data: dict[str, Any]) -> None:
    guide = hashlib.sha256((JUDGMENT / "label-guide.md").read_bytes()).hexdigest()
    rules = (JUDGMENT / "label-guide.md").read_text()
    labels = data["labels"]
    assert sorted(row["case_id"] for row in labels) == sorted(data["cases"])
    for row in labels:
        assert row["question_id"] == QUESTION
        assert row["label"] in (0, 1)
        assert row["provenance"] == "agent_reviewed"
        assert (row["labeller"], row["arbiter"]) == ("innovation-council", "claude")
        assert row["guide_version"] == guide
        assert f"- {row['rule']}. " in rules
        assert row["label"] == (1 if row["rule"].startswith("Y") else 0)
        assert row["note"]


def test_scores_match_cases_and_counting_labels(data: dict[str, Any]) -> None:
    labels = {row["case_id"]: row["label"] for row in data["labels"]}
    rows = data["scores"]
    assert sorted(row["case_id"] for row in rows) == sorted(data["cases"])
    for row in rows:
        case = data["cases"][row["case_id"]]
        assert row["question_id"] == QUESTION
        assert int(row["label"]) == labels[row["case_id"]]
        assert (row["split"], row["unit"], row["stratum"]) == (
            case["split"],
            case["unit"],
            case["stratum"],
        )
        assert 0 <= float(row["score"]) <= 1


def test_thresholds_report_the_measured_label_set_and_band(
    data: dict[str, Any],
) -> None:
    thresholds = data["thresholds"]
    decision = thresholds["decisions"][QUESTION]
    assert thresholds["status"] == decision["status"] == "provisional"
    assert decision["threshold"] is None
    rows = [
        (row["split"], int(row["label"]), float(row["score"])) for row in data["scores"]
    ]
    label_set = thresholds["label_set"]
    for split in ("dev", "test"):
        assert label_set[split] == sum(1 for s, _, _ in rows if s == split)
        assert label_set["positives"][split] == sum(
            label for s, label, _ in rows if s == split
        )
    band = decision["escalation_band"]
    low, high = band["no_below"], band["yes_at_or_above"]
    assert 0 < low < high < 1
    for split in ("dev", "test"):
        part = [(label, score) for s, label, score in rows if s == split]
        no = [label for label, score in part if score < low]
        yes = [label for label, score in part if score >= high]
        measured = band[split]
        assert measured["cases"] == len(part)
        assert (measured["confident_no"], measured["missed_positives"]) == (
            len(no),
            sum(no),
        )
        assert (measured["confident_yes"], measured["false_yes"]) == (
            len(yes),
            len(yes) - sum(yes),
        )
        assert measured["escalated"] == len(part) - len(no) - len(yes)
        assert measured["escalation_rate"] == round(
            measured["escalated"] / len(part), 3
        )


def test_tuner_accepts_the_saved_scores_as_one_comparable_group() -> None:
    spec = importlib.util.spec_from_file_location("tune_threshold", TUNER)
    assert spec is not None
    assert spec.loader is not None
    tune = importlib.util.module_from_spec(spec)
    sys.modules["tune_threshold"] = tune
    spec.loader.exec_module(tune)
    groups = tune.load(str(JUDGMENT / "scores.csv"))
    assert len(groups) == 1
    [(name, cases)] = groups.items()
    tune.check_group(name, cases)
    assert tune.comparable(groups)
    assert len(cases) == 93
