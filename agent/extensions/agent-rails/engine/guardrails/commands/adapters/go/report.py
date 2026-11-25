from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from pathlib import Path

from guardrails.commands.adapters.go.dialect import SKIP_TYPE, XFAIL_PREFIX, XFAIL_TYPE
from guardrails.commands.adapters.go.packages import Package

FRAMING = re.compile(r"^(=== (RUN|PAUSE|CONT|NAME)|--- (PASS|FAIL|SKIP)):?\s")
LOCATION = re.compile(r"^\S+\.go:\d+: ")
BUILD_SUFFIX = re.compile(r" \[.*\]$")
MESSAGE_LIMIT = 1000
TERMINAL = frozenset({"pass", "fail", "skip"})
FAILING = frozenset({"failed", "error"})


@dataclass
class Record:
    package: str
    name: str
    action: str | None = None
    elapsed: float | None = None
    output: list[str] = field(default_factory=list)


@dataclass
class Stream:
    records: dict[tuple[str, str], Record] = field(default_factory=dict)
    package_actions: dict[str, str] = field(default_factory=dict)
    package_elapsed: dict[str, float] = field(default_factory=dict)
    package_output: dict[str, list[str]] = field(default_factory=dict)
    loose: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Converted:
    case_id: str
    file: str
    name: str
    outcome: str
    message: str
    text: str
    elapsed: float | None
    package: str


def read_stream(stdout: str) -> Stream:
    stream = Stream()
    for raw in stdout.splitlines():
        try:
            event = json.loads(raw)
        except ValueError:
            stream.loose.append(raw)
            continue
        if not isinstance(event, dict):
            stream.loose.append(raw)
            continue
        action = str(event.get("Action", ""))
        package = str(
            event.get("Package")
            or BUILD_SUFFIX.sub("", str(event.get("ImportPath", "")))
        )
        test = event.get("Test")
        output = event.get("Output")
        if isinstance(test, str) and test:
            top = test.split("/", 1)[0]
            record = stream.records.setdefault((package, top), Record(package, top))
            if isinstance(output, str):
                record.output.append(output)
            if test == top and action in TERMINAL:
                record.action = action
                elapsed = event.get("Elapsed")
                record.elapsed = (
                    float(elapsed) if isinstance(elapsed, (int, float)) else None
                )
            continue
        if isinstance(output, str):
            stream.package_output.setdefault(package, []).append(output)
        if action in TERMINAL or action == "build-fail":
            stream.package_actions[package] = (
                "fail" if action == "build-fail" else action
            )
            elapsed = event.get("Elapsed")
            if isinstance(elapsed, (int, float)):
                stream.package_elapsed[package] = float(elapsed)
    return stream


def messages(lines: list[str]) -> list[str]:
    found = []
    for raw in "".join(lines).splitlines():
        text = raw.strip()
        if not text or FRAMING.match(text + " "):
            continue
        found.append(LOCATION.sub("", text))
    return found


def headline(found: list[str]) -> str:
    panics = [item for item in found if item.startswith("panic:")]
    return " ".join(panics or found)[:MESSAGE_LIMIT]


def located(record: Record, packages: dict[str, Package]) -> str:
    package = packages.get(record.package)
    if package is None:
        return record.package
    return package.tests.get(record.name, package.directory)


def record_case(
    record: Record, packages: dict[str, Package], stream: Stream
) -> Converted:
    file = located(record, packages)
    found = messages(record.output)
    text = "".join(record.output)
    if record.action == "pass":
        outcome, message = "passed", ""
    elif record.action == "skip":
        reason = " ".join(found)
        expected = reason.startswith(XFAIL_PREFIX)
        outcome = "xfail" if expected else "skipped"
        message = reason.removeprefix(XFAIL_PREFIX).strip() if expected else reason
    elif record.action == "fail":
        outcome, message = "failed", headline(found)
    else:
        package_output = stream.package_output.get(record.package, [])
        outcome = "error"
        message = (
            headline([*found, *messages(package_output)])
            or "the test binary stopped before the test finished"
        )
        text += "".join(package_output)
    return Converted(
        f"{file}::{record.name}",
        file,
        record.name,
        outcome,
        message,
        text,
        record.elapsed,
        record.package,
    )


def package_case(
    stream: Stream, packages: dict[str, Package], package: str
) -> Converted:
    known = packages.get(package)
    file = known.directory if known is not None else package
    output = stream.package_output.get(package, [])
    return Converted(
        f"{file}::package {package}",
        file,
        f"package {package}",
        "error",
        headline(messages(output)) or f"package {package} failed outside its tests",
        "".join(output),
        stream.package_elapsed.get(package),
        package,
    )


def convert(
    stream: Stream, packages: dict[str, Package], violations: list[str]
) -> list[Converted]:
    cases = [
        record_case(record, packages, stream) for record in stream.records.values()
    ]
    failing = {case.package for case in cases if case.outcome in FAILING}
    cases.extend(
        package_case(stream, packages, package)
        for package, action in sorted(stream.package_actions.items())
        if action == "fail" and package not in failing
    )
    reported = "\n".join(case.text for case in cases if case.outcome in FAILING)
    cases.extend(
        Converted(
            f".::{violation}", ".", violation, "error", violation, violation, None, ""
        )
        for violation in dict.fromkeys(violations)
        if violation not in reported
    )
    return cases


def testcase(case: Converted) -> ElementTree.Element:
    element = ElementTree.Element(
        "testcase",
        classname=case.file,
        name=case.name,
        file=case.file,
        time=f"{case.elapsed:.3f}" if case.elapsed is not None else "0",
    )
    if case.outcome == "failed":
        child = ElementTree.SubElement(element, "failure", message=case.message)
        child.text = case.text
    elif case.outcome == "error":
        child = ElementTree.SubElement(element, "error", message=case.message)
        child.text = case.text
    elif case.outcome == "xfail":
        ElementTree.SubElement(
            element, "skipped", type=XFAIL_TYPE, message=case.message
        )
    elif case.outcome == "skipped":
        ElementTree.SubElement(element, "skipped", type=SKIP_TYPE, message=case.message)
    return element


def counts(cases: list[Converted]) -> dict[str, str]:
    return {
        "tests": str(len(cases)),
        "failures": str(sum(case.outcome == "failed" for case in cases)),
        "errors": str(sum(case.outcome == "error" for case in cases)),
        "skipped": str(sum(case.outcome in {"skipped", "xfail"} for case in cases)),
    }


def write_junit(
    out: Path, cases: list[Converted], properties: dict[str, str], wall_s: float
) -> None:
    root = ElementTree.Element(
        "testsuites", {"name": "go test", "time": f"{wall_s:.3f}", **counts(cases)}
    )
    group = ElementTree.SubElement(root, "properties")
    for name, value in properties.items():
        ElementTree.SubElement(group, "property", name=name, value=value)
    by_package: dict[str, list[Converted]] = {}
    for case in cases:
        by_package.setdefault(case.package, []).append(case)
    for package, members in sorted(by_package.items()):
        suite = ElementTree.SubElement(
            root, "testsuite", {"name": package or "go test", **counts(members)}
        )
        suite.extend(testcase(case) for case in members)
    out.parent.mkdir(parents=True, exist_ok=True)
    ElementTree.ElementTree(root).write(out, encoding="utf-8", xml_declaration=True)
