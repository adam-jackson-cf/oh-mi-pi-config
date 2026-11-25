from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/agent-rails/engine")
)

import pytest

from guardrails.commands.adapters.go.evidence import guard_evidence, load_report
from guardrails.commands.adapters.go.packages import Package
from guardrails.commands.adapters.go.report import convert, read_stream, write_junit
from guardrails.commands.adapters.go.syscall_overlay import (
    HOOKS,
    GuardError,
    write_overlay,
)
from guardrails.commands.test_evidence import propagation

pytestmark = pytest.mark.fast

PACKAGE = "example.com/shop/tests"


def event(
    action: str, test: str | None = None, output: str | None = None, **extra: object
) -> str:
    value: dict[str, object] = {"Action": action, "Package": PACKAGE, **extra}
    if test is not None:
        value["Test"] = test
    if output is not None:
        value["Output"] = output
    return json.dumps(value)


def stream(*lines: str) -> str:
    return "\n".join(lines) + "\n"


PACKAGES = {
    PACKAGE: Package(
        PACKAGE,
        "tests",
        {
            "TestTotal": "tests/total_test.go",
            "TestVendor": "tests/total_test.go",
            "TestRounding": "tests/total_test.go",
            "TestProcess": "tests/process_test.go",
            "TestCrash": "tests/process_test.go",
        },
    )
}


