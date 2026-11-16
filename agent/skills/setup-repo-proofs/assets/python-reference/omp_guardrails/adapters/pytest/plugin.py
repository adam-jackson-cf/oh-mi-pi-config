"""pytest plugin: tier guards, run-report properties and collect-only ID listing.

Load it with ``-p omp_guardrails.adapters.pytest.plugin``. ``omp-guardrails adapter junit-args``
prints the full argument list for a tier.

- ``--omp-guard fast-tier`` (TEST.fast-tier-isolation): blocks process creation and IP network
  traffic during every test's setup, call and teardown. The T1 command must pass it.
- ``--omp-guard user-data`` (TEST.user-data-guard): points ``HOME`` and ``XDG_*`` at a fresh
  temporary directory for the session and blocks writes under the real home directory outside
  the allowed roots (the temporary home, the temporary-files directory, pytest's base temp
  directory and the project root).
- ``--omp-tier T``: the profile tier this run selects, written as the ``omp.tier`` property.
- ``--omp-collect-out FILE``: after collection, writes ``[[absolute file, node ID], ...]`` as
  JSON for the tier lister, which turns it into repository-relative test IDs.

With ``--junitxml`` the plugin adds the run properties ``omp.tier``, ``omp.runtime``
(``python <version>``), ``omp.revision`` (``git rev-parse HEAD`` in the rootdir, omitted outside
Git), ``omp.wall_s``, ``omp.exit_code`` and ``omp.guards`` (the armed guards), so the report
proves which tier ran, on which revision and runtime, and under which guards.

A blocked operation raises ``GuardViolation`` and fails the phase it ran in, even when the test
catches the exception. The failure text starts with the rule ID.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import tempfile
import time
from collections.abc import Generator
from pathlib import Path

import pytest
from _pytest.junitxml import xml_key

from omp_guardrails.adapters.pytest import guards
from omp_guardrails.adapters.pytest.dialect import GUARDS_PROPERTY

VIOLATIONS = pytest.StashKey[dict[str, list[str]]]()
STARTED = pytest.StashKey[float]()
XDG_VARIABLES = ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME")


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("omp-guardrails")
    group.addoption(
        "--omp-guard",
        action="append",
        default=[],
        metavar="GUARD",
        help=f"arm a tier guard ({', '.join(guards.GUARDS)}); repeat or comma-separate",
    )
    group.addoption("--omp-tier", default=None, metavar="TIER", help="profile tier of this run")
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
    """Session-wide guard setup: the redirected home and the guard roots."""

    def __init__(self, config: pytest.Config, armed: frozenset[str]) -> None:
        self.config = config
        self.armed = armed
        self.saved_environment: dict[str, str | None] = {}
        self.temporary_home: str | None = None

    def start(self) -> None:
        protected = None
        allowed = [str(self.config.rootpath)]
        if guards.USER_DATA in self.armed:
            protected = os.path.expanduser("~")
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
        values.update({name: os.path.join(home, name.lower()) for name in XDG_VARIABLES})
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


def pytest_configure(config: pytest.Config) -> None:
    config.stash[STARTED] = time.monotonic()
    armed = armed_guards(config)
    if armed:
        session = GuardSession(config, armed)
        session.start()
        config.stash[SESSION_KEY] = session


def pytest_unconfigure(config: pytest.Config) -> None:
    session = config.stash.get(SESSION_KEY, None)
    if session is not None:
        session.stop()


def guarded_phase(item: pytest.Item, when: str) -> Generator[None, object, object]:
    session = item.config.stash.get(SESSION_KEY, None)
    if session is None:
        return (yield)
    guards.arm(session.armed)
    try:
        return (yield)
    finally:
        found = guards.disarm()
        if found:
            item.stash.setdefault(VIOLATIONS, {})[when] = found


@pytest.hookimpl(wrapper=True)
def pytest_runtest_setup(item: pytest.Item) -> Generator[None, object, object]:
    return (yield from guarded_phase(item, "setup"))


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None, object, object]:
    return (yield from guarded_phase(item, "call"))


@pytest.hookimpl(wrapper=True)
def pytest_runtest_teardown(item: pytest.Item) -> Generator[None, object, object]:
    return (yield from guarded_phase(item, "teardown"))


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
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
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
    }
    return {name: value for name, value in values.items() if value is not None}


@pytest.hookimpl(tryfirst=True)
def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Add the run properties before the JUnit plugin writes the report."""
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
