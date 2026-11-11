"""Behavioral checks for the bundled rule-coverage gate."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

VALIDATOR = (
    Path(__file__).resolve().parents[1]
    / "agent/skills/setup-repository-guardrails/scripts/validate_rule_coverage.py"
)
AGENTIC_RULE_IDS = {
    "STRUCT.dependency-direction",
    "STRUCT.folder-placement",
    "NAME.conventions",
    "TEST.placement",
    "TEST.budget.fast-tier-time",
    "TEST.budget.per-test-time",
    "TEST.growth.justified",
    "TEST.duplicate-path",
    "GATE.local-ci-parity",
    "GATE.pre-commit",
    "GATE.path-check",
    "GIT.required-checks",
}
# Rules added with Python reference v2 (test-guidance v2 determinism map, verification surface).
V2_RULE_IDS = {
    "TEST.no-self-mocking",
    "TEST.no-test-hooks",
    "TEST.no-evasion",
    "TEST.fast-tier-isolation",
    "TEST.user-data-guard",
    "TEST.tier-selectors",
    "TEST.coverage-ratchet",
    "TEST.golden-change-record",
    "TEST.identity-artefacts",
    "TEST.property-seed",
    "TEST.mutation-sensitivity",
    "NAME.test-files",
    "PROFILE.complete",
    "SURFACE.feature-map",
    "SURFACE.journeys",
    "SURFACE.lever-isolation",
}
REFERENCE_ROOT = VALIDATOR.parents[1] / "assets/python-reference"


def _module_sources(module: str) -> list[Path]:
    """Source files of a reference module: one ``.py`` file, or a package's code and rules."""
    path = REFERENCE_ROOT.joinpath(*module.split("."))
    if path.is_dir():
        return sorted(p for p in path.rglob("*") if p.suffix in {".py", ".yml", ".json"})
    return [path.with_suffix(".py")] if path.with_suffix(".py").is_file() else []


def _reference_imports(sources: list[Path]) -> set[str]:
    """``omp_guardrails`` modules imported by the given Python sources."""
    imported: set[str] = set()
    for source in sources:
        if source.suffix != ".py":
            continue
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "omp_guardrails."
            ):
                imported.add(node.module)
    return imported


class CoverageGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "check.py").write_text(
            "import pathlib, sys\n"
            "rule, fixture = sys.argv[1:]\n"
            "if pathlib.Path(fixture).name.startswith('bad'):\n"
            "    print(rule)\n"
            "    raise SystemExit(1)\n"
        )
        (self.root / "runner.sh").write_text("python check.py\n")
        (self.root / "ci.yml").write_text("runner.sh\n")
        (self.root / "config.toml").write_text("enabled = true\n")
        (self.root / "good.txt").write_text("good\n")
        (self.root / "bad.txt").write_text("bad\n")
        (self.root / "evidence.txt").write_text("no analogous construct\n")
        self.catalog = {
            "schema_version": 1,
            "rules": [
                {"id": "RULE.A", "kind": "bespoke", "applicability": "always"},
                {"id": "RULE.B", "kind": "tool", "applicability": "conditional"},
            ],
        }
        self.ledger = {
            "schema_version": 1,
            "rules": [self._implemented("RULE.A"), self._implemented("RULE.B")],
        }

    @staticmethod
    def _implemented(rule_id: str) -> dict[str, object]:
        row: dict[str, object] = {
            "id": rule_id,
            "status": "implemented",
            "implementation_paths": ["check.py"],
            "local_runner": "runner.sh",
            "local_marker": "check.py",
            "ci_runner": "ci.yml",
            "ci_marker": "runner.sh",
            "canary": {
                "command": [sys.executable, "check.py", rule_id, "{fixture}"],
                "passing": "good.txt",
                "violating": "bad.txt",
                "diagnostic": rule_id,
                "timeout_seconds": 10,
            },
        }
        if rule_id == "RULE.B":
            row["configuration"] = {
                "path": "config.toml",
                "contains": ["enabled = true"],
            }
        return row

    def _hook(self, rule_id: str = "RULE.A") -> dict[str, object]:
        row = self._implemented(rule_id)
        row["evidence_kind"] = "git_hook"
        del row["ci_runner"], row["ci_marker"]
        row["hook_install_check"] = {
            "command": [sys.executable, "-c", f"print('{rule_id}: installed')"],
            "diagnostic": f"{rule_id}: installed",
        }
        return row

    def _host(self) -> dict[str, object]:
        (self.root / "host-query.json").write_text(json.dumps({
            "command": ["gh", "api", "repos/owner/repo/branches/trunk/protection"],
            "exit_code": 0,
            "stdout": '{"required_status_checks":{"contexts":["gate"]}}',
            "stderr": "",
        }))
        return {
            "id": "RULE.B",
            "status": "implemented",
            "evidence_kind": "host_query",
            "host_query": {"path": "host-query.json", "contains": ['"gate"']},
        }

    def _run(self, *, structure_only: bool = False) -> subprocess.CompletedProcess[str]:
        catalog_path = self.root / "catalog.json"
        ledger_path = self.root / "ledger.json"
        catalog_path.write_text(json.dumps(self.catalog))
        ledger_path.write_text(json.dumps(self.ledger))
        return subprocess.run(
            [
                sys.executable,
                str(VALIDATOR),
                "--root",
                str(self.root),
                "--catalog",
                str(catalog_path),
                "--ledger",
                str(ledger_path),
                *(["--structure-only"] if structure_only else []),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )

    def test_every_rule_with_live_canaries_passes(self) -> None:
        result = self._run()
        assert result.returncode == 0, result.stderr
        assert "PASS: 2 rules, 0 exclusions" in result.stdout

    def test_missing_rule_fails(self) -> None:
        self.ledger["rules"].pop()
        result = self._run()
        assert result.returncode != 0
        assert "missing rule: RULE.B" in result.stderr

    def test_tool_configuration_drift_fails(self) -> None:
        (self.root / "config.toml").write_text("enabled = false\n")
        result = self._run()
        assert result.returncode != 0
        assert "configuration assertion failed" in result.stderr

    def test_violating_canary_must_fail_with_its_diagnostic(self) -> None:
        (self.root / "check.py").write_text("raise SystemExit(0)\n")
        result = self._run()
        assert result.returncode != 0
        assert "violating canary passed" in result.stderr

    def test_always_applicable_rule_cannot_be_excluded(self) -> None:
        self.ledger["rules"][0] = {
            "id": "RULE.A",
            "status": "not_applicable",
            "reason": "no analogous construct",
            "user_approval": "approved",
            "evidence_path": "evidence.txt",
            "evidence_marker": "no analogous construct",
        }
        result = self._run()
        assert result.returncode != 0
        assert "always-applicable rule cannot be excluded" in result.stderr

    def test_conditional_exclusion_requires_approval_and_evidence(self) -> None:
        self.ledger["rules"][1] = {
            "id": "RULE.B",
            "status": "not_applicable",
            "reason": "no analogous construct",
            "evidence_path": "evidence.txt",
            "evidence_marker": "no analogous construct",
        }
        assert "exclusion lacks user approval" in self._run().stderr
        self.ledger["rules"][1]["user_approval"] = "approved"
        result = self._run()
        assert result.returncode == 0, result.stderr
        assert "PASS_WITH_APPROVED_EXCLUSIONS" in result.stdout

    def test_hook_requires_local_invocation_installation_and_behavior_not_ci(self) -> None:
        self.catalog["rules"][0]["evidence_kind"] = "git_hook"
        self.ledger["rules"][0] = self._hook()
        result = self._run()
        assert result.returncode == 0, result.stderr
        for field, message in (
            ("local_runner", "missing relative path"),
            ("hook_install_check", "missing hook installation check"),
            ("canary", "missing canary"),
        ):
            with self.subTest(field=field):
                self.ledger["rules"][0] = self._hook()
                del self.ledger["rules"][0][field]
                result = self._run()
                assert result.returncode != 0
                assert message in result.stderr

    def test_hook_installation_must_succeed_and_report_its_proof(self) -> None:
        self.catalog["rules"][0]["evidence_kind"] = "git_hook"
        for code, message in (
            ("raise SystemExit(1)", "hook installation check failed"),
            ("print('unrelated')", "hook installation diagnostic missing"),
        ):
            with self.subTest(code=code):
                self.ledger["rules"][0] = self._hook()
                self.ledger["rules"][0]["hook_install_check"]["command"] = [
                    sys.executable, "-c", code
                ]
                result = self._run()
                assert result.returncode != 0
                assert message in result.stderr

    def test_host_query_proves_host_settings_without_local_or_ci_runner(self) -> None:
        self.catalog["rules"][1]["evidence_kind"] = "host_query"
        self.ledger["rules"][1] = self._host()
        result = self._run()
        assert result.returncode == 0, result.stderr
        for field, value, message in (
            ("command", [], "invalid host query command"),
            ("exit_code", 1, "host query failed"),
            ("stdout", "unprotected", "host query assertion failed"),
        ):
            with self.subTest(field=field):
                self.ledger["rules"][1] = self._host()
                path = self.root / "host-query.json"
                record = json.loads(path.read_text())
                record[field] = value
                path.write_text(json.dumps(record))
                result = self._run()
                assert result.returncode != 0
                assert message in result.stderr
        self.ledger["rules"][1] = self._host()
        del self.ledger["rules"][1]["host_query"]
        assert "missing host query evidence" in self._run().stderr

    def test_host_exclusion_requires_recorded_query_reason_and_approval(self) -> None:
        self.catalog["rules"][1]["evidence_kind"] = "host_query"
        row = self._host()
        path = self.root / "host-query.json"
        record = json.loads(path.read_text())
        record.update(exit_code=1, stdout="", stderr="HTTP 403: private repo on GitHub Free")
        path.write_text(json.dumps(record))
        row.update(
            status="not_applicable",
            reason="Protection unavailable for this private repository on GitHub Free",
            user_approval="approved",
            host_query={"path": "host-query.json", "contains": ["HTTP 403"]},
        )
        self.ledger["rules"][1] = row
        result = self._run()
        assert result.returncode == 0, result.stderr
        for field, message in (
            ("reason", "missing not-applicable reason"),
            ("user_approval", "exclusion lacks user approval"),
            ("host_query", "missing host query evidence"),
        ):
            with self.subTest(field=field):
                self.ledger["rules"][1] = dict(row)
                del self.ledger["rules"][1][field]
                result = self._run()
                assert result.returncode != 0
                assert message in result.stderr

    def test_evidence_kind_cannot_be_changed_to_bypass_catalog_contract(self) -> None:
        self.ledger["rules"][0] = self._hook()
        result = self._run()
        assert result.returncode != 0
        assert "evidence kind does not match catalog" in result.stderr
        self.catalog["rules"][0]["evidence_kind"] = "invented"
        self.ledger["rules"][0]["evidence_kind"] = "invented"
        assert "unknown evidence kind" in self._run().stderr

    def test_agentic_rules_match_catalog_schema_and_have_contracts(self) -> None:
        catalog_path = VALIDATOR.parent.parent / "assets/guardrail-rules.json"
        catalog = json.loads(catalog_path.read_text())
        rows = {row["id"]: row for row in catalog["rules"]}
        assert len(rows) == len(catalog["rules"])
        assert AGENTIC_RULE_IDS | V2_RULE_IDS <= rows.keys()
        for rule_id in AGENTIC_RULE_IDS | V2_RULE_IDS:
            with self.subTest(rule=rule_id):
                row = rows[rule_id]
                assert set(row) - {"reference"} == {
                    "id", "kind", "applicability", "contract", "summary", "evidence_kind"
                }
                assert rule_id not in V2_RULE_IDS or "reference" in row
                assert row["kind"] in {"bespoke", "process", "tool"}
                assert row["applicability"] in {"always", "conditional"}
                assert isinstance(row["contract"], dict) and row["contract"]
                assert isinstance(row["summary"], str) and row["summary"]

    def test_ci_parity_contract_names_the_host_neutral_ci_fields(self) -> None:
        catalog_path = VALIDATOR.parent.parent / "assets/guardrail-rules.json"
        rows = {row["id"]: row for row in json.loads(catalog_path.read_text())["rules"]}
        contract = rows["GATE.local-ci-parity"]["contract"]
        assert contract["required_context_field"] == "ci.requiredContext"
        assert contract["runner_field"] == "ci.runner"
        assert "ciHost" not in json.dumps(contract)

    def test_agentic_rules_use_their_catalog_evidence_protocol(self) -> None:
        catalog_path = VALIDATOR.parent.parent / "assets/guardrail-rules.json"
        catalog = json.loads(catalog_path.read_text())
        self.catalog["rules"] = [
            row for row in catalog["rules"] if row["id"] in AGENTIC_RULE_IDS
        ]
        self.ledger["rules"] = []
        for row in self.catalog["rules"]:
            rule_id = row["id"]
            if row["evidence_kind"] == "git_hook":
                proof = self._hook(rule_id)
            elif row["evidence_kind"] == "host_query":
                proof = self._host()
                proof["id"] = rule_id
            else:
                proof = self._implemented(rule_id)
            self.ledger["rules"].append(proof)
        result = self._run()
        assert result.returncode == 0, result.stderr
        assert f"PASS: {len(AGENTIC_RULE_IDS)} rules, 0 exclusions" in result.stdout
        self.ledger["rules"].pop()
        result = self._run()
        assert result.returncode != 0
        assert "missing rule:" in result.stderr

    def test_catalog_reference_must_name_omp_guardrails_modules(self) -> None:
        for reference in (
            [],
            "omp-guardrails naming",
            [{"subcommand": "naming"}],
            [{"subcommand": "naming", "module": "checks.naming"}],
            [{"subcommand": " ", "module": "omp_guardrails.structure.naming"}],
        ):
            with self.subTest(reference=reference):
                self.catalog["rules"][0]["reference"] = reference
                result = self._run()
                assert result.returncode != 0
                assert "invalid catalog reference: RULE.A" in result.stderr
        self.catalog["rules"][0]["reference"] = [
            {"subcommand": "naming", "module": "omp_guardrails.structure.naming"}
        ]
        result = self._run()
        assert result.returncode == 0, result.stderr

    def test_catalog_references_resolve_to_reference_subcommands_and_modules(self) -> None:
        sys.path.insert(0, str(REFERENCE_ROOT))
        self.addCleanup(sys.path.remove, str(REFERENCE_ROOT))
        from omp_guardrails import cli

        catalog = json.loads((VALIDATOR.parents[1] / "assets/guardrail-rules.json").read_text())
        for row in catalog["rules"]:
            for item in row.get("reference", []):
                subcommand, module = item["subcommand"], item["module"]
                with self.subTest(rule=row["id"], subcommand=subcommand, module=module):
                    assert subcommand in cli.SUBCOMMANDS
                    if subcommand == "adapter":
                        assert module.startswith(cli.ADAPTER_PACKAGE + ".")
                    else:
                        assert module in cli.SUBCOMMANDS[subcommand].modules
                    sources = _module_sources(module)
                    assert sources, f"missing module {module}"
                    # The module, or a reference module it imports, emits the rule ID.
                    sources += [
                        source
                        for imported in _reference_imports(sources)
                        for source in _module_sources(imported)
                    ]
                    assert any(
                        row["id"] in source.read_text(encoding="utf-8") for source in sources
                    ), f"{module} never names {row['id']}"

    def test_target_derived_tool_rules_require_live_configuration_evidence(self) -> None:
        assets = VALIDATOR.parent.parent / "assets"
        bundled = json.loads((assets / "guardrail-rules.json").read_text())
        target_rules = {"MYPY.SETTING.mypy_path", "MYPY.package-coverage"}
        self.catalog["rules"] = [
            row for row in bundled["rules"] if row["id"] in target_rules
        ]
        for roots, packages in (
            ("src", "invoice, billing"),
            ("lib:plugins", "server, adapter"),
        ):
            with self.subTest(roots=roots, packages=packages):
                settings = [f"mypy_path = {roots}", f"packages = {packages}"]
                (self.root / "config.toml").write_text("\n".join(settings))
                self.ledger["rules"] = []
                for row in self.catalog["rules"]:
                    proof = self._implemented(row["id"])
                    proof["configuration"] = {
                        "path": "config.toml", "contains": settings,
                    }
                    self.ledger["rules"].append(proof)
                result = self._run()
                assert result.returncode == 0, result.stderr
                assert "PASS: 2 rules, 0 exclusions" in result.stdout
                (self.root / "config.toml").write_text("packages = missing\n")
                result = self._run()
                assert result.returncode != 0
                assert "configuration assertion failed" in result.stderr

    def test_complete_bundled_ledger_passes_structure_only(self) -> None:
        assets = VALIDATOR.parent.parent / "assets"
        self.catalog = json.loads((assets / "guardrail-rules.json").read_text())
        self.ledger["rules"] = []
        for rule in self.catalog["rules"]:
            rule_id = rule["id"]
            if rule.get("evidence_kind") == "git_hook":
                proof = self._hook(rule_id)
            elif rule.get("evidence_kind") == "host_query":
                proof = self._host()
                proof["id"] = rule_id
            else:
                proof = self._implemented(rule_id)
            if rule["kind"] == "tool":
                proof["configuration"] = {
                    "path": "config.toml", "contains": ["enabled = true"],
                }
            self.ledger["rules"].append(proof)
        result = self._run(structure_only=True)
        assert result.returncode == 0, result.stderr
        assert result.stdout == (
            f"rule coverage STRUCTURE_ONLY: {len(self.catalog['rules'])} rules, 0 exclusions\n"
        )

    def test_bundled_example_fails_for_every_unimplemented_rule(self) -> None:
        assets = VALIDATOR.parent.parent / "assets"
        catalog = json.loads((assets / "guardrail-rules.json").read_text())
        result = subprocess.run(
            [
                sys.executable,
                str(VALIDATOR),
                "--root",
                str(self.root),
                "--catalog",
                str(assets / "guardrail-rules.json"),
                "--ledger",
                str(assets / "coverage-ledger.example.json"),
                "--structure-only",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        assert result.returncode != 0
        example = json.loads((assets / "coverage-ledger.example.json").read_text())
        assert {rule["id"] for rule in example["rules"]} == {
            rule["id"] for rule in catalog["rules"]
        }
        assert "missing rule:" not in result.stderr
        for rule in catalog["rules"]:
            assert f"{rule['id']}: rule is not implemented" in result.stderr


if __name__ == "__main__":
    unittest.main()