class OverlayTests(unittest.TestCase):
    def fake_goroot(self, root: Path, names: list[str]) -> Path:
        folder = root / "src" / "syscall"
        folder.mkdir(parents=True)
        lines = ["package syscall", ""]
        signatures = {
            "forkExec": "func forkExec(argv0 string, argv []string, attr *ProcAttr) (pid int, err error) {",
            "Exec": "func Exec(argv0 string, argv []string, envv []string) (err error) {",
            "Socket": "func Socket(domain, typ, proto int) (fd int, err error) {",
            "Open": "func Open(path string, mode int, perm uint32) (fd int, err error) {",
            "Rename": "func Rename(from string, to string) (err error) {",
            "Symlink": "func Symlink(path string, link string) (err error) {",
            "Link": "func Link(path string, link string) (err error) {",
        }
        for name in names:
            lines.extend([
                signatures.get(name, f"func {name}(path string) (err error) {{"),
                "\treturn",
                "}",
                "",
            ])
        (folder / "zsyscall_fake.go").write_text("\n".join(lines))
        (folder / "untouched.go").write_text("package syscall\n")
        return root

    def test_every_hooked_syscall_calls_its_guard_first(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            goroot = self.fake_goroot(Path(scratch, "goroot"), sorted(HOOKS))
            overlay = write_overlay(goroot, Path(scratch, "overlay"))
            replaced = json.loads(overlay.read_text())["Replace"]
            patched = Path(
                replaced[str(goroot / "src/syscall/zsyscall_fake.go")]
            ).read_text()
            assert (
                "func Socket(domain, typ, proto int) (fd int, err error) {\n\tguardrailsGuardSocket(domain)"
                in patched
            )
            assert "\tguardrailsGuardWrite(from)\n\tguardrailsGuardWrite(to)" in patched
            assert "\tguardrailsGuardWrite(link)" in patched
            assert "\tguardrailsGuardProcess(argv0)" in patched
            assert "\tguardrailsGuardOpen(path, mode)" in patched
            assert str(goroot / "src/syscall/untouched.go") not in replaced
            guard = Path(
                replaced[str(goroot / "src/syscall/zz_guardrails.go")]
            ).read_text()
            assert 'Getenv("GUARDRAILS_GO_GUARDS")' in guard
            assert "TEST.fast-tier-isolation" in guard
            assert "TEST.user-data-guard" in guard

    def test_a_toolchain_without_a_hooked_syscall_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            goroot = self.fake_goroot(
                Path(scratch, "goroot"), sorted(set(HOOKS) - {"Socket"})
            )
            with pytest.raises(GuardError, match="defines no syscall Socket"):
                write_overlay(goroot, Path(scratch, "overlay"))


class ReportTests(unittest.TestCase):
    def converted(
        self, text: str, violations: list[str] | None = None
    ) -> list[tuple[str, str, str]]:
        cases = convert(read_stream(text), PACKAGES, violations or [])
        return [(case.case_id, case.outcome, case.message) for case in cases]

    def test_go_test_events_become_cases_with_outcomes_and_reasons(self) -> None:
        text = stream(
            event("run", "TestTotal"),
            event("output", "TestTotal/rows", "    total_test.go:9: row two\n"),
            event("pass", "TestTotal/rows", Elapsed=0.01),
            event("pass", "TestTotal", Elapsed=0.02),
            event(
                "output", "TestVendor", "    total_test.go:14: waiting on vendor fix\n"
            ),
            event("output", "TestVendor", "--- SKIP: TestVendor (0.00s)\n"),
            event("skip", "TestVendor", Elapsed=0),
            event(
                "output", "TestRounding", "    total_test.go:18: xfail: known defect\n"
            ),
            event("skip", "TestRounding", Elapsed=0),
            event("output", "TestProcess", "--- FAIL: TestProcess (0.00s)\n"),
            event(
                "output",
                "TestProcess",
                "panic: TEST.fast-tier-isolation: blocked process start /bin/true [recovered]\n",
            ),
            event("output", "TestProcess", "goroutine 6 [running]:\n"),
            event("fail", "TestProcess", Elapsed=0),
            event("run", "TestCrash"),
            event("output", None, "FAIL\texample.com/shop/tests\t0.1s\n"),
            event("fail", Elapsed=0.1),
        )
        assert self.converted(text) == [
            ("tests/total_test.go::TestTotal", "passed", ""),
            ("tests/total_test.go::TestVendor", "skipped", "waiting on vendor fix"),
            ("tests/total_test.go::TestRounding", "xfail", "known defect"),
            (
                "tests/process_test.go::TestProcess",
                "failed",
                "panic: TEST.fast-tier-isolation: blocked process start /bin/true [recovered]",
            ),
            (
                "tests/process_test.go::TestCrash",
                "error",
                "FAIL\texample.com/shop/tests\t0.1s",
            ),
        ]

    def test_a_package_that_fails_outside_its_tests_is_an_error_case(self) -> None:
        text = stream(
            json.dumps({
                "Action": "build-output",
                "ImportPath": f"{PACKAGE} [{PACKAGE}.test]",
                "Output": "tests/total_test.go:3:2: undefined: shop\n",
            }),
            json.dumps({
                "Action": "build-fail",
                "ImportPath": f"{PACKAGE} [{PACKAGE}.test]",
            }),
            event("fail", Elapsed=0, FailedBuild=f"{PACKAGE} [{PACKAGE}.test]"),
        )
        assert self.converted(text) == [
            (
                f"tests::package {PACKAGE}",
                "error",
                "tests/total_test.go:3:2: undefined: shop",
            )
        ]

    def test_a_guard_violation_the_test_swallowed_still_fails_the_run(self) -> None:
        violation = "TEST.fast-tier-isolation: blocked process start /bin/true"
        text = stream(
            event("pass", "TestTotal", Elapsed=0.01), event("pass", Elapsed=0.01)
        )
        assert self.converted(text, [violation]) == [
            ("tests/total_test.go::TestTotal", "passed", ""),
            (f".::{violation}", "error", violation),
        ]

    def test_the_written_report_reads_through_the_go_dialect(self) -> None:
        text = stream(
            event("pass", "TestTotal", Elapsed=0.25),
            event(
                "output", "TestRounding", "    total_test.go:18: xfail: known defect\n"
            ),
            event("skip", "TestRounding", Elapsed=0),
            event("pass", Elapsed=0.3),
        )
        cases = convert(read_stream(text), PACKAGES, [])
        properties = {
            "guardrails.tier": "t1",
            "guardrails.exit_code": "0",
            "guardrails.guards": "fast-tier,user-data",
        }
        with tempfile.TemporaryDirectory() as scratch:
            report = Path(scratch, "go.xml")
            write_junit(report, cases, properties, 0.5)
            parsed = load_report(report)
            assert guard_evidence(report, "t1") == []
            assert [item.message for item in guard_evidence(report, "t2")] == [
                "run report is missing or invalid: guardrails.tier is 't1', not 't2'"
            ]
        assert [
            (case.id, case.outcome, case.reason, case.duration_s)
            for case in parsed.cases
        ] == [
            ("tests/total_test.go::TestTotal", "passed", "", 0.25),
            ("tests/total_test.go::TestRounding", "xfail", "known defect", 0.0),
        ]
        assert parsed.wall_s == 0.5
        assert [item.message for item in propagation.evaluate(parsed, {}, None)] == [
            "tests/total_test.go::TestRounding is xfail without approval (known defect)"
        ]

    def test_a_go_report_without_an_exit_code_never_passes(self) -> None:
        cases = convert(
            read_stream(stream(event("pass", "TestTotal", Elapsed=0.1))), PACKAGES, []
        )
        with tempfile.TemporaryDirectory() as scratch:
            report = Path(scratch, "go.xml")
            write_junit(report, cases, {"guardrails.tier": "t1"}, 0.1)
            parsed = load_report(report)
        assert [item.rule_id for item in propagation.evaluate(parsed, {}, None)] == [
            "TEST.failure-propagation"
        ]


if __name__ == "__main__":
    unittest.main()
