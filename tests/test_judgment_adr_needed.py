import csv
import hashlib
import importlib.util
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.fast

ROOT = Path(__file__).resolve().parents[1]
JUDGMENT = ROOT / "agent/extensions/dynaflows/judgments/adr-needed"
TUNER = ROOT / "agent/skills/jev-actions/scripts/tune_threshold.py"
LEAK = re.compile(r"\bADRs?\b|docs/adrs?/|decision record", re.IGNORECASE)


def load_jsonl(name: str) -> list[dict[str, Any]]:
    lines = (JUDGMENT / name).read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def digest(payload: object) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def thresholds() -> dict[str, Any]:
    saved: dict[str, Any] = json.loads((JUDGMENT / "thresholds.json").read_text())
    return saved


def score_rows() -> list[dict[str, str]]:
    with (JUDGMENT / "scores.csv").open(newline="") as handle:
        return list(csv.DictReader(handle))


def counting_labels() -> dict[str, int]:
    final: dict[str, int] = {}
    for event in load_jsonl("labels.jsonl"):
        if event["provenance"] == "agent_reviewed":
            final[event["case_id"]] = event["label"]
    return final


def tuner() -> Any:
    spec = importlib.util.spec_from_file_location("tune_threshold", TUNER)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["tune_threshold"] = module
    spec.loader.exec_module(module)
    return module


def tuned(
    rows: list[dict[str, str]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *options: str,
) -> dict[str, Any]:
    path = tmp_path / "scores.csv"
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    argv = ["tune_threshold.py", str(path), "--repeats", "5", "--json", *options]
    monkeypatch.setattr(sys, "argv", argv)
    tuner().main()
    report: dict[str, Any] = json.loads(capsys.readouterr().out)[0]
    return report


def test_thresholds_belong_to_the_current_question_and_model() -> None:
    document = json.loads((JUDGMENT / "questions.json").read_text())
    saved = thresholds()
    model = saved["model"]
    assert model == "typesafe/jev-1.14.0" == saved["model_reported"]
    assert saved["version_key"] == digest({
        "questions": document["questions"],
        "state": document["state"],
        "model": model,
    })
    assert saved["decisions"]["adr_needed"]["version_key"] == digest({
        "question": document["questions"]["adr_needed"],
        "state": document["state"],
        "model": model,
    })
    assert set(document["questions"]) == set(saved["decisions"]) == {"adr_needed"}
    assert document["questions"]["adr_needed"]["type"] == "bool"


def test_scores_are_the_saved_run_over_every_case_with_its_final_label() -> None:
    saved = thresholds()
    cases = {case["case_id"]: case for case in load_jsonl("cases.jsonl")}
    labels = counting_labels()
    rows = score_rows()
    assert (
        hashlib.sha256((JUDGMENT / "scores.csv").read_bytes()).hexdigest()
        == saved["results"]["scores_sha256"]
    )
    assert [row["case_id"] for row in rows] == list(cases)
    for row in rows:
        case = cases[row["case_id"]]
        assert int(row["label"]) == labels[row["case_id"]]
        assert 0 <= float(row["score"]) <= 1
        assert row["model"] == saved["model"]
        assert row["question_version"] == saved["question_version"]
        assert (row["split"], row["unit"], row["stratum"]) == (
            case["split"],
            case["unit"],
            case["stratum"],
        )


def test_cases_split_by_unit_and_hide_the_decision_record_outcome() -> None:
    cases = load_jsonl("cases.jsonl")
    units = Counter(case["unit"] for case in cases)
    assert max(units.values()) == 1
    for case in cases:
        expected = (
            "test"
            if int(hashlib.sha256(case["unit"].encode()).hexdigest(), 16) % 100 < 30
            else "dev"
        )
        assert case["split"] == expected
        assert case["source"] == "replay"
        assert not LEAK.search(json.dumps(case["state"]))
    summary = thresholds()["label_set"]
    assert Counter(case["split"] for case in cases) == {
        "dev": summary["dev"],
        "test": summary["test"],
    }
    assert 60 <= len(cases) <= 100


def test_every_label_has_council_provenance_and_an_arbiter_decision() -> None:
    events = load_jsonl("labels.jsonl")
    guide = hashlib.sha256((JUDGMENT / "label-guide.md").read_bytes()).hexdigest()
    council = {
        event["case_id"]: event
        for event in events
        if event["labeller"] == "innovation-council" and "arbiter" not in event
    }
    arbiter = {
        event["case_id"]: event for event in events if event.get("arbiter") == "claude"
    }
    cases = [case["case_id"] for case in load_jsonl("cases.jsonl")]
    assert set(council) == set(arbiter) == set(cases)
    assert len(events) == 2 * len(cases)
    for case_id in cases:
        first, final = council[case_id], arbiter[case_id]
        assert first["blind"] is True
        assert first["guide_version"] == final["guide_version"] == guide
        assert final["council_label"] == first["label"]
        assert final["label"] in (0, 1)
        assert re.fullmatch(r"[YNT][1-8]", final["rule"])
        if final["label"] != first["label"]:
            assert final["note"].startswith("Arbiter overrides")
    overridden = sorted(c for c in cases if council[c]["label"] != arbiter[c]["label"])
    assert overridden == ["A017", "A043", "A054", "A074"]


def test_the_escalate_band_reproduces_from_saved_scores() -> None:
    decision = thresholds()["decisions"]["adr_needed"]
    band = decision["escalate_band"]
    assert band["no_at_most"] < band["yes_at_least"] == decision["threshold"]
    rows = score_rows()
    for split in ("dev", "test"):
        chosen = [
            (float(r["score"]), int(r["label"])) for r in rows if r["split"] == split
        ]
        yes = [label for score, label in chosen if score >= band["yes_at_least"]]
        no = [label for score, label in chosen if score <= band["no_at_most"]]
        recorded = band[split]
        assert (recorded["yes"], recorded["no"]) == (len(yes), len(no))
        assert recorded["escalated"] == len(chosen) - len(yes) - len(no)
        assert recorded["yes_precision"] == round(sum(yes) / len(yes), 3)
        assert recorded["no_precision"] == round(no.count(0) / len(no), 3)


def test_the_tuner_ships_the_recorded_thresholds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rows = score_rows()
    band = thresholds()["decisions"]["adr_needed"]["escalate_band"]
    main = tuned(
        rows,
        tmp_path,
        monkeypatch,
        capsys,
        "--min-precision",
        "0.8",
        "--min-recall",
        "0.8",
    )
    assert (
        round(main["shipped_threshold"], 4)
        == thresholds()["decisions"]["adr_needed"]["threshold"]
    )
    yes = tuned(
        rows,
        tmp_path,
        monkeypatch,
        capsys,
        "--min-precision",
        "0.9",
        "--min-recall",
        "0.8",
    )
    assert round(yes["shipped_threshold"], 4) == band["yes_at_least"]
    inverted = [
        {
            **row,
            "label": str(1 - int(row["label"])),
            "score": str(round(1 - float(row["score"]), 6)),
        }
        for row in rows
    ]
    no = tuned(
        inverted,
        tmp_path,
        monkeypatch,
        capsys,
        "--min-precision",
        "0.9",
        "--min-recall",
        "0.5",
    )
    assert round(1 - no["shipped_threshold"], 4) == band["no_at_most"]
