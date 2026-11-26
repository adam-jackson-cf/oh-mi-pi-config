from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from guardrails.foundation import conventions
from guardrails.test_report.junit import Dialect, Report, as_int

SUBTEST_PROPERTIES = {
    "tests": "guardrails.subtests.tests",
    "failures": "guardrails.subtests.failures",
    "errors": "guardrails.subtests.errors",
}


@dataclass(frozen=True)
class PytestDialect(Dialect):
    def complete(self, path: Path, report: Report) -> Report:
        declared = {
            key: count - (as_int(report.properties.get(SUBTEST_PROPERTIES[key])) or 0)
            for key, count in report.declared.items()
        }
        return replace(report, declared=declared)


NAME = __name__.split(".")[-2]
# A dotted classname names a Python module that runs: its path plus the runtime extension.
(SOURCE_SUFFIX,) = conventions.runtime_extensions("python")
DIALECT = PytestDialect(name=NAME, source_suffix=SOURCE_SUFFIX)


def junit_arguments(report_path: str) -> list[str]:
    return [
        f"--junitxml={report_path}",
        "-o",
        "junit_family=xunit1",
        "-o",
        "junit_duration_report=total",
        "-o",
        "junit_logging=no",
    ]
