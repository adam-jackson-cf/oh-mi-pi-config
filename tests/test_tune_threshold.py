import importlib.util
import io
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
    assert "- Brier raw " in out
    assert "Per stratum at the shipped threshold, dev (in-sample):" in out
    assert "| x1 | s0 | 0 | 0.9500 | false_positive |" in out
    assert "| x2 | s1 | 1 | 0.0500 | false_negative |" in out


def test_unmet_minimums_ship_no_threshold_and_say_so(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rows = [f"d{i},{i % 2},0.5,m,,dev," for i in range(20)]
    csv = scores_csv(tmp_path / "scores.csv", rows)
    code, out, _ = run_main(
        monkeypatch, capsys, str(csv), "--min-precision", "0.9", "--min-recall", "0.9"
    )
    assert code == 0
    assert "- Shipped threshold: none meets minimums" in out
    assert "no threshold met minimums on train" in out


def test_a_model_without_two_cases_of_each_label_reports_an_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rows = ["a,1,0.9,lonely,,dev,", "b,0,0.1,lonely,,dev,", "c,0,0.2,lonely,,dev,"]
    csv = scores_csv(tmp_path / "scores.csv", rows)
    code, out, _ = run_main(monkeypatch, capsys, str(csv))
    assert code == 0
    assert "need at least 2 dev units with each label" in out


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
        for label in (1, 0)
        for u in range(units_per_label)
        for c in range(cases_per_unit)
    ]


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
        folds = tune.unit_folds(cases, 3, seed)
        assert sorted(i for fold in folds for i in fold) == list(range(60))
        homes = {cases[i]["unit"]: n for n, fold in enumerate(folds) for i in fold}
        assert all(homes[cases[i]["unit"]] == n for n, f in enumerate(folds) for i in f)


def test_too_few_units_per_label_reports_an_error_even_with_many_cases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    csv = unit_csv(tmp_path / "scores.csv", unit_rows(1, 30))
    code, out, _ = run_main(monkeypatch, capsys, str(csv))
    assert code == 0
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
