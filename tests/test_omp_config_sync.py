"""Behavioral checks for tracked-file deployment boundaries."""

import importlib.util
import json
import os
import signal
from collections.abc import Iterator
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.fast

MODULE = Path(__file__).resolve().parents[1] / "scripts/omp_config_sync.py"

spec = importlib.util.spec_from_file_location("omp_config_sync", MODULE)

assert spec is not None

assert spec.loader is not None

sync = importlib.util.module_from_spec(spec)

spec.loader.exec_module(sync)


@pytest.fixture
def home(tmp_path: Path) -> Iterator[Path]:
    mask = os.umask(0o077)
    os.umask(mask)
    handler = signal.getsignal(signal.SIGTERM)
    with (
        patch.object(sync.Path, "home", return_value=tmp_path),
        patch.object(sync.sys, "platform", "linux"),
    ):
        yield tmp_path
    os.umask(mask)
    signal.signal(signal.SIGTERM, handler)


@pytest.mark.parametrize(
    "content",
    [
        "broken private fixture",
        "[]",
        "{}",
        '{"revision":"private fixture"}',
        json.dumps({"revision": "a" * 40, "hashes": {"x": "invalid"}}),
    ],
)
def test_corrupt_state_is_explicit_without_echoing_content(
    tmp_path: Path, content: str
) -> None:
    state = tmp_path / "deployment.json"
    state.write_text(content)
    with pytest.raises(RuntimeError) as raised:
        sync.load_state(tmp_path / "source", state)
    assert "private fixture" not in str(raised.value)


def test_missing_dependency_has_friendly_error(home: Path) -> None:
    with pytest.raises(RuntimeError, match="Required executable is missing"):
        sync.dependencies()


def staging_argv(home: Path) -> list[str]:
    return [
        "sync",
        "--runtime",
        str(home / "staging"),
        "--state",
        str(home / "state"),
    ]


def test_cli_rejects_staging_runtime_before_writes(home: Path) -> None:
    with (
        patch.object(sync.sys, "argv", staging_argv(home)),
        pytest.raises(RuntimeError, match="does not support staging"),
    ):
        sync.main()
    assert not (home / "state").exists()


def test_entry_point_reports_runtime_errors_and_exits_one(home: Path) -> None:
    errors = StringIO()
    with patch.object(sync.sys, "argv", staging_argv(home)), redirect_stderr(errors):
        assert sync.run() == 1
    assert "does not support staging" in errors.getvalue()
    assert signal.getsignal(signal.SIGTERM) is sync.terminated
    assert not (home / "state").exists()


def test_termination_requests_a_recovering_retry() -> None:
    with pytest.raises(RuntimeError, match="retry to recover pending work"):
        sync.terminated(signal.SIGTERM, None)
