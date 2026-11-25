import json
import re
import unittest
from pathlib import Path

import pytest

pytestmark = pytest.mark.fast

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "agent/extensions/dynaflows/references"
CATALOGUE = (
    ROOT / "agent/extensions/dynaflows/engine/guardrails/data/guardrail-rules.json"
)
JUDGMENTS = ROOT / "agent/extensions/dynaflows/judgments"
RULES = ROOT / "agent/extensions/dynaflows/rules"
THRESHOLDS = JUDGMENTS / "structure-naming/thresholds.json"
ENFORCED = re.compile(
    r"Enforced by ([A-Z]+\.[A-Za-z0-9.-]+) \(guardrails ([a-z-]+(?:, [a-z-]+)*)\)\."
)
JUDGED = re.compile(r"Judged by ([a-z-]+)/([a-z_-]+)\.")
FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
QUESTION = re.compile(r"^question: >-\n((?:  \S.*\n?)+)", re.M)


def rule_question(name: str) -> str:
    path = RULES / f"dynaflows-{name}.md"
    assert path.is_file(), path
    frontmatter = FRONTMATTER.match(path.read_text(encoding="utf-8"))
    assert frontmatter, path
    question = QUESTION.search(frontmatter.group(1))
    assert question, path
    return " ".join(line.strip() for line in question.group(1).splitlines())


def assert_policy_line(
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
        assert judgment == "structure-naming"
        assert decision in decisions
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
        lines = (POLICY / "structure-and-naming.md").read_text(encoding="utf-8")
        rule_lines = [
            (number, line)
            for number, line in enumerate(lines.splitlines(), 1)
            if line.startswith("- ")
        ]
        assert rule_lines
        for number, line in rule_lines:
            with self.subTest(line=number):
                assert_policy_line(line, rules, native_rules, decisions)

    def test_judged_rules_carry_their_guidance_and_question(self) -> None:
        names = sorted(
            path.stem.removeprefix("dynaflows-") for path in RULES.glob("*.md")
        )
        assert names == [
            "clean-cutovers",
            "flakes",
            "measured-claims",
            "regression-coverage",
        ]
        for name in names:
            with self.subTest(rule=name):
                assert "?" in rule_question(name)
                text = (RULES / f"dynaflows-{name}.md").read_text(encoding="utf-8")
                assert "skill://" not in text

    def test_style_lists_have_one_canonical_data_home(self) -> None:
        style_path = CATALOGUE.with_name("house-style.json")
        house_style = json.loads(style_path.read_text(encoding="utf-8"))
        text = (POLICY / "structure-and-naming.md").read_text(encoding="utf-8")
        assert "house-style.json" in text
        assert [
            word for word in house_style["banned_words"] if f"`{word}`" in text
        ] == []
        local = [
            source["source"].removeprefix("oh-mi-pi-config ")
            for source in house_style["sources"]
            if source["source"].startswith("oh-mi-pi-config agent/")
        ]
        assert len(local) == 3
        for path in local:
            assert (ROOT / path).is_file(), path


if __name__ == "__main__":
    unittest.main()
