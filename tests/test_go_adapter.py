from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/agent-rails/engine")
)

import pytest

import guardrails.commands.adapters.go.lint as go_lint
from guardrails.checker.growth import Snapshot, growth_findings
from guardrails.commands.adapters.go.cases import collect
from guardrails.commands.adapters.go.mutation import normalise
from guardrails.commands.adapters.go.packages import build_arguments
from guardrails.commands.adapters.go.syntax import imports, parse
from guardrails.commands.adapters.go.tiers import flag_value, listed_names, top_level
from guardrails.commands.adapters.go.toolchain import package_patterns
from guardrails.context.config.adapter_declarations import installed

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


def source(path: str, text: str) -> go_lint.Source:
    root = parse(text)
    return go_lint.Source(path, root, imports(root))


OWNED = ("example.com/shop/shop",)

CONTAINERFILE = Path(__file__).resolve().parents[1] / ".devcontainer/Containerfile"


class SourceRuleTests(unittest.TestCase):
    def test_patching_rebinding_or_mocking_owned_code_is_self_mocking(self) -> None:
        test = source(
            "tests/total_test.go",
            """package tests

import (
\t"testing"
\t"time"

\t"bou.ke/monkey"
\t"example.com/shop/shop"
\t"example.com/shop/shop/mocks"
)

func TestTotal(t *testing.T) {
\tmonkey.Patch(shop.Total, func() int { return 1 })
\tmonkey.Patch(time.Now, func() time.Time { return time.Time{} })
\tshop.Rate = 3
\t_ = mocks.NewMockBasket(nil)
\t_ = mocks.NewMockClock(nil)
}
""",
        )
        assert [
            item.message
            for item in go_lint.self_mocking(test, OWNED, frozenset({"Basket"}))
        ] == [
            "line 13: patches owned code (monkey.Patch(shop.Total, func() int { return 1 }))",
            "line 16: builds a mock of owned type Basket",
            "line 15: rebinds owned package state (shop.Rate = 3)",
        ]

    def test_static_skips_expected_failures_and_retries_are_evasion(self) -> None:
        test = source(
            "tests/total_test.go",
            """package tests

import (
\t"os"
\t"testing"

\t"github.com/hashicorp/consul/sdk/testutil/retry"
)

func TestVendor(t *testing.T) {
\tt.Skip("vendor outage")
}

func TestReady(t *testing.T) {
\tif os.Getenv("READY") == "" {
\t\tt.Skip("waiting on vendor fix")
\t}
\tretry.Run(t, func(r *retry.R) {})
}

func TestRounding(t *testing.T) {
\tif true {
\t\tt.Skipf("xfail: known defect")
\t}
}
""",
        )
        assert [item.message for item in go_lint.evasion(test)] == [
            'line 11: static skip (t.Skip("vendor outage"))',
            "line 18: retries until green (retry.Run(t, func(r *retry.R) {}))",
            'line 23: expected-failure marker (t.Skipf("xfail: known defect"))',
        ]

    def test_quick_checks_need_a_seeded_rand(self) -> None:
        test = source(
            "tests/total_test.go",
            """package tests

import (
\t"math/rand"
\t"testing"
\t"testing/quick"
)

func TestZero(t *testing.T) {
\tseeded := &quick.Config{Rand: rand.New(rand.NewSource(7))}
\t_ = quick.Check(func(v int) bool { return v+0 == v }, seeded)
\t_ = quick.Check(func(v int) bool { return v+0 == v }, &quick.Config{MaxCount: 5})
\t_ = quick.CheckEqual(func(v int) int { return v }, func(v int) int { return v }, nil)
}
""",
        )
        assert [
            item.message.split(":", 1)[0] for item in go_lint.property_seed(test)
        ] == [
            "line 12",
            "line 13",
        ]

    def test_production_test_markers_and_hook_names_are_test_hooks(self) -> None:
        production = source(
            "shop/region.go",
            """package shop

import (
\t"os"
\t"testing"
)

func Region(testOnly bool) string {
\tif os.Getenv("SHOP_UNDER_TEST") != "" || testing.Testing() {
\t\treturn "test"
\t}
\treturn os.Getenv("SHOP_REGION")
}
""",
        )
        assert [item.message for item in go_lint.hook_findings(production)] == [
            "line 8: production code declares a test-only hook (testOnly)",
            "line 9: production code asks whether it runs under test (testing.Testing())",
            "line 9: production code reads a test marker from the environment or test flags "
            '("SHOP_UNDER_TEST")',
        ]

    def test_marker_reads_are_test_hooks_and_marker_shaped_data_is_not(
        self,
    ) -> None:
        production = source(
            "shop/region.go",
            """package shop

import (
\t"flag"
\t"os"
)

var runners = []string{"GO_TEST", "TESTING", "test.v"}

func Region() string {
\tif _, ok := os.LookupEnv("TEST_MODE"); ok || flag.Lookup("test.v") != nil {
\t\treturn runners[0]
\t}
\treturn "eu"
}
""",
        )
        assert [item.message for item in go_lint.hook_findings(production)] == [
            "line 11: production code reads a test marker from the environment or test flags "
            '("TEST_MODE")',
            "line 11: production code reads a test marker from the environment or test flags "
            '("test.v")',
        ]

    def test_marker_reads_through_a_constant_are_test_hooks(self) -> None:
        production = source(
            "shop/region.go",
            """package shop

import "os"

const marker = "GO_TEST"
const mode = "SHOP_MODE"

func Region() string {
\tif os.Getenv(marker) != "" || os.Getenv(mode) == "eu" {
\t\treturn "local"
\t}
\treturn "eu"
}
""",
        )
        assert [item.message for item in go_lint.hook_findings(production)] == [
            "line 9: production code reads a test marker from the environment or test flags "
            '("GO_TEST")',
        ]


