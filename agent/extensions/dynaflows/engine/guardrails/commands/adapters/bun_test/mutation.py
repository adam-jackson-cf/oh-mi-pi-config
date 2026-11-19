"""Bun mutation evidence (adapter item 5): StrykerJS with its built-in command test runner.

Stryker ships no Bun test runner; its ``command`` test runner runs any test command and reports
each mutant (https://stryker-mutator.io/docs/stryker-js/configuration/#commandrunner-object).
The T3 run uses ``testRunner: "command"``, ``commandRunner.command: "bun test"``,
``coverageAnalysis: "off"`` (the command runner reports no per-test coverage) and the JSON
reporter. The report follows the same mutation-testing-report schema as the Vitest run, so the
shared Stryker normaliser converts it.
"""

from __future__ import annotations

from guardrails.commands.adapters.vitest.mutation import TOOL, VERSION, write_report

RUNNER = "command"
COMMAND = "bun test"

__all__ = ["COMMAND", "RUNNER", "TOOL", "VERSION", "write_report"]
