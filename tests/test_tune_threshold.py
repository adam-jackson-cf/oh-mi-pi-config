import importlib.util
import io
import itertools
import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.fast

MODULE = (
    Path(__file__).resolve().parents[1]
    / "agent/skills/jev-actions/scripts/tune_threshold.py"
)
spec = importlib.util.spec_from_file_location("tune_threshold", MODULE)
assert spec is not None
assert spec.loader is not None
tune = importlib.util.module_from_spec(spec)
sys.modules["tune_threshold"] = tune
spec.loader.exec_module(tune)

HEADER = "case_id,label,score,model,question_version,split,stratum\n"
NO_DECISION = (
    "- Decision: none tuned; set the cost-rule default and record why in shortfalls"
)


def scores_csv(path: Path, rows: list[str]) -> Path:
    path.write_text(HEADER + "".join(f"{row}\n" for row in rows))
    return path


def separable(model: str = "m", version: str = "v1") -> list[str]:
    dev = [
        f"d{i},{int(i < 30)},{0.80 if i < 30 else 0.20},{model},{version},dev,"
        f"{'phone' if i % 2 else 'web'}"
        for i in range(60)
    ]
    test = [
        f"t{i},{int(i < 5)},{0.90 if i < 5 else 0.10},{model},{version},test,web"
        for i in range(10)
    ]
    return dev + test


def run_main(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *args: str,
) -> tuple[int, str, str]:
    monkeypatch.setattr(sys, "argv", ["tune_threshold.py", *args])
    code = 0
    try:
        tune.main()
    except SystemExit as exit_:
        code = exit_.code if isinstance(exit_.code, int) else 1
        if isinstance(exit_.code, str):
            print(exit_.code, file=sys.stderr)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_self_test_passes_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = run_main(monkeypatch, capsys, "--self-test")
    assert (code, out.strip()) == (0, "self-test ok")


def test_json_report_tunes_on_dev_and_scores_test_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    csv = scores_csv(tmp_path / "scores.csv", separable())
    code, out, _ = run_main(
        monkeypatch,
        capsys,
        str(csv),
        "--min-precision",
        "0.9",
        "--min-recall",
        "0.9",
        "--calibration",
        "--errors",
        "--repeats",
        "2",
        "--json",
    )
    assert code == 0
    [result] = json.loads(out)
    assert result["model"] == "m @ v1"
    assert (result["dev_cases"], result["test_cases"], result["folds"]) == (60, 10, 5)
    assert result["shipped_threshold"] == pytest.approx(0.5)
    assert result["decision"] == {"threshold": pytest.approx(0.5)}
    assert result["discrimination"]["status"] == "passes"
    assert result["tuned_folds"] == result["total_folds"] == 10
    assert result["test_at_shipped"]["precision"] == 1.0
    assert result["test_at_shipped"]["recall"] == 1.0
    assert result["strata_scope"] == "test"
    assert set(result["strata"]) == {"web"}
    assert result["errors"] == []
    assert result["calibration"]["keep"] in {"raw", "platt"}
    assert "test_brier_platt" in result["calibration"]
    assert (
        "dev classes are perfectly separated; expect optimistic results"
        in result["warnings"]
    )


def test_markdown_report_lists_warnings_thresholds_strata_and_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    dev = [
        f"d{i},{i % 2},{0.3 + 0.4 * (i % 2) + 0.01 * (i % 7)},m,,dev,s{i % 3}"
        for i in range(40)
    ]
    dev += ["x1,0,0.95,m,,dev,s0", "x2,1,0.05,m,,dev,s1"]
    csv = scores_csv(tmp_path / "scores.csv", dev)
    code, out, _ = run_main(
        monkeypatch,
        capsys,
        str(csv),
        "--min-precision",
        "0.5",
        "--calibration",
        "--errors",
    )
    assert code == 0
    assert "Minimums: precision 0.5, recall 0.0 (point estimates)" in out
    assert "## m\n" in out
    assert "Warning: fewer than 50 dev cases per label; treat results as noisy" in out
    assert "- Shipped threshold: " in out
    assert '- Decision: {"threshold": ' in out
    assert "- Brier raw " in out
    assert "Per stratum at the shipped threshold, dev (in-sample):" in out
    assert "| x1 | s0 | 0 | 0.9500 | false_positive |" in out
    assert "| x2 | s1 | 1 | 0.0500 | false_negative |" in out


