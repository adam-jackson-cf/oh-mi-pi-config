import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.fast

MODULE = (
    Path(__file__).resolve().parents[1]
    / "agent/extensions/agent-rails/skills/jev-actions/scripts/check_labels.py"
)
spec = importlib.util.spec_from_file_location("check_labels", MODULE)
assert spec is not None
assert spec.loader is not None
check = importlib.util.module_from_spec(spec)
sys.modules["check_labels"] = check
spec.loader.exec_module(check)

QUESTION = "folder-name-fits"


def case(case_id: str, unit: str, split: str = "dev") -> dict[str, Any]:
    return {"case_id": case_id, "unit": unit, "split": split, "state": {}}


def arbiter(case_id: str, label: int | str, when: str, **extra: Any) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "question_id": QUESTION,
        "label": label,
        "by": "main-agent",
        "provenance": "arbiter",
        "rule": "r1",
        "note": "",
        "timestamp": when,
        "confirmed": "individual",
        "blind": True,
        **extra,
    }


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return path


def run_main(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *args: str,
) -> tuple[int, str, str]:
    monkeypatch.setattr(sys, "argv", ["check_labels.py", *args])
    code = 0
    try:
        check.main()
    except SystemExit as exit_:
        code = exit_.code if isinstance(exit_.code, int) else 1
        if isinstance(exit_.code, str):
            print(exit_.code, file=sys.stderr)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def eval_files(
    tmp_path: Path, cases: list[dict[str, Any]], labels: list[dict[str, Any]]
) -> list[str]:
    return [
        "--cases",
        str(write_jsonl(tmp_path / "cases.jsonl", cases)),
        "--labels",
        str(write_jsonl(tmp_path / "labels.jsonl", labels)),
    ]


def spread_cases() -> list[dict[str, Any]]:
    return [case(f"c{i}", f"repo{i % 4}") for i in range(8)]


def spread_labels() -> list[dict[str, Any]]:
    return [arbiter(f"c{i}", i % 2, f"2026-11-21T10:{i:02d}:00Z") for i in range(8)]


def test_individually_confirmed_labels_over_enough_units_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    files = eval_files(tmp_path, spread_cases(), spread_labels())
    code, out, _ = run_main(monkeypatch, capsys, *files, "--json")
    assert code == 0
    result = json.loads(out)
    [report] = result["questions"]
    assert report["passes"] is True
    assert report["labels"] == {
        "0": {"dev_cases": 4, "dev_units": 2},
        "1": {"dev_cases": 4, "dev_units": 2},
    }
    assert report["provenance"] == {"arbiter": 8}
    assert report["abstain"] == []
    assert result["not_counted"] == []
    assert result["counted_as_agent"] == []
    code, out, _ = run_main(monkeypatch, capsys, *files)
    assert "- Label 1: 4 dev cases from 2 units" in out
    assert "- Result: pass" in out


def test_bulk_confirmations_do_not_count_as_arbiter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    labels = [
        arbiter("c0", 1, "2026-11-21T10:00:00Z"),
        arbiter("c1", 0, "2026-11-21T10:00:00Z"),
        arbiter("c2", 1, "2026-11-21T10:05:00Z", confirmed="bulk"),
        arbiter("c3", 0, "2026-11-21T10:06:00Z", by=""),
        arbiter("c4", 1, ""),
        arbiter("c5", 0, "2026-11-21T10:07:00Z"),
    ]
    files = eval_files(tmp_path, spread_cases(), labels)
    code, out, _ = run_main(monkeypatch, capsys, *files, "--json")
    assert code == 1
    result = json.loads(out)
    assert [(row["case_id"], row["reason"]) for row in result["not_counted"]] == [
        ("c0", "arbiter and timestamp shared with 1 other cases"),
        ("c1", "arbiter and timestamp shared with 1 other cases"),
        ("c2", "confirmed is not individual"),
        ("c3", "no arbiter in by"),
        ("c4", "no timestamp"),
    ]
    [report] = result["questions"]
    assert report["counting"] == {"c5": {"label": "0", "provenance": "arbiter"}}
    assert report["reasons"] == [
        "label 0 has dev cases from 1 units; need 2",
        "label 1 has dev cases from 0 units; need 2",
    ]
    code, out, _ = run_main(monkeypatch, capsys, *files)
    assert "| 3 | c2 | folder-name-fits | confirmed is not individual |" in out
    assert "- FAIL: label 1 has dev cases from 0 units; need 2" in out


