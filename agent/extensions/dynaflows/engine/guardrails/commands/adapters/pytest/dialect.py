"""pytest's JUnit emission settings and dialect (adapter items 1 and 6).

Emission: :func:`junit_arguments` returns the pytest arguments that write one report per tier.
The ``xunit1`` family is used because only it records each test's ``file`` attribute, so the
shared parser rebuilds the same ``<file>::<Class>::<name>`` IDs the tier listing writes.
``junit_duration_report=total`` makes ``time`` cover setup, call and teardown, which is what the
per-test budget measures. The plugin adds the run properties the shared checkers read
(``omp.tier``, ``omp.runtime``, ``omp.revision``, ``omp.wall_s``, ``omp.exit_code``) plus
``omp.guards``, the tier guards it armed.

Dialect: ``guardrails.toolkit.test_report.junit`` loads ``DIALECT`` from this module when the
report's adapter is ``pytest`` (``--dialect pytest``, or the profile's only adapter). pytest needs no hook overrides: the generic dialect already
reads ``file``, writes an expected failure as ``<skipped type="pytest.xfail">`` (normalised to
``xfail``), keeps the skip reason in the ``message`` attribute and counts rerun elements as
retries. ``source_suffix`` lets an ``xunit2`` report without ``file`` still resolve its files.

IDs match the tier listing only when pytest's rootdir is the repository root, so tier commands
run pytest from the repository root.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from guardrails.toolkit.test_report.junit import Dialect, Report, as_int

GUARDS_PROPERTY = "omp.guards"
SUBTEST_PROPERTIES = {
    "tests": "omp.subtests.tests",
    "failures": "omp.subtests.failures",
    "errors": "omp.subtests.errors",
}


@dataclass(frozen=True)
class PytestDialect(Dialect):
    def complete(self, path: Path, report: Report) -> Report:
        declared = {
            key: count - (as_int(report.properties.get(SUBTEST_PROPERTIES[key])) or 0)
            for key, count in report.declared.items()
        }
        return replace(report, declared=declared)


DIALECT = PytestDialect(name="pytest", source_suffix=".py")


def junit_arguments(report_path: str) -> list[str]:
    """pytest arguments that write a report the shared parser reads with ``DIALECT``."""
    return [
        f"--junitxml={report_path}",
        "-o",
        "junit_family=xunit1",
        "-o",
        "junit_duration_report=total",
        "-o",
        "junit_logging=no",
    ]
