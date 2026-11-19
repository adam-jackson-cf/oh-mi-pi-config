"""Frozen source fixtures for Vitest static collection and AST lint (no Node/network)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/dynaflows/engine")
)


import pytest

from guardrails.adapters.vitest import cases, lint
from guardrails.diffs.growth import Snapshot, duplicate_findings, growth_findings
from guardrails.findings import Finding

pytestmark = pytest.mark.fast

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "agent/extensions/dynaflows/engine"

PROFILE: dict[str, Any] = {
    "public_entry_points": {"shop/core.ts": ["checkout"]},
    "adapters": [{"name": "vitest", "tiers": {"t1": {"location": "tests"}}}],
    "layout": {"test_file_patterns": ["*.test.ts", "*.spec.tsx"]},
}


def findings(
    source: str, path: str = "tests/case.test.ts", profile: dict[str, Any] = PROFILE
) -> list[Finding]:
    rules = [
        rule
        for rule in lint.load_rules()
        if rule.scope
        == ("tests" if lint.is_test(path, profile, "vitest") else "production")
    ]
    return lint.file_findings(path, source, rules, lint.owned_roots(profile))


class StaticCases(unittest.TestCase):
    def collect(self, source: str, path: str = "tests/case.test.ts") -> cases.Collected:
        return cases.collect(path, source)

    def test_nested_suite_collects_behavior_titles_without_comments(self) -> None:
        result = self.collect("""import { describe as suite, it as example, expect } from 'vitest';
        suite('basket', () => {
          example('totals prices', () => { expect(1 + 2).toBe(3); });
          example('starts empty', () => { expect([]).toHaveLength(0); });
        });""")
        assert [item.selector for item in result.cases] == [
            "tests/case.test.ts::basket > totals prices",
            "tests/case.test.ts::basket > starts empty",
        ]
        assert list(result.behaviours.values()) == ["totals prices", "starts empty"]

    def test_comments_do_not_replace_behavior_titles(self) -> None:
        result = self.collect("""// behaviour: unrelated
        test('first', () => { expect(true).toBeTruthy(); });
        // behaviour: second only
        test('second', () => { expect(true).toBe(true); });""")
        assert list(result.behaviours.values()) == ["first", "second"]

    def test_each_expands_static_rows_and_binds_values(self) -> None:
        result = self.collect("""const rows = [[1, 2], [2, 3], [1, 2]];
        // behaviour: increments
        test.each(rows)('increments %s', (input, expected) => { expect(input + 1).toBe(expected); });""")
        assert len(result.cases) == 3
        assert result.cases[0].fingerprint == result.cases[2].fingerprint
        assert result.cases[0].fingerprint != result.cases[1].fingerprint
        assert (
            len(
                duplicate_findings(
                    {}, {case.selector: case.fingerprint for case in result.cases}
                )
            )
            == 1
        )

    def test_object_rows_match_plain_assertions(self) -> None:
        rows = self.collect(
            "test.each([{input: 1, expected: 2}])('case', ({input, expected}) => { expect(input + 1).toBe(expected); });"
        )
        plain = self.collect("test('plain', () => { expect(1 + 1).toBe(2); });")
        assert rows.cases[0].fingerprint == plain.cases[0].fingerprint

    def test_parameterized_suite_inherits_bindings(self) -> None:
        result = self.collect(
            "describe.each([1, 2])('suite', input => { test('case', () => { expect(input).toBe(1); }); });"
        )
        assert [case.selector for case in result.cases] == [
            "tests/case.test.ts::suite[0] > case",
            "tests/case.test.ts::suite[1] > case",
        ]
        assert result.cases[0].fingerprint != result.cases[1].fingerprint

    def test_single_use_temporary_and_direct_call_match(self) -> None:
        direct = self.collect("test('direct', () => { expect(price(2)).toBe(4); });")
        temporary = self.collect(
            "test('temporary', () => { const result = price(2); expect(result).toBe(4); });"
        )
        assert direct.cases[0].fingerprint == temporary.cases[0].fingerprint

    def test_fingerprints_ignore_alias_local_name_format_and_comments(self) -> None:
        first = self.collect("""import { price } from '../shop/core';
        test('one', () => { const subtotal = price(2); expect(subtotal).toBe(4); });""")
        second = self.collect("""import {price as total} from "../shop/core"
        it("renamed",()=>{const result=total(2)
        // an explanation
        expect(result).toBe(4)})""")
        assert first.cases[0].fingerprint == second.cases[0].fingerprint

    def test_assertion_and_call_order_are_meaningful(self) -> None:
        sources = [
            "first(); second(); expect(1).toBe(1);",
            "second(); first(); expect(1).toBe(1);",
            "first(); second(); expect(1).toBe(2);",
        ]
        fingerprints = {
            self.collect("test('case', () => {" + body + "});").cases[0].fingerprint
            for body in sources
        }
        assert len(fingerprints) == 3

    def test_shared_growth_protocol(self) -> None:
        result = self.collect("test('missing', () => { expect(1).toBe(1); });")
        head = Snapshot(
            {case.selector: case.fingerprint for case in result.cases},
            dict(result.behaviours),
            {},
        )
        assert len(growth_findings(Snapshot({}, {}, {}), head)) == 1

    def test_tsx_and_async(self) -> None:
        result = self.collect(
            "test('jsx', async () => { const element = <div />; expect(await render(element)).toBeTruthy(); });",
            "tests/view.spec.tsx",
        )
        assert len(result.cases) == 1

    def test_dynamic_collection_fails_closed(self) -> None:
        for source in [
            "test(title(), () => {});",
            "test.each(load())('case', () => {});",
            "for (const x of rows) { test('case', () => {}); }",
            "test('case', callback);",
            "test('same', () => {}); test('same', () => {});",
            "test('broken', () => {",
        ]:
            with self.subTest(source=source), pytest.raises(cases.CollectionError):
                self.collect(source)


class SourceLint(unittest.TestCase):
    def assert_rule(
        self,
        source: str,
        rule: str,
        expected: bool = True,
        path: str = "tests/case.test.ts",
    ) -> None:
        found = findings(source, path)
        assert any(item.rule_id == rule for item in found) == expected, found

    def test_owned_mock_forms(self) -> None:
        for source in [
            "import { vi } from 'vitest'; vi.mock('../shop/core');",
            "import { vi as mocker } from 'vitest'; mocker.doMock('../shop/core');",
            "import { vi } from 'vitest'; vi.mock(import('../shop/core'));",
            "import * as core from '../shop/core'; vi.spyOn(core, 'checkout');",
            "import { checkout as owned } from '../shop/core'; vi.mocked(owned);",
            "import { Basket } from '../shop/core'; vi.mocked(new Basket());",
            "import * as core from '../shop/core'; core.checkout = replacement;",
        ]:
            with self.subTest(source=source):
                self.assert_rule(source, "TEST.no-self-mocking")

    def test_external_mock_and_comment_are_clean(self) -> None:
        self.assert_rule(
            "vi.mock('third-party'); // vi.mock('../shop/core')\nconst text = `vi.mock('../shop/core')`;",
            "TEST.no-self-mocking",
            False,
        )

    def test_evasion_forms(self) -> None:
        for call in [
            "test.skip('x', () => {})",
            "it.only('x', () => {})",
            "test.fails('x', () => {})",
            "describe.skipIf(flag)('x', () => {})",
            "test('x', {retry: 2}, () => {})",
            "test('x', () => {}, {retry: 1})",
            "test.todo('x')",
        ]:
            with self.subTest(call=call):
                self.assert_rule(call, "TEST.no-evasion")
        self.assert_rule("test('x', {retry: 0}, () => {})", "TEST.no-evasion", False)

    def test_aliased_evasion(self) -> None:
        self.assert_rule(
            "import { test as check } from 'vitest'; check.skip('x', () => {});",
            "TEST.no-evasion",
        )

    def test_fast_check_seed_forms(self) -> None:
        for prefix in [
            "import fc from 'fast-check';",
            "import * as fc from 'fast-check';",
        ]:
            self.assert_rule(
                prefix + "fc.assert(fc.property(fc.integer(), x => x === x));",
                "TEST.property-seed",
            )
            self.assert_rule(
                prefix
                + "fc.assert(fc.property(fc.integer(), x => x === x), {seed: 42});",
                "TEST.property-seed",
                False,
            )
            self.assert_rule(
                prefix + "fc.assert(prop, {seed: Date.now()});", "TEST.property-seed"
            )
            self.assert_rule(
                prefix + "fc.configureGlobal({seed: -42}); fc.check(prop);",
                "TEST.property-seed",
                False,
            )
            self.assert_rule(
                prefix + "fc.check(prop); fc.configureGlobal({seed: 42});",
                "TEST.property-seed",
            )
        self.assert_rule(
            "import {check as run} from 'fast-check'; run(prop, {seed: 9});",
            "TEST.property-seed",
            False,
        )

    def test_production_hook_declaration(self) -> None:
        self.assert_rule(
            "export function resetForTests() {}",
            "TEST.no-test-hooks",
            path="shop/settings.ts",
        )
        self.assert_rule(
            "export const description = 'resetForTests';",
            "TEST.no-test-hooks",
            False,
            "shop/settings.ts",
        )
        self.assert_rule(
            "export function publicApi(testOnly: boolean) { return testOnly ? 42 : 0; }",
            "TEST.no-test-hooks",
            path="shop/settings.ts",
        )
        self.assert_rule(
            "export function publicApi(testOnly?: boolean) { return testOnly; }",
            "TEST.no-test-hooks",
            path="shop/settings.ts",
        )

    def test_environment_test_hooks(self) -> None:
        self.assert_rule(
            "if (process.env.VITEST) reset();",
            "TEST.no-test-hooks",
            path="shop/settings.ts",
        )
        self.assert_rule(
            "if (process.env.NODE_ENV === 'test') reset();",
            "TEST.no-test-hooks",
            path="shop/settings.ts",
        )

    def test_symbols_used_only_by_tests_resolve_aliases(self) -> None:
        sources = {
            "shop/core.ts": "export function checkout() {} export function inspect() {}",
            "tests/case.test.ts": "import { inspect as view } from '../shop/core'; test('x', () => { view(); });",
        }
        found = lint.hook_findings(sources, PROFILE, {"shop/core.ts"})
        assert len(found) == 1
        assert "inspect" in found[0].message
        sources["shop/consumer.ts"] = "import { inspect } from './core'; inspect();"
        assert lint.hook_findings(sources, PROFILE, {"shop/core.ts"}) == []

    def test_public_entry_symbol_and_unused_import_are_not_hooks(self) -> None:
        sources = {
            "shop/core.ts": "export function checkout() {} export function unused() {}",
            "tests/case.test.ts": "import {checkout, unused} from '../shop/core'; checkout();",
        }
        assert lint.hook_findings(sources, PROFILE, {"shop/core.ts"}) == []

    def test_inventory_scope_waivers_and_no_target_writes(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder)
            paths = ["shop/core.ts", "tests/case.test.ts"]
            for path, source in zip(
                paths,
                ["export function checkout() {}", "vi.mock('../shop/core');"],
                strict=True,
            ):
                destination = repo / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(source)
            before = {path: (repo / path).read_bytes() for path in paths}
            with patch.object(lint, "repo_paths", return_value=paths):
                found = lint.lint(repo, PROFILE, [])
                assert [item.rule_id for item in found] == ["TEST.no-self-mocking"]
                profile = {
                    **PROFILE,
                    "guardrails": {
                        "exceptions": [
                            {
                                "rule": "TEST.no-self-mocking",
                                "path": "tests/*",
                                "reason": "Owner-approved boundary replacement",
                            }
                        ]
                    },
                }
                assert lint.lint(repo, profile, []) == []
            assert before == {path: (repo / path).read_bytes() for path in paths}

    def test_inventory_filters_binary_assets_and_reports_unreadable_owned_source(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder)
            paths = ["shop/broken.ts", "assets/image.png", "unowned/video.ts"]
            for path in paths:
                destination = repo / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(b"\xff\xfe\x00")
            with patch.object(lint, "repo_paths", return_value=paths):
                found = lint.lint(repo, PROFILE, [])
            assert len(found) == 1
            assert found[0].path == "shop/broken.ts"
            assert found[0].rule_id == "TEST.no-test-hooks"
            assert "cannot read source" in found[0].message

    def test_workspace_names_resolve_to_owned_source_directories(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder)
            sources = {
                "packages/shop/package.json": '{"name":"@shop/core","exports":{".":"./src/index.ts"}}',
                "packages/shop/src/index.ts": "export function publicApi(testOnly: boolean) { return testOnly; }",
                "tests/case.test.ts": "vi.mock('../packages/shop/src/index'); vi.mock('@shop/core');",
            }
            for path, source in sources.items():
                destination = repo / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(source)
            profile = {
                **PROFILE,
                "public_entry_points": {"@shop/core": {".": "./src/index.ts"}},
            }
            with patch.object(lint, "repo_paths", return_value=list(sources)):
                found = lint.lint(repo, profile, [])
            assert sum(item.rule_id == "TEST.no-self-mocking" for item in found) == 2
            assert any(
                item.rule_id == "TEST.no-test-hooks"
                and item.path == "packages/shop/src/index.ts"
                for item in found
            )

    def test_invalid_owned_syntax_is_a_finding_not_a_crash(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder)
            (repo / "shop").mkdir()
            (repo / "shop/core.ts").write_text("export function broken( {")
            with patch.object(lint, "repo_paths", return_value=["shop/core.ts"]):
                found = lint.lint(repo, PROFILE, [])
            assert len(found) == 1
            assert found[0].rule_id == "TEST.no-test-hooks"
            assert "cannot parse source" in found[0].message

    def test_root_package_ownership_does_not_turn_locals_or_external_modules_into_owned_imports(
        self,
    ) -> None:
        rules = [rule for rule in lint.load_rules() if rule.scope == "tests"]
        source = """import { external } from 'third-party';
        let local = 0; local = 1; const state = {value: 0}; state.value = 1;
        vi.mock('third-party'); vi.spyOn(external, 'run'); vi.spyOn(process, 'exit');"""
        found = lint.file_findings(
            "tests/case.test.ts", source, rules, (".", "owned-app")
        )
        assert found == []
        owned = lint.file_findings(
            "tests/case.test.ts",
            "import * as core from '../src/core'; core.run = replacement;",
            rules,
            (".", "owned-app"),
        )
        assert [item.rule_id for item in owned] == ["TEST.no-self-mocking"]


class PatternContract(unittest.TestCase):
    def test_yaml_patterns_cover_required_rules(self) -> None:
        rules = lint.load_rules()
        assert {rule.rule_id for rule in rules} == {
            "TEST.no-self-mocking",
            "TEST.no-test-hooks",
            "TEST.no-evasion",
            "TEST.property-seed",
        }
        assert all("rule" in rule.config for rule in rules)


if __name__ == "__main__":
    unittest.main()
