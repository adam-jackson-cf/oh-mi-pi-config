"""Bun's JUnit reporter, its guard journal and the shared evidence dialect (items 1 and 6).

Emission: :func:`junit_arguments` writes a preload entry beside the external report and returns
the ``bun test`` arguments that load it and write Bun's native JUnit report
(``--reporter=junit --reporter-outfile``). The preload (``preload.mjs``) arms the tier's guards
and appends a journal beside the report (``<report>.journal.jsonl``): the run identity, the guards
it armed, every guard violation, every test registration with its modifiers and every attempt of
a test body.

Bun's native report alone loses evidence the shared checkers need: an expected failure
(``test.failing``) that fails is reported as passed, a retry to green is invisible, tests a
``--test-name-pattern`` filters out appear as skipped, nested suites carry their titles in reverse
in ``classname`` and a test file that fails to load is missing altogether. ``DIALECT.complete``
therefore rebuilds the cases from the report's suite nesting and the journal:

- IDs are ``<file>::<suite> > ... > <title>``, the same IDs the tier listing writes;
- a registered skip (``skip``, ``todo``, ``skipIf``, ``if``, ``xtest`` or a skipped suite) stays
  skipped, its title as the reason (Bun records no skip reason); a skipped case with no skip
  registration was filtered out by the selector and is dropped; when its file focuses tests with
  ``only`` it stays skipped as an evasion;
- an expected failure that failed is ``xfail``, its title as the reason;
- extra attempts of a body are retries;
- each guard violation is an error case, so a caught guard error still fails the report;
- the run properties (``omp.tier``, ``omp.guards``, ``omp.runtime``, ``omp.revision``) come from the
  journal.

A missing journal is a report error, and the report requires the runner's exit code
(``report-check --exit-code``), because only the exit code shows a test file that failed to load.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ElementTree
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from guardrails.foundation.findings import Finding
from guardrails.toolkit.test_report.junit import (
    Case,
    Dialect,
    Report,
    ReportError,
    as_int,
    as_seconds,
    parse_report,
)

JOURNAL_SUFFIX = ".journal.jsonl"
ENTRY_SUFFIX = ".preload.mjs"
PRELOAD = Path(__file__).with_name("preload.ts")
GUARDS_PROPERTY = "omp.guards"
TIER_GUARDS = {
    "t1": ("fast-tier", "user-data"),
    "t2": ("user-data",),
    "t3": ("user-data",),
}
GUARD_RULES = {
    "fast-tier": "TEST.fast-tier-isolation",
    "user-data": "TEST.user-data-guard",
}
# Bun's each-title placeholders: printf tokens and ``$key`` object-row fields.
PLACEHOLDER = re.compile(r"%[sdifjoOp#%]|\$[A-Za-z_][\w.]*")
FOCUS_REASON = "not run: its file focuses other tests with only"


def journal_path(report: Path) -> Path:
    return report.with_name(report.name + JOURNAL_SUFFIX)


def write_entry(entry: Path, settings: dict[str, str]) -> None:
    """The preload entry Bun loads: it starts the adapter's preload with ``settings``."""
    entry.write_text(
        f"import {{ start }} from {json.dumps(PRELOAD.resolve().as_uri())};\n"
        f"start({json.dumps(settings)});\n",
        encoding="utf-8",
    )


def junit_arguments(tier: str, report_path: str) -> list[str]:
    """``bun test`` arguments for a guarded tier run; writes the preload entry beside the report.

    The report must lie outside the target tree, as for every evidence writer. The journal
    starts empty, so evidence from an earlier run never mixes in.
    """
    if tier not in TIER_GUARDS:
        raise ValueError(f"unknown Bun tier {tier!r}")
    report = Path(report_path).resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    journal = journal_path(report)
    journal.write_text("", encoding="utf-8")
    entry = report.with_name(report.name + ENTRY_SUFFIX)
    write_entry(entry, {"mode": "run", "tier": tier, "journal": str(journal)})
    return [
        "--preload",
        str(entry),
        "--reporter=junit",
        f"--reporter-outfile={report}",
    ]


