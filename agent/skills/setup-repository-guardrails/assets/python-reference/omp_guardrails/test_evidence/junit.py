"""Parse a JUnit XML run report into the normalised report schema the shared checkers read.

Normalised schema
-----------------
- ``Case``: ``id`` (``<repo-relative file>::<qualified name>``), ``file``, ``duration_s``,
  ``outcome`` (one of ``passed``, ``failed``, ``skipped``, ``xfail``, ``error``), ``retries``
  (re-runs recorded before the final outcome) and ``reason`` (skip, xfail or failure message).
- ``Report``: the cases in document order, the run ``properties`` (every ``<property>`` under a
  ``<testsuites>`` or ``<testsuite>`` element), the outer ``wall_s``, and the counts the
  report's leaf suites declare (``tests``, ``failures``, ``errors``), used to detect truncation.

Run properties
--------------
Adapters write these ``<property>`` names into the report so a single file carries the run's
identity. Checkers read them; none is framework-specific.

- ``omp.revision``: the commit the run measured (``git rev-parse HEAD``); freshness check.
- ``omp.runtime``: the test runtime and version, e.g. ``python 3.12.4`` or ``node 22.3.0``;
  input to the budget checker's machine-id probe.
- ``omp.tier``: the profile tier key the run selected, e.g. ``t1``.
- ``omp.wall_s``: outer wall-clock seconds of the run; falls back to the root ``time``.
- ``omp.exit_code``: the test runner's exit code.

Dialects
--------
JUnit dialects differ in where they put the file, how they mark xfail and how they record
retries. ``Dialect`` holds those choices as data, so most frameworks are configured, not
adapted: profile ``guardrails.test_evidence.dialect`` may be an object with the ``Dialect``
fields below. An adapter whose dialect needs code subclasses ``Dialect``, overrides a hook
(``case_file``, ``case_name``, ``outcome``, ``retries``) and calls ``register_dialect``, or
exposes ``DIALECT`` from ``omp_guardrails.adapters.<name>.dialect``.

File resolution (``Dialect.case_file``), first match wins:

1. a ``file`` attribute on the test case;
2. a ``classname`` that contains ``/`` is a file path (vitest and jest style);
3. a dotted ``classname`` is a module path when ``source_suffix`` is set: the trailing
   segments that start with an upper-case letter are classes, the rest form
   ``a/b/module<source_suffix>`` (pytest xunit2 and unittest style);
4. otherwise the classname as written.
"""

from __future__ import annotations

import importlib
import math
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from pathlib import Path

OUTCOMES = ("passed", "failed", "skipped", "xfail", "error")
FAILING = frozenset({"failed", "error"})


class ReportError(Exception):
    """The run report is absent, unreadable or not JUnit XML."""


class UsageError(Exception):
    """A checker was called with arguments it cannot use."""


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


@dataclass(frozen=True)
class Dialect:
    """How one JUnit dialect maps onto the normalised schema. Subclass to override hooks."""

    name: str = "generic"
    source_suffix: str | None = None
    xfail_markers: tuple[str, ...] = ("xfail",)
    retry_tags: tuple[str, ...] = ("rerunFailure", "rerunError", "flakyFailure", "flakyError")
    retry_properties: tuple[str, ...] = ("retries", "retry")

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
        """``Class::name`` for the classname segments below the file's module, else ``name``."""
        name = case.get("name", "").strip()
        classname = case.get("classname", "").strip()
        if not classname or "/" in classname or classname == file:
            return name
        stem, dot, _ = file.rpartition(".")
        module = (stem if dot and "/" not in file[len(stem) :] else file).replace("/", ".")
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
    """A finite non-negative number of seconds, or None when absent or malformed."""
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
    """The registered or adapter-provided dialect, or the generic one configured by data."""
    if isinstance(configured, dict):
        unknown = sorted(set(configured) - set(DIALECT_FIELDS))
        if unknown:
            raise UsageError(f"guardrails.test_evidence.dialect has unknown fields {unknown}")
        values = {
            key: tuple(value) if isinstance(value, list) else value
            for key, value in configured.items()
        }
        return Dialect(name=name or "configured", **values)
    if not name or name in DIALECTS:
        return DIALECTS[name or "generic"]
    module_name = f"omp_guardrails.adapters.{name}.dialect"
    try:
        module = importlib.import_module(module_name)
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
    return parse_text(text, dialect, source or path.as_posix())


def parse_text(text: str, dialect: Dialect, source: str) -> Report:
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as error:
        raise ReportError(f"run report is not well-formed XML: {error}") from error
    if root.tag not in {"testsuites", "testsuite"}:
        raise ReportError(f"run report root is <{root.tag}>, not <testsuites> or <testsuite>")
    cases = tuple(normalise(case, dialect) for case in root.iter("testcase"))
    properties = {}
    for holder in [root, *root.iter("testsuite")]:
        for group in holder.findall("properties"):
            for item in group.findall("property"):
                if item.get("name"):
                    properties[item.get("name", "")] = item.get("value", item.text or "")
    wall = properties.get("omp.wall_s", root.get("time"))
    return Report(source, cases, properties, as_seconds(wall), declared_counts(root))


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
    """Counts declared by leaf suites (or the root when it is the only suite)."""
    suites = [suite for suite in root.iter("testsuite") if suite.find("testsuite") is None]
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
    mode: str
    revision: str | None
    exit_code: int | None
    dialect: str | None


VALUED = {"--junit", "--tier", "--mode", "--revision", "--exit-code", "--dialect"}
MODES = ("local", "ci")


def report_options(repo: Path, argv: list[str]) -> ReportOptions:
    """Read the report-check arguments; unknown arguments belong to other checkers."""
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
    mode = values.get("--mode", "local")
    if mode not in MODES:
        raise UsageError(f"--mode must be one of {', '.join(MODES)}")
    exit_text = values.get("--exit-code")
    exit_code = as_int(exit_text) if exit_text is not None else None
    if exit_text is not None and exit_code is None:
        raise UsageError("--exit-code must be an integer")
    junit = values.get("--junit")
    return ReportOptions(
        junit=(repo / junit) if junit else None,
        tier=values.get("--tier"),
        mode=mode,
        revision=values.get("--revision"),
        exit_code=exit_code,
        dialect=values.get("--dialect"),
    )


def evidence_settings(profile: dict) -> dict:
    section = profile.get("guardrails")
    value = section.get("test_evidence") if isinstance(section, dict) else None
    return value if isinstance(value, dict) else {}


def load_run_report(repo: Path, profile: dict, options: ReportOptions) -> Report:
    """Parse ``--junit`` with the dialect named by ``--dialect``, settings or the adapter."""
    if options.junit is None:
        raise ReportError("no run report given; pass --junit <path>")
    settings = evidence_settings(profile)
    adapter = profile.get("adapter") if isinstance(profile.get("adapter"), str) else None
    dialect = dialect_for(options.dialect or adapter, settings.get("dialect"))
    return parse_report(options.junit, dialect, display_path(repo, options.junit))


def display_path(repo: Path, path: Path | None) -> str:
    """A report path relative to the repository when inside it; the subcommand otherwise."""
    if path is None:
        return "report-check"
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return path.as_posix()
