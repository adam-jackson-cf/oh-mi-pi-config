from pathlib import Path
import json
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "agent/skills"
INDEX = SKILLS / "omp-dyna-workflows/references/test-enforcement.md"
POINTER = re.compile(
    r"^- ([A-Z]+\.[A-Za-z0-9.-]+) \(omp-guardrails ([a-z-]+(?:, [a-z-]+)*)\)"
    r" → skill://test-guidance/references/([a-z-]+\.md)#([a-z0-9-]+)$",
    re.MULTILINE,
)


class TestEnforcementTests(unittest.TestCase):
    def test_every_enforcement_pointer_resolves_to_a_catalogue_rule_command_and_guidance_anchor(self):
        catalogue = json.loads((SKILLS / "../extensions/dynaflows/guardrails/omp_guardrails/data/guardrail-rules.json").read_text(encoding="utf-8"))
        commands = {
            row["id"]: {item["subcommand"] for item in row.get("reference", [])}
            for row in catalogue["rules"]
        }
        text = INDEX.read_text(encoding="utf-8")
        pointers = POINTER.findall(text)
        self.assertTrue(pointers)
        self.assertEqual(len(pointers), len(re.findall(r"^- ", text, re.MULTILINE)))
        self.assertEqual(len(pointers), len({row[0] for row in pointers}))
        for rule, subcommands, filename, anchor in pointers:
            with self.subTest(rule=rule, target=f"{filename}#{anchor}"):
                self.assertIn(rule, commands)
                self.assertLessEqual(set(subcommands.split(", ")), commands[rule])
                target = SKILLS / "test-guidance/references" / filename
                self.assertTrue(target.is_file())
                headings = re.findall(r"^#{1,6} (.+)$", target.read_text(encoding="utf-8"), re.MULTILINE)
                anchors = {re.sub(r"[^\w-]", "", heading.lower().replace(" ", "-")) for heading in headings}
                self.assertIn(anchor, anchors)

    def test_generic_guidance_has_no_workflow_mechanisms_or_enforcement_tags(self):
        prohibited = re.compile(r"dynaflows|freez|frozen|prove\(|proof task|omp-guardrails|\bomp\b|Enforced by", re.IGNORECASE)
        for source in (SKILLS / "test-guidance").rglob("*.md"):
            with self.subTest(source=str(source.relative_to(ROOT))):
                self.assertIsNone(prohibited.search(source.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
