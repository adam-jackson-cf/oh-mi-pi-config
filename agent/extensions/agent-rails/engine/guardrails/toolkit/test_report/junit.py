from __future__ import annotations

import math
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from guardrails.context.config import adapter_declarations
from guardrails.foundation.inventory import settings

FAILING = frozenset({"failed", "error"})
TIER_PROPERTY = "guardrails.tier"
GUARDS_PROPERTY = "guardrails.guards"
RUNTIME_PROPERTY = "guardrails.runtime"
REVISION_PROPERTY = "guardrails.revision"
WALL_PROPERTY = "guardrails.wall_s"
EXIT_CODE_PROPERTY = "guardrails.exit_code"


class ReportError(Exception):
    pass


class UsageError(Exception):
    pass


@dataclass(frozen=True)
class Case:
    id: str
    file: str
    duration_s: float | None
    outcome: str
    retries: int = 0
    reason: str = ""


@dataclass(frozen=True)
class Report:
    source: str
    cases: tuple[Case, ...]
    properties: dict[str, str] = field(default_factory=dict)
    wall_s: float | None = None
    declared: dict[str, int] = field(default_factory=dict)
    requires_exit_code: bool = False


@dataclass(frozen=True)
class Dialect:
    name: str = "generic"
    source_suffix: str | None = None
    xfail_markers: tuple[str, ...] = ("xfail",)
    retry_tags: tuple[str, ...] = (
        "rerunFailure",
        "rerunError",
        "flakyFailure",
        "flakyError",
    )
    retry_properties: tuple[str, ...] = ("retries", "retry")
    requires_exit_code: bool = False

    def case_file(self, case: ElementTree.Element) -> str:
        explicit = case.get("file", "").strip()
        if explicit:
            return explicit.replace("\\", "/").removeprefix("./")
        classname = case.get("classname", "").strip()
        if "/" in classname or not self.source_suffix or not classname:
            return classname
        modules = classname.split(".")
        while len(modules) > 1 and modules[-1][:1].isupper():
            modules.pop()
        return "/".join(modules) + self.source_suffix

    def case_name(self, case: ElementTree.Element, file: str) -> str:
        name = case.get("name", "").strip()
        classname = case.get("classname", "").strip()
        if not classname or "/" in classname or classname == file:
            return name
        stem, dot, _ = file.rpartition(".")
        module = (stem if dot and "/" not in file[len(stem) :] else file).replace(
            "/", "."
        )
        if classname == module or classname.startswith(f"{module}."):
            classname = classname[len(module) :]
        return "::".join([*filter(None, classname.split(".")), name])

    def outcome(self, case: ElementTree.Element) -> tuple[str, str]:
        for tag, outcome in (("error", "error"), ("failure", "failed")):
            element = case.find(tag)
            if element is not None:
                return outcome, message(element)
        skipped = case.find("skipped")
        if skipped is not None:
            marker = f"{skipped.get('type', '')} {skipped.get('message', '')}".lower()
            xfail = any(item.lower() in marker for item in self.xfail_markers)
            return ("xfail" if xfail else "skipped"), message(skipped)
        return "passed", ""

    def complete(self, path: Path, report: Report) -> Report:
        del path
        return report

    def retries(self, case: ElementTree.Element) -> int:
        count = sum(1 for child in case if child.tag in self.retry_tags)
        for item in case.iter("property"):
            if item.get("name") in self.retry_properties:
                count = max(count, as_int(item.get("value")) or 0)
        return count


def message(element: ElementTree.Element) -> str:
    text = element.get("message") or element.text or ""
    return " ".join(text.split())[:200]


def as_int(value: str | None) -> int | None:
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def as_seconds(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value.replace(",", "").strip())
    except ValueError:
        return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


DIALECTS: dict[str, Dialect] = {"generic": Dialect()}
DIALECT_FIELDS = ("source_suffix", "xfail_markers", "retry_tags", "retry_properties")


def register_dialect(dialect: Dialect) -> None:
    DIALECTS[dialect.name] = dialect


def dialect_for(name: str | None, configured: object = None) -> Dialect:
    if isinstance(configured, dict):
        unknown = sorted(set(configured) - set(DIALECT_FIELDS))
        if unknown:
            raise UsageError(
                f"checkers.test_evidence.dialect has unknown fields {unknown}"
            )
        values: dict[str, Any] = {
            key: tuple(value) if isinstance(value, list) else value
            for key, value in configured.items()
        }
        return Dialect(name=name or "configured", **values)
    if not name or name in DIALECTS:
        return DIALECTS[name or "generic"]
    module_name = f"{adapter_declarations.module_name(name)}.dialect"
    try:
        module = adapter_declarations.adapter_part(name, "dialect")
    except ModuleNotFoundError as error:
        if error.name and module_name.startswith(error.name):
            return DIALECTS["generic"]
        raise
    dialect = getattr(module, "DIALECT", None)
    if not isinstance(dialect, Dialect):
        raise UsageError(f"{module_name} must expose DIALECT as a Dialect instance")
    register_dialect(dialect)
    return dialect