@dataclass(frozen=True)
class NativeCase:
    """One ``<testcase>`` of Bun's report with the suite titles that enclose it."""

    file: str
    suites: tuple[str, ...]
    name: str
    element: ElementTree.Element

    @property
    def id(self) -> str:
        return f"{self.file}::{' > '.join([*self.suites, self.name])}"


def native_cases(root: ElementTree.Element) -> list[NativeCase]:
    """Cases in document order; a top-level suite is a file, nested suites are ``describe``s."""
    found: list[NativeCase] = []

    def visit(element: ElementTree.Element, file: str, suites: tuple[str, ...]) -> None:
        for child in element:
            if child.tag == "testsuite":
                nested = file is not None and file != ""
                visit(
                    child,
                    file or child.get("file", child.get("name", "")),
                    (*suites, child.get("name", "")) if nested else suites,
                )
            elif child.tag == "testcase":
                found.append(
                    NativeCase(
                        child.get("file") or file, suites, child.get("name", ""), child
                    )
                )

    if root.tag == "testsuite":
        visit(root, root.get("file", root.get("name", "")), ())
    else:
        visit(root, "", ())
    return found


def matches_title(template: str, each: bool, name: str) -> bool:
    if template == name:
        return True
    if not each:
        return False
    tokens = PLACEHOLDER.findall(template)
    pieces = PLACEHOLDER.split(template)
    pattern = "".join(
        re.escape(piece) + (".*?" if index < len(tokens) else "")
        for index, piece in enumerate(pieces)
    )
    return re.fullmatch(pattern, name, re.DOTALL) is not None


@dataclass(frozen=True)
class Registration:
    """One journalled ``test`` registration."""

    file: str
    suites: tuple[str, ...]
    suite_each: tuple[bool, ...]
    title: str
    each: bool
    skipped: bool
    failing: bool
    only: bool

    def matches(self, case: NativeCase) -> bool:
        return (
            self.file == case.file
            and len(self.suites) == len(case.suites)
            and all(
                matches_title(template, each, name)
                for template, each, name in zip(
                    self.suites, self.suite_each, case.suites, strict=True
                )
            )
            and matches_title(self.title, self.each, case.name)
        )

    @property
    def key(self) -> tuple[str, tuple[str, ...], str]:
        return self.file, self.suites, self.title


@dataclass(frozen=True)
class Journal:
    run: dict[str, Any]
    guards: tuple[str, ...]
    violations: tuple[str, ...]
    registrations: tuple[Registration, ...]
    # (file, suites, title) -> attempts per row, rows in first-attempt order.
    attempts: dict[tuple[str, tuple[str, ...], str], dict[str, int]]