def test_a_bulk_arbiter_row_falls_back_to_the_next_counting_label() -> None:
    cases = {"c0": check.CaseInfo("u0", "dev")}
    reviewed = check.Label(0, "c0", QUESTION, "1", "", "agent_reviewed", "t0", "", True)
    bulk = check.Label(
        1, "c0", QUESTION, "0", "main-agent", "arbiter", "t1", "bulk", True
    )
    result = check.audit(cases, [reviewed, bulk], 2)
    [report] = result["questions"]
    assert report["counting"] == {"c0": {"label": "1", "provenance": "agent_reviewed"}}


def test_a_later_individual_decision_replaces_an_earlier_one() -> None:
    cases = {"c0": check.CaseInfo("u0", "dev")}
    first = check.Label(
        0, "c0", QUESTION, "1", "main-agent", "arbiter", "t0", "individual", True
    )
    second = check.Label(
        1, "c0", QUESTION, "0", "main-agent", "arbiter", "t1", "individual", True
    )
    agent = check.Label(2, "c0", QUESTION, "1", "", "agent", "t2", "")
    [report] = check.audit(cases, [first, second, agent], 2)["questions"]
    assert report["counting"] == {"c0": {"label": "0", "provenance": "arbiter"}}


def test_labels_from_one_unit_fail_coverage_whatever_their_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cases = [case(f"c{i}", "repo0") for i in range(60)]
    cases += [case("t0", "repo9", "test"), case("t1", "repo8", "test")]
    labels = [
        arbiter(f"c{i}", i % 2, f"2026-11-21T{i // 60:02d}:{i % 60:02d}:00Z")
        for i in range(60)
    ]
    labels += [
        arbiter("t0", 1, "2026-11-22T00:00:00Z"),
        arbiter("t1", 0, "2026-11-22T00:01:00Z"),
    ]
    files = eval_files(tmp_path, cases, labels)
    code, out, _ = run_main(monkeypatch, capsys, *files, "--json")
    assert code == 1
    [report] = json.loads(out)["questions"]
    assert report["labels"] == {
        "0": {"dev_cases": 30, "dev_units": 1},
        "1": {"dev_cases": 30, "dev_units": 1},
    }


def test_proposals_count_agent_labels_for_planning_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    labels = [
        {**arbiter(f"c{i}", i % 2, ""), "provenance": "agent", "by": "session-a"}
        for i in range(8)
    ]
    files = eval_files(tmp_path, spread_cases(), labels)
    code, out, _ = run_main(monkeypatch, capsys, *files)
    assert code == 1
    assert "- FAIL: no counting labels" in out
    code, out, _ = run_main(monkeypatch, capsys, *files, "--proposals")
    assert code == 0
    assert "agent proposals counted: yes, so this result never approves tuning" in out
    assert "- Counting provenance: agent 8" in out


def test_choice_labels_need_units_for_each_option_seen() -> None:
    cases = {f"c{i}": check.CaseInfo(f"u{i % 3}", "dev") for i in range(6)}
    labels = [
        check.Label(
            i,
            f"c{i}",
            "route",
            "refund" if i < 5 else "escalate",
            "main-agent",
            "arbiter",
            f"t{i}",
            "individual",
            True,
        )
        for i in range(6)
    ]
    [report] = check.audit(cases, labels, 2)["questions"]
    assert report["labels"] == {
        "escalate": {"dev_cases": 1, "dev_units": 1},
        "refund": {"dev_cases": 5, "dev_units": 3},
    }
    assert report["reasons"] == ["label escalate has dev cases from 1 units; need 2"]


def test_a_named_question_without_labels_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    files = eval_files(tmp_path, spread_cases(), spread_labels())
    code, out, _ = run_main(monkeypatch, capsys, *files, "--question", "other")
    assert code == 1
    assert "## other" in out
    assert "- FAIL: no counting labels" in out