def parse_report(path: Path, dialect: Dialect, source: str | None = None) -> Report:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise ReportError("run report does not exist") from error
    except (OSError, UnicodeError) as error:
        raise ReportError(f"run report cannot be read: {error}") from error
    return dialect.complete(path, parse_text(text, dialect, source or path.as_posix()))


def property_items(holder: ElementTree.Element) -> list[ElementTree.Element]:
    return [
        item
        for group in holder.findall("properties")
        for item in group.findall("property")
    ]


def parse_text(text: str, dialect: Dialect, source: str) -> Report:
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as error:
        raise ReportError(f"run report is not well-formed XML: {error}") from error
    if root.tag not in {"testsuites", "testsuite"}:
        raise ReportError(
            f"run report root is <{root.tag}>, not <testsuites> or <testsuite>"
        )
    cases = tuple(normalise(case, dialect) for case in root.iter("testcase"))
    properties = {}
    for holder in [root, *root.iter("testsuite")]:
        for item in property_items(holder):
            if item.get("name"):
                properties[item.get("name", "")] = item.get("value", item.text or "")
    wall = properties.get(WALL_PROPERTY, root.get("time"))
    return Report(
        source,
        cases,
        properties,
        as_seconds(wall),
        declared_counts(root),
        dialect.requires_exit_code,
    )


def normalise(case: ElementTree.Element, dialect: Dialect) -> Case:
    file = dialect.case_file(case)
    name = dialect.case_name(case, file)
    outcome, reason = dialect.outcome(case)
    return Case(
        id=f"{file}::{name}",
        file=file,
        duration_s=as_seconds(case.get("time")),
        outcome=outcome,
        retries=dialect.retries(case),
        reason=reason,
    )


def declared_counts(root: ElementTree.Element) -> dict[str, int]:
    suites = [
        suite for suite in root.iter("testsuite") if suite.find("testsuite") is None
    ]
    totals: dict[str, int] = {}
    for suite in suites or [root]:
        for key in ("tests", "failures", "errors"):
            value = as_int(suite.get(key))
            if value is not None:
                totals[key] = totals.get(key, 0) + value
    return totals


@dataclass(frozen=True)
class ReportOptions:
    junit: Path | None
    tier: str | None
    revision: str | None
    exit_code: int | None
    dialect: str | None


VALUED = {"--junit", "--tier", "--revision", "--exit-code", "--dialect"}


def report_options(repo: Path, argv: list[str]) -> ReportOptions:
    values: dict[str, str] = {}
    index = 0
    while index < len(argv):
        flag, _, inline = argv[index].partition("=")
        if flag in VALUED:
            if inline:
                values[flag] = inline
            elif index + 1 < len(argv):
                index += 1
                values[flag] = argv[index]
            else:
                raise UsageError(f"{flag} needs a value")
        index += 1
    exit_text = values.get("--exit-code")
    exit_code = as_int(exit_text) if exit_text is not None else None
    if exit_text is not None and exit_code is None:
        raise UsageError("--exit-code must be an integer")
    junit = values.get("--junit")
    return ReportOptions(
        junit=(repo / junit) if junit else None,
        tier=values.get("--tier"),
        revision=values.get("--revision"),
        exit_code=exit_code,
        dialect=values.get("--dialect"),
    )


def evidence_settings(config: dict[str, Any]) -> dict[str, Any]:
    return settings(config, "test_evidence")


def load_run_report(
    repo: Path, config: dict[str, Any], options: ReportOptions
) -> Report:
    if options.junit is None:
        raise ReportError("no run report given; pass --junit <path>")
    settings = evidence_settings(config)
    dialect = dialect_for(
        options.dialect or report_adapter(config), settings.get("dialect")
    )
    return parse_report(
        options.junit, dialect, display_path(repo, options.junit, "run report")
    )


def report_adapter(config: dict[str, Any]) -> str | None:
    declared = adapter_declarations.names(config)
    if len(declared) > 1:
        raise UsageError(
            f"tests.adapters declares {declared}; pass --dialect <adapter> naming "
            "the one that wrote this report"
        )
    return declared[0] if declared else None


def display_path(repo: Path, path: Path | None, subcommand: str) -> str:
    if path is None:
        return subcommand
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return path.as_posix()
