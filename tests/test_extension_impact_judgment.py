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
JUDGMENT = ROOT / "agent/extensions/agent-rails/judgments/extension-impact"
TUNER = (
    ROOT / "agent/extensions/agent-rails/skills/jev-actions/scripts/tune_threshold.py"
)
LABEL_CHECK = (
    ROOT / "agent/extensions/agent-rails/skills/jev-actions/scripts/check_labels.py"
)
# The config sections the rails send extension-impact (judged-rails.ts IMPACT_SECTIONS).
IMPACT_SECTIONS = {
    "autonomy",
    "ci",
    "gates",
    "judgments",
    "mergePolicies",
    "afterMerge",
    "commands",
}


def load_script(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_tuner() -> Any:
    return load_script("extension_impact_tuner", TUNER)


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


def test_every_case_has_one_arbitrated_council_label_under_the_current_key() -> None:
    cases = read_jsonl("cases.jsonl")
    labels = read_jsonl("labels.jsonl")
    saved = thresholds()
    versions = saved["label_guide"]
    guide = versions["labelled_version"]
    key = saved["decisions"]["impact"]["version_key"]
    unblind = set(saved["council_queue"]["no_blind_label"]["cases"])
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
        assert (label["question_id"], label["question_version"]) == ("impact", key)
        assert label["provenance"] == "agent_reviewed"
        assert label["blind"] is (label["case_id"] not in unblind)
        assert label["guide_version"] == guide
        assert label["rule"][:1] in ("Y", "N", "T")
    assert {label["case_id"] for label in labels if "owner_ruling" in label} == {
        "EI049"
    }


def test_labels_pass_the_label_check_under_the_current_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    checker = load_script("extension_impact_label_check", LABEL_CHECK)
    key = thresholds()["decisions"]["impact"]["version_key"]
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "check_labels.py",
            "--cases",
            str(JUDGMENT / "cases.jsonl"),
            "--labels",
            str(JUDGMENT / "labels.jsonl"),
            "--version-key",
            f"impact={key}",
            "--json",
        ],
    )
    with pytest.raises(SystemExit) as exit_:
        checker.main()
    assert exit_.value.code == 0
    assert json.loads(capsys.readouterr().out)["passes"] is True


def test_council_queue_names_labelled_cases_the_one_mode_guide_affects() -> None:
    queue = thresholds()["council_queue"]
    labels = {label["case_id"]: label for label in read_jsonl("labels.jsonl")}
    queued = [
        case
        for part in ("relabel", "no_blind_label", "evidence_review")
        for case in queue[part]["cases"]
    ]
    assert len(queued) == len(set(queued))
    assert set(queued) <= set(labels)
    assert {
        case for case, label in labels.items() if label["rule"] in ("Y5", "T2")
    } == set(queue["relabel"]["cases"])
    assert {case for case, label in labels.items() if not label["blind"]} == set(
        queue["no_blind_label"]["cases"]
    )
    assert all(
        labels[case]["rule"] in ("Y3", "N4")
        for case in queue["evidence_review"]["cases"]
    )


def test_cases_hold_the_config_state_the_rails_send_and_split_by_unit() -> None:
    cases = read_jsonl("cases.jsonl")
    shape = set(questions()["state"])
    assert shape == {"config", "change"}
    splits: dict[str, set[str]] = {}
    for case in cases:
        config = case["state"]["config"]
        assert set(case["state"]) == shape
        assert case["stratum"] == "structured"
        assert case["builder"].startswith("rekey 2026-11-25")
        assert IMPACT_SECTIONS - {"afterMerge"} <= set(config) <= IMPACT_SECTIONS
        assert set(config["autonomy"]) == {"branch", "pr", "merge"}
        assert set(config["autonomy"].values()) <= {"allowed", "ask"}
        assert isinstance(config["ci"]["enabled"], bool)
        assert all(config["gates"][gate] for gate in ("commitChecks", "prove"))
        assert config["gates"]["audit"] == ("audit" in config["commands"])
        assert "profile" not in config["commands"]
        assert case["unit"] == case["state"]["change"]["head"][:12]
        splits.setdefault(case["unit"], set()).add(case["split"])
    assert all(len(split) == 1 for split in splits.values())
    assert {next(iter(split)) for split in splits.values()} == {"dev", "test"}


def test_scores_match_the_cases_labels_and_pinned_model() -> None:
    cases = {case["case_id"]: case for case in read_jsonl("cases.jsonl")}
    labels = {label["case_id"]: label["label"] for label in read_jsonl("labels.jsonl")}
    saved = thresholds()
    measured = saved["decisions"]["impact"]["measured_on"]
    version = measured["question_version"]
    assert version != "impact@" + saved["decisions"]["impact"]["version_key"]
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


def test_measured_band_figures_follow_from_saved_scores() -> None:
    decision = thresholds()["decisions"]["impact"]
    band = decision["measured_band"]
    low, high = band["no_at_most"], band["yes_at_least"]
    assert decision["threshold"] == low < decision["tuned_threshold"] < high
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
    band = thresholds()["decisions"]["impact"]["measured_band"]
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
    assert report["shipped_threshold"] == decision["tuned_threshold"]
    assert (
        report["test_at_shipped"]["recall"]
        == decision["results"]["tuned"]["test"]["recall"]
    )
    assert "status" not in decision