@pytest.mark.parametrize(
    ("cases", "labels", "message"),
    [
        (
            '{"case_id": "c0", "unit": "u0"}\n',
            "",
            "cases.jsonl line 1: split must be a non-empty string",
        ),
        (
            '{"case_id": "c0", "split": "dev"}\n',
            "",
            "cases.jsonl line 1: unit must be a non-empty string",
        ),
        (
            '{"case_id": "c0", "unit": "u0", "split": "holdout"}\n',
            "",
            "cases.jsonl line 1: split must be dev or test",
        ),
        (
            '{"case_id": "c0", "unit": "u0", "split": "dev"}\n' * 2,
            "",
            "cases.jsonl line 2: case_id 'c0' repeats",
        ),
        ("not json\n", "", "cases.jsonl line 1: invalid JSON: Expecting value"),
        ("[1]\n", "", "cases.jsonl line 1: expected a JSON object"),
        (
            '{"case_id": "c0", "unit": "u0", "split": "dev"}\n',
            '{"case_id": "c9", "question_id": "q", "label": 1, "provenance": "arbiter"}\n',
            "labels.jsonl line 1: case_id 'c9' is not in cases.jsonl",
        ),
        (
            '{"case_id": "c0", "unit": "u0", "split": "dev"}\n',
            '{"case_id": "c0", "question_id": "q", "label": 1, "provenance": "bulk"}\n',
            "labels.jsonl line 1: unknown provenance 'bulk'",
        ),
        (
            '{"case_id": "c0", "unit": "u0", "split": "dev"}\n',
            '{"case_id": "c0", "question_id": "q", "label": 1, "provenance": "human"}\n',
            "labels.jsonl line 1: unknown provenance 'human'",
        ),
        (
            '{"case_id": "c0", "unit": "u0", "split": "dev"}\n',
            '{"case_id": "c0", "question_id": "q", "label": "unsure", "provenance": "agent"}\n',
            "labels.jsonl line 1: unsure is not a label value; use abstain",
        ),
        (
            '{"case_id": "c0", "unit": "u0", "split": "dev"}\n',
            '{"case_id": "c0", "question_id": "q", "label": true, "provenance": "arbiter"}\n',
            "labels.jsonl line 1: label must be 1, 0, abstain or an option key",
        ),
    ],
)
def test_invalid_eval_files_exit_with_the_reason(
    tmp_path: Path, cases: str, labels: str, message: str
) -> None:
    (tmp_path / "cases.jsonl").write_text(cases + "\n")
    (tmp_path / "labels.jsonl").write_text(labels)
    with pytest.raises(SystemExit) as exit_:
        check.load(str(tmp_path / "cases.jsonl"), str(tmp_path / "labels.jsonl"))
    assert isinstance(exit_.value.code, str)
    assert exit_.value.code.startswith(message)


def test_usage_and_read_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    files = eval_files(tmp_path, spread_cases(), spread_labels())
    code, _, err = run_main(monkeypatch, capsys, *files, "--min-units", "1")
    assert code == 2
    assert "--min-units must be at least 2" in err
    code, _, err = run_main(monkeypatch, capsys, *files, "--version-key", "no-key")
    assert code == 2
    assert "--version-key must be QUESTION=KEY, got 'no-key'" in err
    missing = tmp_path / "absent.jsonl"
    code, _, err = run_main(
        monkeypatch, capsys, "--cases", str(missing), "--labels", str(missing)
    )
    assert code == 1
    assert "cannot read eval files" in err


def test_rows_that_saw_a_score_or_label_count_only_as_agent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    labels = spread_labels()
    labels[0] = {**labels[0], "blind": False}
    labels[1] = {**labels[1], "provenance": "agent_reviewed", "blind": False}
    files = eval_files(tmp_path, spread_cases(), labels)
    code, out, _ = run_main(monkeypatch, capsys, *files, "--json")
    result = json.loads(out)
    assert [(r["case_id"], r["reason"]) for r in result["counted_as_agent"]] == [
        ("c0", "arbiter row is not blind"),
        ("c1", "agent_reviewed row is not blind"),
    ]
    [report] = result["questions"]
    assert set(report["counting"]) == {f"c{i}" for i in range(2, 8)}
    assert report["provenance"] == {"arbiter": 6}
    assert code == 0
    code, out, _ = run_main(monkeypatch, capsys, *files, "--proposals", "--json")
    [report] = json.loads(out)["questions"]
    assert report["counting"]["c0"] == {"label": "0", "provenance": "agent"}
    assert report["provenance"] == {"agent": 2, "arbiter": 6}
    code, out, _ = run_main(monkeypatch, capsys, *files)
    assert "Rows counted as agent:" in out
    assert "| 1 | c0 | folder-name-fits | arbiter row is not blind |" in out


