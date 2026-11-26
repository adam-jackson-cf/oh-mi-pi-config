"""PYQ, PYS and FANOUT checkers of the guardrails engine on small configured repositories."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/agent-rails/engine")
)

import pytest

from guardrails.checker.python_rules import settings
from guardrails.commands.python_policy import quality

pytestmark = pytest.mark.fast


class FailClosedDefaultTests(unittest.TestCase):
    def test_unset_class_limits_use_the_catalogue_contract(self) -> None:
        assert quality.class_limits(settings.load_settings({})) == {
            "max_lines": 700,
            "max_public_methods": None,
            "max_init_args": 8,
            "max_instance_attributes": 12,
        }


if __name__ == "__main__":
    unittest.main()
