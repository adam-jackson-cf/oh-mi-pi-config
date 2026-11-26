from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ElementTree
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from guardrails.commands.adapters import contract
from guardrails.foundation.findings import Finding
from guardrails.test_report.junit import (
    GUARDS_PROPERTY,
    REVISION_PROPERTY,
    RUNTIME_PROPERTY,
    TIER_PROPERTY,
    Case,
    Dialect,
    Report,
    ReportError,
    as_int,
    as_seconds,
)

JOURNAL_SUFFIX = ".journal.jsonl"
ENTRY_SUFFIX = ".preload.mjs"
PRELOAD = Path(__file__).with_name("preload.ts")
RERUN = "rerun bun test with the adapter's junit-args"
PLACEHOLDER = re.compile(r"%[sdifjoOp#%]|\$[A-Za-z_][\w.]*")
FOCUS_REASON = "not run: its file focuses other tests with only"


def journal_path(report: Path) -> Path:
    return report.with_name(report.name + JOURNAL_SUFFIX)


def write_entry(entry: Path, settings: dict[str, str]) -> None:
    entry.write_text(
        f"import {{ start }} from {json.dumps(PRELOAD.resolve().as_uri())};\n"
        f"start({json.dumps(settings)});\n",
        encoding="utf-8",
    )


def junit_arguments(tier: str, report_path: str) -> list[str]:
    if tier not in contract.TIER_GUARDS:
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
    file: str
    suites: tuple[str, ...]
    name: str
    element: ElementTree.Element

    @property
    def id(self) -> str:
        return f"{self.file}::{' > '.join([*self.suites, self.name])}"


def native_cases(root: ElementTree.Element) -> list[NativeCase]:
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
    rows = list(journal.attempts.get(registration.key, {}).values())
    if not rows:
        return 0
    count = rows[position] if position < len(rows) else rows[-1]
    return max(count - 1, 0)


class BunDialect(Dialect):
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
            properties[TIER_PROPERTY] = str(journal.run.get("tier", ""))
            properties[RUNTIME_PROPERTY] = str(journal.run.get("runtime", ""))
            revision = str(journal.run.get("revision", ""))
            if revision:
                properties[REVISION_PROPERTY] = revision
        properties[GUARDS_PROPERTY] = ",".join(journal.guards)
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
    return contract.guard_evidence(report, tier, DIALECT, RERUN)