def test_an_abstain_gives_its_case_no_label_and_is_reported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    labels = spread_labels()
    labels.append({
        **labels[7],
        "provenance": "agent_reviewed",
        "timestamp": "2026-11-21T09:00:00Z",
    })
    labels.append(
        arbiter("c7", "abstain", "2026-11-21T11:00:00Z", note="policy silent")
    )
    files = eval_files(tmp_path, spread_cases(), labels)
    code, out, _ = run_main(monkeypatch, capsys, *files, "--json")
    assert code == 0
    [report] = json.loads(out)["questions"]
    assert report["abstain"] == ["c7"]
    assert "c7" not in report["counting"]
    assert report["labels"] == {
        "0": {"dev_cases": 4, "dev_units": 2},
        "1": {"dev_cases": 3, "dev_units": 2},
    }
    code, out, _ = run_main(monkeypatch, capsys, *files)
    assert "- Abstain: 1 cases" in out


def test_after_a_meaning_change_only_labels_under_the_new_key_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    old = [{**row, "question_version": "v1"} for row in spread_labels()]
    new = [
        arbiter(f"c{i}", i % 2, f"2026-11-22T10:{i:02d}:00Z", question_version="v2")
        for i in range(2)
    ]
    unkeyed = arbiter("c4", 0, "2026-11-22T11:00:00Z")
    files = eval_files(tmp_path, spread_cases(), [*old, *new, unkeyed])
    code, out, _ = run_main(monkeypatch, capsys, *files, "--json")
    assert code == 0
    code, out, _ = run_main(
        monkeypatch, capsys, *files, "--version-key", f"{QUESTION}=v2", "--json"
    )
    assert code == 1
    result = json.loads(out)
    assert result["version_keys"] == {QUESTION: "v2"}
    [report] = result["questions"]
    assert set(report["counting"]) == {"c0", "c1"}
    reasons = {(r["case_id"], r["reason"]) for r in result["not_counted"]}
    assert ("c5", "made under v1, not the current v2") in reasons
    assert ("c4", "made under no version key, not the current v2") in reasons
    assert report["reasons"] == [
        "label 0 has dev cases from 1 units; need 2",
        "label 1 has dev cases from 1 units; need 2",
    ]


def test_one_unit_per_case_fails_as_a_judged_item_unit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cases = [case(f"c{i}", f"folder{i}") for i in range(8)]
    files = eval_files(tmp_path, cases, spread_labels())
    code, out, _ = run_main(monkeypatch, capsys, *files, "--json")
    assert code == 1
    result = json.loads(out)
    assert result["unit_check"] == {
        "cases": 8,
        "units": 8,
        "passes": False,
        "reason": "every one of 8 cases has its own unit; recompute unit under unit_rule",
    }
    [report] = result["questions"]
    assert report["passes"] is True
    code, out, _ = run_main(monkeypatch, capsys, *files, "--proposals")
    assert code == 1
    assert "- FAIL: every one of 8 cases has its own unit" in out


def test_owner_labels_never_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    labels = spread_labels()
    labels.append({
        **arbiter("c0", 1, "2026-11-21T12:00:00Z"),
        "provenance": "owner",
        "by": "owner",
    })
    files = eval_files(tmp_path, spread_cases(), labels)
    code, out, _ = run_main(monkeypatch, capsys, *files, "--json")
    assert code == 0
    result = json.loads(out)
    assert [(r["case_id"], r["reason"]) for r in result["not_counted"]] == [
        ("c0", "owner label never counts")
    ]
    [report] = result["questions"]
    assert report["counting"]["c0"] == {"label": "0", "provenance": "arbiter"}
