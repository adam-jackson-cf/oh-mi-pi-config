import json
import re
import unittest
from pathlib import Path

import pytest

pytestmark = pytest.mark.fast

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "agent/extensions/agent-rails/references"
CATALOGUE = ROOT / "agent/extensions/agent-rails/engine/data/guardrail-rules.json"
JUDGMENTS = ROOT / "agent/extensions/agent-rails/judgments"
RULES = ROOT / "agent/extensions/agent-rails/rules"
THRESHOLDS = JUDGMENTS / "structure-naming/thresholds.json"
FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
QUESTION = re.compile(r"^question: >-\n((?:  \S.*\n?)+)", re.M)


def rule_question(name: str) -> str:
    path = RULES / f"{name}.md"
    assert path.is_file(), path
    frontmatter = FRONTMATTER.match(path.read_text(encoding="utf-8"))
    assert frontmatter, path
    question = QUESTION.search(frontmatter.group(1))
    assert question, path
    return " ".join(line.strip() for line in question.group(1).splitlines())


class DeliveryPolicyTests(unittest.TestCase):
    def test_structure_guidance_holds_only_what_no_rule_or_judgment_decides(
        self,
    ) -> None:
        catalogue = json.loads(CATALOGUE.read_text(encoding="utf-8"))
        decisions = json.loads(THRESHOLDS.read_text(encoding="utf-8"))["decisions"]
        names = {row["id"] for row in catalogue["rules"]} | set(decisions)
        text = (POLICY / "structure-and-naming.md").read_text(encoding="utf-8")
        lines = [line for line in text.splitlines() if line.startswith("- ")]
        assert lines
        for line in lines:
            with self.subTest(line=line):
                assert [name for name in names if name in line] == []

    def test_judged_rules_carry_their_guidance_and_question(self) -> None:
        names = sorted(path.stem for path in RULES.glob("*.md"))
        assert names == [
            "clean-cutovers",
            "flakes",
            "measured-claims",
            "regression-coverage",
        ]
        for name in names:
            with self.subTest(rule=name):
                assert "?" in rule_question(name)
                text = (RULES / f"{name}.md").read_text(encoding="utf-8")
                assert "skill://" not in text

    def test_style_lists_have_one_canonical_data_home(self) -> None:
        style_path = CATALOGUE.with_name("house-style.json")
        house_style = json.loads(style_path.read_text(encoding="utf-8"))
        text = (POLICY / "structure-and-naming.md").read_text(encoding="utf-8")
        assert [
            word for word in house_style["banned_words"] if f"`{word}`" in text
        ] == []
        local = [
            source["source"].removeprefix("oh-mi-pi-config ")
            for source in house_style["sources"]
            if source["source"].startswith("oh-mi-pi-config agent/")
        ]
        assert len(local) == 1
        for path in local:
            assert (ROOT / path).is_file(), path


if __name__ == "__main__":
    unittest.main()
