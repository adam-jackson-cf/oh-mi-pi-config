import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.fast

ROOT = Path(__file__).resolve().parents[1]
JUDGMENT = ROOT / "agent/extensions/dynaflows/judgments/in-scope"
RULES = {"Y1": 1, "Y2": 1, "Y3": 1, "N1": 0, "N2": 0, "N3": 0, "N4": 0}
SECRET = re.compile(
    r"sk-[A-Za-z0-9_-]{10,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_|xox[abp]-|AKIA[0-9A-Z]{16}"
    r"|eyJ[A-Za-z0-9_-]{20,}|tskey-|BEGIN [A-Z ]*PRIVATE KEY"
    r"|[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z]{2,}"
)


def digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def jsonl(name: str) -> list[dict[str, Any]]:
    lines = (JUDGMENT / name).read_text().splitlines()
    return [json.loads(line) for line in lines if line]


def load() -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    questions = json.loads((JUDGMENT / "questions.json").read_text())
    thresholds = json.loads((JUDGMENT / "thresholds.json").read_text())
    return questions, thresholds, jsonl("cases.jsonl")


def counting_labels() -> dict[str, dict[str, Any]]:
    """Latest human, else latest agent_reviewed label per case (labeling-cases)."""
    best: dict[str, dict[str, Any]] = {}
    for row in jsonl("labels.jsonl"):
        current = best.get(row["case_id"])
        if row["provenance"] == "human" or (
            row["provenance"] == "agent_reviewed"
            and (current is None or current["provenance"] != "human")
        ):
            best[row["case_id"]] = row
    return best


def test_question_is_one_bool_over_the_declared_state() -> None:
    questions, _, cases = load()
    assert questions["version"] == "in-scope@1"
    assert list(questions["questions"]) == ["ask_first"]
    question = questions["questions"]["ask_first"]
    assert question["type"] == "bool"
    assert set(question["criteria"]) == {"true", "false"}
    assert "`work.proposed_action`" in question["instructions"]
    fields = set(questions["state"]["work"])
    assert all(set(case["state"]["work"]) == fields for case in cases)


def test_version_keys_match_questions_state_and_model() -> None:
    questions, thresholds, _ = load()
    model = thresholds["model"]
    assert thresholds["version_key"] == digest({
        "questions": questions["questions"],
        "state": questions["state"],
        "model": model,
    })
    decision = thresholds["decisions"]["ask_first"]
    assert decision["version_key"] == digest({
        "question": questions["questions"]["ask_first"],
        "state": questions["state"],
        "model": model,
    })
    assert thresholds["model_reported"] == model == "typesafe/jev-1.14.0"


def test_cases_have_unique_ids_one_split_per_unit_and_no_secrets() -> None:
    _, _, cases = load()
    ids = [case["case_id"] for case in cases]
    assert len(ids) == len(set(ids))
    assert 60 <= len(ids) <= 100
    splits: dict[str, set[str]] = {}
    for case in cases:
        assert case["source"] == "replay"
        assert case["split"] in {"dev", "test"}
        splits.setdefault(case["unit"], set()).add(case["split"])
        text = json.dumps(case["state"])
        assert not SECRET.search(text), case["case_id"]
        runs = re.findall(r"[A-Za-z0-9+=_]{40,}", text)
        assert all(re.fullmatch(r"[0-9a-f]{40,64}", run) for run in runs), case[
            "case_id"
        ]
    assert all(len(found) == 1 for found in splits.values())


def test_every_case_has_a_blind_council_label_then_an_arbiter_label() -> None:
    _, _, cases = load()
    rows = jsonl("labels.jsonl")
    for case in cases:
        own = [row for row in rows if row["case_id"] == case["case_id"]]
        assert [row["provenance"] for row in own] == ["agent", "agent_reviewed"]
        council, arbiter = own
        assert council["by"] == "innovation-council"
        assert (arbiter["labeller"], arbiter["arbiter"]) == (
            "innovation-council",
            "claude",
        )
        assert arbiter["arbiter_agrees"] == (arbiter["label"] == council["label"])
        if not arbiter["arbiter_agrees"]:
            assert arbiter["note"].startswith("Disagrees with council")
        for row in own:
            assert row["question_id"] == "ask_first"
            assert RULES[row["rule"]] == row["label"], row


def test_scores_cover_every_case_with_its_counting_label() -> None:
    _, thresholds, cases = load()
    labels = counting_labels()
    decision = thresholds["decisions"]["ask_first"]
    with (JUDGMENT / "scores.csv").open() as handle:
        rows = list(csv.DictReader(handle))
    assert sorted(row["case_id"] for row in rows) == sorted(c["case_id"] for c in cases)
    by_case = {case["case_id"]: case for case in cases}
    for row in rows:
        case = by_case[row["case_id"]]
        assert int(row["label"]) == labels[row["case_id"]]["label"]
        assert 0 <= float(row["score"]) <= 1
        assert row["model"] == thresholds["model"]
        assert row["question_version"] == f"ask_first@{decision['version_key']}"
        assert (row["split"], row["unit"], row["stratum"]) == (
            case["split"],
            case["unit"],
            case["stratum"],
        )


def test_label_set_summary_matches_the_counting_labels() -> None:
    _, thresholds, cases = load()
    labels = counting_labels()
    counts = {"dev": {"yes": 0, "no": 0}, "test": {"yes": 0, "no": 0}}
    for case in cases:
        counts[case["split"]]["yes" if labels[case["case_id"]]["label"] else "no"] += 1
    summary = thresholds["decisions"]["ask_first"]["label_set"]
    assert summary["dev"] == counts["dev"]
    assert summary["test"] == counts["test"]
    assert summary["yes"] == counts["dev"]["yes"] + counts["test"]["yes"]
    assert summary["no"] == counts["dev"]["no"] + counts["test"]["no"]
    assert thresholds["label_set"]["cases"] == len(cases)


def test_the_enforced_threshold_is_the_former_band_yes_side() -> None:
    _, thresholds, _ = load()
    decision = thresholds["decisions"]["ask_first"]
    assert "status" not in thresholds
    assert "status" not in decision
    assert thresholds["wired"] is True
    assert "scopeReason" in thresholds["wiring"]
    assert "ADR 0021" in thresholds["wiring"]
    assert thresholds["score_space"] == "raw"
    band = decision["measured_band"]
    assert (
        0 <= band["no_threshold"] < band["yes_threshold"] == decision["threshold"] <= 1
    )
    assert band["adopted"] is False
    assert "ADR 0021" in band["adopted_by"]
    assert band["why_not"]
    for split in ("dev", "test"):
        assert 0 <= band[split]["escalation_rate"] <= 1
    assert thresholds["shortfalls"]