def test_unmet_minimums_ship_no_threshold_and_say_so(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    csv = scores_csv(tmp_path / "scores.csv", shifted_rows())
    args = ("--min-auc", "0.6", "--min-precision", "0.99", "--min-recall", "0.99")
    code, out, _ = run_main(monkeypatch, capsys, str(csv), *args)
    assert code == 0
    assert "- Shipped threshold: none meets minimums" in out
    assert NO_DECISION in out
    assert "no threshold met minimums on train" in out
    code, out, _ = run_main(monkeypatch, capsys, str(csv), *args, "--json")
    [result] = json.loads(out)
    assert result["discrimination"]["status"] == "passes"
    assert result["decision"] is None


def test_a_model_without_two_cases_of_each_label_reports_an_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rows = ["a,1,0.9,lonely,,dev,", "b,0,0.1,lonely,,dev,", "c,0,0.2,lonely,,dev,"]
    csv = scores_csv(tmp_path / "scores.csv", rows)
    code, out, _ = run_main(monkeypatch, capsys, str(csv))
    assert code == tune.EXIT_INSUFFICIENT_UNITS == 4
    assert "need at least 2 dev units with each label" in out
    assert NO_DECISION in out


def test_conservative_minimums_use_the_wilson_lower_bound() -> None:
    rows = [(1, 0.9)] * 5 + [(0, 0.1)] * 5
    options = tune.Options(
        min_precision=0.8,
        min_recall=0.8,
        conservative=False,
        beta=1.0,
        folds=5,
        repeats=1,
        seed=0,
        calibration=False,
        errors=False,
    )
    assert tune.choose(rows, options) is not None
    strict = tune.Options(**{**options.__dict__, "conservative": True})
    assert tune.choose(rows, strict) is None
    assert tune.wilson_lower(0, 0) == 0.0


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("case_id,score\n1,0.5\n", "missing columns: label"),
        ("label,score\n2,0.5\n", "label must be 0 or 1, got '2'"),
        (
            "label,score,split\n1,0.5,holdout\n",
            "split must be dev or test, got 'holdout'",
        ),
    ],
)
def test_invalid_scores_exit_with_the_reason(text: str, message: str) -> None:
    with pytest.raises(SystemExit) as exit_:
        tune.parse(io.StringIO(text))
    assert exit_.value.code == message


def test_usage_errors_exit_two(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, _, err = run_main(monkeypatch, capsys)
    assert code == 2
    assert "the csv argument is required unless --self-test is given" in err
    csv = scores_csv(tmp_path / "scores.csv", separable())
    code, _, err = run_main(monkeypatch, capsys, str(csv), "--repeats", "0")
    assert code == 2
    assert "--repeats must be at least 1" in err


def test_an_unreadable_file_exits_with_its_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing = tmp_path / "absent.csv"
    code, _, err = run_main(monkeypatch, capsys, str(missing))
    assert code == 1
    assert f"cannot read {missing}" in err


def unit_rows(units_per_label: int, cases_per_unit: int) -> list[str]:
    return [
        f"{label}-{u}-{c},{label},{0.8 if label else 0.2},m,,dev,,{label}-{u}"
        for label, u, c in itertools.product(
            (1, 0), range(units_per_label), range(cases_per_unit)
        )
    ]


def fold_placements(folds: list[list[int]]) -> list[tuple[int, int]]:
    return [(case, number) for number, fold in enumerate(folds) for case in fold]


def unit_csv(path: Path, rows: list[str]) -> Path:
    path.write_text(
        "case_id,label,score,model,question_version,split,stratum,unit\n"
        + "".join(f"{row}\n" for row in rows)
    )
    return path


def test_cross_validation_folds_whole_units_and_caps_folds_by_units(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    csv = unit_csv(tmp_path / "scores.csv", unit_rows(3, 10))
    code, out, _ = run_main(monkeypatch, capsys, str(csv), "--json")
    assert code == 0
    [result] = json.loads(out)
    assert (result["dev_cases"], result["folds"]) == (60, 3)
    cases = tune.parse(io.StringIO(csv.read_text()))["m"]
    for seed in range(5):
        placements = fold_placements(tune.unit_folds(cases, 3, seed))
        assert sorted(case for case, _ in placements) == list(range(60))
        homes = {cases[case]["unit"]: number for case, number in placements}
        assert all(homes[cases[case]["unit"]] == number for case, number in placements)


def test_too_few_units_per_label_reports_an_error_even_with_many_cases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    csv = unit_csv(tmp_path / "scores.csv", unit_rows(1, 30))
    code, out, _ = run_main(monkeypatch, capsys, str(csv))
    assert code == 4
    assert "need at least 2 dev units with each label" in out


def test_a_unit_in_both_dev_and_test_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rows = [*unit_rows(3, 2), "t1,1,0.8,m,,test,,1-0"]
    csv = unit_csv(tmp_path / "scores.csv", rows)
    code, _, err = run_main(monkeypatch, capsys, str(csv))
    assert code == 1
    assert "m: unit '1-0' appears in both dev and test; split by unit" in err


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (
            "case_id,label,score\n1,1,1.5\n",
            "score must be a finite probability in [0, 1], got '1.5'",
        ),
        (
            "case_id,label,score\n1,1,nan\n",
            "score must be a finite probability in [0, 1], got 'nan'",
        ),
        (
            "case_id,label,score\n1,1,0.9\n1,0,0.1\n",
            "model: case_id '1' appears more than once",
        ),
    ],
)
def test_scores_outside_probabilities_and_repeated_cases_are_refused(
    text: str, message: str
) -> None:
    with pytest.raises(SystemExit) as exit_:
        tune.parse(io.StringIO(text))
    assert exit_.value.code == message


