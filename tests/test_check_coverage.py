import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.fast

MODULE = Path(__file__).resolve().parents[1] / "scripts/check_coverage.py"
spec = importlib.util.spec_from_file_location("check_coverage", MODULE)
assert spec is not None
assert spec.loader is not None
check_coverage = importlib.util.module_from_spec(spec)
sys.modules["check_coverage"] = check_coverage
spec.loader.exec_module(check_coverage)

PYPROJECT = """
[tool.coverage.run]
source = ["lib", "scripts"]
omit = ["lib/fixtures/*", "vendor/*"]
"""


def summary(covered: int, statements: int, branches: tuple[int, int]) -> dict[str, Any]:
    return {
        "summary": {
            "covered_lines": covered,
            "num_statements": statements,
            "covered_branches": branches[0],
            "num_branches": branches[1],
        }
    }


def repository(root: Path, files: dict[str, str]) -> set[str]:
    (root / "pyproject.toml").write_text(PYPROJECT)
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text)
    return {*files, "pyproject.toml"}


def reports(
    root: Path, python: dict[str, dict[str, Any]], lcov: str
) -> argparse.Namespace:
    (root / "python.json").write_text(json.dumps({"files": python}))
    (root / "lcov.info").write_text(lcov)
    return argparse.Namespace(
        python_json=root / "python.json",
        lcov=[root / "lcov.info"],
        line_floor=50.0,
        branch_floor=50.0,
    )


OWNED = {
    "lib/core.py": "x = 1\n",
    "scripts/tool.py": "y = 2\n",
    "lib/fixtures/frozen.py": "z = 3\n",
    "tests/test_core.py": "assert True\n",
    "app/loaded.ts": "export const a = 1;\n",
    "app/unloaded.ts": "// a note\n\nexport const b = 2;\nexport const c = 3;\n",
    "app/types.d.ts": "export type T = string;\n",
    "vendor/rule.ts": "export const v = 1;\n",
    "tests/helper.test.ts": "test();\n",
}

LOADED = "SF:app/loaded.ts\nDA:1,1\nDA:2,0\nend_of_record\n"


def test_owned_scope_leaves_out_tests_fixtures_declarations_and_vendor() -> None:
    scope = check_coverage.OwnedScope(
        ("lib", "scripts"), ("lib/fixtures/*", "vendor/*")
    )
    assert scope.python_files(OWNED) == {"lib/core.py", "scripts/tool.py"}
    assert scope.script_files(OWNED) == {"app/loaded.ts", "app/unloaded.ts"}


def test_a_script_no_test_loads_counts_its_code_lines_as_uncovered(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tracked = repository(tmp_path, OWNED)
    options = reports(
        tmp_path,
        {
            "lib/core.py": summary(8, 10, (4, 4)),
            "scripts/tool.py": summary(2, 2, (0, 0)),
        },
        LOADED,
    )
    assert check_coverage.check(options, tmp_path, tracked) == 0
    out = capsys.readouterr().out
    assert "TypeScript 25.00%" in out
    assert "lines 68.75%" in out
    assert "1 owned TypeScript and JavaScript files no test loads" in out
    assert "app/unloaded.ts" in out


def test_python_and_script_lines_join_against_the_line_floor(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tracked = repository(tmp_path, OWNED)
    options = reports(
        tmp_path,
        {
            "lib/core.py": summary(8, 10, (4, 4)),
            "scripts/tool.py": summary(2, 2, (0, 0)),
        },
        LOADED,
    )
    options.line_floor = 69.0
    assert check_coverage.check(options, tmp_path, tracked) == 1
    assert "Coverage is below its floor." in capsys.readouterr().err


def test_the_branch_floor_reads_python_branches(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tracked = repository(tmp_path, OWNED)
    options = reports(
        tmp_path,
        {
            "lib/core.py": summary(10, 10, (1, 4)),
            "scripts/tool.py": summary(2, 2, (0, 0)),
        },
        LOADED,
    )
    assert check_coverage.check(options, tmp_path, tracked) == 1
    assert "Python branches 25.00% (floor 50%)" in capsys.readouterr().out


def test_an_owned_python_file_missing_from_the_report_is_an_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tracked = repository(tmp_path, OWNED)
    options = reports(tmp_path, {"lib/core.py": summary(10, 10, (4, 4))}, LOADED)
    assert check_coverage.check(options, tmp_path, tracked) == 2
    assert "scripts/tool.py" in capsys.readouterr().err


def test_files_outside_the_owned_scope_do_not_count(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tracked = repository(tmp_path, OWNED)
    options = reports(
        tmp_path,
        {
            "lib/core.py": summary(10, 10, (4, 4)),
            "scripts/tool.py": summary(2, 2, (0, 0)),
            "lib/fixtures/frozen.py": summary(0, 50, (0, 10)),
            "/tmp/elsewhere/copy.py": summary(0, 50, (0, 10)),
        },
        LOADED
        + "SF:tests/helper.test.ts\nDA:1,0\nend_of_record\n"
        + "SF:vendor/rule.ts\nDA:1,0\nend_of_record\n",
    )
    assert check_coverage.check(options, tmp_path, tracked) == 0
    assert "Python 100.00%" in capsys.readouterr().out


def test_a_line_any_run_hit_counts_once_across_reports(tmp_path: Path) -> None:
    hits = check_coverage.LineHits()
    first = tmp_path / "t1.info"
    second = tmp_path / "t2.info"
    first.write_text("SF:app/loaded.ts\nDA:1,0\nDA:2,3\nend_of_record\n")
    second.write_text("SF:app/loaded.ts\nDA:1,1\nDA:2,0\nend_of_record\n")
    check_coverage.read_lcov(first, hits)
    check_coverage.read_lcov(second, hits)
    totals, unloaded = check_coverage.typescript_totals(
        hits, {"app/loaded.ts"}, lambda path: ""
    )
    assert (totals.covered_lines, totals.lines, unloaded) == (2, 2, [])


def test_a_missing_report_is_an_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tracked = repository(tmp_path, OWNED)
    options = reports(tmp_path, {}, LOADED)
    options.python_json = tmp_path / "absent.json"
    assert check_coverage.check(options, tmp_path, tracked) == 2
    assert "Coverage report missing or unreadable" in capsys.readouterr().err


def test_code_lines_skip_blank_and_comment_lines() -> None:
    text = "// note\n/* block\n * more\n */\n\nconst a = 1;\n  return a;\n"
    assert check_coverage.code_lines(text) == 2
