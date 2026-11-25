"""The shared git blob batch reader the structure-naming scans use."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/agent-rails/engine")
)

import pytest

from guardrails.context import changes


@pytest.mark.fast
class BlobBatchTests(unittest.TestCase):
    def test_no_blobs_need_no_git_and_a_failed_batch_is_a_git_error(self) -> None:
        assert changes.read_blobs(Path("."), []) == {}
        failed = SimpleNamespace(returncode=1, stdout=b"", stderr=b"bad object")
        with (
            patch.object(changes, "run_bytes", return_value=failed),
            pytest.raises(changes.DiffError, match="bad object"),
        ):
            changes.read_blobs(Path("."), ["0" * 40])


if __name__ == "__main__":
    unittest.main()
