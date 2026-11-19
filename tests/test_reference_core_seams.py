"""Record profile loading, PROFILE.complete, findings and CLI dispatch of the guardrails reference: the T2 seams.

These tests start processes or drive real Git repositories, so they run in T2.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/dynaflows/engine")
)


import pytest
from test_reference_core import CONTRACT_SUBCOMMANDS, ROOT, CliCase

pytestmark = pytest.mark.seam


REFERENCE = ROOT / "agent/extensions/dynaflows/engine"


class CliTests(CliCase):
    def test_subcommand_help_needs_no_profile_or_pytest_extra(self) -> None:
        # The child blocks pytest and ast_grep_py imports, as an install without the extra would.
        entry = (
            "import sys; sys.modules['pytest'] = None; sys.modules['ast_grep_py'] = None; "
            "from guardrails.commands import cli; raise SystemExit(cli.main(sys.argv[1:]))"
        )
        for name in sorted(CONTRACT_SUBCOMMANDS - {"adapter"}):
            with self.subTest(subcommand=name):
                result = subprocess.run(
                    [sys.executable, "-c", entry, *self.located(name, "--help")],
                    cwd=REFERENCE,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                assert result.returncode == 0, result.stderr
                assert name in result.stdout
                assert "does not exist" not in result.stdout

    def test_checker_modules_are_imported_only_when_their_subcommand_runs(self) -> None:
        probe = (
            "import sys; from guardrails.commands import cli; "
            "listed = {m for item in cli.SUBCOMMANDS.values() for m in item.modules}; "
            "print(sorted(listed & set(sys.modules) - {'guardrails.context.config.profile'}))"
        )
        result = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=REFERENCE,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        assert result.stdout.strip() == "[]"


if __name__ == "__main__":
    unittest.main()
