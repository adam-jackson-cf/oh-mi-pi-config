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
JUDGMENT = ROOT / "agent/extensions/dynaflows/judgments/extension-impact"
TUNER = ROOT / "agent/skills/jev-actions/scripts/tune_threshold.py"


def load_tuner() -> Any:
    spec = importlib.util.spec_from_file_location("extension_impact_tuner", TUNER)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def read_jsonl(name: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (JUDGMENT / name).read_text().splitlines()]


def thresholds() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((JUDGMENT / "thresholds.json").read_text())
    return loaded


def questions() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((JUDGMENT / "questions.json").read_text())
    return loaded


def scores() -> list[dict[str, str]]:
    with (JUDGMENT / "scores.csv").open(newline="") as handle:
        return list(csv.DictReader(handle))


def test_version_keys_hash_the_questions_state_and_model() -> None:
    document, saved = questions(), thresholds()
    assert list(document["questions"]) == ["impact"]
    assert document["questions"]["impact"]["type"] == "bool"
    assert saved["model"] == "typesafe/jev-1.14.0"
    assert saved["version_key"] == digest({
        "questions": document["questions"],
        "state": document["state"],
        "model": saved["model"],
    })
    assert saved["decisions"]["impact"]["version_key"] == digest({
        "question": document["questions"]["impact"],
        "state": document["state"],
        "model": saved["model"],
    })


def test_every_case_has_one_arbitrated_council_label() -> None:
    cases = read_jsonl("cases.jsonl")
    labels = read_jsonl("labels.jsonl")
    versions = thresholds()["label_guide"]
    guide = versions["labelled_version"]
    assert (
        versions["current_version"]
        == hashlib.sha256((JUDGMENT / "label-guide.md").read_bytes()).hexdigest()
    )
    assert [label["case_id"] for label in labels] == sorted(
        case["case_id"] for case in cases
    )
    for label in labels:
        assert label["label"] in (0, 1)
        assert (label["labeller"], label["arbiter"]) == ("innovation-council", "claude")
        assert label["provenance"] == "agent_reviewed"
        assert label["guide_version"] == guide
        assert label["rule"][:1] in ("Y", "N", "T")


def test_cases_hold_the_declared_state_and_split_by_unit() -> None:
    cases = read_jsonl("cases.jsonl")
    shape = set(questions()["state"])
    splits: dict[str, set[str]] = {}
    for case in cases:
        assert set(case["state"]) == shape
        assert case["stratum"] == case["state"]["mode"]["name"] == "structured"
        assert "dynaflows" not in case["state"]["record"]["gate"]
        assert case["unit"] == case["state"]["change"]["head"][:12]
        splits.setdefault(case["unit"], set()).add(case["split"])
    assert all(len(split) == 1 for split in splits.values())
    assert {next(iter(split)) for split in splits.values()} == {"dev", "test"}


def test_scores_match_the_cases_labels_and_pinned_model() -> None:
    cases = {case["case_id"]: case for case in read_jsonl("cases.jsonl")}
    labels = {label["case_id"]: label["label"] for label in read_jsonl("labels.jsonl")}
    saved = thresholds()
    version = "impact@" + saved["decisions"]["impact"]["version_key"]
    rows = scores()
    assert sorted(row["case_id"] for row in rows) == sorted(cases)
    for row in rows:
        case = cases[row["case_id"]]
        assert int(row["label"]) == labels[row["case_id"]]
        assert 0 <= float(row["score"]) <= 1
        assert (row["model"], row["question_version"]) == (saved["model"], version)
        assert (row["split"], row["unit"], row["stratum"]) == (
            case["split"],
            case["unit"],
            case["stratum"],
        )


def test_label_set_summary_counts_the_labels() -> None:
    label_set = thresholds()["decisions"]["impact"]["label_set"]
    rows = scores()
    assert label_set["cases"] == len(rows)
    assert label_set["positives"] == sum(int(row["label"]) for row in rows)
    for split in ("dev", "test"):
        part = [row for row in rows if row["split"] == split]
        assert label_set[split]["cases"] == len(part)
        assert label_set[split]["positives"] == sum(int(row["label"]) for row in part)
        assert label_set[split]["units"] == len({row["unit"] for row in part})


def band_outcome(rows: list[dict[str, str]], low: float, high: float) -> dict[str, Any]:
    scored = [(float(row["score"]), int(row["label"])) for row in rows]
    auto_no = [label for score, label in scored if score <= low]
    auto_yes = [label for score, label in scored if score >= high]
    escalated = [label for score, label in scored if low < score < high]
    return {
        "cases": len(scored),
        "auto_no": len(auto_no),
        "auto_no_positives": sum(auto_no),
        "auto_yes": len(auto_yes),
        "auto_yes_negatives": len(auto_yes) - sum(auto_yes),
        "escalated": len(escalated),
        "escalated_positives": sum(escalated),
        "escalation_rate": round(len(escalated) / len(scored), 3),
    }


def test_escalation_band_figures_follow_from_saved_scores() -> None:
    decision = thresholds()["decisions"]["impact"]
    band = decision["escalation_band"]
    low, high = band["no_at_most"], band["yes_at_least"]
    assert low < decision["threshold"] < high
    rows = scores()
    for split in ("dev", "test"):
        part = [row for row in rows if row["split"] == split]
        recorded = {key: band[split][key] for key in band_outcome(part, low, high)}
        assert recorded == band_outcome(part, low, high)
    test_misses = sorted(
        row["case_id"]
        for row in rows
        if row["split"] == "test" and int(row["label"]) and float(row["score"]) <= low
    )
    assert band["test_misses"] == test_misses


def test_band_is_the_narrowest_clean_band_on_dev() -> None:
    band = thresholds()["decisions"]["impact"]["escalation_band"]
    dev = [row for row in scores() if row["split"] == "dev"]
    positives = [float(row["score"]) for row in dev if int(row["label"])]
    negatives = [float(row["score"]) for row in dev if not int(row["label"])]
    assert (
        max(s for s in negatives + positives if s < min(positives))
        == band["no_at_most"]
    )
    assert band["yes_at_least"] > max(negatives)
    assert not [s for s in negatives if max(negatives) < s < band["yes_at_least"]]


def test_shipped_threshold_reproduces_from_saved_scores(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    tuner = load_tuner()
    decision = thresholds()["decisions"]["impact"]
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "tune_threshold.py",
            str(JUDGMENT / "scores.csv"),
            "--min-precision",
            str(decision["minimums"]["precision"]),
            "--min-recall",
            str(decision["minimums"]["recall"]),
            "--beta",
            str(decision["metric"]["beta"]),
            "--repeats",
            "5",
            "--json",
        ],
    )
    code: object = 0
    try:
        tuner.main()
    except SystemExit as exit_:
        code = exit_.code
    assert code in (0, None)
    report = json.loads(capsys.readouterr().out)[0]
    assert report["shipped_threshold"] == decision["threshold"]
    assert (
        report["test_at_shipped"]["recall"]
        == decision["results"]["tuned"]["test"]["recall"]
    )
    assert decision["status"] == "provisional"
