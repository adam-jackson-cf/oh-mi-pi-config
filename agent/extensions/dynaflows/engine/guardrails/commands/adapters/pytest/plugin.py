from __future__ import annotations

import json
import os
import platform
import shutil
import tempfile
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol

import pytest
from _pytest.junitxml import xml_key
from _pytest.subtests import SubtestReport

from guardrails.commands.adapters.pytest import guards
from guardrails.commands.adapters.pytest.dialect import (
    GUARDS_PROPERTY,
    SUBTEST_PROPERTIES,
)
from guardrails.host.process import run_text

VIOLATIONS = pytest.StashKey[dict[str, list[str]]]()
STARTED = pytest.StashKey[float]()
XDG_VARIABLES = ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME")
PROTECTED_HOME = "omp_protected_home"


class WorkerNode(Protocol):
    config: pytest.Config
    workerinput: dict[str, object]


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("guardrails")
    group.addoption(
        "--omp-guard",
        action="append",
        default=[],
        metavar="GUARD",
        help=f"arm a tier guard ({', '.join(guards.GUARDS)}); repeat or comma-separate",
    )
    group.addoption(
        "--omp-tier", default=None, metavar="TIER", help="profile tier of this run"
    )
    group.addoption(
        "--omp-collect-out",
        default=None,
        metavar="FILE",
        help="write collected files and node IDs as JSON",
    )


def armed_guards(config: pytest.Config) -> frozenset[str]:
    names = {
        name.strip()
        for value in config.getoption("--omp-guard")
        for name in value.split(",")
        if name.strip()
    }
    unknown = sorted(names - set(guards.GUARDS))
    if unknown:
        raise pytest.UsageError(f"unknown --omp-guard value(s): {', '.join(unknown)}")
    return frozenset(names)


class GuardSession:
    def __init__(self, config: pytest.Config, armed: frozenset[str]) -> None:
        self.config = config
        self.armed = armed
        self.saved_environment: dict[str, str | None] = {}
        self.temporary_home: str | None = None
        self.protected_home: str | None = None

    def start(self) -> None:
        protected = None
        allowed = [str(self.config.rootpath)]
        if guards.USER_DATA in self.armed:
            protected = real_home(self.config)
            self.protected_home = protected
            self.temporary_home = tempfile.mkdtemp(prefix="omp-guarded-home-")
            allowed.extend([self.temporary_home, tempfile.gettempdir()])
            basetemp = self.config.getoption("basetemp", None)
            if basetemp:
                allowed.append(str(basetemp))
            self.redirect_home(self.temporary_home)
        guards.configure(protected, allowed)
        guards.install()

    def redirect_home(self, home: str) -> None:
        values = {"HOME": home, "USERPROFILE": home}
        values.update({
            name: os.path.join(home, name.lower()) for name in XDG_VARIABLES
        })
        for name, value in values.items():
            self.saved_environment[name] = os.environ.get(name)
            os.environ[name] = value

    def stop(self) -> None:
        for name, value in self.saved_environment.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        if self.temporary_home:
            shutil.rmtree(self.temporary_home, ignore_errors=True)


SESSION_KEY = pytest.StashKey[GuardSession]()


def real_home(config: pytest.Config) -> str:
    workerinput = getattr(config, "workerinput", None)
    if workerinput is None:
        return os.path.expanduser("~")
    return str(workerinput[PROTECTED_HOME])


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node: WorkerNode) -> None:
    session = node.config.stash.get(SESSION_KEY, None)
    if session is not None and session.protected_home is not None:
        node.workerinput[PROTECTED_HOME] = session.protected_home


class SubtestTally:
    def __init__(self) -> None:
        self.counts = dict.fromkeys(SUBTEST_PROPERTIES, 0)

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if not isinstance(report, SubtestReport):
            return
        if report.passed and report.when != "call":
            return
        self.counts["tests"] += 1
        if report.failed:
            self.counts["failures" if report.when == "call" else "errors"] += 1


SUBTESTS_KEY = pytest.StashKey[SubtestTally]()


def pytest_configure(config: pytest.Config) -> None:
    config.stash[STARTED] = time.monotonic()
    tally = SubtestTally()
    config.stash[SUBTESTS_KEY] = tally
    config.pluginmanager.register(tally, "omp-subtest-tally")
    armed = armed_guards(config)
    if armed:
        session = GuardSession(config, armed)
        session.start()
        config.stash[SESSION_KEY] = session


def pytest_unconfigure(config: pytest.Config) -> None:
    session = config.stash.get(SESSION_KEY, None)
    if session is not None:
        session.stop()


@contextmanager
def guarded_phase(item: pytest.Item, when: str) -> Generator[None, None, None]:
    session = item.config.stash.get(SESSION_KEY, None)
    if session is None:
        yield
        return
    guards.arm(session.armed)
    try:
        yield
    finally:
        found = guards.disarm()
        if found:
            item.stash.setdefault(VIOLATIONS, {})[when] = found


@pytest.hookimpl(wrapper=True)
def pytest_runtest_setup(item: pytest.Item) -> Generator[None, object, object]:
    with guarded_phase(item, "setup"):
        return (yield)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None, object, object]:
    with guarded_phase(item, "call"):
        return (yield)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_teardown(item: pytest.Item) -> Generator[None, object, object]:
    with guarded_phase(item, "teardown"):
        return (yield)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    report = yield
    found = item.stash.get(VIOLATIONS, {}).get(call.when)
    if found:
        report.outcome = "failed"
        report.longrepr = "\n".join(dict.fromkeys(found))
        if hasattr(report, "wasxfail"):
            del report.wasxfail
    return report


def revision(root: Path) -> str | None:
    try:
        result = run_text(["git", "-C", str(root), "rev-parse", "HEAD"], check=False)
    except OSError:
        return None
    return result.stdout.strip() or None if result.returncode == 0 else None


def run_properties(config: pytest.Config, exit_status: int) -> dict[str, str]:
    session = config.stash.get(SESSION_KEY, None)
    values = {
        "omp.tier": config.getoption("--omp-tier"),
        "omp.runtime": f"python {platform.python_version()}",
        "omp.revision": revision(config.rootpath),
        "omp.wall_s": f"{time.monotonic() - config.stash[STARTED]:.3f}",
        "omp.exit_code": str(int(exit_status)),
        GUARDS_PROPERTY: ",".join(sorted(session.armed)) if session else "",
        **{
            SUBTEST_PROPERTIES[key]: str(count)
            for key, count in config.stash[SUBTESTS_KEY].counts.items()
        },
    }
    return {name: value for name, value in values.items() if value is not None}


@pytest.hookimpl(tryfirst=True)
def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    xml = session.config.stash.get(xml_key, None)
    if xml is None:
        return
    for name, value in run_properties(session.config, exitstatus).items():
        xml.add_global_property(name, value)


def pytest_collection_finish(session: pytest.Session) -> None:
    target = session.config.getoption("--omp-collect-out")
    if not target:
        return
    items = [[str(Path(item.path).resolve()), item.nodeid] for item in session.items]
    Path(target).write_text(json.dumps(items, indent=2) + "\n", encoding="utf-8")