def events(path: Path) -> Iterator[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as error:
        raise ReportError(
            f"Bun guard journal {path.name} is missing; run bun test with the adapter's junit-args"
        ) from error
    except (OSError, UnicodeError) as error:
        raise ReportError(f"Bun guard journal cannot be read: {error}") from error
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError as error:
            raise ReportError(f"Bun guard journal line {number} is not JSON") from error
        if not isinstance(event, dict):
            raise ReportError(f"Bun guard journal line {number} is not an object")
        yield event


def read_journal(path: Path) -> Journal:
    run: dict[str, Any] = {}
    armed: list[set[str]] = []
    violations: list[str] = []
    registrations: list[Registration] = []
    attempts: dict[tuple[str, tuple[str, ...], str], dict[str, int]] = {}
    for event in events(path):
        kind = event.get("kind")
        if kind == "run":
            run = event
        elif kind == "armed":
            armed.append(set(event.get("guards", [])))
        elif kind == "violation":
            violations.append(str(event.get("reason", "")))
        elif kind == "case":
            registrations.append(
                Registration(
                    str(event.get("file", "")),
                    tuple(event.get("suites", [])),
                    tuple(bool(each) for each in event.get("suiteEach", [])),
                    str(event.get("title", "")),
                    bool(event.get("each")),
                    bool(event.get("skipped")),
                    bool(event.get("failing")),
                    bool(event.get("only")),
                )
            )
        elif kind == "attempt":
            key = (
                str(event.get("file", "")),
                tuple(event.get("suites", [])),
                str(event.get("title", "")),
            )
            rows = attempts.setdefault(key, {})
            row = str(event.get("row", ""))
            rows[row] = rows.get(row, 0) + 1
    guards = set.intersection(*armed) if armed else set()
    return Journal(
        run, tuple(sorted(guards)), tuple(violations), tuple(registrations), attempts
    )


def retries_for(registration: Registration, journal: Journal, position: int) -> int:
    """Extra attempts of the ``position``-th row (document order) of a registration."""
    rows = list(journal.attempts.get(registration.key, {}).values())
    if not rows:
        return 0
    count = rows[position] if position < len(rows) else rows[-1]
    return max(count - 1, 0)


class BunDialect(Dialect):
    """Bun's native JUnit completed from the preload's journal (see the module docstring)."""

    def complete(self, path: Path, report: Report) -> Report:
        journal = read_journal(journal_path(path))
        try:
            root = ElementTree.parse(path).getroot()
        except ElementTree.ParseError as error:
            raise ReportError(f"run report is not well-formed XML: {error}") from error
        focused = {item.file for item in journal.registrations if item.only}
        cases: list[Case] = []
        dropped = 0
        positions: dict[Registration, int] = {}
        for native in native_cases(root):
            registration = next(
                (item for item in journal.registrations if item.matches(native)), None
            )
            outcome, reason = self.outcome(native.element)
            retries = 0
            if outcome == "skipped":
                if registration is not None and registration.skipped:
                    reason = native.name
                elif native.file in focused:
                    reason = FOCUS_REASON
                else:
                    dropped += 1
                    continue
            elif registration is not None:
                position = positions.get(registration, 0)
                positions[registration] = position + 1
                retries = retries_for(registration, journal, position)
                if registration.failing and outcome == "passed":
                    outcome, reason = "xfail", native.name
            cases.append(
                Case(
                    native.id,
                    native.file,
                    as_seconds(native.element.get("time")),
                    outcome,
                    retries,
                    reason,
                )
            )
        cases.extend(
            Case(f"::guardrails::violation[{index}]", "", 0.0, "error", 0, reason)
            for index, reason in enumerate(journal.violations)
        )
        properties = dict(report.properties)
        if journal.run:
            properties["omp.tier"] = str(journal.run.get("tier", ""))
            properties["omp.runtime"] = str(journal.run.get("runtime", ""))
            revision = str(journal.run.get("revision", ""))
            if revision:
                properties["omp.revision"] = revision
        properties[GUARDS_PROPERTY] = ",".join(journal.guards)
        # Bun's leaf suites leave out a file's top-level tests; its outer element counts them all.
        declared = {
            key: count - (dropped if key == "tests" else 0)
            for key in ("tests", "failures")
            if (count := as_int(root.get(key))) is not None
        }
        return replace(
            report, cases=tuple(cases), properties=properties, declared=declared
        )


DIALECT = BunDialect(name="bun_test", source_suffix=".ts", requires_exit_code=True)


def guard_evidence(report: Path, tier: str) -> list[Finding]:
    """Reject absent evidence or a run that did not arm every guard of its tier."""
    if tier not in TIER_GUARDS:
        raise ValueError(f"unknown Bun tier {tier!r}")
    try:
        parsed = parse_report(report, DIALECT)
        if parsed.properties.get("omp.tier") != tier:
            raise ReportError(f"report does not record tier {tier}")
    except ReportError as error:
        return [
            Finding(
                "TEST.failure-propagation",
                str(report),
                str(error),
                "rerun bun test with the adapter's junit-args",
            )
        ]
    armed = set(parsed.properties.get(GUARDS_PROPERTY, "").split(","))
    return [
        Finding(
            GUARD_RULES[guard],
            str(report),
            f"tier {tier} ran without the {guard} guard",
            "rerun bun test with the adapter's junit-args",
        )
        for guard in TIER_GUARDS[tier]
        if guard not in armed
    ]