class CollectionTests(unittest.TestCase):
    def test_test_functions_collect_with_behaviour_names_from_their_words(self) -> None:
        collected = collect(
            "tests/total_test.go",
            """package tests

import "testing"

func TestBasketTotalSumsPrices(t *testing.T) {}

func TestSum(t *testing.T) {}

func helper() {}
""",
        )
        assert dict(collected.behaviours) == {
            "tests/total_test.go::TestBasketTotalSumsPrices": "basket total sums prices",
            "tests/total_test.go::TestSum": "sum",
        }
        findings = growth_findings(
            Snapshot({}, {}, {}), Snapshot({}, dict(collected.behaviours), {})
        )
        assert [item.message for item in findings] == [
            "tests/total_test.go::TestSum has no descriptive behavior name"
        ]

    def test_identical_test_bodies_share_a_fingerprint(self) -> None:
        collected = collect(
            "tests/total_test.go",
            "package tests\n\nfunc TestOneTotal(t *T) { check(1) }\n\nfunc TestTwoTotal(t *T) {\n\tcheck(1)\n}\n",
        )
        first, second = collected.cases
        assert first.fingerprint == second.fingerprint

    def test_listing_reads_go_test_list_output_per_package(self) -> None:
        text = stream(
            event("output", None, "TestTotal\n"),
            event("output", None, "ExampleBasket\n"),
            event("output", None, "ok  \texample.com/shop/tests\t0.1s\n"),
        )
        assert listed_names(text) == {PACKAGE: ["TestTotal", "ExampleBasket"]}
        arguments = ["-run", "^TestFast/rows$", "-tags=integration", "-count", "1"]
        assert top_level(flag_value(arguments, "-run")) == "^TestFast"
        assert build_arguments([*arguments, "-race", "-mod", "vendor"]) == [
            "-tags=integration",
            "-race",
            "-mod",
            "vendor",
        ]
        assert package_patterns(["tests", "./cmd/...", "."]) == [
            "./tests",
            "./cmd/...",
            "./",
        ]


class MutationTests(unittest.TestCase):
    def test_gremlins_results_normalise_to_shared_mutants(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            repo = Path(scratch)
            report = repo / "gremlins.json"
            report.write_text(
                json.dumps({
                    "go_module": "example.com/shop",
                    "files": [
                        {
                            "file_name": "shop/core.go",
                            "mutations": [
                                {
                                    "type": "CONDITIONALS_BOUNDARY",
                                    "status": "LIVED",
                                    "line": 7,
                                    "column": 9,
                                },
                                {
                                    "type": "ARITHMETIC_BASE",
                                    "status": "KILLED",
                                    "line": 9,
                                    "column": 12,
                                },
                                {
                                    "type": "INVERT_NEGATIVES",
                                    "status": "NOT COVERED",
                                    "line": 11,
                                    "column": 3,
                                },
                            ],
                        }
                    ],
                })
            )
            normalised = normalise(repo, report)
        assert (
            normalised["tool"] == "github.com/go-gremlins/gremlins/cmd/gremlins@v0.6.0"
        )
        assert [
            (item["id"], item["start_line"], item["status"])
            for item in normalised["mutants"]
        ] == [
            ("shop/core.go::7:9:CONDITIONALS_BOUNDARY", 7, "survived"),
            ("shop/core.go::9:12:ARITHMETIC_BASE", 9, "killed"),
            ("shop/core.go::11:3:INVERT_NEGATIVES", 11, "no_tests"),
        ]

    def test_the_go_adapter_is_installed(self) -> None:
        assert installed("go")

    def test_the_ci_image_installs_a_pinned_go_for_the_conformance_run(self) -> None:
        text = CONTAINERFILE.read_text()
        assert re.search(
            r'"https://go\.dev/dl/go1\.\d+\.\d+\.linux-\$arch\.tar\.gz"', text
        )
        assert len(re.findall(r"(amd64|arm64)\) sum=[0-9a-f]{64} ;;", text)) == 2
        assert "sha256sum -c -" in text
        assert 'ENV PATH="/usr/local/go/bin:${PATH}" GOTOOLCHAIN=local' in text


if __name__ == "__main__":
    unittest.main()