NOT_COMPARABLE = (
    "models or question versions do not share the same cases, labels, splits "
    "and units; do not compare them"
)


def test_groups_over_different_cases_are_flagged_as_not_comparable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rows = separable("a") + separable("b")[1:]
    csv = scores_csv(tmp_path / "scores.csv", rows)
    code, out, _ = run_main(monkeypatch, capsys, str(csv), "--json")
    assert code == 0
    results = json.loads(out)
    assert [r["comparable"] for r in results] == [False, False]
    assert all(NOT_COMPARABLE in r["warnings"] for r in results)
    code, out, _ = run_main(monkeypatch, capsys, str(csv))
    assert f"Warning: {NOT_COMPARABLE}" in out


def test_groups_over_the_same_cases_are_comparable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    csv = scores_csv(tmp_path / "scores.csv", separable("a") + separable("b"))
    code, out, _ = run_main(monkeypatch, capsys, str(csv), "--json")
    assert code == 0
    results = json.loads(out)
    assert [r["comparable"] for r in results] == [True, True]
    assert all(NOT_COMPARABLE not in r["warnings"] for r in results)


def test_a_platt_mapping_worse_than_raw_on_test_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    dev = [f"d{i},{int(i < 30)},{0.40 if i < 30 else 0.10},m,,dev," for i in range(60)]
    test = [f"t{i},{i % 2},0.40,m,,test," for i in range(20)]
    csv = scores_csv(tmp_path / "scores.csv", dev + test)
    code, out, _ = run_main(monkeypatch, capsys, str(csv), "--calibration", "--json")
    assert code == 0
    [result] = json.loads(out)
    calibration = result["calibration"]
    assert calibration["brier_platt_heldout"] < calibration["brier_raw"]
    assert calibration["test_brier_platt"] > calibration["test_brier_raw"]
    assert (calibration["keep"], calibration["platt_rejected_on_test"]) == ("raw", True)
    code, out, _ = run_main(monkeypatch, capsys, str(csv), "--calibration")
    assert "- Platt mapping rejected: worse than raw on test" in out


RANK_WARNING = (
    "ranking fails on dev (AUC interval includes 0.5, or AUC is below the "
    "floor); do not tune or band, use the cost-rule default, and take the "
    "question to error analysis as wording, missing state or a policy gap"
)


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        ([(1, 0.9), (1, 0.4), (0, 0.6), (0, 0.1)], 0.75),
        ([(1, 0.5), (0, 0.5), (1, 0.7), (0, 0.2)], 0.875),
        ([(1, 0.1), (0, 0.9)], 0.0),
        ([(1, 0.9), (1, 0.8)], None),
    ],
)
def test_auc_counts_ties_as_half_and_needs_both_labels(
    rows: list[tuple[int, float]], expected: float | None
) -> None:
    assert tune.auc(rows) == expected


def test_ranking_is_reported_with_its_interval_in_json_and_markdown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    csv = scores_csv(tmp_path / "scores.csv", separable())
    code, out, _ = run_main(monkeypatch, capsys, str(csv), "--json")
    assert code == 0
    [result] = json.loads(out)
    assert result["discrimination"] == {
        "auc": 1.0,
        "interval": [1.0, 1.0],
        "floor": 0.7,
        "resamples": 1000,
        "status": "passes",
        "passes": True,
    }
    assert RANK_WARNING not in result["warnings"]
    code, out, _ = run_main(monkeypatch, capsys, str(csv))
    assert (
        "- Ranking on dev: AUC 1.00 (95% unit interval 1.00 to 1.00; floor 0.7): passes"
        in out
    )
    assert '- Decision: {"threshold": 0.5000}' in out


