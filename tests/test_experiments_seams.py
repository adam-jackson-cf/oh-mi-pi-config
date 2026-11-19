import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest
from child_process import run_text

pytestmark = pytest.mark.seam

EXPERIMENTS = (
    Path(__file__).resolve().parents[1] / "agent/skills/omp-config-optimise/experiments"
)
PROCESS_TIMEOUT = 120

FAKE_OMP = """#!{python}
import json, os, pathlib, sys
args = sys.argv[1:]
if args[:2] == ["config", "list"]:
    print(json.dumps({{"find.enabled": {{"value": "auto"}}, "modelRoles": {{"value": {{"judge": "j"}}}}}}))
    sys.exit(0)
cwd = pathlib.Path(args[args.index("--cwd") + 1])
(cwd / "notes.txt").write_text("outside the allowed paths")
reply = os.environ.get("FAKE_REPLY", "done")
event = {{"type": "message_end", "message": {{"role": "assistant", "provider": "openai-codex",
    "model": "gpt-6-sol", "stopReason": "stop", "usage": {{"input": 10, "output": 5, "cost": {{"total": 0.1}}}},
    "content": [{{"type": "text", "text": reply}}]}}}}
print(json.dumps(event))
sys.exit(int(os.environ.get("FAKE_EXIT", "0")))
"""


def fake_path(tmp_path: Path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    omp = bin_dir / "omp"
    omp.write_text(FAKE_OMP.format(python=sys.executable))
    omp.chmod(0o755)
    return {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}


def script(relative: str, *args: str, env: dict[str, str] | None = None) -> str:
    result = run_text(
        [sys.executable, str(EXPERIMENTS / relative), *args],
        env=env,
        timeout=PROCESS_TIMEOUT,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def records(out: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in (out / "results.jsonl").read_text().splitlines()
    ]


def remove_workspaces(root: str, rows: list[dict[str, Any]]) -> None:
    for row in rows:
        shutil.rmtree(Path(root) / str(row.get("runId", "-")), ignore_errors=True)


def test_every_frozen_fixture_fails_before_and_passes_after_its_reference() -> None:
    out = script("fixed-plan-effort/validate_fixtures.py")
    assert "FAIL" not in out
    assert out.count("PASS") == 9


def test_every_seeded_defect_case_fails_its_hidden_check() -> None:
    lines = script("review-detection/validate_cases.py").splitlines()
    assert len(lines) == 30
    assert all(re.match(r"\s*\S+\s+\d+/\d+\s+PASS", line) for line in lines)


def test_a_trial_scores_the_workspace_and_flags_paths_outside_the_plan(
    tmp_path: Path,
) -> None:
    out = tmp_path / "out"
    printed = script(
        "fixed-plan-effort/run_trials.py",
        "--model",
        "openai-codex/gpt-6-luna",
        "--arm",
        "A-low=low@priority",
        "--reps",
        "1",
        "--fixtures",
        "py-score-summary",
        "--out",
        str(out),
        env=fake_path(tmp_path),
    )
    [row] = records(out)
    remove_workspaces("/tmp/omp-effort-ws", [row])
    assert json.loads(printed)["fixture"] == "py-score-summary"
    assert row["tier"] == "priority"
    assert row["scopeViolations"] == ["notes.txt"]
    assert row["checkPassed"] is False
    assert (row["testsPassed"], row["testsTotal"]) == (2, 7)
    assert row["findAvailable"] is True
    assert row["finalText"] == "done"
    assert (out / "runs" / str(row["runId"]) / "diff.patch").exists()


def test_review_trials_record_failures_rerun_them_and_feed_the_analysis(
    tmp_path: Path,
) -> None:
    out = tmp_path / "out"
    env = fake_path(tmp_path)
    arguments = [
        "review-detection/run_reviews.py",
        "--arm",
        "G-high=openai-codex/gpt-6-sol:high",
        "--cases",
        "inventory-m1",
        "--reps",
        "1",
        "--out",
        str(out),
    ]
    script(*arguments, env={**env, "FAKE_EXIT": "1"})
    reply = 'Found it.\n{"findings": [{"file": "inventory/store.py", "line": 1, "step": 2, "severity": "blocking"}]}'
    script(*arguments, "--rerun-failed", env={**env, "FAKE_REPLY": reply})
    first, second = records(out)
    remove_workspaces("/tmp/omp-review-ws", [first, second])
    assert first["failure"] == "exit 1"
    assert second["failure"] is None
    assert second["modifiedRepo"] is False
    assert second["findings"] == [
        {"file": "inventory/store.py", "line": 1, "step": 2, "severity": "blocking"}
    ]
    assert set(second["defects"]) == {"I-expiry-boundary", "I-low-stock"}
    analysis = script("review-detection/analyze.py", str(out))
    assert "| G-high | gpt-6-sol:high | 1/1 | 0 |" in analysis
    assert "Complete (every arm 1/1 scored): True" in analysis


def test_generated_items_are_reproducible_with_computed_answers(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    assert "16 items" in script("vision-reading/generate_items.py", str(first))
    script("vision-reading/generate_items.py", str(second))
    items = json.loads((first / "items.json").read_text())
    assert items == json.loads((second / "items.json").read_text())
    assert {item["image"] for item in items} <= {
        path.name for path in first.glob("*.png")
    }
    assert all(item["answer"] for item in items)


def test_vision_trials_score_the_last_answer_line(tmp_path: Path) -> None:
    items = tmp_path / "items"
    items.mkdir()
    (items / "chart.png").write_bytes(b"png")
    (items / "items.json").write_text(
        json.dumps([
            {
                "id": "chart-01",
                "image": "chart.png",
                "question": "q?",
                "answer": "R1,C2",
            }
        ])
    )
    out = tmp_path / "out"
    script(
        "vision-reading/run_vision.py",
        "--items",
        str(items),
        "--arm",
        "G=openai-codex/gpt-6-sol:low",
        "--reps",
        "1",
        "--out",
        str(out),
        env={**fake_path(tmp_path), "FAKE_REPLY": "Reading it.\nANSWER: r1, c2"},
    )
    [row] = records(out)
    remove_workspaces("/tmp/omp-vision-ws", [row])
    assert (row["given"], row["correct"]) == ("r1, c2", True)
    table = script("vision-reading/run_vision.py", "--analyze", str(out))
    assert "| G | gpt-6-sol:low | 1 | 1/1 | 0 |" in table
