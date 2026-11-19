import json
import re
import unittest
from pathlib import Path

import pytest

pytestmark = pytest.mark.fast

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "agent/extensions/dynaflows/skills/dynaflows/references"
CATALOGUE = (
    ROOT / "agent/extensions/dynaflows/engine/guardrails/data/guardrail-rules.json"
)
THRESHOLDS = (
    ROOT / "agent/extensions/dynaflows/judgments/structure-naming/thresholds.json"
)
ENFORCED = re.compile(
    r"Enforced by ([A-Z]+\.[A-Za-z0-9.-]+) \(guardrails ([a-z-]+(?:, [a-z-]+)*)\)\."
)
JUDGED = re.compile(r"Judged by ([a-z-]+)/([a-z_-]+)\.")


def assert_policy_line(
    name: str,
    line: str,
    rules: dict[str, set[str]],
    native_rules: set[str],
    decisions: dict[str, object],
) -> None:
    enforced = list(ENFORCED.finditer(line))
    judged = list(JUDGED.finditer(line))
    guidance = line.count("Guidance only.")
    assert len(re.findall(r"Enforced by|Judged by|Guidance only", line)) == 1, line
    assert len(enforced) + len(judged) + guidance == 1, line
    assert line.endswith("."), line
    for match in enforced:
        rule, commands = match.groups()
        assert rule in rules
        if rules[rule]:
            assert set(commands.split(", ")) <= rules[rule]
        else:
            assert rule in native_rules
            assert commands == "toolchain-parity"
        assert match.end() == len(line)
    for match in judged:
        judgment, decision = match.groups()
        if name == "structure-and-naming.md":
            assert judgment == "structure-naming"
            assert decision in decisions
        else:
            assert (judgment, decision) in {
                ("main", "implementation-integrity"),
                ("dynaflows", "clean-cutovers"),
                ("dynaflows", "regression-coverage"),
            }
        assert match.end() == len(line)


class DeliveryPolicyTests(unittest.TestCase):
    def test_policy_rules_have_exactly_one_valid_status(self) -> None:
        catalogue = json.loads(CATALOGUE.read_text(encoding="utf-8"))
        rules = {
            row["id"]: {ref["subcommand"] for ref in row.get("reference", [])}
            for row in catalogue["rules"]
        }
        native_rules = {
            row["id"] for row in catalogue["rules"] if row["kind"] == "tool"
        }
        decisions = json.loads(THRESHOLDS.read_text(encoding="utf-8"))["decisions"]
        for name in ("structure-and-naming.md", "delivery-principles.md"):
            lines = (POLICY / name).read_text(encoding="utf-8").splitlines()
            rule_lines = [
                (number, line)
                for number, line in enumerate(lines, 1)
                if line.startswith("- ")
            ]
            assert rule_lines, name
            for number, line in rule_lines:
                with self.subTest(source=name, line=number):
                    assert_policy_line(name, line, rules, native_rules, decisions)

    def test_style_lists_have_one_canonical_data_home(self) -> None:
        style_path = CATALOGUE.with_name("house-style.json")
        house_style = json.loads(style_path.read_text(encoding="utf-8"))
        text = (POLICY / "structure-and-naming.md").read_text(encoding="utf-8")
        assert "house-style.json" in text
        assert [
            word for word in house_style["banned_words"] if f"`{word}`" in text
        ] == []
        for source in house_style["sources"][:2]:
            assert (ROOT / source["source"].removeprefix("oh-mi-pi-config ")).is_file()


if __name__ == "__main__":
    unittest.main()