def block_rows(unit_per_block: bool) -> list[str]:
    blocks = [(1, 0.9), (1, 0.8), (1, 0.3), (0, 0.2), (0, 0.7), (0, 0.1)]
    rows = []
    for i in range(60):
        label, score = blocks[i // 10]
        unit = f"b{i // 10}" if unit_per_block else f"c{i}"
        rows.append(f"c{i},{label},{score},m,,dev,,{unit}")
    return rows


def test_scores_from_few_units_fail_ranking_that_independent_cases_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    results = {}
    for grouped, expected in ((False, 0), (True, tune.EXIT_RANK_FAILS)):
        csv = unit_csv(tmp_path / f"scores-{grouped}.csv", block_rows(grouped))
        code, out, _ = run_main(monkeypatch, capsys, str(csv), "--json")
        assert code == expected
        [results[grouped]] = json.loads(out)
    alone, grouped_result = results[False], results[True]
    assert alone["discrimination"]["auc"] == grouped_result["discrimination"]["auc"]
    assert alone["discrimination"]["interval"][0] > 0.5
    assert alone["discrimination"]["passes"] is True
    assert grouped_result["discrimination"]["interval"][0] <= 0.5
    assert grouped_result["discrimination"]["passes"] is False
    assert grouped_result["discrimination"]["status"] == "fails"
    assert grouped_result["decision"] is None
    assert RANK_WARNING in grouped_result["warnings"]


def test_tied_scores_fail_ranking_and_say_why(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rows = [f"d{i},{i % 2},0.5,m,,dev," for i in range(40)]
    csv = scores_csv(tmp_path / "scores.csv", rows)
    code, out, _ = run_main(monkeypatch, capsys, str(csv))
    assert code == 3
    assert "- Ranking on dev: AUC 0.50 (95% unit interval 0.50 to 0.50" in out
    assert "floor 0.7): fails" in out
    assert f"Warning: {RANK_WARNING}" in out
    assert NO_DECISION in out


def shifted_rows() -> list[str]:
    rows = []
    for i in range(50):
        rows.append(f"p{i},1,{0.2 + 0.6 * i / 49:.4f},m,,dev,")
        rows.append(f"n{i},0,{0.1 + 0.6 * i / 49:.4f},m,,dev,")
    return rows


def test_ranking_above_chance_but_below_the_floor_fails_until_the_floor_drops(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    csv = scores_csv(tmp_path / "scores.csv", shifted_rows())
    code, out, _ = run_main(monkeypatch, capsys, str(csv), "--json")
    assert code == 3
    [result] = json.loads(out)
    rank = result["discrimination"]
    assert rank["interval"][0] > 0.5
    assert rank["auc"] == pytest.approx(0.6556)
    assert rank["passes"] is False
    code, out, _ = run_main(monkeypatch, capsys, str(csv), "--min-auc", "0.6", "--json")
    assert code == 0
    [result] = json.loads(out)
    assert result["discrimination"]["passes"] is True
    assert result["discrimination"]["floor"] == 0.6


def test_the_unit_refusal_still_reports_ranking_without_an_interval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    csv = unit_csv(tmp_path / "scores.csv", unit_rows(1, 30))
    code, out, _ = run_main(monkeypatch, capsys, str(csv), "--json")
    assert code == 4
    [result] = json.loads(out)
    assert result["discrimination"]["auc"] == 1.0
    assert result["discrimination"]["interval"] is None
    assert result["discrimination"]["status"] == "insufficient units"
    assert result["discrimination"]["passes"] is False
    assert result["decision"] is None
    code, out, _ = run_main(monkeypatch, capsys, str(csv))
    assert (
        "- Ranking on dev: AUC 1.00 (no interval without 2 units of each label; "
        "floor 0.7): insufficient units"
    ) in out
    rows = ["a,1,0.9,m,,dev,", "b,1,0.8,m,,dev,"]
    csv = scores_csv(tmp_path / "one-label.csv", rows)
    code, out, _ = run_main(monkeypatch, capsys, str(csv))
    assert code == 4
    assert "- Ranking on dev: no AUC without both labels; insufficient units" in out


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("--auc-resamples", "0"), "--auc-resamples must be at least 1"),
        (("--min-auc", "0.4"), "--min-auc must be between 0.5 and 1"),
    ],
)
def test_ranking_options_are_checked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    args: tuple[str, str],
    message: str,
) -> None:
    csv = scores_csv(tmp_path / "scores.csv", separable())
    code, _, err = run_main(monkeypatch, capsys, str(csv), *args)
    assert code == 2
    assert message in err


def test_a_failing_group_outranks_one_with_insufficient_units_in_the_exit_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    tied = [f"d{i},{i % 2},0.5,tied,,dev," for i in range(40)]
    lonely = ["a,1,0.9,lonely,,dev,", "b,0,0.1,lonely,,dev,"]
    csv = scores_csv(tmp_path / "scores.csv", tied + lonely)
    code, out, _ = run_main(monkeypatch, capsys, str(csv), "--json")
    assert code == tune.EXIT_RANK_FAILS
    statuses = {r["model"]: r["discrimination"]["status"] for r in json.loads(out)}
    assert statuses == {"lonely": "insufficient units", "tied": "fails"}


def test_help_documents_the_exit_codes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = run_main(monkeypatch, capsys, "--help")
    assert code == 0
    assert "- 3 when ranking fails in any group;" in out
    assert (
        "- 4 when ranking has insufficient units in any group and fails in none." in out
    )
